"""Category A — requirement coverage rules."""

from __future__ import annotations

from app.models.enums import (
    ComplianceFindingStatus,
    ComplianceRuleCategory,
    ComplianceSeverity,
)
from app.schemas.compliance import ComplianceContext, ComplianceFinding
from app.services.compliance.config import (
    HIGH_RISK_LEVELS,
    POSITIVE_MATCH_STATUSES,
    UNCOVERED_MATCH_STATUSES,
)
from app.services.compliance.draft_utils import (
    current_draft_versions,
    draft_covered_requirement_ids,
)
from app.services.compliance.findings import enum_value, make_finding
from app.services.compliance.registry import ComplianceRule


class MandatoryRequirementCoverageRule:
    rule_id = "A001_mandatory_coverage"
    name = "强制要求匹配覆盖"
    category = ComplianceRuleCategory.coverage
    description = "强制要求必须存在有效的企业材料匹配（supported / partially_supported）。"
    default_severity = ComplianceSeverity.error

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
                    message="项目尚无抽取的要求条目，无法判定强制要求覆盖。",
                    finding_suffix="no_requirements",
                    remediation="先完成招标要求抽取并人工确认后再运行覆盖检查。",
                )
            )
            return findings

        mandatory = [r for r in ctx.requirements if bool(getattr(r, "mandatory", False))]
        if not mandatory:
            findings.append(
                make_finding(
                    rule_id=self.rule_id,
                    rule_name=self.name,
                    category=self.category,
                    severity=ComplianceSeverity.info,
                    status=ComplianceFindingStatus.pass_,
                    message="当前项目没有标记为强制的要求条目。",
                    finding_suffix="no_mandatory",
                )
            )
            return findings

        for req in mandatory:
            matches = ctx.matches_by_requirement_id.get(req.id) or []
            positive = [m for m in matches if enum_value(m.status) in POSITIVE_MATCH_STATUSES]
            if positive:
                findings.append(
                    make_finding(
                        rule_id=self.rule_id,
                        rule_name=self.name,
                        category=self.category,
                        severity=ComplianceSeverity.info,
                        status=ComplianceFindingStatus.pass_,
                        message=f"强制要求「{req.title}」已有正向匹配。",
                        finding_suffix=str(req.id),
                        requirement_id=req.id,
                        match_id=positive[0].id,
                    )
                )
            elif matches:
                findings.append(
                    make_finding(
                        rule_id=self.rule_id,
                        rule_name=self.name,
                        category=self.category,
                        severity=self.default_severity,
                        status=ComplianceFindingStatus.fail,
                        message=(
                            f"强制要求「{req.title}」存在匹配，但状态为"
                            f" {enum_value(matches[0].status)}，未达到正向覆盖。"
                        ),
                        finding_suffix=str(req.id),
                        requirement_id=req.id,
                        match_id=matches[0].id,
                        remediation="补充企业侧材料后重新匹配，或人工审核确认材料缺口。",
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
                        message=f"强制要求「{req.title}」尚无企业材料匹配记录。",
                        finding_suffix=str(req.id),
                        requirement_id=req.id,
                        remediation="在材料匹配工作区对该要求发起匹配。",
                    )
                )
        return findings


class RequirementMatchPresenceRule:
    rule_id = "A002_match_presence"
    name = "要求匹配存在性"
    category = ComplianceRuleCategory.coverage
    description = "每条要求应至少有一条 active 匹配记录；缺失时标记为未知/警告。"
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
                    message="无要求数据，跳过匹配存在性检查。",
                    finding_suffix="no_requirements",
                )
            )
            return findings

        missing = 0
        for req in ctx.requirements:
            matches = ctx.matches_by_requirement_id.get(req.id) or []
            if matches:
                continue
            missing += 1
            findings.append(
                make_finding(
                    rule_id=self.rule_id,
                    rule_name=self.name,
                    category=self.category,
                    severity=self.default_severity,
                    status=ComplianceFindingStatus.fail,
                    message=f"要求「{req.title}」尚无 active 匹配。",
                    finding_suffix=str(req.id),
                    requirement_id=req.id,
                    remediation="运行企业材料匹配或确认该要求不适用。",
                )
            )
        if missing == 0:
            findings.append(
                make_finding(
                    rule_id=self.rule_id,
                    rule_name=self.name,
                    category=self.category,
                    severity=ComplianceSeverity.info,
                    status=ComplianceFindingStatus.pass_,
                    message=f"全部 {len(ctx.requirements)} 条要求均有 active 匹配。",
                    finding_suffix="all_present",
                )
            )
        return findings


class TenderEvidenceLinkRule:
    rule_id = "A003_tender_evidence_link"
    name = "招标侧证据链接"
    category = ComplianceRuleCategory.coverage
    description = "要求应关联招标侧 EvidenceLink；缺失时警告（数据不足不编造）。"
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
                    message="无要求数据，无法检查招标侧证据链接。",
                    finding_suffix="no_requirements",
                )
            )
            return findings

        linked_req_ids = {
            link.requirement_id for link in ctx.tender_evidence_links if link.requirement_id
        }
        missing = 0
        for req in ctx.requirements:
            if req.id in linked_req_ids:
                continue
            missing += 1
            findings.append(
                make_finding(
                    rule_id=self.rule_id,
                    rule_name=self.name,
                    category=self.category,
                    severity=self.default_severity,
                    status=ComplianceFindingStatus.fail,
                    message=f"要求「{req.title}」缺少招标侧证据链接。",
                    finding_suffix=str(req.id),
                    requirement_id=req.id,
                    remediation="重新抽取或人工补齐 EvidenceLink。",
                    source_location_json={
                        "source_page": getattr(req, "source_page", None),
                        "source_section": getattr(req, "source_section", None),
                        "source_document_id": str(req.source_document_id)
                        if getattr(req, "source_document_id", None)
                        else None,
                    },
                )
            )
        if missing == 0:
            findings.append(
                make_finding(
                    rule_id=self.rule_id,
                    rule_name=self.name,
                    category=self.category,
                    severity=ComplianceSeverity.info,
                    status=ComplianceFindingStatus.pass_,
                    message="全部要求均已关联招标侧证据链接。",
                    finding_suffix="all_linked",
                )
            )
        return findings


class UncoveredMatchStatusRule:
    rule_id = "A004_uncovered_match_status"
    name = "匹配仍为缺口/冲突"
    category = ComplianceRuleCategory.coverage
    description = "active 匹配仍处于 insufficient_evidence / conflicting_evidence 时记为未覆盖。"
    default_severity = ComplianceSeverity.error

    def evaluate(self, ctx: ComplianceContext) -> list[ComplianceFinding]:
        findings: list[ComplianceFinding] = []
        if not ctx.evidence_matches:
            findings.append(
                make_finding(
                    rule_id=self.rule_id,
                    rule_name=self.name,
                    category=self.category,
                    severity=ComplianceSeverity.warning,
                    status=ComplianceFindingStatus.unknown,
                    message="无匹配记录，无法判定未覆盖状态。",
                    finding_suffix="no_matches",
                )
            )
            return findings

        hits = 0
        for match in ctx.evidence_matches:
            status = enum_value(match.status)
            if status not in UNCOVERED_MATCH_STATUSES:
                continue
            hits += 1
            req = ctx.requirements_by_id.get(match.requirement_id)
            title = getattr(req, "title", None) or str(match.requirement_id)
            findings.append(
                make_finding(
                    rule_id=self.rule_id,
                    rule_name=self.name,
                    category=self.category,
                    severity=self.default_severity,
                    status=ComplianceFindingStatus.fail,
                    message=f"要求「{title}」匹配状态为 {status}，尚未正向覆盖。",
                    finding_suffix=str(match.id),
                    requirement_id=match.requirement_id,
                    match_id=match.id,
                    remediation="补充材料或人工审核；冲突须先裁决。",
                )
            )
        if hits == 0:
            findings.append(
                make_finding(
                    rule_id=self.rule_id,
                    rule_name=self.name,
                    category=self.category,
                    severity=ComplianceSeverity.info,
                    status=ComplianceFindingStatus.pass_,
                    message="无 insufficient/conflicting 状态的 active 匹配。",
                    finding_suffix="ok",
                )
            )
        return findings


class HighPriorityUncoveredRule:
    rule_id = "A005_high_priority_uncovered"
    name = "高优先级要求未覆盖"
    category = ComplianceRuleCategory.coverage
    description = "Requirement.risk_level 为 high/critical 时必须有正向匹配。"
    default_severity = ComplianceSeverity.error

    def evaluate(self, ctx: ComplianceContext) -> list[ComplianceFinding]:
        findings: list[ComplianceFinding] = []
        targets = [r for r in ctx.requirements if enum_value(r.risk_level) in HIGH_RISK_LEVELS]
        if not targets:
            findings.append(
                make_finding(
                    rule_id=self.rule_id,
                    rule_name=self.name,
                    category=self.category,
                    severity=ComplianceSeverity.info,
                    status=ComplianceFindingStatus.pass_,
                    message="无 high/critical 风险等级的要求。",
                    finding_suffix="none",
                )
            )
            return findings

        for req in targets:
            matches = ctx.matches_by_requirement_id.get(req.id) or []
            positive = [m for m in matches if enum_value(m.status) in POSITIVE_MATCH_STATUSES]
            if positive:
                findings.append(
                    make_finding(
                        rule_id=self.rule_id,
                        rule_name=self.name,
                        category=self.category,
                        severity=ComplianceSeverity.info,
                        status=ComplianceFindingStatus.pass_,
                        message=f"高优先级要求「{req.title}」已有正向匹配。",
                        finding_suffix=f"ok:{req.id}",
                        requirement_id=req.id,
                        match_id=positive[0].id,
                    )
                )
            elif not matches:
                findings.append(
                    make_finding(
                        rule_id=self.rule_id,
                        rule_name=self.name,
                        category=self.category,
                        severity=ComplianceSeverity.warning,
                        status=ComplianceFindingStatus.unknown,
                        message=(f"高优先级要求「{req.title}」尚无匹配，无法确认覆盖。"),
                        finding_suffix=f"nomatch:{req.id}",
                        requirement_id=req.id,
                        remediation="先运行材料匹配。",
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
                            f"高优先级要求「{req.title}」无正向匹配"
                            f"（当前 {enum_value(matches[0].status)}）。"
                        ),
                        finding_suffix=str(req.id),
                        requirement_id=req.id,
                        match_id=matches[0].id,
                        remediation="补齐企业证据至 supported/partially_supported。",
                    )
                )
        return findings


class DraftMissingMandatoryRule:
    rule_id = "A006_draft_missing_mandatory"
    name = "草稿缺少强制要求响应"
    category = ComplianceRuleCategory.coverage
    description = "当前草稿版本须覆盖强制要求（来源/块/合规矩阵中出现 requirement_id）。"
    default_severity = ComplianceSeverity.error

    def evaluate(self, ctx: ComplianceContext) -> list[ComplianceFinding]:
        findings: list[ComplianceFinding] = []
        versions = current_draft_versions(ctx)
        mandatory = [r for r in ctx.requirements if bool(getattr(r, "mandatory", False))]
        if not versions:
            findings.append(
                make_finding(
                    rule_id=self.rule_id,
                    rule_name=self.name,
                    category=self.category,
                    severity=ComplianceSeverity.warning,
                    status=ComplianceFindingStatus.unknown,
                    message="无可检查的响应草稿，跳过强制要求草稿覆盖检查。",
                    finding_suffix="no_draft",
                    draft_id=ctx.draft_id,
                )
            )
            return findings
        if not mandatory:
            findings.append(
                make_finding(
                    rule_id=self.rule_id,
                    rule_name=self.name,
                    category=self.category,
                    severity=ComplianceSeverity.info,
                    status=ComplianceFindingStatus.pass_,
                    message="无强制要求需要草稿响应。",
                    finding_suffix="no_mandatory",
                    draft_id=versions[0].draft_id,
                )
            )
            return findings

        covered = draft_covered_requirement_ids(ctx, versions)
        missing = 0
        for req in mandatory:
            if req.id in covered:
                findings.append(
                    make_finding(
                        rule_id=self.rule_id,
                        rule_name=self.name,
                        category=self.category,
                        severity=ComplianceSeverity.info,
                        status=ComplianceFindingStatus.pass_,
                        message=f"强制要求「{req.title}」已在草稿中引用。",
                        finding_suffix=f"ok:{req.id}",
                        requirement_id=req.id,
                        draft_id=versions[0].draft_id,
                    )
                )
            else:
                missing += 1
                findings.append(
                    make_finding(
                        rule_id=self.rule_id,
                        rule_name=self.name,
                        category=self.category,
                        severity=self.default_severity,
                        status=ComplianceFindingStatus.fail,
                        message=f"强制要求「{req.title}」未出现在当前草稿响应中。",
                        finding_suffix=str(req.id),
                        requirement_id=req.id,
                        draft_id=versions[0].draft_id,
                        remediation="在草稿来源/正文块/合规矩阵中纳入该强制要求。",
                    )
                )
        return findings


COVERAGE_RULES: list[ComplianceRule] = [
    MandatoryRequirementCoverageRule(),
    RequirementMatchPresenceRule(),
    TenderEvidenceLinkRule(),
    UncoveredMatchStatusRule(),
    HighPriorityUncoveredRule(),
    DraftMissingMandatoryRule(),
]
