"""Category E — cross-entity consistency rules."""

from __future__ import annotations

import re
from typing import Any

from app.models.enums import (
    ComplianceFindingStatus,
    ComplianceRuleCategory,
    ComplianceSeverity,
    MatchReviewStatus,
)
from app.schemas.compliance import ComplianceContext, ComplianceFinding
from app.services.compliance.config import (
    POSITIVE_MATCH_STATUSES,
    STRONG_SATISFACTION_PATTERNS,
)
from app.services.compliance.draft_utils import current_draft_versions, draft_blob
from app.services.compliance.findings import enum_value, make_finding
from app.services.compliance.registry import ComplianceRule

_STRONG_RE = re.compile("|".join(STRONG_SATISFACTION_PATTERNS))


class MatchStatusVsLinksRule:
    rule_id = "E001_status_vs_links"
    name = "匹配状态与证据链接一致"
    category = ComplianceRuleCategory.consistency
    description = "正向匹配应至少有一条企业证据链接。"
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
                    message="无匹配记录，跳过状态/链接一致性检查。",
                    finding_suffix="no_matches",
                )
            )
            return findings

        for match in ctx.evidence_matches:
            status = enum_value(match.status)
            if status not in POSITIVE_MATCH_STATUSES:
                continue
            links = list(match.company_links or [])
            if links:
                findings.append(
                    make_finding(
                        rule_id=self.rule_id,
                        rule_name=self.name,
                        category=self.category,
                        severity=ComplianceSeverity.info,
                        status=ComplianceFindingStatus.pass_,
                        message="正向匹配已关联企业证据链接。",
                        finding_suffix=f"ok:{match.id}",
                        match_id=match.id,
                        requirement_id=match.requirement_id,
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
                        message="正向匹配缺少企业证据链接，状态与证据不一致。",
                        finding_suffix=str(match.id),
                        match_id=match.id,
                        requirement_id=match.requirement_id,
                        remediation="重新匹配并写入 company evidence links。",
                    )
                )
        if not any(f.rule_id == self.rule_id for f in findings):
            findings.append(
                make_finding(
                    rule_id=self.rule_id,
                    rule_name=self.name,
                    category=self.category,
                    severity=ComplianceSeverity.info,
                    status=ComplianceFindingStatus.pass_,
                    message="无正向匹配需要检查链接一致性。",
                    finding_suffix="no_positive",
                )
            )
        return findings


class ReviewLifecycleConsistencyRule:
    rule_id = "E002_review_lifecycle"
    name = "审核与生命周期一致"
    category = ComplianceRuleCategory.consistency
    description = "已确认匹配不得处于 superseded；active 匹配不应指向自身 supersede 环。"
    default_severity = ComplianceSeverity.error

    def evaluate(self, ctx: ComplianceContext) -> list[ComplianceFinding]:
        findings: list[ComplianceFinding] = []
        # Context only loads active matches; still validate fields.
        if not ctx.evidence_matches:
            findings.append(
                make_finding(
                    rule_id=self.rule_id,
                    rule_name=self.name,
                    category=self.category,
                    severity=ComplianceSeverity.warning,
                    status=ComplianceFindingStatus.unknown,
                    message="无匹配记录，跳过生命周期一致性检查。",
                    finding_suffix="no_matches",
                )
            )
            return findings

        bad = 0
        for match in ctx.evidence_matches:
            lifecycle = getattr(match, "lifecycle_status", None) or "active"
            review = enum_value(match.review_status)
            if lifecycle != "active":
                bad += 1
                findings.append(
                    make_finding(
                        rule_id=self.rule_id,
                        rule_name=self.name,
                        category=self.category,
                        severity=self.default_severity,
                        status=ComplianceFindingStatus.fail,
                        message=f"期望 active 匹配，实际 lifecycle={lifecycle}。",
                        finding_suffix=f"lifecycle:{match.id}",
                        match_id=match.id,
                        requirement_id=match.requirement_id,
                    )
                )
                continue
            if (
                review == MatchReviewStatus.confirmed.value
                and match.superseded_by_match_id is not None
            ):
                bad += 1
                findings.append(
                    make_finding(
                        rule_id=self.rule_id,
                        rule_name=self.name,
                        category=self.category,
                        severity=self.default_severity,
                        status=ComplianceFindingStatus.fail,
                        message="已确认匹配同时标记为被 supersede，数据不一致。",
                        finding_suffix=f"confirmed_superseded:{match.id}",
                        match_id=match.id,
                        requirement_id=match.requirement_id,
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
                        message="匹配审核状态与生命周期一致。",
                        finding_suffix=f"ok:{match.id}",
                        match_id=match.id,
                        requirement_id=match.requirement_id,
                    )
                )
        return findings


class DeadlineFieldPresenceRule:
    rule_id = "E003_deadline_presence"
    name = "截止日期一致性"
    category = ComplianceRuleCategory.consistency
    description = "存在 deadline 类要求时，项目 bid_deadline 应已填写；否则警告。"
    default_severity = ComplianceSeverity.warning

    def evaluate(self, ctx: ComplianceContext) -> list[ComplianceFinding]:
        findings: list[ComplianceFinding] = []
        deadline_reqs = [
            r for r in ctx.requirements if enum_value(r.category) == "deadline"
        ]
        project = ctx.project
        bid_deadline = getattr(project, "bid_deadline", None) if project else None

        if not deadline_reqs and bid_deadline is None:
            findings.append(
                make_finding(
                    rule_id=self.rule_id,
                    rule_name=self.name,
                    category=self.category,
                    severity=ComplianceSeverity.warning,
                    status=ComplianceFindingStatus.unknown,
                    message="未识别到截止日期要求，且项目未填写 bid_deadline。",
                    finding_suffix="insufficient",
                    remediation="确认招标文件是否含截止时间条款，或在项目信息中补录。",
                )
            )
            return findings

        if deadline_reqs and bid_deadline is None:
            for req in deadline_reqs:
                findings.append(
                    make_finding(
                        rule_id=self.rule_id,
                        rule_name=self.name,
                        category=self.category,
                        severity=self.default_severity,
                        status=ComplianceFindingStatus.fail,
                        message=(
                            f"存在截止日期要求「{req.title}」，"
                            "但项目 bid_deadline 为空。"
                        ),
                        finding_suffix=str(req.id),
                        requirement_id=req.id,
                        remediation="在项目详情补录投标截止时间。",
                        source_location_json={
                            "source_page": getattr(req, "source_page", None),
                            "source_section": getattr(req, "source_section", None),
                        },
                    )
                )
            return findings

        findings.append(
            make_finding(
                rule_id=self.rule_id,
                rule_name=self.name,
                category=self.category,
                severity=ComplianceSeverity.info,
                status=ComplianceFindingStatus.pass_,
                message=(
                    "截止日期字段检查通过"
                    + (
                        f"（bid_deadline={bid_deadline.isoformat()}）"
                        if bid_deadline is not None
                        else ""
                    )
                    + "。"
                ),
                finding_suffix="ok",
            )
        )
        return findings


MUTUALLY_EXCLUSIVE_STATUSES = frozenset(
    {
        "supported",
        "partially_supported",
        "insufficient_evidence",
        "conflicting_evidence",
        "not_applicable",
    }
)


class MultipleExclusiveMatchStatusesRule:
    rule_id = "E004_exclusive_match_statuses"
    name = "同一要求多条互斥匹配状态"
    category = ComplianceRuleCategory.consistency
    description = "同一 requirement 不应同时存在多条互斥的 active 匹配状态。"
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
                    message="无要求数据，跳过互斥匹配状态检查。",
                    finding_suffix="no_requirements",
                )
            )
            return findings

        bad = 0
        for req in ctx.requirements:
            matches = ctx.matches_by_requirement_id.get(req.id) or []
            statuses = {
                enum_value(m.status)
                for m in matches
                if enum_value(m.status) in MUTUALLY_EXCLUSIVE_STATUSES
            }
            # Positive statuses are mutually exclusive with gap/conflict/n/a when coexisting
            positive = statuses & POSITIVE_MATCH_STATUSES
            negative = statuses & {
                "insufficient_evidence",
                "conflicting_evidence",
                "not_applicable",
            }
            if positive and negative:
                bad += 1
                findings.append(
                    make_finding(
                        rule_id=self.rule_id,
                        rule_name=self.name,
                        category=self.category,
                        severity=self.default_severity,
                        status=ComplianceFindingStatus.fail,
                        message=(
                            f"要求「{req.title}」同时存在正向与缺口/冲突匹配状态："
                            f"{sorted(statuses)}。"
                        ),
                        finding_suffix=str(req.id),
                        requirement_id=req.id,
                        remediation="保留一条 active 匹配或 supersede 历史记录。",
                        metadata_json={"statuses": sorted(statuses)},
                    )
                )
            elif len(statuses) > 1:
                only_positive = positive == statuses and positive <= {
                    "supported",
                    "partially_supported",
                }
                if only_positive:
                    continue
                # multiple distinct exclusive statuses (e.g. conflicting + insufficient)
                if len(statuses - POSITIVE_MATCH_STATUSES) > 1 or (
                    "supported" in statuses
                    and "partially_supported" in statuses
                    and negative
                ):
                    bad += 1
                    findings.append(
                        make_finding(
                            rule_id=self.rule_id,
                            rule_name=self.name,
                            category=self.category,
                            severity=self.default_severity,
                            status=ComplianceFindingStatus.fail,
                            message=(
                                f"要求「{req.title}」存在多条互斥 active 匹配状态："
                                f"{sorted(statuses)}。"
                            ),
                            finding_suffix=str(req.id),
                            requirement_id=req.id,
                            metadata_json={"statuses": sorted(statuses)},
                        )
                    )
        if bad == 0:
            findings.append(
                make_finding(
                    rule_id=self.rule_id,
                    rule_name=self.name,
                    category=self.category,
                    severity=ComplianceSeverity.info,
                    status=ComplianceFindingStatus.pass_,
                    message="未发现同一要求下互斥的 active 匹配状态组合。",
                    finding_suffix="ok",
                )
            )
        return findings


class ProjectOwnershipConsistencyRule:
    rule_id = "E005_project_ownership"
    name = "实体项目归属一致"
    category = ComplianceRuleCategory.consistency
    description = "requirement / match / draft / document 的 project_id 必须与当前项目一致。"
    default_severity = ComplianceSeverity.critical

    def evaluate(self, ctx: ComplianceContext) -> list[ComplianceFinding]:
        findings: list[ComplianceFinding] = []
        pid = ctx.project_id
        bad = 0

        def _check(
            obj: Any,
            label: str,
            *,
            requirement_id=None,
            match_id=None,
            draft_id=None,
        ) -> None:
            nonlocal bad
            opid = getattr(obj, "project_id", None)
            if opid is None or opid == pid:
                return
            bad += 1
            findings.append(
                make_finding(
                    rule_id=self.rule_id,
                    rule_name=self.name,
                    category=self.category,
                    severity=self.default_severity,
                    status=ComplianceFindingStatus.fail,
                    message=f"{label} 的 project_id 与当前项目不一致。",
                    finding_suffix=f"{label}:{getattr(obj, 'id', id(obj))}",
                    requirement_id=requirement_id,
                    match_id=match_id,
                    draft_id=draft_id,
                    metadata_json={
                        "expected_project_id": str(pid),
                        "actual_project_id": str(opid),
                    },
                )
            )

        for req in ctx.requirements:
            _check(req, "requirement", requirement_id=req.id)
        for match in ctx.evidence_matches:
            _check(
                match,
                "match",
                requirement_id=match.requirement_id,
                match_id=match.id,
            )
        for draft in ctx.drafts:
            _check(draft, "draft", draft_id=draft.id)
        for doc in ctx.documents_by_id.values():
            _check(doc, "document")
        for chunk in ctx.chunks_by_id.values():
            _check(chunk, "chunk")

        if bad == 0:
            findings.append(
                make_finding(
                    rule_id=self.rule_id,
                    rule_name=self.name,
                    category=self.category,
                    severity=ComplianceSeverity.info,
                    status=ComplianceFindingStatus.pass_,
                    message="已加载实体的 project_id 均与当前项目一致。",
                    finding_suffix="ok",
                )
            )
        return findings


class InsufficientMatchDefinitiveDraftRule:
    rule_id = "E006_gap_match_definitive_draft"
    name = "材料不足却写明确满足"
    category = ComplianceRuleCategory.consistency
    description = (
        "匹配为 insufficient_evidence 时，草稿不得出现强满足/明确响应表述。"
    )
    default_severity = ComplianceSeverity.error

    def evaluate(self, ctx: ComplianceContext) -> list[ComplianceFinding]:
        findings: list[ComplianceFinding] = []
        gap_matches = [
            m
            for m in ctx.evidence_matches
            if enum_value(m.status) == "insufficient_evidence"
        ]
        versions = current_draft_versions(ctx)
        if not gap_matches:
            findings.append(
                make_finding(
                    rule_id=self.rule_id,
                    rule_name=self.name,
                    category=self.category,
                    severity=ComplianceSeverity.info,
                    status=ComplianceFindingStatus.pass_,
                    message="无 insufficient_evidence 匹配需要与草稿交叉检查。",
                    finding_suffix="no_gap",
                )
            )
            return findings
        if not versions:
            findings.append(
                make_finding(
                    rule_id=self.rule_id,
                    rule_name=self.name,
                    category=self.category,
                    severity=ComplianceSeverity.warning,
                    status=ComplianceFindingStatus.unknown,
                    message="存在材料不足匹配，但无可检查草稿。",
                    finding_suffix="no_draft",
                    draft_id=ctx.draft_id,
                )
            )
            return findings

        version_ids = {v.id for v in versions}
        hits = 0
        for match in gap_matches:
            # gather draft text tied to this requirement
            related_texts: list[str] = []
            for ver in versions:
                content = ver.content_json if isinstance(ver.content_json, dict) else {}
                for section in content.get("sections") or []:
                    if not isinstance(section, dict):
                        continue
                    for block in section.get("blocks") or []:
                        if not isinstance(block, dict):
                            continue
                        req_ids = {
                            str(x) for x in (block.get("requirement_ids") or [])
                        }
                        if str(match.requirement_id) in req_ids:
                            related_texts.append(str(block.get("content") or ""))
                related_texts.append(draft_blob(content, ver.content_markdown))
            for src in ctx.draft_sources:
                if (
                    src.draft_version_id in version_ids
                    and src.requirement_id == match.requirement_id
                    and src.source_quote
                ):
                    related_texts.append(src.source_quote)

            blob = "\n".join(related_texts)
            strong = _STRONG_RE.search(blob)
            # also catch "明确满足" / "已满足该要求" style
            definitive = strong or re.search(r"明确满足|已满足该要求|完全响应", blob)
            if definitive:
                hits += 1
                findings.append(
                    make_finding(
                        rule_id=self.rule_id,
                        rule_name=self.name,
                        category=self.category,
                        severity=self.default_severity,
                        status=ComplianceFindingStatus.fail,
                        message=(
                            "匹配为 insufficient_evidence，但草稿写出明确满足表述。"
                        ),
                        finding_suffix=str(match.id),
                        requirement_id=match.requirement_id,
                        match_id=match.id,
                        draft_id=versions[0].draft_id,
                        remediation="改为材料缺口表述，或补齐证据后再写正向响应。",
                        metadata_json={
                            "matched": definitive.group(0)
                            if hasattr(definitive, "group")
                            else str(definitive)
                        },
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
                    message="材料不足匹配未与草稿强满足表述冲突。",
                    finding_suffix="ok",
                )
            )
        return findings


CONSISTENCY_RULES: list[ComplianceRule] = [
    MatchStatusVsLinksRule(),
    ReviewLifecycleConsistencyRule(),
    DeadlineFieldPresenceRule(),
    MultipleExclusiveMatchStatusesRule(),
    ProjectOwnershipConsistencyRule(),
    InsufficientMatchDefinitiveDraftRule(),
]
