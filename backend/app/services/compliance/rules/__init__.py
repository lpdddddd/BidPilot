"""Register all built-in compliance rules."""

from __future__ import annotations

from app.services.compliance.registry import ComplianceRule, RuleRegistry
from app.services.compliance.rules.consistency import CONSISTENCY_RULES
from app.services.compliance.rules.coverage import COVERAGE_RULES
from app.services.compliance.rules.draft_safety import DRAFT_SAFETY_RULES
from app.services.compliance.rules.evidence import EVIDENCE_RULES
from app.services.compliance.rules.qualification_risk import QUALIFICATION_RULES


def register_all_rules(registry: RuleRegistry) -> None:
    rules: list[ComplianceRule] = [
        *COVERAGE_RULES,
        *EVIDENCE_RULES,
        *QUALIFICATION_RULES,
        *DRAFT_SAFETY_RULES,
        *CONSISTENCY_RULES,
    ]
    for rule in rules:
        registry.register(rule)
