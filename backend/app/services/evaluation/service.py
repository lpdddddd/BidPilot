"""Project-scoped evaluation service."""

from __future__ import annotations

import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.models.enums import EvaluationRunStatus, EvaluationTargetType
from app.models.evaluation import EvaluationCaseResult, EvaluationRun, EvaluationSuite
from app.models.project import BidProject
from app.services.evaluation import BUILTIN_SUITE_NAME, BUILTIN_SUITE_VERSION, EVALUATOR_VERSION
from app.services.evaluation.citations import validate_citations_for_result
from app.services.evaluation.claims import (
    EvalClaimOutcome,
    EvalClaimResult,
    claim_evaluation_run,
    release_evaluation_claim,
)
from app.services.evaluation.profiles import EVALUATOR_PROFILE_VERSION, all_profiles, get_profile
from app.services.evaluation.report import (
    build_report_dict,
    serialize_csv,
    serialize_json,
    serialize_markdown,
)
from app.services.evaluation.runner import execute_evaluation_run
from app.services.evaluation.suite_loader import build_manifest_snapshot, load_reference_suite
from app.services.evaluation.targets import allow_fake_targets, list_capabilities


def _now():
    return datetime.now(UTC)


def _git_sha() -> str | None:
    env_sha = os.getenv("GITHUB_SHA") or os.getenv("SOURCE_COMMIT")
    if env_sha:
        return env_sha
    try:
        repo = Path(__file__).resolve().parents[4]
        out = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo),
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        return out or None
    except Exception:
        return None


def _detail_url(project_id: UUID, run_id: UUID) -> str:
    return f"/api/v1/projects/{project_id}/evaluation-runs/{run_id}"


def _pass_rate(run: EvaluationRun) -> float | None:
    done = int(run.passed_cases or 0) + int(run.failed_cases or 0)
    if done <= 0:
        return None
    return float(run.passed_cases or 0) / float(done)


class EvaluationService:
    def __init__(self, db: Session):
        self.db = db

    def _project(self, project_id: UUID) -> BidProject:
        project = self.db.get(BidProject, project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="project not found")
        return project

    def ensure_builtin_suite(self, project_id: UUID | None = None) -> EvaluationSuite:
        bundle = load_reference_suite()
        existing = self.db.scalar(
            select(EvaluationSuite).where(
                EvaluationSuite.name == BUILTIN_SUITE_NAME,
                EvaluationSuite.version == BUILTIN_SUITE_VERSION,
                EvaluationSuite.dataset_hash == bundle.dataset_hash,
            )
        )
        if existing:
            return existing
        suite = EvaluationSuite(
            project_id=None,
            name=BUILTIN_SUITE_NAME,
            version=BUILTIN_SUITE_VERSION,
            description="Step 1 auto_reference multi-task suite (never human_gold)",
            manifest_snapshot=build_manifest_snapshot(bundle),
            dataset_hash=bundle.dataset_hash,
            evaluator_profile_version=EVALUATOR_PROFILE_VERSION,
            task_family_config=bundle.stats.get("task_family_counts"),
        )
        self.db.add(suite)
        self.db.commit()
        self.db.refresh(suite)
        return suite

    def capabilities(self, project_id: UUID) -> dict[str, Any]:
        self._project(project_id)
        caps = list_capabilities(allow_fake=allow_fake_targets())
        bundle = load_reference_suite()
        stats = bundle.stats
        rk = dict(stats.get("reference_kind_counts") or {})
        profiles = []
        for family, profile in all_profiles().items():
            profiles.append(
                {
                    "id": family,
                    "name": f"{family} profile",
                    "version": profile["version"],
                    "enabled_metrics": list(profile["metric_weights"].keys()),
                    "ai_judge_enabled": bool(profile.get("include_judge_in_overall")),
                }
            )
        # default aggregate profile entry
        default = get_profile("rag")
        if not any(p["id"] == "default" for p in profiles):
            profiles.insert(
                0,
                {
                    "id": "default",
                    "name": "default",
                    "version": default["version"],
                    "enabled_metrics": sorted(
                        {m for p in all_profiles().values() for m in p["metric_weights"]}
                    ),
                    "ai_judge_enabled": False,
                },
            )
        from app.services.evaluation.target_capabilities import required_capability_for_target

        return {
            "items": [
                {
                    "target_type": c.target_type,
                    "available": c.available,
                    "reason": c.reason,
                    "reason_code": c.reason_code,
                    "required_capability": required_capability_for_target(c.target_type),
                }
                for c in caps
            ],
            "profiles": profiles,
            "evaluator_version": EVALUATOR_VERSION,
            "dataset": {
                "name": BUILTIN_SUITE_NAME,
                "version": BUILTIN_SUITE_VERSION,
                "dataset_hash": bundle.dataset_hash,
                "hash_short": bundle.dataset_hash[:12],
                "total_cases": stats.get("total_cases"),
                "task_family_counts": stats.get("task_family_counts"),
                "split_counts": stats.get("split_counts"),
                "reference_kind_counts": rk,
                "direct_reference_coverage": stats.get("direct_reference_coverage"),
                "human_gold_count": int(rk.get("human_gold") or 0),
                "auto_reference_count": int(rk.get("auto_reference") or 0),
                "rule_expected_count": int(rk.get("rule_expected") or 0),
                "no_direct_reference_count": int(rk.get("no_direct_reference") or 0),
                "label_policy": stats.get("label_policy"),
            },
            "task_families": sorted((stats.get("task_family_counts") or {}).keys()),
            "splits": sorted((stats.get("split_counts") or {}).keys()),
        }

    def list_suites(
        self, project_id: UUID, *, page: int = 1, page_size: int = 50
    ) -> tuple[list[EvaluationSuite], int]:
        self._project(project_id)
        self.ensure_builtin_suite()
        q = select(EvaluationSuite).where(
            (EvaluationSuite.project_id.is_(None)) | (EvaluationSuite.project_id == project_id)
        )
        total = self.db.scalar(select(func.count()).select_from(q.subquery())) or 0
        offset = max(0, (page - 1) * page_size)
        rows = list(
            self.db.scalars(
                q.order_by(EvaluationSuite.created_at.desc()).offset(offset).limit(page_size)
            ).all()
        )
        return rows, int(total)

    def get_suite(self, project_id: UUID, suite_id: UUID) -> EvaluationSuite:
        self._project(project_id)
        suite = self.db.get(EvaluationSuite, suite_id)
        if suite is None or (suite.project_id is not None and suite.project_id != project_id):
            raise HTTPException(status_code=404, detail="suite not found")
        return suite

    def create_run(
        self,
        project_id: UUID,
        payload: dict[str, Any],
        *,
        idempotency_key: str | None = None,
        execute: bool = False,
    ) -> tuple[EvaluationRun, EvalClaimResult | None]:
        """Persist queued run, claim, optionally execute sync. Returns (run, claim).

        When execute=False, caller must schedule background work for a claimed run.
        Concurrent identical idempotency keys are resolved via unique constraint.
        """
        from sqlalchemy.exc import IntegrityError

        self._project(project_id)
        idem = idempotency_key or payload.get("idempotency_key")
        if idem:
            existing = self.db.scalar(
                select(EvaluationRun).where(
                    EvaluationRun.project_id == project_id,
                    EvaluationRun.idempotency_key == idem,
                )
            )
            if existing:
                return existing, None
        suite_id = payload.get("suite_id")
        suite = self.get_suite(project_id, suite_id) if suite_id else self.ensure_builtin_suite()
        target_type = (
            payload.get("target")
            or payload.get("target_type")
            or EvaluationTargetType.deterministic_fake.value
        )
        try:
            tt = EvaluationTargetType(target_type)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="unknown target_type") from exc

        allow_fake = allow_fake_targets()
        if tt == EvaluationTargetType.deterministic_fake and not allow_fake:
            raise HTTPException(
                status_code=422,
                detail="deterministic_fake is not available in this environment",
            )
        caps = {c.target_type: c for c in list_capabilities(allow_fake=allow_fake)}
        cap = caps.get(tt.value)
        if cap and not cap.available:
            raise HTTPException(
                status_code=422,
                detail={
                    "message": cap.reason or "target unavailable",
                    "reason_code": cap.reason_code,
                },
            )

        safe_config = {
            k: v
            for k, v in dict(payload.get("target_config") or {}).items()
            if k.lower()
            not in {
                "api_key",
                "token",
                "authorization",
                "password",
                "database_url",
                "prompt",
                "cookie",
            }
        }
        safe_config["seed"] = int(payload.get("seed") or 42)
        # fail_case_keys / fixture_path only via internal test path when fake allowed
        if allow_fake and payload.get("fail_case_keys"):
            safe_config["fail_case_keys"] = list(payload["fail_case_keys"])

        model_id = safe_config.get("model_id")
        if model_id:
            from app.services.evaluation.target_capabilities import required_capability_for_target
            from app.services.evaluation.targets import get_target
            from app.services.model_serving import resolve_model_selection

            resolution = resolve_model_selection(
                str(model_id),
                allow_fallback=False,
                probe=True,
                required_capability=required_capability_for_target(tt.value),
            )
            if not resolution.available:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "message": "模型尚未启动在线服务",
                        "reason_code": (
                            resolution.reason_codes[0]
                            if resolution.reason_codes
                            else "model_not_served"
                        ),
                        "requested_model_id": resolution.requested_model_id,
                    },
                )
            safe_config["model_display_name"] = resolution.display_name
            safe_config["model_type"] = resolution.model_type
            safe_config["adapter_version"] = resolution.adapter_version
            safe_config["served_model_name"] = resolution.served_model_name
            safe_config["dataset_version"] = suite.dataset_hash
            # Re-check adapter-level capability with model_id in config.
            probe = get_target(tt.value, config=safe_config, db=self.db)
            probe_cap = probe.capability()
            if not probe_cap.available:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "message": probe_cap.reason or "模型尚未启动在线服务",
                        "reason_code": probe_cap.reason_code or "model_not_served",
                    },
                )

        case_limit = payload.get("case_limit")
        if case_limit is None:
            case_limit = payload.get("limit")
        filt: dict[str, Any] = {
            "split": payload.get("split"),
            "splits": payload.get("splits"),
            "task_family": payload.get("task_family"),
            "task_families": payload.get("task_families"),
            "limit": case_limit,
            "case_keys": payload.get("case_keys"),
            "evaluator_profile": payload.get("evaluator_profile") or payload.get("profile"),
        }
        if allow_fake and payload.get("fixture_path"):
            filt["fixture_path"] = payload["fixture_path"]
        if allow_fake and payload.get("fixture_id"):
            filt["fixture_id"] = payload["fixture_id"]

        run = EvaluationRun(
            project_id=project_id,
            suite_id=suite.id,
            status=EvaluationRunStatus.queued,
            target_type=tt,
            target_config_snapshot=safe_config,
            dataset_hash=suite.dataset_hash,
            evaluator_version=EVALUATOR_VERSION,
            seed=int(payload.get("seed") or 42),
            idempotency_key=idem,
            source_commit_sha=_git_sha(),
            created_by=payload.get("created_by"),
            filter_json=filt,
        )
        self.db.add(run)
        try:
            self.db.flush()
        except IntegrityError as exc:
            self.db.rollback()
            # Only treat unique-(project_id, idempotency_key) as idempotent hit.
            constraint = getattr(getattr(exc, "orig", None), "diag", None)
            constraint_name = getattr(constraint, "constraint_name", None) or ""
            msg = str(getattr(exc, "orig", exc)).lower()
            is_idem_unique = idem and (
                "idempotency" in msg
                or "idempotency_key" in msg
                or "uq_evaluation_runs_project_idempotency" in msg
                or "uq_evaluation_runs_project_idempotency" in str(constraint_name).lower()
            )
            if is_idem_unique:
                existing = self.db.scalar(
                    select(EvaluationRun).where(
                        EvaluationRun.project_id == project_id,
                        EvaluationRun.idempotency_key == idem,
                    )
                )
                if existing:
                    return existing, None
            raise
        self.db.commit()
        self.db.refresh(run)

        claim = claim_evaluation_run(self.db, run.id, action="start", project_id=project_id)
        if claim.outcome != EvalClaimOutcome.claimed:
            self.db.refresh(run)
            return run, claim

        if execute:
            try:
                run = execute_evaluation_run(self.db, run.id)
            finally:
                release_evaluation_claim(self.db, run.id, claim_token=claim.claim_token)
            return run, None
        return run, claim

    def get_run(self, project_id: UUID, run_id: UUID) -> EvaluationRun:
        self._project(project_id)
        run = self.db.get(EvaluationRun, run_id)
        if run is None or run.project_id != project_id:
            raise HTTPException(status_code=404, detail="evaluation run not found")
        return run

    def list_runs(
        self,
        project_id: UUID,
        *,
        status: str | None = None,
        suite_id: UUID | None = None,
        target_type: str | None = None,
        task_family: str | None = None,
        started_after: datetime | None = None,
        started_before: datetime | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> tuple[list[EvaluationRun], int]:
        self._project(project_id)
        q = select(EvaluationRun).where(EvaluationRun.project_id == project_id)
        if status:
            q = q.where(EvaluationRun.status == status)
        if suite_id:
            q = q.where(EvaluationRun.suite_id == suite_id)
        if target_type:
            q = q.where(EvaluationRun.target_type == target_type)
        if started_after:
            q = q.where(EvaluationRun.started_at >= started_after)
        if started_before:
            q = q.where(EvaluationRun.started_at <= started_before)
        if task_family:
            # Best-effort: filter by exact task_family in filter_json when present.
            rows_all = list(self.db.scalars(q.order_by(EvaluationRun.created_at.desc())).all())
            filtered = [
                r
                for r in rows_all
                if (r.filter_json or {}).get("task_family") == task_family
                or task_family in ((r.filter_json or {}).get("task_families") or [])
            ]
            total = len(filtered)
            offset = max(0, (page - 1) * page_size)
            return filtered[offset : offset + page_size], int(total)
        total = self.db.scalar(select(func.count()).select_from(q.subquery())) or 0
        offset = max(0, (page - 1) * page_size)
        rows = list(
            self.db.scalars(
                q.order_by(EvaluationRun.created_at.desc()).offset(offset).limit(page_size)
            ).all()
        )
        return rows, int(total)

    def get_results(
        self,
        project_id: UUID,
        run_id: UUID,
        *,
        status: str | None = None,
        task_family: str | None = None,
        passed: bool | None = None,
        failed: bool | None = None,
        error: bool | None = None,
        hard_gate: bool | None = None,
        metric: str | None = None,
        page: int = 1,
        page_size: int = 100,
    ) -> tuple[list[EvaluationCaseResult], int]:
        run = self.get_run(project_id, run_id)
        q = select(EvaluationCaseResult).where(EvaluationCaseResult.evaluation_run_id == run.id)
        if status:
            q = q.where(EvaluationCaseResult.status == status)
        if task_family:
            q = q.where(EvaluationCaseResult.task_family == task_family)
        if passed is True:
            q = q.where(EvaluationCaseResult.passed.is_(True))
        if failed is True:
            q = q.where(EvaluationCaseResult.passed.is_(False))
        if error is True:
            q = q.where(EvaluationCaseResult.status == "error")
        rows = list(self.db.scalars(q.order_by(EvaluationCaseResult.case_key.asc())).all())
        if hard_gate is True:
            rows = [r for r in rows if r.hard_gate_failures]
        if metric:
            from app.models.evaluation import EvaluationMetricResult

            ids_with_metric = {
                m.case_result_id
                for m in self.db.scalars(
                    select(EvaluationMetricResult).where(
                        EvaluationMetricResult.metric_name == metric,
                        EvaluationMetricResult.case_result_id.in_(
                            [r.id for r in rows] or [UUID(int=0)]
                        ),
                    )
                ).all()
            }
            rows = [r for r in rows if r.id in ids_with_metric]
        total = len(rows)
        offset = max(0, (page - 1) * page_size)
        return rows[offset : offset + page_size], int(total)

    def get_result(self, project_id: UUID, run_id: UUID, result_id: UUID) -> EvaluationCaseResult:
        self.get_run(project_id, run_id)
        row = self.db.scalar(
            select(EvaluationCaseResult)
            .where(
                EvaluationCaseResult.id == result_id,
                EvaluationCaseResult.evaluation_run_id == run_id,
            )
            .options(selectinload(EvaluationCaseResult.metric_results))
        )
        if row is None:
            raise HTTPException(status_code=404, detail="result not found")
        return row

    def serialize_result(
        self, project_id: UUID, row: EvaluationCaseResult, *, include_metrics: bool = False
    ) -> dict[str, Any]:
        citations = validate_citations_for_result(
            self.db, project_id=project_id, response_snapshot=row.response_snapshot
        )
        data: dict[str, Any] = {
            "id": row.id,
            "evaluation_run_id": row.evaluation_run_id,
            "case_key": row.case_key,
            "case_content_hash": row.case_content_hash,
            "task_family": row.task_family,
            "split": row.split,
            "status": row.status.value if hasattr(row.status, "value") else str(row.status),
            "reference_kind": row.reference_kind.value
            if hasattr(row.reference_kind, "value")
            else str(row.reference_kind),
            "score": row.score,
            "passed": row.passed,
            "hard_gate_failures": row.hard_gate_failures,
            "safe_error_summary": row.safe_error_summary,
            "started_at": row.started_at,
            "finished_at": row.finished_at,
            "duration_ms": row.duration_ms,
            "agent_run_id": row.agent_run_id,
            "input_snapshot": row.input_snapshot,
            "reference_summary": row.reference_summary,
            "response_snapshot": row.response_snapshot,
            "citations": citations,
        }
        if include_metrics:
            data["metric_results"] = [
                {
                    "metric_name": m.metric_name,
                    "metric_version": m.metric_version,
                    "value": m.value,
                    "applicable": m.applicable,
                    "weight": m.weight,
                    "threshold": m.threshold,
                    "passed": m.passed,
                    "evidence_summary": m.evidence_summary,
                    "reference_kind": m.reference_kind.value
                    if hasattr(m.reference_kind, "value")
                    else str(m.reference_kind),
                }
                for m in (row.metric_results or [])
            ]
        return data

    def cancel(self, project_id: UUID, run_id: UUID) -> EvaluationRun:
        run = self.get_run(project_id, run_id)
        if run.status in {EvaluationRunStatus.completed, EvaluationRunStatus.cancelled}:
            return run
        run.cancel_requested = True
        if run.status == EvaluationRunStatus.queued and run.execution_claim_token is None:
            run.status = EvaluationRunStatus.cancelled
            run.finished_at = _now()
        self.db.commit()
        self.db.refresh(run)
        return run

    def resume(
        self, project_id: UUID, run_id: UUID, *, execute: bool = False
    ) -> tuple[EvaluationRun, EvalClaimResult | None]:
        run = self.get_run(project_id, run_id)
        claim = claim_evaluation_run(self.db, run.id, action="resume", project_id=project_id)
        if claim.outcome == EvalClaimOutcome.already_running and claim.run:
            return claim.run, claim
        if claim.outcome != EvalClaimOutcome.claimed:
            raise HTTPException(status_code=409, detail=claim.detail or "cannot resume")
        if execute:
            try:
                run = execute_evaluation_run(self.db, run.id)
            finally:
                release_evaluation_claim(self.db, run.id, claim_token=claim.claim_token)
            return run, None
        return self.get_run(project_id, run_id), claim

    def compare(self, project_id: UUID, left_id: UUID, right_id: UUID) -> dict[str, Any]:
        left = self.get_run(project_id, left_id)
        right = self.get_run(project_id, right_id)
        left_cases = {
            r.case_key: r for r in self.get_results(project_id, left_id, page_size=10_000)[0]
        }
        right_cases = {
            r.case_key: r for r in self.get_results(project_id, right_id, page_size=10_000)[0]
        }
        common = sorted(set(left_cases) & set(right_cases))
        only_left = sorted(set(left_cases) - set(right_cases))
        only_right = sorted(set(right_cases) - set(left_cases))

        def row(k: str) -> dict[str, Any]:
            a, b = left_cases[k], right_cases[k]
            sa, sb = a.score, b.score
            delta = None if sa is None or sb is None else sb - sa
            return {
                "case_key": k,
                "left_score": sa,
                "right_score": sb,
                "left_status": a.status.value if hasattr(a.status, "value") else str(a.status),
                "right_status": b.status.value if hasattr(b.status, "value") else str(b.status),
                "delta": delta,
            }

        improved, regressed, unchanged = [], [], []
        for k in common:
            a, b = left_cases[k], right_cases[k]
            sa, sb = a.score, b.score
            item = row(k)
            if sa is None or sb is None:
                unchanged.append(item)
            elif sb > sa + 1e-9:
                improved.append(item)
            elif sb < sa - 1e-9:
                regressed.append(item)
            else:
                unchanged.append(item)

        warnings: list[str] = []
        if left.dataset_hash != right.dataset_hash:
            warnings.append("dataset_hash_mismatch")
        if left.evaluator_version != right.evaluator_version:
            warnings.append("evaluator_version_mismatch")
        left_suite = self.db.get(EvaluationSuite, left.suite_id)
        right_suite = self.db.get(EvaluationSuite, right.suite_id)
        if left_suite and right_suite and left_suite.version != right_suite.version:
            warnings.append("suite_version_mismatch")

        left_pr = _pass_rate(left)
        right_pr = _pass_rate(right)
        pass_rate_delta = None if left_pr is None or right_pr is None else right_pr - left_pr

        # Task family / metric deltas from summary_json when present
        left_sum = dict(left.summary_json or {})
        right_sum = dict(right.summary_json or {})
        left_fam = dict(left_sum.get("task_family_scores") or {})
        right_fam = dict(right_sum.get("task_family_scores") or {})
        fam_keys = sorted(set(left_fam) | set(right_fam))
        task_family_deltas = {
            k: (
                None
                if left_fam.get(k) is None or right_fam.get(k) is None
                else float(right_fam[k]) - float(left_fam[k])
            )
            for k in fam_keys
        }
        left_met = dict(left_sum.get("metric_averages") or {})
        right_met = dict(right_sum.get("metric_averages") or {})
        met_keys = sorted(set(left_met) | set(right_met))
        metric_deltas = {
            k: (
                None
                if left_met.get(k) is None or right_met.get(k) is None
                else float(right_met[k]) - float(left_met[k])
            )
            for k in met_keys
        }

        left_cfg = dict(left.target_config_snapshot or {})
        right_cfg = dict(right.target_config_snapshot or {})
        config_diff = {
            "left_only": {k: left_cfg[k] for k in left_cfg.keys() - right_cfg.keys()},
            "right_only": {k: right_cfg[k] for k in right_cfg.keys() - left_cfg.keys()},
            "changed": {
                k: {"left": left_cfg[k], "right": right_cfg[k]}
                for k in left_cfg.keys() & right_cfg.keys()
                if left_cfg[k] != right_cfg[k]
            },
        }

        def run_read(run: EvaluationRun) -> dict[str, Any]:
            suite = self.db.get(EvaluationSuite, run.suite_id)
            return {
                "id": run.id,
                "project_id": run.project_id,
                "suite_id": run.suite_id,
                "suite_name": suite.name if suite else None,
                "suite_version": suite.version if suite else None,
                "status": run.status.value,
                "target_type": run.target_type.value,
                "target_config_snapshot": run.target_config_snapshot,
                "dataset_hash": run.dataset_hash,
                "evaluator_version": run.evaluator_version,
                "seed": run.seed,
                "total_cases": run.total_cases,
                "completed_cases": run.completed_cases,
                "passed_cases": run.passed_cases,
                "failed_cases": run.failed_cases,
                "error_cases": run.error_cases,
                "overall_score": run.overall_score,
                "started_at": run.started_at,
                "finished_at": run.finished_at,
                "duration_ms": run.duration_ms,
                "safe_error_summary": run.safe_error_summary,
                "source_commit_sha": run.source_commit_sha,
                "summary_json": run.summary_json,
                "filter_json": run.filter_json,
                "created_at": run.created_at,
                "updated_at": run.updated_at,
                "detail_url": _detail_url(run.project_id, run.id),
            }

        return {
            "left": run_read(left),
            "right": run_read(right),
            "warnings": warnings,
            "overall_score_delta": (
                None
                if left.overall_score is None or right.overall_score is None
                else right.overall_score - left.overall_score
            ),
            "pass_rate_delta": pass_rate_delta,
            "task_family_deltas": task_family_deltas,
            "metric_deltas": metric_deltas,
            "improved_cases": improved,
            "regressed_cases": regressed,
            "unchanged_cases": unchanged,
            "left_only_cases": only_left,
            "right_only_cases": only_right,
            "config_diff": config_diff,
            "common_cases": common,
            "only_left": only_left,
            "only_right": only_right,
        }

    def export(self, project_id: UUID, run_id: UUID, fmt: str = "json") -> tuple[str, str]:
        run = self.get_run(project_id, run_id)
        results, _ = self.get_results(project_id, run_id, page_size=10_000)
        suite = self.db.get(EvaluationSuite, run.suite_id)
        stats = (suite.manifest_snapshot or {}) if suite else {}
        report = build_report_dict(run, results, suite_stats=stats)
        if fmt == "csv":
            return serialize_csv(report), "text/csv"
        if fmt == "markdown":
            return serialize_markdown(report), "text/markdown"
        return serialize_json(report), "application/json"

    def run_to_read(self, run: EvaluationRun) -> dict[str, Any]:
        suite = self.db.get(EvaluationSuite, run.suite_id)
        return {
            "id": run.id,
            "project_id": run.project_id,
            "suite_id": run.suite_id,
            "suite_name": suite.name if suite else None,
            "suite_version": suite.version if suite else None,
            "status": run.status.value if hasattr(run.status, "value") else str(run.status),
            "target_type": run.target_type.value
            if hasattr(run.target_type, "value")
            else str(run.target_type),
            "target_config_snapshot": run.target_config_snapshot,
            "dataset_hash": run.dataset_hash,
            "evaluator_version": run.evaluator_version,
            "seed": run.seed,
            "total_cases": run.total_cases,
            "completed_cases": run.completed_cases,
            "passed_cases": run.passed_cases,
            "failed_cases": run.failed_cases,
            "error_cases": run.error_cases,
            "overall_score": run.overall_score,
            "started_at": run.started_at,
            "finished_at": run.finished_at,
            "duration_ms": run.duration_ms,
            "safe_error_summary": run.safe_error_summary,
            "source_commit_sha": run.source_commit_sha,
            "summary_json": run.summary_json,
            "filter_json": run.filter_json,
            "created_by": run.created_by,
            "idempotency_key": run.idempotency_key,
            "cancel_requested": run.cancel_requested,
            "detail_url": _detail_url(run.project_id, run.id),
            "created_at": run.created_at,
            "updated_at": run.updated_at,
        }
