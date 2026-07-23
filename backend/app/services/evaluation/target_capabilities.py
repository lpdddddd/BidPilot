"""Central Evaluation target → model capability mapping (backend authority)."""

from __future__ import annotations

from app.services.model_serving import (
    CAP_AGENT_PIPELINE,
    CAP_COMPLIANCE_ANALYSIS,
    CAP_GROUNDED_QA,
    CAP_STRUCTURED_EXTRACTION,
)

# Final authority for which model capability each evaluation target requires.
# None means the target does not need a selectable LLM model_id.
TARGET_REQUIRED_CAPABILITY: dict[str, str | None] = {
    "rag": CAP_GROUNDED_QA,
    "extraction": CAP_STRUCTURED_EXTRACTION,
    "agent_pipeline": CAP_AGENT_PIPELINE,
    "compliance": CAP_COMPLIANCE_ANALYSIS,
    "matching": None,
    "drafting": None,
    "deterministic_fake": None,
}


def required_capability_for_target(target_type: str) -> str | None:
    return TARGET_REQUIRED_CAPABILITY.get(str(target_type))


def targets_requiring_model_select() -> list[str]:
    return [t for t, cap in TARGET_REQUIRED_CAPABILITY.items() if cap is not None]
