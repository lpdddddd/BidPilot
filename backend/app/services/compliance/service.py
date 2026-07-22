"""Persist compliance runs/findings and expose report APIs."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.db.session import SessionLocal
from app.models.compliance import ComplianceFinding as ComplianceFindingRow
from app.models.compliance import ComplianceRun
from app.models.enums import ExtractionRunStatus
from app.models.project import BidProject
from app.schemas.compliance import (
    ComplianceFinding,
    ComplianceFindingFilters,
    ComplianceFindingListResponse,
    ComplianceReport,
    ComplianceRuleInfo,
    ComplianceRuleListResponse,
    ComplianceRunRead,
    ComplianceStartRequest,
)
from app.services.compliance.config import ENGINE_VERSION
from app.services.compliance.context import load_compliance_context
from app.services.compliance.engine import ComplianceEngine
from app.services.compliance.registry import get_default_registry

_SECRET_PATTERNS = (
    re.compile(r"(?i)(api[_-]?key|token|secret|password|authorization)\s*[:=]\s*\S+"),
    re.compile(r"(?i)bearer\s+[a-z0-9\-._~+/]+=*"),
    re.compile(r"(?i)postgres(?:ql)?://\S+"),
)


def _payload_hash(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _now() -> datetime:
    return datetime.now(UTC)


def sanitize_error_summary(message: str | None, *, max_len: int = 500) -> str:
    """Truncate and strip secrets/stack traces from error summaries."""
    text = " ".join(str(message or "compliance run failed").split())
    for pat in _SECRET_PATTERNS:
        text = pat.sub("[REDACTED]", text)
    # Drop traceback-like tails
    if "Traceback" in text:
        text = text.split("Traceback", 1)[0].strip() or "compliance run failed"
    if len(text) > max_len:
        text = text[: max_len - 1] + "…"
    return text


def _persist_failed_run(
    run_id: UUID,
    *,
    error_code: str,
    error_summary: str,
    db: Session | None = None,
    bind=None,
) -> None:
    """Persist failed status so it survives the request session rollback.

    Prefer the caller's session after rollback (same DB as the already-committed
    running row). Fall back to a short-lived session on ``bind`` / SessionLocal.
    """
    summary = sanitize_error_summary(error_summary)
    code = error_code[:64]
    if db is not None:
        run = db.get(ComplianceRun, run_id)
        if run is None:
            return
        run.status = ExtractionRunStatus.failed
        run.finished_at = _now()
        run.error_code = code
        run.error_summary = summary
        db.commit()
        return

    if bind is not None:
        factory = sessionmaker(
            bind=bind,
            autoflush=False,
            autocommit=False,
            expire_on_commit=False,
            class_=Session,
        )
        local = factory()
    else:
        local = SessionLocal()
    try:
        run = local.get(ComplianceRun, run_id)
        if run is None:
            return
        run.status = ExtractionRunStatus.failed
        run.finished_at = _now()
        run.error_code = code
        run.error_summary = summary
        local.commit()
    except Exception:  # noqa: BLE001
        local.rollback()
    finally:
        local.close()


class ComplianceService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.engine = ComplianceEngine()

    def list_rules(self) -> ComplianceRuleListResponse:
        rules = get_default_registry().list_rules()
        items = [
            ComplianceRuleInfo(
                rule_id=r.rule_id,
                name=r.name,
                category=r.category,
                description=r.description,
                default_severity=r.default_severity,
            )
            for r in rules
        ]
        return ComplianceRuleListResponse(
            items=items,
            total=len(items),
            engine_version=ENGINE_VERSION,
        )

    def start_run(
        self,
        project_id: UUID,
        request: ComplianceStartRequest | None = None,
        *,
        idempotency_key: str | None = None,
        draft_id: UUID | None = None,
    ) -> ComplianceReport:
        project = self.db.get(BidProject, project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="project not found")

        req = request or ComplianceStartRequest()
        effective_draft_id = draft_id if draft_id is not None else req.draft_id
        payload = {
            "draft_id": str(effective_draft_id) if effective_draft_id else None,
            "rule_ids": req.rule_ids,
            "categories": [c.value for c in req.categories] if req.categories else None,
            "engine_version": ENGINE_VERSION,
        }
        payload_hash = _payload_hash(payload)

        if idempotency_key:
            existing = self.db.scalar(
                select(ComplianceRun).where(
                    ComplianceRun.project_id == project_id,
                    ComplianceRun.idempotency_key == idempotency_key,
                )
            )
            if existing is not None:
                prev_hash = (existing.config_json or {}).get("payload_hash")
                if prev_hash and prev_hash != payload_hash:
                    raise HTTPException(
                        status_code=409,
                        detail="idempotency key reused with different payload",
                    )
                return self.get_report(project_id, existing.id)

        # 1) Create + commit run as running immediately so failure can be durably recorded
        run = ComplianceRun(
            project_id=project_id,
            status=ExtractionRunStatus.running,
            draft_id=effective_draft_id,
            engine_version=ENGINE_VERSION,
            idempotency_key=idempotency_key,
            config_json={**payload, "payload_hash": payload_hash},
            rule_ids_json=req.rule_ids,
            started_at=_now(),
        )
        self.db.add(run)
        self.db.commit()
        self.db.refresh(run)
        run_id = run.id

        try:
            ctx = load_compliance_context(self.db, project_id, draft_id=effective_draft_id)
            findings, stats = self.engine.run(
                ctx,
                rule_ids=req.rule_ids,
                categories=req.categories,
            )
            self._persist_findings(run, findings, stats)
            run.status = ExtractionRunStatus.succeeded
            run.finished_at = _now()
            run.error_code = None
            run.error_summary = None
            self.db.commit()
            self.db.refresh(run)
            return self._to_report(run)
        except HTTPException as exc:
            # Roll back any partial business work on the request session
            self.db.rollback()
            _persist_failed_run(
                run_id,
                error_code=f"http_{exc.status_code}",
                error_summary=str(exc.detail),
                db=self.db,
            )
            # Keep run row refreshed before re-raising structured HTTP error.
            self._load_run_fresh(project_id, run_id)
            raise HTTPException(
                status_code=exc.status_code,
                detail={
                    "message": exc.detail if isinstance(exc.detail, str) else "HTTP error",
                    "run_id": str(run_id),
                    "error_code": f"http_{exc.status_code}",
                    "status": "failed",
                },
            ) from exc
        except Exception as exc:  # noqa: BLE001
            self.db.rollback()
            error_code = type(exc).__name__[:64]
            summary = sanitize_error_summary(f"{type(exc).__name__}: {exc}")
            _persist_failed_run(
                run_id,
                error_code=error_code,
                error_summary=summary,
                db=self.db,
            )
            raise HTTPException(
                status_code=500,
                detail={
                    "message": "compliance run failed",
                    "run_id": str(run_id),
                    "error_code": error_code,
                    "error_summary": summary,
                    "status": "failed",
                },
            ) from exc

    def _load_run_fresh(self, project_id: UUID, run_id: UUID) -> ComplianceRun:
        self.db.expire_all()
        run = self.db.get(ComplianceRun, run_id)
        if run is None or run.project_id != project_id:
            raise HTTPException(status_code=404, detail="compliance run not found")
        return run

    def _persist_findings(
        self,
        run: ComplianceRun,
        findings: list[ComplianceFinding],
        stats: dict[str, Any],
    ) -> None:
        run.total_checks = int(stats.get("total_checks") or 0)
        run.passed_checks = int(stats.get("passed_checks") or 0)
        run.finding_count = int(stats.get("finding_count") or 0)
        run.severity_counts_json = dict(stats.get("severity_counts") or {})
        run.category_counts_json = dict(stats.get("category_counts") or {})
        run.rule_ids_json = list(stats.get("rule_ids") or [])
        run.engine_version = str(stats.get("engine_version") or ENGINE_VERSION)

        for finding in findings:
            row = ComplianceFindingRow(
                project_id=run.project_id,
                run_id=run.id,
                finding_id=finding.finding_id,
                rule_id=finding.rule_id,
                rule_name=finding.rule_name,
                category=finding.category,
                severity=finding.severity,
                status=finding.status,
                message=finding.message,
                remediation=finding.remediation,
                requirement_id=finding.requirement_id,
                match_id=finding.match_id,
                draft_id=finding.draft_id or run.draft_id,
                evidence_json=finding.evidence_json,
                source_location_json=finding.source_location_json,
                metadata_json=finding.metadata_json,
            )
            self.db.add(row)
        self.db.flush()

    def get_run(self, project_id: UUID, run_id: UUID) -> ComplianceRunRead:
        run = self._get_run(project_id, run_id)
        return ComplianceRunRead.model_validate(run)

    def get_report(self, project_id: UUID, run_id: UUID) -> ComplianceReport:
        run = self._get_run(project_id, run_id)
        return self._to_report(run)

    def get_latest(self, project_id: UUID) -> ComplianceReport | None:
        run = self.db.scalar(
            select(ComplianceRun)
            .where(ComplianceRun.project_id == project_id)
            .order_by(ComplianceRun.created_at.desc())
            .limit(1)
        )
        if run is None:
            return None
        return self._to_report(run)

    def list_findings(
        self,
        project_id: UUID,
        filters: ComplianceFindingFilters | None = None,
    ) -> ComplianceFindingListResponse:
        filters = filters or ComplianceFindingFilters()
        stmt = select(ComplianceFindingRow).where(ComplianceFindingRow.project_id == project_id)
        if filters.run_id:
            stmt = stmt.where(ComplianceFindingRow.run_id == filters.run_id)
        else:
            latest = self.db.scalar(
                select(ComplianceRun)
                .where(ComplianceRun.project_id == project_id)
                .order_by(ComplianceRun.created_at.desc())
                .limit(1)
            )
            if latest is None:
                return ComplianceFindingListResponse(items=[], total=0, run_id=None)
            stmt = stmt.where(ComplianceFindingRow.run_id == latest.id)
            filters.run_id = latest.id

        if filters.severity:
            stmt = stmt.where(ComplianceFindingRow.severity == filters.severity)
        if filters.category:
            stmt = stmt.where(ComplianceFindingRow.category == filters.category)
        if filters.rule_id:
            stmt = stmt.where(ComplianceFindingRow.rule_id == filters.rule_id)
        if filters.requirement_id:
            stmt = stmt.where(ComplianceFindingRow.requirement_id == filters.requirement_id)
        if filters.draft_id:
            stmt = stmt.where(ComplianceFindingRow.draft_id == filters.draft_id)
        if filters.status:
            stmt = stmt.where(ComplianceFindingRow.status == filters.status)

        rows = list(
            self.db.scalars(
                stmt.order_by(
                    ComplianceFindingRow.severity.asc(),
                    ComplianceFindingRow.finding_id.asc(),
                )
            ).all()
        )
        total = len(rows)
        page = rows[filters.offset : filters.offset + filters.limit]
        return ComplianceFindingListResponse(
            items=[ComplianceFinding.model_validate(r) for r in page],
            total=total,
            run_id=filters.run_id,
        )

    def _get_run(self, project_id: UUID, run_id: UUID) -> ComplianceRun:
        run = self.db.get(ComplianceRun, run_id)
        if run is None or run.project_id != project_id:
            raise HTTPException(status_code=404, detail="compliance run not found")
        return run

    def _to_report(self, run: ComplianceRun) -> ComplianceReport:
        rows = list(
            self.db.scalars(
                select(ComplianceFindingRow)
                .where(ComplianceFindingRow.run_id == run.id)
                .order_by(
                    ComplianceFindingRow.category.asc(),
                    ComplianceFindingRow.rule_id.asc(),
                    ComplianceFindingRow.finding_id.asc(),
                )
            ).all()
        )
        return ComplianceReport(
            run=ComplianceRunRead.model_validate(run),
            findings=[ComplianceFinding.model_validate(r) for r in rows],
            engine_version=run.engine_version,
            total_checks=run.total_checks,
            passed_checks=run.passed_checks,
            finding_count=run.finding_count,
            severity_counts=dict(run.severity_counts_json or {}),
            category_counts=dict(run.category_counts_json or {}),
        )
