"""Target adapter registry — production never lists deterministic_fake by default."""

from __future__ import annotations

import os
from typing import Any

from app.models.enums import EvaluationTargetType
from app.services.evaluation.targets.base import TargetCapability, TargetResult
from app.services.evaluation.targets.fake import DeterministicFakeTarget
from app.services.evaluation.types import TargetCaseInput, TargetExecutionContext


class UnavailableTarget:
    def __init__(self, target_type: str, reason: str, *, reason_code: str = "unavailable"):
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
        return TargetResult(ok=False, unavailable=True, error_summary=self.reason)


def allow_fake_targets(*, explicit: bool | None = None) -> bool:
    """deterministic_fake is CI/dev only unless EVALUATION_ALLOW_FAKE=1."""
    if explicit is not None:
        return explicit
    flag = os.getenv("EVALUATION_ALLOW_FAKE", "").strip().lower()
    if flag in {"1", "true", "yes", "on"}:
        return True
    if flag in {"0", "false", "no", "off"}:
        return False
    try:
        from app.core.config import get_settings

        env = (get_settings().app_env or "").strip().lower()
    except Exception:
        env = "development"
    return env in {"development", "dev", "test", "testing", "ci"}


def _llm_configured() -> tuple[bool, str | None]:
    """Local vLLM uses api_key=local; enabled flag is the gate."""
    try:
        from app.core.config import get_settings

        settings = get_settings()
    except Exception:
        return False, "settings_unavailable"
    if not getattr(settings, "llm_enabled", False):
        return False, "provider_not_configured"
    base = (settings.llm_base_url or "").strip()
    if not base:
        return False, "provider_not_configured"
    return True, None


def _retrieval_configured() -> tuple[bool, str | None]:
    try:
        from app.services.embeddings import get_embedding_service

        get_embedding_service()
        return True, None
    except Exception as exc:  # noqa: BLE001
        return False, f"project_dependency_missing:{type(exc).__name__}"


def list_capabilities(*, allow_fake: bool | None = None) -> list[TargetCapability]:
    allow = allow_fake_targets(explicit=allow_fake)
    caps: list[TargetCapability] = []
    if allow:
        caps.append(DeterministicFakeTarget().capability())

    llm_ok, llm_code = _llm_configured()
    rag_ok, rag_code = _retrieval_configured()

    # Real available set: only adapters that can execute today.
    caps.append(
        TargetCapability(
            target_type=EvaluationTargetType.rag.value,
            available=rag_ok,
            reason=None if rag_ok else "Embedding/retrieval stack not available",
            reason_code=None if rag_ok else (rag_code or "project_dependency_missing"),
        )
    )
    for t, reason, code in (
        (
            EvaluationTargetType.extraction.value,
            "extraction case-level evaluation adapter is not wired to formal service",
            "service_not_wired",
        ),
        (
            EvaluationTargetType.matching.value,
            "matching case-level evaluation adapter is not wired to formal service",
            "service_not_wired",
        ),
        (
            EvaluationTargetType.drafting.value,
            "drafting case-level evaluation adapter is not wired to formal service",
            "service_not_wired",
        ),
    ):
        caps.append(
            TargetCapability(target_type=t, available=False, reason=reason, reason_code=code)
        )
    caps.append(TargetCapability(target_type=EvaluationTargetType.compliance.value, available=True))
    caps.append(
        TargetCapability(
            target_type=EvaluationTargetType.agent_pipeline.value,
            available=llm_ok,
            reason=None if llm_ok else "LLM provider not configured",
            reason_code=None if llm_ok else (llm_code or "provider_not_configured"),
        )
    )
    return caps


def get_target(target_type: str, *, config: dict[str, Any] | None = None, db=None) -> Any:
    config = config or {}
    if target_type == EvaluationTargetType.deterministic_fake.value:
        if not allow_fake_targets() and not config.get("force_fake"):
            return UnavailableTarget(
                target_type,
                "deterministic_fake is not available in this environment",
                reason_code="fake_disabled",
            )
        return DeterministicFakeTarget(
            seed=int(config.get("seed") or 42),
            fail_case_keys=set(config.get("fail_case_keys") or []),
        )
    from app.services.evaluation.targets.adapters import build_adapter

    return build_adapter(target_type, config=config, db=db)
