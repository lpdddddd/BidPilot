"""Deterministic compliance engine — no LLM."""

from __future__ import annotations

import logging
import traceback
from collections import Counter

from app.models.enums import (
    ComplianceFindingStatus,
    ComplianceRuleCategory,
    ComplianceSeverity,
)
from app.schemas.compliance import ComplianceContext, ComplianceFinding
from app.services.compliance.config import ENGINE_VERSION
from app.services.compliance.findings import make_finding
from app.services.compliance.registry import RuleRegistry, get_default_registry

logger = logging.getLogger(__name__)


class ComplianceEngine:
    def __init__(self, registry: RuleRegistry | None = None) -> None:
        self.registry = registry or get_default_registry()
        self.engine_version = ENGINE_VERSION

    def run(
        self,
        ctx: ComplianceContext,
        *,
        rule_ids: list[str] | None = None,
        categories: list[ComplianceRuleCategory] | None = None,
    ) -> tuple[list[ComplianceFinding], dict]:
        rules = self.registry.list_rules(rule_ids=rule_ids, categories=categories)
        all_findings: list[ComplianceFinding] = []

        for rule in rules:
            try:
                produced = rule.evaluate(ctx) or []
            except Exception as exc:  # noqa: BLE001 — per-rule isolation
                logger.exception("compliance rule %s failed", rule.rule_id)
                produced = [
                    make_finding(
                        rule_id=rule.rule_id,
                        rule_name=rule.name,
                        category=ComplianceRuleCategory.engine,
                        severity=ComplianceSeverity.error,
                        status=ComplianceFindingStatus.unknown,
                        message=f"规则执行异常：{type(exc).__name__}: {exc}",
                        finding_suffix="exception",
                        remediation="查看服务日志；修复数据或规则后重跑。",
                        metadata_json={
                            "exception_type": type(exc).__name__,
                            "traceback": traceback.format_exc()[-2000:],
                        },
                    )
                ]
            all_findings.extend(produced)

        all_findings.sort(
            key=lambda f: (
                f.category.value,
                f.rule_id,
                f.severity.value,
                f.finding_id,
            )
        )

        severity_counts = Counter(f.severity.value for f in all_findings)
        category_counts = Counter(f.category.value for f in all_findings)
        total_checks = len(rules)
        passed_checks = sum(
            1
            for rule in rules
            if all(
                f.status == ComplianceFindingStatus.pass_
                for f in all_findings
                if f.rule_id == rule.rule_id
            )
            and any(f.rule_id == rule.rule_id for f in all_findings)
        )
        # A rule with only pass findings counts as passed; rules that produced
        # only fail/unknown do not. Empty production counts as not passed.
        stats = {
            "engine_version": self.engine_version,
            "total_checks": total_checks,
            "passed_checks": passed_checks,
            "finding_count": len(all_findings),
            "severity_counts": dict(severity_counts),
            "category_counts": dict(category_counts),
            "rule_ids": [r.rule_id for r in rules],
        }
        return all_findings, stats


def run_compliance_rules(
    ctx: ComplianceContext,
    *,
    rule_ids: list[str] | None = None,
    categories: list[ComplianceRuleCategory] | None = None,
    registry: RuleRegistry | None = None,
) -> tuple[list[ComplianceFinding], dict]:
    return ComplianceEngine(registry).run(
        ctx, rule_ids=rule_ids, categories=categories
    )
