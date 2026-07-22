"""Category C — qualification / risk rules."""

from __future__ import annotations

from typing import Any

from app.models.enums import (
    ComplianceFindingStatus,
    ComplianceRuleCategory,
    ComplianceSeverity,
    MatchReviewStatus,
)
from app.schemas.compliance import ComplianceContext, ComplianceFinding
from app.services.compliance.config import (
    DEFINITIVE_NEGATIVE_STATUSES,
    GAP_MATCH_STATUSES,
    HIGH_RISK_LEVELS,
    QUALIFICATION_CATEGORIES,
    STRUCTURED_AMOUNT_KEYS,
    STRUCTURED_CONFLICT_KEYS,
    STRUCTURED_EXPIRY_KEYS,
)
from app.services.compliance.findings import enum_value, make_finding
from app.services.compliance.registry import ComplianceRule


class QualificationInsufficientRule:
    rule_id = "C001_qualification_insufficient"
    name = "资格类材料不足"
    category = ComplianceRuleCategory.qualification_risk
    description = "qualification / mandatory / invalid_bid 类要求若匹配为材料不足或冲突则告警。"
    default_severity = ComplianceSeverity.error

    def evaluate(self, ctx: ComplianceContext) -> list[ComplianceFinding]:
        findings: list[ComplianceFinding] = []
        targets = [
            r
            for r in ctx.requirements
            if enum_value(r.category) in QUALIFICATION_CATEGORIES
        ]
        if not targets:
            findings.append(
                make_finding(
                    rule_id=self.rule_id,
                    rule_name=self.name,
                    category=self.category,
                    severity=ComplianceSeverity.warning,
                    status=ComplianceFindingStatus.unknown,
                    message="项目无资格/强制/废标类要求，跳过本检查。",
                    finding_suffix="no_targets",
                )
            )
            return findings

        hit = 0
        for req in targets:
            matches = ctx.matches_by_requirement_id.get(req.id) or []
            if not matches:
                findings.append(
                    make_finding(
                        rule_id=self.rule_id,
                        rule_name=self.name,
                        category=self.category,
                        severity=ComplianceSeverity.warning,
                        status=ComplianceFindingStatus.unknown,
                        message=(
                            f"资格相关要求「{req.title}」尚无匹配结果，"
                            "无法判定材料是否充分。"
                        ),
                        finding_suffix=f"unmatched:{req.id}",
                        requirement_id=req.id,
                        remediation="先运行材料匹配再复查。",
                    )
                )
                continue
            for match in matches:
                status = enum_value(match.status)
                if status in GAP_MATCH_STATUSES:
                    hit += 1
                    findings.append(
                        make_finding(
                            rule_id=self.rule_id,
                            rule_name=self.name,
                            category=self.category,
                            severity=self.default_severity,
                            status=ComplianceFindingStatus.fail,
                            message=(
                                f"资格相关要求「{req.title}」匹配状态为 {status}。"
                            ),
                            finding_suffix=str(match.id),
                            requirement_id=req.id,
                            match_id=match.id,
                            remediation="补充资格证明材料或标记 needs_more_material。",
                        )
                    )
                else:
                    findings.append(
                        make_finding(
                            rule_id=self.rule_id,
                            rule_name=self.name,
                            category=self.category,
                            severity=ComplianceSeverity.info,
                            status=ComplianceFindingStatus.pass_,
                            message=(
                                f"资格相关要求「{req.title}」匹配状态为 {status}。"
                            ),
                            finding_suffix=f"ok:{match.id}",
                            requirement_id=req.id,
                            match_id=match.id,
                        )
                    )
        return findings


class HighRiskUnconfirmedRule:
    rule_id = "C002_high_risk_unconfirmed"
    name = "高风险未确认"
    category = ComplianceRuleCategory.qualification_risk
    description = "high/critical 风险匹配若仍 pending 则警告。"
    default_severity = ComplianceSeverity.warning

    def evaluate(self, ctx: ComplianceContext) -> list[ComplianceFinding]:
        findings: list[ComplianceFinding] = []
        risky = [
            m
            for m in ctx.evidence_matches
            if enum_value(m.risk_level) in HIGH_RISK_LEVELS
        ]
        if not risky:
            findings.append(
                make_finding(
                    rule_id=self.rule_id,
                    rule_name=self.name,
                    category=self.category,
                    severity=ComplianceSeverity.info,
                    status=ComplianceFindingStatus.pass_,
                    message="当前无 high/critical 风险匹配。",
                    finding_suffix="none",
                )
            )
            return findings

        for match in risky:
            review = enum_value(match.review_status)
            req = ctx.requirements_by_id.get(match.requirement_id)
            title = getattr(req, "title", None) or str(match.requirement_id)
            if review == MatchReviewStatus.confirmed.value:
                findings.append(
                    make_finding(
                        rule_id=self.rule_id,
                        rule_name=self.name,
                        category=self.category,
                        severity=ComplianceSeverity.info,
                        status=ComplianceFindingStatus.pass_,
                        message=f"高风险匹配「{title}」已确认。",
                        finding_suffix=f"confirmed:{match.id}",
                        requirement_id=match.requirement_id,
                        match_id=match.id,
                    )
                )
            else:
                findings.append(
                    make_finding(
                        rule_id=self.rule_id,
                        rule_name=self.name,
                        category=self.category,
                        severity=self.default_severity,
                        status=ComplianceFindingStatus.fail,
                        message=(
                            f"高风险匹配「{title}」审核状态为 {review}，尚未确认。"
                        ),
                        finding_suffix=str(match.id),
                        requirement_id=match.requirement_id,
                        match_id=match.id,
                        remediation="在审核队列中人工确认或驳回。",
                    )
                )
        return findings


class InvalidBidAttentionRule:
    rule_id = "C003_invalid_bid_attention"
    name = "废标条款关注"
    category = ComplianceRuleCategory.qualification_risk
    description = "invalid_bid 类要求必须被识别并提示人工关注。"
    default_severity = ComplianceSeverity.critical

    def evaluate(self, ctx: ComplianceContext) -> list[ComplianceFinding]:
        findings: list[ComplianceFinding] = []
        invalids = [
            r for r in ctx.requirements if enum_value(r.category) == "invalid_bid"
        ]
        if not invalids:
            findings.append(
                make_finding(
                    rule_id=self.rule_id,
                    rule_name=self.name,
                    category=self.category,
                    severity=ComplianceSeverity.info,
                    status=ComplianceFindingStatus.pass_,
                    message="当前项目未识别到 invalid_bid 类要求。",
                    finding_suffix="none",
                )
            )
            return findings

        for req in invalids:
            findings.append(
                make_finding(
                    rule_id=self.rule_id,
                    rule_name=self.name,
                    category=self.category,
                    severity=self.default_severity,
                    status=ComplianceFindingStatus.fail,
                    message=(
                        f"存在废标/无效投标相关要求「{req.title}」，须人工重点核对。"
                    ),
                    finding_suffix=str(req.id),
                    requirement_id=req.id,
                    remediation="逐条核对响应材料是否触碰废标条款；引擎不自动下结论。",
                    source_location_json={
                        "source_page": getattr(req, "source_page", None),
                        "source_section": getattr(req, "source_section", None),
                    },
                )
            )
        return findings


def _dig_keys(obj: Any, keys: tuple[str, ...]) -> dict[str, Any]:
    found: dict[str, Any] = {}
    if not isinstance(obj, dict):
        return found
    lower_map = {str(k).lower(): k for k in obj}
    for key in keys:
        raw = lower_map.get(key.lower())
        if raw is not None and obj.get(raw) not in (None, "", [], {}):
            found[key] = obj.get(raw)
    return found


class DefinitiveNegativeQualificationRule:
    rule_id = "C004_definitive_negative"
    name = "资格/强制要求明确负面匹配"
    category = ComplianceRuleCategory.qualification_risk
    description = (
        "mandatory/qualification 在匹配为 definitive conflicting"
        "（或元数据明确 not_supported）时记 critical。"
    )
    default_severity = ComplianceSeverity.critical

    def evaluate(self, ctx: ComplianceContext) -> list[ComplianceFinding]:
        findings: list[ComplianceFinding] = []
        targets = [
            r
            for r in ctx.requirements
            if enum_value(r.category) in {"qualification", "mandatory"}
            or bool(getattr(r, "mandatory", False))
        ]
        if not targets:
            findings.append(
                make_finding(
                    rule_id=self.rule_id,
                    rule_name=self.name,
                    category=self.category,
                    severity=ComplianceSeverity.warning,
                    status=ComplianceFindingStatus.unknown,
                    message="无资格/强制要求，跳过明确负面匹配检查。",
                    finding_suffix="no_targets",
                )
            )
            return findings

        hits = 0
        for req in targets:
            matches = ctx.matches_by_requirement_id.get(req.id) or []
            if not matches:
                findings.append(
                    make_finding(
                        rule_id=self.rule_id,
                        rule_name=self.name,
                        category=self.category,
                        severity=ComplianceSeverity.warning,
                        status=ComplianceFindingStatus.unknown,
                        message=f"资格/强制要求「{req.title}」尚无匹配，无法判定负面结论。",
                        finding_suffix=f"unmatched:{req.id}",
                        requirement_id=req.id,
                    )
                )
                continue
            for match in matches:
                status = enum_value(match.status)
                meta = match.metadata_json if isinstance(match.metadata_json, dict) else {}
                notes_flag = str(meta.get("not_supported") or meta.get("status_alias") or "")
                definitive = status in DEFINITIVE_NEGATIVE_STATUSES or notes_flag in {
                    "not_supported",
                    "true",
                    "1",
                }
                if not definitive:
                    continue
                hits += 1
                findings.append(
                    make_finding(
                        rule_id=self.rule_id,
                        rule_name=self.name,
                        category=self.category,
                        severity=self.default_severity,
                        status=ComplianceFindingStatus.fail,
                        message=(
                            f"资格/强制要求「{req.title}」匹配为明确负面"
                            f"（status={status}）。"
                        ),
                        finding_suffix=str(match.id),
                        requirement_id=req.id,
                        match_id=match.id,
                        remediation="不得以不足材料冒充负面结论；冲突须人工裁决。",
                    )
                )
        if hits == 0 and not any(
            f.status == ComplianceFindingStatus.unknown for f in findings
        ):
            findings.append(
                make_finding(
                    rule_id=self.rule_id,
                    rule_name=self.name,
                    category=self.category,
                    severity=ComplianceSeverity.info,
                    status=ComplianceFindingStatus.pass_,
                    message="未发现资格/强制要求的明确负面匹配。",
                    finding_suffix="ok",
                )
            )
        return findings


class StructuredThresholdFieldsRule:
    rule_id = "C005_structured_thresholds"
    name = "结构化有效期/金额阈值"
    category = ComplianceRuleCategory.qualification_risk
    description = (
        "若要求/元数据含 expiry 或金额阈值字段则检查；否则 warning/unknown，绝不编造。"
    )
    default_severity = ComplianceSeverity.warning

    def evaluate(self, ctx: ComplianceContext) -> list[ComplianceFinding]:
        findings: list[ComplianceFinding] = []
        if not ctx.requirements:
            findings.append(
                make_finding(
                    rule_id=self.rule_id,
                    rule_name=self.name,
                    category=self.category,
                    severity=ComplianceSeverity.warning,
                    status=ComplianceFindingStatus.unknown,
                    message="无要求数据，无法检查结构化阈值字段。",
                    finding_suffix="no_requirements",
                )
            )
            return findings

        any_structured = False
        for req in ctx.requirements:
            bags = [
                req.metadata_json if isinstance(req.metadata_json, dict) else {},
                req.evidence_required_json
                if isinstance(req.evidence_required_json, dict)
                else {},
            ]
            found: dict[str, Any] = {}
            for bag in bags:
                found.update(_dig_keys(bag, STRUCTURED_EXPIRY_KEYS))
                found.update(_dig_keys(bag, STRUCTURED_AMOUNT_KEYS))
                found.update(_dig_keys(bag, STRUCTURED_CONFLICT_KEYS))
            if not found:
                continue
            any_structured = True
            # Presence-only check: values must be non-empty; we do not invent comparisons.
            empty_keys = [k for k, v in found.items() if v in (None, "", [], {})]
            conflict_notes = found.get("conflict_notes") or found.get("conflicts")
            if empty_keys:
                findings.append(
                    make_finding(
                        rule_id=self.rule_id,
                        rule_name=self.name,
                        category=self.category,
                        severity=self.default_severity,
                        status=ComplianceFindingStatus.fail,
                        message=(
                            f"要求「{req.title}」声明了结构化字段但值为空："
                            f"{', '.join(empty_keys)}。"
                        ),
                        finding_suffix=f"empty:{req.id}",
                        requirement_id=req.id,
                        metadata_json={"fields": found},
                        remediation="补全结构化字段或移除空键。",
                    )
                )
            elif conflict_notes:
                findings.append(
                    make_finding(
                        rule_id=self.rule_id,
                        rule_name=self.name,
                        category=self.category,
                        severity=ComplianceSeverity.error,
                        status=ComplianceFindingStatus.fail,
                        message=f"要求「{req.title}」带有冲突标记/说明，须人工处理。",
                        finding_suffix=f"conflict:{req.id}",
                        requirement_id=req.id,
                        metadata_json={"fields": found},
                    )
                )
            else:
                findings.append(
                    make_finding(
                        rule_id=self.rule_id,
                        rule_name=self.name,
                        category=self.category,
                        severity=ComplianceSeverity.info,
                        status=ComplianceFindingStatus.pass_,
                        message=f"要求「{req.title}」结构化阈值字段已填写。",
                        finding_suffix=f"ok:{req.id}",
                        requirement_id=req.id,
                        metadata_json={"fields": list(found.keys())},
                    )
                )

        if not any_structured:
            findings.append(
                make_finding(
                    rule_id=self.rule_id,
                    rule_name=self.name,
                    category=self.category,
                    severity=ComplianceSeverity.warning,
                    status=ComplianceFindingStatus.unknown,
                    message=(
                        "未发现 expiry/金额阈值等结构化字段；"
                        "不编造阈值，跳过数值比对。"
                    ),
                    finding_suffix="no_structured_fields",
                    remediation=(
                        "若业务需要，在 requirement.metadata_json / "
                        "evidence_required_json 中写入真实字段。"
                    ),
                )
            )
        return findings


QUALIFICATION_RULES: list[ComplianceRule] = [
    QualificationInsufficientRule(),
    HighRiskUnconfirmedRule(),
    InvalidBidAttentionRule(),
    DefinitiveNegativeQualificationRule(),
    StructuredThresholdFieldsRule(),
]
