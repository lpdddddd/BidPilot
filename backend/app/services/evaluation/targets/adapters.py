"""Real-service target adapters — unavailable when dependencies missing.

Never read private reference / citation_metadata gold into predictions.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from app.services.evaluation.case_loader import EvaluationCase, assert_no_reference_in_target_input
from app.services.evaluation.targets.base import TargetCapability, TargetResult


def _safe_uuid(value: Any) -> UUID | None:
    if value is None:
        return None
    try:
        return UUID(str(value))
    except Exception:
        return None


class ComplianceServiceAdapter:
    """Calls the formal ComplianceEngine with a minimal non-gold context."""

    target_type = "compliance"

    def __init__(self, *, db=None, config: dict[str, Any] | None = None):
        self.db = db
        self.config = config or {}

    def capability(self) -> TargetCapability:
        return TargetCapability(target_type=self.target_type, available=True)

    def run_case(self, case: EvaluationCase) -> TargetResult:
        assert_no_reference_in_target_input(case.target_input())
        from app.schemas.compliance import ComplianceContext
        from app.services.compliance.engine import ComplianceEngine

        project_uuid = _safe_uuid(case.project_id) or _safe_uuid(
            (case.input_data or {}).get("project_id")
        )
        if project_uuid is None:
            # Engine still runs with a nil-safe placeholder project scope for offline cases.
            project_uuid = UUID(int=0)
        rule_id = (case.input_data or {}).get("rule_id")
        rule_ids = [str(rule_id)] if rule_id else None
        ctx = ComplianceContext(
            project_id=project_uuid,
            metadata={
                "evaluation_case_key": case.case_key,
                "rule_type": (case.input_data or {}).get("rule_type"),
            },
        )
        findings, stats = ComplianceEngine().run(ctx, rule_ids=rule_ids)
        # Map to evaluation prediction shape without inventing gold citations.
        statuses = [
            f.status.value if hasattr(f.status, "value") else str(f.status) for f in findings
        ]
        if any(s == "fail" for s in statuses):
            verdict = "fail"
        elif findings and all(s == "pass" for s in statuses):
            verdict = "pass"
        else:
            verdict = "unknown"
        severities = [
            f.severity.value if hasattr(f.severity, "value") else str(f.severity) for f in findings
        ]
        severity = (
            "critical" if "critical" in severities else (severities[0] if severities else "info")
        )
        return TargetResult(
            ok=True,
            output={
                "verdict": verdict,
                "severity": severity,
                "rule_type": (case.input_data or {}).get("rule_type") or "coverage",
                "finding": findings[0].message if findings else "no findings",
                "rule_ids": list(stats.get("rule_ids") or []),
                "findings": [
                    {
                        "rule_id": f.rule_id,
                        "status": f.status.value if hasattr(f.status, "value") else str(f.status),
                        "severity": f.severity.value
                        if hasattr(f.severity, "value")
                        else str(f.severity),
                        "message": f.message,
                    }
                    for f in findings[:20]
                ],
                "citations": [],
                "engine_version": stats.get("engine_version"),
            },
            duration_ms=1,
        )


class RagServiceAdapter:
    """Calls formal RetrievalService using the case question only."""

    target_type = "rag"

    def __init__(self, *, db=None, config: dict[str, Any] | None = None):
        self.db = db
        self.config = config or {}

    def capability(self) -> TargetCapability:
        try:
            from app.services.embeddings import get_embedding_service

            get_embedding_service()
            return TargetCapability(target_type=self.target_type, available=True)
        except Exception as exc:  # noqa: BLE001
            return TargetCapability(
                target_type=self.target_type,
                available=False,
                reason=f"Embedding/retrieval stack not available: {type(exc).__name__}",
                reason_code="retrieval_unavailable",
            )

    def run_case(self, case: EvaluationCase) -> TargetResult:
        assert_no_reference_in_target_input(case.target_input())
        if self.db is None:
            return TargetResult(
                ok=False,
                unavailable=True,
                error_summary="RAG target requires database session",
            )
        project_uuid = _safe_uuid(case.project_id) or _safe_uuid(
            (case.input_data or {}).get("project_id")
        )
        question = str(
            (case.input_data or {}).get("question") or (case.input_data or {}).get("query") or ""
        ).strip()
        if not project_uuid or not question:
            return TargetResult(
                ok=False,
                error_summary="RAG case missing project_id or question",
            )
        try:
            from app.schemas.search import SearchRequest
            from app.services.retrieval import RetrievalService

            top_k = int(self.config.get("top_k") or 5)
            resp = RetrievalService(self.db).search(
                project_uuid,
                SearchRequest(query=question, top_k=top_k),
            )
        except Exception as exc:  # noqa: BLE001 — surface as case error, not fake hits
            return TargetResult(
                ok=False,
                error_summary=f"retrieval_failed:{type(exc).__name__}",
            )
        hits = list(getattr(resp, "results", None) or getattr(resp, "hits", None) or [])
        chunk_ids: list[str] = []
        doc_ids: list[str] = []
        citations: list[dict[str, Any]] = []
        for hit in hits[:top_k]:
            chunk_id = getattr(hit, "chunk_id", None) or (
                hit.get("chunk_id") if isinstance(hit, dict) else None
            )
            doc_id = getattr(hit, "document_id", None) or (
                hit.get("document_id") if isinstance(hit, dict) else None
            )
            page = getattr(hit, "page_start", None) or (
                hit.get("page_start") if isinstance(hit, dict) else None
            )
            if chunk_id:
                chunk_ids.append(str(chunk_id))
            if doc_id:
                doc_ids.append(str(doc_id))
            citations.append(
                {
                    "chunk_id": str(chunk_id) if chunk_id else None,
                    "document_id": str(doc_id) if doc_id else None,
                    "page": page,
                }
            )
        return TargetResult(
            ok=True,
            output={
                "answer": "",
                "answerable": bool(chunk_ids),
                "citations": citations,
                "retrieved_chunk_ids": chunk_ids,
                "document_ids": list(dict.fromkeys(doc_ids)),
                "top_k": top_k,
            },
        )


class LlmGatedAdapter:
    """Placeholder for LLM-backed families — never invents scores."""

    def __init__(self, target_type: str, *, db=None, config: dict[str, Any] | None = None):
        self.target_type = target_type
        self.db = db
        self.config = config or {}

    def capability(self) -> TargetCapability:
        from app.services.evaluation.targets import _llm_configured

        ok, code = _llm_configured()
        return TargetCapability(
            target_type=self.target_type,
            available=ok,
            reason=None if ok else "LLM provider not configured",
            reason_code=None if ok else code,
        )

    def run_case(self, case: EvaluationCase) -> TargetResult:
        assert_no_reference_in_target_input(case.target_input())
        cap = self.capability()
        if not cap.available:
            return TargetResult(ok=False, unavailable=True, error_summary=cap.reason)
        # Production LLM invocation is out of scope for CI; require explicit force.
        if not self.config.get("force_llm_invoke"):
            return TargetResult(
                ok=False,
                unavailable=True,
                error_summary=f"{self.target_type} requires online provider invoke (not enabled)",
            )
        return TargetResult(
            ok=False,
            unavailable=True,
            error_summary=f"{self.target_type} online invoke not wired in this build",
        )


def build_adapter(target_type: str, *, config: dict[str, Any] | None = None, db=None):
    config = config or {}
    if target_type == "compliance":
        return ComplianceServiceAdapter(db=db, config=config)
    if target_type == "rag":
        return RagServiceAdapter(db=db, config=config)
    if target_type in {"extraction", "matching", "drafting", "agent_pipeline"}:
        from app.services.evaluation.targets import UnavailableTarget, _llm_configured

        ok, code = _llm_configured()
        if not ok and not config.get("force_available"):
            return UnavailableTarget(
                target_type,
                "LLM provider not configured",
                reason_code=code or "llm_provider_not_configured",
            )
        return LlmGatedAdapter(target_type, db=db, config=config)
    from app.services.evaluation.targets import UnavailableTarget

    return UnavailableTarget(
        target_type, f"unknown target {target_type}", reason_code="unknown_target"
    )
