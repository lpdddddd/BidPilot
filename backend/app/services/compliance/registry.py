"""Rule protocol and registry for the compliance engine."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.models.enums import ComplianceRuleCategory, ComplianceSeverity
from app.schemas.compliance import ComplianceContext, ComplianceFinding


@runtime_checkable
class ComplianceRule(Protocol):
    rule_id: str
    name: str
    category: ComplianceRuleCategory
    description: str
    default_severity: ComplianceSeverity

    def evaluate(self, ctx: ComplianceContext) -> list[ComplianceFinding]:
        """Return findings for this rule (may be empty on full pass)."""


class RuleRegistry:
    def __init__(self) -> None:
        self._rules: dict[str, ComplianceRule] = {}

    def register(self, rule: ComplianceRule) -> None:
        if rule.rule_id in self._rules:
            raise ValueError(f"duplicate rule_id: {rule.rule_id}")
        self._rules[rule.rule_id] = rule

    def get(self, rule_id: str) -> ComplianceRule | None:
        return self._rules.get(rule_id)

    def list_rules(
        self,
        *,
        rule_ids: list[str] | None = None,
        categories: list[ComplianceRuleCategory] | None = None,
    ) -> list[ComplianceRule]:
        rules = list(self._rules.values())
        if rule_ids is not None:
            wanted = set(rule_ids)
            rules = [r for r in rules if r.rule_id in wanted]
        if categories is not None:
            cats = set(categories)
            rules = [r for r in rules if r.category in cats]
        return sorted(rules, key=lambda r: (r.category.value, r.rule_id))

    def all_rule_ids(self) -> list[str]:
        return [r.rule_id for r in self.list_rules()]


_DEFAULT: RuleRegistry | None = None
_DEFAULT_VERSION: str | None = None


def get_default_registry() -> RuleRegistry:
    global _DEFAULT, _DEFAULT_VERSION
    from app.services.compliance.config import ENGINE_VERSION
    from app.services.compliance.rules import register_all_rules

    if _DEFAULT is None or _DEFAULT_VERSION != ENGINE_VERSION:
        reg = RuleRegistry()
        register_all_rules(reg)
        _DEFAULT = reg
        _DEFAULT_VERSION = ENGINE_VERSION
    return _DEFAULT
