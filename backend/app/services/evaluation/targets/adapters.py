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
    """Calls formal RetrievalService using run project_id (never case source project).

    When ``model_id`` is set in target_config, also runs grounded LLM answer via
    RagAnswerService so Base vs Course LoRA can be compared.
    """

    target_type = "rag"

    def __init__(self, *, db=None, config: dict[str, Any] | None = None):
        self.db = db
        self.config = config or {}

    def capability(self) -> TargetCapability:
        try:
            from app.services.embeddings import get_embedding_service

            get_embedding_service()
        except Exception as exc:  # noqa: BLE001
            return TargetCapability(
                target_type=self.target_type,
                available=False,
                reason="Embedding/retrieval stack not available",
                reason_code=f"project_dependency_missing:{type(exc).__name__}",
            )
        model_id = self.config.get("model_id")
        if model_id:
            from app.services.model_serving import resolve_model_selection

            resolution = resolve_model_selection(str(model_id), allow_fallback=False, probe=True)
            if not resolution.available:
                return TargetCapability(
                    target_type=self.target_type,
                    available=False,
                    reason="模型尚未启动在线服务",
                    reason_code=(
                        resolution.reason_codes[0]
                        if resolution.reason_codes
                        else "model_not_served"
                    ),
                )
        return TargetCapability(target_type=self.target_type, available=True)

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

        model_id = self.config.get("model_id") or context.target_config.get("model_id")
        if model_id:
            return self._run_grounded_ask(question, context, str(model_id))

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

    def _run_grounded_ask(
        self, question: str, context: TargetExecutionContext, model_id: str
    ) -> TargetResult:
        from app.schemas.ask import AskRequest
        from app.services.model_serving import resolve_model_selection
        from app.services.rag_answer_service import RagAnswerService

        resolution = resolve_model_selection(model_id, allow_fallback=False, probe=True)
        if not resolution.available:
            return TargetResult(
                ok=False,
                unavailable=True,
                error_summary="模型尚未启动在线服务",
                metadata={"reason_codes": resolution.reason_codes},
            )
        try:
            top_k = int(self.config.get("top_k") or context.target_config.get("top_k") or 5)
            resp = RagAnswerService(self.db).answer(
                context.project_id,
                AskRequest(question=question[:512], top_k=top_k, model_id=model_id, stream=False),
            )
        except Exception as exc:  # noqa: BLE001
            return TargetResult(ok=False, error_summary=f"rag_ask_failed:{type(exc).__name__}")

        gt = resp.generation_trace
        citations = [
            {
                "chunk_id": c.chunk_id,
                "document_id": c.document_id,
                "page": c.page_start,
                "file_name": c.file_name,
                "section": c.section,
            }
            for c in (resp.citations or [])
        ]
        chunk_ids = [str(c.chunk_id) for c in (resp.citations or []) if c.chunk_id]
        output = {
            "answer": resp.answer,
            "answerable": resp.status == "answered",
            "citations": citations,
            "retrieved_chunk_ids": chunk_ids,
            "status": resp.status,
            "model": {
                "requested_model_id": getattr(gt, "requested_model_id", None),
                "resolved_model_id": getattr(gt, "resolved_model_id", None),
                "served_model_name": (
                    getattr(gt, "served_model_name", None) or (gt.model if gt else None)
                ),
                "model_type": getattr(gt, "model_type", None),
                "adapter_version": getattr(gt, "adapter_version", None),
                "fallback_used": bool(getattr(gt, "fallback_used", False)),
                "display_name": resolution.display_name,
            },
        }
        return TargetResult(
            ok=True,
            output=output,
            citations=citations,
            retrieved_chunk_ids=chunk_ids,
            metadata={
                "project_id": str(context.project_id),
                "model": output["model"],
            },
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
        model_id = self.config.get("model_id")
        if model_id:
            from app.services.model_serving import resolve_model_selection

            resolution = resolve_model_selection(str(model_id), allow_fallback=False, probe=True)
            if not resolution.available:
                return TargetCapability(
                    target_type=self.target_type,
                    available=False,
                    reason="模型尚未启动在线服务",
                    reason_code=(
                        resolution.reason_codes[0]
                        if resolution.reason_codes
                        else "model_not_served"
                    ),
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
            from app.services.llm_client import LlmClient
            from app.services.model_serving import resolve_model_selection

            model_id = self.config.get("model_id") or context.target_config.get("model_id")
            llm = None
            model_meta: dict[str, Any] = {}
            if model_id:
                resolution = resolve_model_selection(
                    str(model_id), allow_fallback=False, probe=True
                )
                if not resolution.available or not resolution.served_model_name:
                    return TargetResult(
                        ok=False,
                        unavailable=True,
                        error_summary="模型尚未启动在线服务",
                    )
                llm = LlmClient(model=resolution.served_model_name)
                model_meta = {
                    "requested_model_id": resolution.requested_model_id,
                    "resolved_model_id": resolution.resolved_model_id,
                    "served_model_name": resolution.served_model_name,
                    "model_type": resolution.model_type,
                    "adapter_version": resolution.adapter_version,
                    "fallback_used": False,
                    "display_name": resolution.display_name,
                }

            question = str(
                (target_input.task_input or {}).get("question")
                or (target_input.task_input or {}).get("query")
                or (target_input.task_input or {}).get("text")
                or target_input.case_key
            )
            read = AgentRunService(self.db, llm=llm).start_run(
                context.project_id,
                AgentRunStartRequest(
                    user_request=question[:2000],
                    metadata={
                        "evaluation_case_key": target_input.case_key,
                        **({"model": model_meta} if model_meta else {}),
                    },
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
                    "model": model_meta or None,
                },
                citations=[],
                retrieved_chunk_ids=[],
                metadata={"agent_run_id": str(read.id), "model": model_meta or None},
            )
        except Exception as exc:  # noqa: BLE001
            return TargetResult(ok=False, error_summary=f"agent_failed:{type(exc).__name__}")


class StructuredExtractionAdapter:
    """Case-level structured clause analysis using Course LoRA SFT protocol.

    Uses the same system/user prompts and required JSON keys as training.
    """

    target_type = "extraction"

    def __init__(self, *, db=None, config: dict[str, Any] | None = None):
        self.db = db
        self.config = config or {}

    def capability(self) -> TargetCapability:
        from app.services.model_serving import CAP_STRUCTURED_EXTRACTION, resolve_model_selection

        model_id = self.config.get("model_id")
        try:
            from app.core.config import get_settings

            if not get_settings().llm_enabled:
                return TargetCapability(
                    target_type=self.target_type,
                    available=False,
                    reason="LLM provider not configured",
                    reason_code="provider_not_configured",
                )
        except Exception:  # noqa: BLE001
            return TargetCapability(
                target_type=self.target_type,
                available=False,
                reason="settings unavailable",
                reason_code="settings_unavailable",
            )
        if model_id:
            resolution = resolve_model_selection(
                str(model_id),
                allow_fallback=False,
                probe=True,
                required_capability=CAP_STRUCTURED_EXTRACTION,
            )
            if not resolution.available:
                return TargetCapability(
                    target_type=self.target_type,
                    available=False,
                    reason="模型尚未启动在线服务",
                    reason_code=(
                        resolution.reason_codes[0]
                        if resolution.reason_codes
                        else "model_not_served"
                    ),
                )
        return TargetCapability(target_type=self.target_type, available=True)

    def run_case(
        self, target_input: TargetCaseInput, context: TargetExecutionContext
    ) -> TargetResult:
        assert_no_private_reference(target_input, context)
        from app.services.structured_clause import TASK_SPECS, StructuredClauseService

        task_input = target_input.task_input or {}
        clause = str(
            task_input.get("clause_text")
            or task_input.get("text")
            or task_input.get("original_text")
            or ""
        ).strip()
        if not clause:
            return TargetResult(ok=False, error_summary="missing_clause_text")

        task_type = str(
            self.config.get("task_type")
            or task_input.get("task_type")
            or task_input.get("sft_task_type")
            or "requirement_classify"
        )
        if task_type not in TASK_SPECS:
            # Map legacy evaluation "extraction" samples to qualification_extract
            # when category hints qualification; else classify.
            cat = str(task_input.get("category") or "").lower()
            if "qualif" in cat:
                task_type = "qualification_extract"
            elif "risk" in cat:
                task_type = "risk_detect"
            else:
                task_type = "requirement_classify"

        model_id = self.config.get("model_id") or context.target_config.get("model_id")
        try:
            result = StructuredClauseService().analyze(
                clause_text=clause,
                task_type=task_type,
                model_id=str(model_id) if model_id else None,
                allow_base_fallback=False,
                temperature=float(self.config.get("temperature") or 0.1),
                max_tokens=int(self.config.get("max_tokens") or 512),
            )
        except Exception as exc:  # noqa: BLE001
            detail = getattr(exc, "detail", None)
            if isinstance(detail, dict) and detail.get("reason_code") in {
                "model_not_served",
                "capability_unsupported",
                "adapter_missing",
                "base_model_mismatch",
            }:
                return TargetResult(
                    ok=False,
                    unavailable=True,
                    error_summary=str(detail.get("message") or detail.get("reason_code")),
                )
            return TargetResult(ok=False, error_summary=f"structured_failed:{type(exc).__name__}")

        output = {
            **(result.parsed or {}),
            "schema_valid": result.schema_valid,
            "required_field_coverage": result.required_field_coverage,
            "parse_error": result.parse_error,
            "task_type": result.task_type,
            "requested_model_id": result.requested_model_id,
            "resolved_model_id": result.resolved_model_id,
            "served_model_name": result.served_model_name,
            "model_type": result.model_type,
            "adapter_version": result.adapter_version,
            "dataset_version": result.dataset_version,
            "fallback_used": result.fallback_used,
            "capability": result.capability,
            "raw_output_preview": (result.raw_output or "")[:500],
        }
        return TargetResult(
            ok=result.schema_valid,
            output=output,
            citations=[],
            retrieved_chunk_ids=[],
            duration_ms=int(result.latency_ms),
            metadata={
                "model": {
                    "requested_model_id": result.requested_model_id,
                    "resolved_model_id": result.resolved_model_id,
                    "served_model_name": result.served_model_name,
                    "model_type": result.model_type,
                    "adapter_version": result.adapter_version,
                    "capability": result.capability,
                    "fallback_used": result.fallback_used,
                }
            },
        )


def build_adapter(target_type: str, *, config: dict[str, Any] | None = None, db=None):
    config = config or {}
    if target_type == "compliance":
        return ComplianceServiceAdapter(db=db, config=config)
    if target_type == "rag":
        return RagServiceAdapter(db=db, config=config)
    if target_type == "agent_pipeline":
        return AgentPipelineAdapter(db=db, config=config)
    if target_type == "extraction":
        return StructuredExtractionAdapter(db=db, config=config)
    if target_type in {"matching", "drafting"}:
        return UnwiredLlmTarget(
            target_type,
            reason=f"{target_type} case-level evaluation adapter is not wired to formal service",
            reason_code="service_not_wired",
        )
    from app.services.evaluation.targets import UnavailableTarget

    return UnavailableTarget(
        target_type, f"unknown target {target_type}", reason_code="unknown_target"
    )
