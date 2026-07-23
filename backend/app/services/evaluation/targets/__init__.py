"""Target adapter registry — production never lists deterministic_fake by default."""

from __future__ import annotations

import os
from typing import Any

from app.models.enums import EvaluationTargetType
from app.services.evaluation.targets.base import TargetCapability
from app.services.evaluation.targets.fake import DeterministicFakeTarget

_PLACEHOLDER_KEYS = frozenset({"", "local", "changeme", "none", "test", "dummy"})


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

    def run_case(self, case):  # pragma: no cover
        from app.services.evaluation.targets.base import TargetResult

        return TargetResult(
            ok=False,
            unavailable=True,
            error_summary=self.reason,
        )


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
    try:
        from app.core.config import get_settings

        settings = get_settings()
    except Exception:
        return False, "settings_unavailable"
    if not getattr(settings, "llm_enabled", False):
        return False, "llm_disabled"
    key = (settings.llm_api_key or settings.openai_api_key or "").strip()
    if key.lower() in _PLACEHOLDER_KEYS:
        return False, "llm_provider_not_configured"
    return True, None


def _retrieval_configured() -> tuple[bool, str | None]:
    """RAG requires embedding stack; mark unavailable when it cannot load."""
    try:
        from app.services.embeddings import get_embedding_service

        get_embedding_service()
        return True, None
    except Exception as exc:  # noqa: BLE001
        return False, f"retrieval_unavailable:{type(exc).__name__}"


def list_capabilities(*, allow_fake: bool | None = None) -> list[TargetCapability]:
    allow = allow_fake_targets(explicit=allow_fake)
    caps: list[TargetCapability] = []
    if allow:
        caps.append(DeterministicFakeTarget().capability())

    llm_ok, llm_code = _llm_configured()
    rag_ok, rag_code = _retrieval_configured()

    mapping: list[tuple[str, bool, str | None, str | None]] = [
        (
            EvaluationTargetType.rag.value,
            rag_ok,
            None if rag_ok else "Embedding/retrieval stack not available",
            None if rag_ok else (rag_code or "retrieval_unavailable"),
        ),
        (
            EvaluationTargetType.extraction.value,
            llm_ok,
            None if llm_ok else "LLM provider not configured",
            None if llm_ok else (llm_code or "llm_provider_not_configured"),
        ),
        (
            EvaluationTargetType.matching.value,
            llm_ok,
            None if llm_ok else "LLM provider not configured",
            None if llm_ok else (llm_code or "llm_provider_not_configured"),
        ),
        (
            EvaluationTargetType.compliance.value,
            True,
            None,
            None,
        ),
        (
            EvaluationTargetType.drafting.value,
            llm_ok,
            None if llm_ok else "LLM provider not configured",
            None if llm_ok else (llm_code or "llm_provider_not_configured"),
        ),
        (
            EvaluationTargetType.agent_pipeline.value,
            llm_ok,
            None if llm_ok else "LLM provider not configured",
            None if llm_ok else (llm_code or "llm_provider_not_configured"),
        ),
    ]
    for t, ok, reason, code in mapping:
        caps.append(
            TargetCapability(
                target_type=t,
                available=ok,
                reason=reason,
                reason_code=code,
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
