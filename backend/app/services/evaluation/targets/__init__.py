"""Target adapter registry."""

from __future__ import annotations

from typing import Any

from app.models.enums import EvaluationTargetType
from app.services.evaluation.targets.base import TargetCapability
from app.services.evaluation.targets.fake import DeterministicFakeTarget


class UnavailableTarget:
    def __init__(self, target_type: str, reason: str):
        self.target_type = target_type
        self.reason = reason

    def capability(self) -> TargetCapability:
        return TargetCapability(target_type=self.target_type, available=False, reason=self.reason)

    def run_case(self, case):  # pragma: no cover
        from app.services.evaluation.targets.base import TargetResult

        return TargetResult(ok=False, unavailable=True, error_summary=self.reason)


def _llm_configured() -> bool:
    try:
        from app.core.config import get_settings

        settings = get_settings()
        return bool(settings.llm_api_key or settings.openai_api_key)
    except Exception:
        return False


def list_capabilities(*, allow_fake: bool = True) -> list[TargetCapability]:
    caps = []
    if allow_fake:
        caps.append(DeterministicFakeTarget().capability())
    llm = _llm_configured()
    mapping = {
        EvaluationTargetType.rag.value: (
            "RAG pipeline",
            True,
            None if True else "RAG service unavailable",
        ),
        EvaluationTargetType.extraction.value: (
            "requirement extraction",
            llm,
            None if llm else "LLM provider not configured",
        ),
        EvaluationTargetType.matching.value: (
            "supplier matching",
            llm,
            None if llm else "LLM provider not configured",
        ),
        EvaluationTargetType.compliance.value: ("compliance engine", True, None),
        EvaluationTargetType.drafting.value: (
            "proposal drafting",
            llm,
            None if llm else "LLM provider not configured",
        ),
        EvaluationTargetType.agent_pipeline.value: (
            "full agent pipeline",
            llm,
            None if llm else "LLM provider not configured",
        ),
    }
    for t, (label, ok, reason) in mapping.items():
        caps.append(
            TargetCapability(
                target_type=t,
                available=ok,
                reason=None if ok else (reason or f"{label} unavailable"),
            )
        )
    return caps


def get_target(target_type: str, *, config: dict[str, Any] | None = None, db=None) -> Any:
    config = config or {}
    if target_type == EvaluationTargetType.deterministic_fake.value:
        return DeterministicFakeTarget(
            seed=int(config.get("seed") or 42),
            fail_case_keys=set(config.get("fail_case_keys") or []),
        )
    # Production adapters: when provider missing, return unavailable rather than fake scores.
    from app.services.evaluation.targets.adapters import build_adapter

    return build_adapter(target_type, config=config, db=db)
