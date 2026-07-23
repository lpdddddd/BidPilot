"""Real-service target adapters — project scope from TargetExecutionContext only."""

from __future__ import annotations

from typing import Any

from app.services.evaluation.targets.base import TargetCapability, TargetResult
from app.services.evaluation.types import (
    TargetCaseInput,
    TargetExecutionContext,
    assert_no_private_reference,
)


class ComplianceServiceAdapter:
    """Calls the formal ComplianceEngine scoped to the authorized run project."""

    target_type = "compliance"

    def __init__(self, *, db=None, config: dict[str, Any] | None = None):
        self.db = db
        self.config = config or {}

    def capability(self) -> TargetCapability:
        return TargetCapability(target_type=self.target_type, available=True)

    def run_case(
        self, target_input: TargetCaseInput, context: TargetExecutionContext
    ) -> TargetResult:
        assert_no_private_reference(target_input, context)
        from app.schemas.compliance import ComplianceContext
        from app.services.compliance.engine import ComplianceEngine

        project_uuid = context.project_id
        rule_id = (target_input.task_input or {}).get("rule_id")
        rule_ids = [str(rule_id)] if rule_id else None
        ctx = ComplianceContext(
            project_id=project_uuid,
            metadata={
                "evaluation_case_key": target_input.case_key,
                "rule_type": (target_input.task_input or {}).get("rule_type"),
            },
        )
        findings, stats = ComplianceEngine().run(ctx, rule_ids=rule_ids)
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
        output = {
            "verdict": verdict,
            "severity": severity,
            "rule_type": (target_input.task_input or {}).get("rule_type") or "coverage",
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
        }
        return TargetResult(
            ok=True, output=output, citations=[], retrieved_chunk_ids=[], duration_ms=1
        )


class RagServiceAdapter:
    """Calls formal RetrievalService using run project_id (never case source project)."""

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
                reason_code="project_dependency_missing",
            )

    def run_case(
        self, target_input: TargetCaseInput, context: TargetExecutionContext
    ) -> TargetResult:
        assert_no_private_reference(target_input, context)
        if self.db is None:
            return TargetResult(
                ok=False,
                unavailable=True,
                error_summary="RAG target requires database session",
            )
        question = str(
            (target_input.task_input or {}).get("question")
            or (target_input.task_input or {}).get("query")
            or ""
        ).strip()
        if not question:
            return TargetResult(ok=False, error_summary="RAG case missing question")
        try:
            from app.schemas.search import SearchRequest
            from app.services.retrieval import RetrievalService

            top_k = int(self.config.get("top_k") or context.target_config.get("top_k") or 5)
            # Authorization scope: EvaluationRun.project_id only.
            resp = RetrievalService(self.db).search(
                context.project_id,
                SearchRequest(query=question, top_k=top_k),
            )
        except Exception as exc:  # noqa: BLE001
            return TargetResult(
                ok=False,
                error_summary=f"retrieval_failed:{type(exc).__name__}",
            )
        hits = list(getattr(resp, "results", None) or [])
        chunk_ids: list[str] = []
        citations: list[dict[str, Any]] = []
        for hit in hits[: int(self.config.get("top_k") or 5)]:
            chunk_id = str(getattr(hit, "chunk_id", "") or "")
            doc_id = str(getattr(hit, "document_id", "") or "")
            page = getattr(hit, "page_start", None)
            if chunk_id:
                chunk_ids.append(chunk_id)
            citations.append(
                {
                    "chunk_id": chunk_id or None,
                    "document_id": doc_id or None,
                    "page": page,
                    "file_name": getattr(hit, "file_name", None),
                    "section": getattr(hit, "section", None),
                }
            )
        output = {
            "answer": "",
            "answerable": bool(chunk_ids),
            "citations": citations,
            "retrieved_chunk_ids": chunk_ids,
            "document_ids": list(
                dict.fromkeys(
                    str(getattr(h, "document_id", ""))
                    for h in hits
                    if getattr(h, "document_id", None)
                )
            ),
            "top_k": int(self.config.get("top_k") or 5),
        }
        return TargetResult(
            ok=True,
            output=output,
            citations=citations,
            retrieved_chunk_ids=chunk_ids,
            metadata={"project_id": str(context.project_id)},
        )


class UnwiredLlmTarget:
    """LLM-family targets that are not yet case-level wired to formal services."""

    def __init__(self, target_type: str, *, reason: str, reason_code: str = "service_not_wired"):
        self.target_type = target_type
        self.reason = reason
        self.reason_code = reason_code

    def capability(self) -> TargetCapability:
        return TargetCapability(
            target_type=self.target_type,
            available=False,
            reason=self.reason,
            reason_code=self.reason_code,
        )

    def run_case(
        self, target_input: TargetCaseInput, context: TargetExecutionContext
    ) -> TargetResult:
        assert_no_private_reference(target_input, context)
        return TargetResult(ok=False, unavailable=True, error_summary=self.reason)


class AgentPipelineAdapter:
    """Full Agent pipeline via formal AgentRunService (sync, project-scoped)."""

    target_type = "agent_pipeline"

    def __init__(self, *, db=None, config: dict[str, Any] | None = None):
        self.db = db
        self.config = config or {}

    def capability(self) -> TargetCapability:
        from app.services.evaluation.targets import _llm_configured

        ok, code = _llm_configured()
        if not ok:
            return TargetCapability(
                target_type=self.target_type,
                available=False,
                reason="LLM provider not configured",
                reason_code=code or "provider_not_configured",
            )
        return TargetCapability(target_type=self.target_type, available=True)

    def run_case(
        self, target_input: TargetCaseInput, context: TargetExecutionContext
    ) -> TargetResult:
        assert_no_private_reference(target_input, context)
        if self.db is None:
            return TargetResult(
                ok=False, unavailable=True, error_summary="agent_pipeline requires db session"
            )
        cap = self.capability()
        if not cap.available:
            return TargetResult(ok=False, unavailable=True, error_summary=cap.reason)
        try:
            from app.schemas.agent_run import AgentRunStartRequest
            from app.services.agent_run.service import AgentRunService

            question = str(
                (target_input.task_input or {}).get("question")
                or (target_input.task_input or {}).get("query")
                or (target_input.task_input or {}).get("text")
                or target_input.case_key
            )
            read = AgentRunService(self.db).start_run(
                context.project_id,
                AgentRunStartRequest(
                    user_request=question[:2000],
                    metadata={"evaluation_case_key": target_input.case_key},
                ),
                execute=True,
            )
            status = read.status.value if hasattr(read.status, "value") else str(read.status)
            return TargetResult(
                ok=True,
                output={
                    "agent_run_id": str(read.id),
                    "status": status,
                    "answer": getattr(read, "result_summary", None) or "",
                },
                citations=[],
                retrieved_chunk_ids=[],
                metadata={"agent_run_id": str(read.id)},
            )
        except Exception as exc:  # noqa: BLE001
            return TargetResult(ok=False, error_summary=f"agent_failed:{type(exc).__name__}")


def build_adapter(target_type: str, *, config: dict[str, Any] | None = None, db=None):
    config = config or {}
    if target_type == "compliance":
        return ComplianceServiceAdapter(db=db, config=config)
    if target_type == "rag":
        return RagServiceAdapter(db=db, config=config)
    if target_type == "agent_pipeline":
        return AgentPipelineAdapter(db=db, config=config)
    if target_type in {"extraction", "matching", "drafting"}:
        # Formal services operate on project documents / match runs, not standalone
        # evaluation case text. Keep unavailable until a case-level entry exists.
        return UnwiredLlmTarget(
            target_type,
            reason=f"{target_type} case-level evaluation adapter is not wired to formal service",
            reason_code="service_not_wired",
        )
    from app.services.evaluation.targets import UnavailableTarget

    return UnavailableTarget(
        target_type, f"unknown target {target_type}", reason_code="unknown_target"
    )
