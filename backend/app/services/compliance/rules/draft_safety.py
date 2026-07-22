"""Category D — proposal draft safety rules."""

from __future__ import annotations

import re
from uuid import UUID

from app.models.enums import (
    ComplianceFindingStatus,
    ComplianceRuleCategory,
    ComplianceSeverity,
)
from app.schemas.compliance import ComplianceContext, ComplianceFinding
from app.schemas.proposal_draft import UNEVIDENCED_MARKER
from app.services.compliance.config import (
    FORBIDDEN_DRAFT_CLAIM_PATTERNS,
    MIN_DRAFT_CONTENT_CHARS,
    PLACEHOLDER_PATTERNS,
    POSITIVE_MATCH_STATUSES,
    STRONG_SATISFACTION_PATTERNS,
)
from app.services.compliance.draft_utils import (
    current_draft_versions,
    draft_blob,
    parse_uuid_safe,
)
from app.services.compliance.findings import enum_value, make_finding
from app.services.compliance.registry import ComplianceRule
from app.services.proposal_draft_validate import content_has_unevidenced_manual

_FORBIDDEN_RE = re.compile("|".join(FORBIDDEN_DRAFT_CLAIM_PATTERNS))
_PLACEHOLDER_RE = re.compile("|".join(PLACEHOLDER_PATTERNS), re.IGNORECASE)
_STRONG_RE = re.compile("|".join(STRONG_SATISFACTION_PATTERNS))


class UnevidencedManualContentRule:
    rule_id = "D001_unevidenced_manual"
    name = "无证据人工增补"
    category = ComplianceRuleCategory.draft_safety
    description = "草稿不得包含未举证的人工增补正文。"
    default_severity = ComplianceSeverity.error

    def evaluate(self, ctx: ComplianceContext) -> list[ComplianceFinding]:
        findings: list[ComplianceFinding] = []
        versions = current_draft_versions(ctx)
        if not versions:
            findings.append(
                make_finding(
                    rule_id=self.rule_id,
                    rule_name=self.name,
                    category=self.category,
                    severity=ComplianceSeverity.warning,
                    status=ComplianceFindingStatus.unknown,
                    message="无可检查的响应草稿版本。",
                    finding_suffix="no_draft",
                    draft_id=ctx.draft_id,
                    remediation="生成或指定 draft_id 后再检查草稿安全。",
                )
            )
            return findings

        for ver in versions:
            content = ver.content_json if isinstance(ver.content_json, dict) else {}
            has_unevidenced = content_has_unevidenced_manual(content)
            marker_hit = UNEVIDENCED_MARKER in (ver.content_markdown or "")
            if has_unevidenced or marker_hit:
                findings.append(
                    make_finding(
                        rule_id=self.rule_id,
                        rule_name=self.name,
                        category=self.category,
                        severity=self.default_severity,
                        status=ComplianceFindingStatus.fail,
                        message="当前草稿版本含未举证的人工增补内容。",
                        finding_suffix=str(ver.id),
                        draft_id=ver.draft_id,
                        remediation="为人工增补补充证据引用，或删除无证据段落。",
                        metadata_json={"version_id": str(ver.id)},
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
                        message="当前草稿版本未检测到无证据人工增补标记。",
                        finding_suffix=f"ok:{ver.id}",
                        draft_id=ver.draft_id,
                        metadata_json={"version_id": str(ver.id)},
                    )
                )
        return findings


class ForbiddenDraftClaimsRule:
    rule_id = "D002_forbidden_claims"
    name = "禁止性承诺措辞"
    category = ComplianceRuleCategory.draft_safety
    description = "草稿不得出现保证中标、建议投标等禁止性承诺措辞。"
    default_severity = ComplianceSeverity.critical

    def evaluate(self, ctx: ComplianceContext) -> list[ComplianceFinding]:
        findings: list[ComplianceFinding] = []
        versions = current_draft_versions(ctx)
        if not versions:
            findings.append(
                make_finding(
                    rule_id=self.rule_id,
                    rule_name=self.name,
                    category=self.category,
                    severity=ComplianceSeverity.warning,
                    status=ComplianceFindingStatus.unknown,
                    message="无可检查的响应草稿版本。",
                    finding_suffix="no_draft",
                    draft_id=ctx.draft_id,
                )
            )
            return findings

        for ver in versions:
            content = ver.content_json if isinstance(ver.content_json, dict) else {}
            blob = draft_blob(content, ver.content_markdown)
            match = _FORBIDDEN_RE.search(blob)
            if match:
                findings.append(
                    make_finding(
                        rule_id=self.rule_id,
                        rule_name=self.name,
                        category=self.category,
                        severity=self.default_severity,
                        status=ComplianceFindingStatus.fail,
                        message=f"草稿含禁止性措辞「{match.group(0)}」。",
                        finding_suffix=str(ver.id),
                        draft_id=ver.draft_id,
                        remediation="删除投标结论/承诺类措辞；本工具不生成投标提交文件。",
                        metadata_json={
                            "version_id": str(ver.id),
                            "matched": match.group(0),
                        },
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
                        message="草稿未检测到禁止性承诺措辞。",
                        finding_suffix=f"ok:{ver.id}",
                        draft_id=ver.draft_id,
                    )
                )
        return findings


class DraftCitationIntegrityRule:
    rule_id = "D003_citation_integrity"
    name = "草稿引用完整性"
    category = ComplianceRuleCategory.draft_safety
    description = "草稿来源快照中的 requirement/match 引用必须存在于当前项目上下文。"
    default_severity = ComplianceSeverity.error

    def evaluate(self, ctx: ComplianceContext) -> list[ComplianceFinding]:
        findings: list[ComplianceFinding] = []
        versions = current_draft_versions(ctx)
        if not versions:
            findings.append(
                make_finding(
                    rule_id=self.rule_id,
                    rule_name=self.name,
                    category=self.category,
                    severity=ComplianceSeverity.warning,
                    status=ComplianceFindingStatus.unknown,
                    message="无可检查的响应草稿版本。",
                    finding_suffix="no_draft",
                    draft_id=ctx.draft_id,
                )
            )
            return findings

        version_ids = {v.id for v in versions}
        sources = [s for s in ctx.draft_sources if s.draft_version_id in version_ids]
        if not sources:
            findings.append(
                make_finding(
                    rule_id=self.rule_id,
                    rule_name=self.name,
                    category=self.category,
                    severity=ComplianceSeverity.warning,
                    status=ComplianceFindingStatus.unknown,
                    message="当前草稿版本没有来源快照条目。",
                    finding_suffix="no_sources",
                    draft_id=versions[0].draft_id,
                )
            )
            return findings

        bad = 0
        for src in sources:
            problems: list[str] = []
            if src.requirement_id and src.requirement_id not in ctx.requirements_by_id:
                problems.append("requirement_missing")
            if src.match_id:
                match = ctx.matches_by_id.get(src.match_id)
                if match is None or getattr(match, "lifecycle_status", "active") != "active":
                    problems.append("match_not_active")
            if problems:
                bad += 1
                findings.append(
                    make_finding(
                        rule_id=self.rule_id,
                        rule_name=self.name,
                        category=self.category,
                        severity=self.default_severity,
                        status=ComplianceFindingStatus.fail,
                        message="草稿来源引用与当前项目上下文不一致。",
                        finding_suffix=str(src.id),
                        draft_id=getattr(
                            next(
                                (v for v in versions if v.id == src.draft_version_id),
                                None,
                            ),
                            "draft_id",
                            None,
                        ),
                        requirement_id=src.requirement_id,
                        match_id=src.match_id,
                        metadata_json={"problems": problems},
                        remediation="重新生成草稿或人工修订以刷新来源快照。",
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
                    message=f"全部 {len(sources)} 条草稿来源引用可解析。",
                    finding_suffix="ok",
                    draft_id=versions[0].draft_id,
                )
            )
        return findings


class DraftPlaceholderRule:
    rule_id = "D004_placeholders"
    name = "草稿占位符/待补充"
    category = ComplianceRuleCategory.draft_safety
    description = "草稿不得残留 TODO / 待补充 / 占位符 / {{模板变量}}。"
    default_severity = ComplianceSeverity.warning

    def evaluate(self, ctx: ComplianceContext) -> list[ComplianceFinding]:
        findings: list[ComplianceFinding] = []
        versions = current_draft_versions(ctx)
        if not versions:
            findings.append(
                make_finding(
                    rule_id=self.rule_id,
                    rule_name=self.name,
                    category=self.category,
                    severity=ComplianceSeverity.warning,
                    status=ComplianceFindingStatus.unknown,
                    message="无可检查的响应草稿版本。",
                    finding_suffix="no_draft",
                    draft_id=ctx.draft_id,
                )
            )
            return findings

        for ver in versions:
            content = ver.content_json if isinstance(ver.content_json, dict) else {}
            blob = draft_blob(content, ver.content_markdown)
            hit = _PLACEHOLDER_RE.search(blob)
            if hit:
                findings.append(
                    make_finding(
                        rule_id=self.rule_id,
                        rule_name=self.name,
                        category=self.category,
                        severity=self.default_severity,
                        status=ComplianceFindingStatus.fail,
                        message=f"草稿含未完成占位标记「{hit.group(0)}」。",
                        finding_suffix=str(ver.id),
                        draft_id=ver.draft_id,
                        remediation="补全或删除占位符后再提交人工复核。",
                        metadata_json={"matched": hit.group(0)},
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
                        message="草稿未检测到占位符/待补充标记。",
                        finding_suffix=f"ok:{ver.id}",
                        draft_id=ver.draft_id,
                    )
                )
        return findings


class DraftEmptyOrShortRule:
    rule_id = "D005_empty_or_short"
    name = "草稿内容过短"
    category = ComplianceRuleCategory.draft_safety
    description = "当前草稿版本不得为空或明显过短。"
    default_severity = ComplianceSeverity.warning

    def evaluate(self, ctx: ComplianceContext) -> list[ComplianceFinding]:
        findings: list[ComplianceFinding] = []
        versions = current_draft_versions(ctx)
        if not versions:
            findings.append(
                make_finding(
                    rule_id=self.rule_id,
                    rule_name=self.name,
                    category=self.category,
                    severity=ComplianceSeverity.warning,
                    status=ComplianceFindingStatus.unknown,
                    message="无可检查的响应草稿版本。",
                    finding_suffix="no_draft",
                    draft_id=ctx.draft_id,
                )
            )
            return findings

        for ver in versions:
            content = ver.content_json if isinstance(ver.content_json, dict) else {}
            blob = " ".join(draft_blob(content, ver.content_markdown).split())
            if len(blob) < MIN_DRAFT_CONTENT_CHARS:
                findings.append(
                    make_finding(
                        rule_id=self.rule_id,
                        rule_name=self.name,
                        category=self.category,
                        severity=self.default_severity,
                        status=ComplianceFindingStatus.fail,
                        message=(
                            f"草稿正文过短（{len(blob)} 字符，阈值 {MIN_DRAFT_CONTENT_CHARS}）。"
                        ),
                        finding_suffix=str(ver.id),
                        draft_id=ver.draft_id,
                        remediation="补充基于证据的响应正文。",
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
                        message="草稿正文长度达到最低阈值。",
                        finding_suffix=f"ok:{ver.id}",
                        draft_id=ver.draft_id,
                    )
                )
        return findings


class DraftStrongClaimWithoutSupportRule:
    rule_id = "D006_strong_claim_without_support"
    name = "无正向匹配的强满足表述"
    category = ComplianceRuleCategory.draft_safety
    description = (
        "出现「完全满足/已具备/保证」等强表述时，对应要求的匹配须为 supported/partially_supported。"
    )
    default_severity = ComplianceSeverity.error

    def evaluate(self, ctx: ComplianceContext) -> list[ComplianceFinding]:
        findings: list[ComplianceFinding] = []
        versions = current_draft_versions(ctx)
        if not versions:
            findings.append(
                make_finding(
                    rule_id=self.rule_id,
                    rule_name=self.name,
                    category=self.category,
                    severity=ComplianceSeverity.warning,
                    status=ComplianceFindingStatus.unknown,
                    message="无可检查的响应草稿版本。",
                    finding_suffix="no_draft",
                    draft_id=ctx.draft_id,
                )
            )
            return findings

        version_ids = {v.id for v in versions}
        for ver in versions:
            content = ver.content_json if isinstance(ver.content_json, dict) else {}
            blob = draft_blob(content, ver.content_markdown)
            if not _STRONG_RE.search(blob):
                findings.append(
                    make_finding(
                        rule_id=self.rule_id,
                        rule_name=self.name,
                        category=self.category,
                        severity=ComplianceSeverity.info,
                        status=ComplianceFindingStatus.pass_,
                        message="草稿未出现强满足类表述。",
                        finding_suffix=f"none:{ver.id}",
                        draft_id=ver.draft_id,
                    )
                )
                continue

            # Collect requirement ids tied to this version
            req_ids: set[UUID] = set()
            for src in ctx.draft_sources:
                if src.draft_version_id in version_ids and src.requirement_id:
                    req_ids.add(src.requirement_id)
            for section in content.get("sections") or []:
                if not isinstance(section, dict):
                    continue
                for block in section.get("blocks") or []:
                    if not isinstance(block, dict):
                        continue
                    for raw in block.get("requirement_ids") or []:
                        rid = parse_uuid_safe(raw)
                        if rid:
                            req_ids.add(rid)

            if not req_ids:
                findings.append(
                    make_finding(
                        rule_id=self.rule_id,
                        rule_name=self.name,
                        category=self.category,
                        severity=ComplianceSeverity.warning,
                        status=ComplianceFindingStatus.unknown,
                        message="草稿含强满足表述，但无法关联到具体要求以核验匹配状态。",
                        finding_suffix=f"orphan:{ver.id}",
                        draft_id=ver.draft_id,
                        remediation="为强表述块绑定 requirement_id / 来源。",
                    )
                )
                continue

            bad = 0
            for rid in sorted(req_ids, key=str):
                matches = ctx.matches_by_requirement_id.get(rid) or []
                positive = [m for m in matches if enum_value(m.status) in POSITIVE_MATCH_STATUSES]
                if positive:
                    continue
                bad += 1
                status = enum_value(matches[0].status) if matches else "missing"
                findings.append(
                    make_finding(
                        rule_id=self.rule_id,
                        rule_name=self.name,
                        category=self.category,
                        severity=self.default_severity,
                        status=ComplianceFindingStatus.fail,
                        message=(f"草稿含强满足表述，但关联要求匹配非正向（status={status}）。"),
                        finding_suffix=f"{ver.id}:{rid}",
                        draft_id=ver.draft_id,
                        requirement_id=rid,
                        match_id=matches[0].id if matches else None,
                        remediation="删除强表述或先取得 supported/partially_supported 匹配。",
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
                        message="强满足表述所关联要求均有正向匹配。",
                        finding_suffix=f"ok:{ver.id}",
                        draft_id=ver.draft_id,
                    )
                )
        return findings


class DraftCrossProjectSourceRule:
    rule_id = "D007_cross_project_source"
    name = "草稿跨项目来源"
    category = ComplianceRuleCategory.draft_safety
    description = "草稿来源的 project_id / 文档归属必须与当前项目一致。"
    default_severity = ComplianceSeverity.critical

    def evaluate(self, ctx: ComplianceContext) -> list[ComplianceFinding]:
        findings: list[ComplianceFinding] = []
        versions = current_draft_versions(ctx)
        if not versions:
            findings.append(
                make_finding(
                    rule_id=self.rule_id,
                    rule_name=self.name,
                    category=self.category,
                    severity=ComplianceSeverity.warning,
                    status=ComplianceFindingStatus.unknown,
                    message="无可检查的响应草稿版本。",
                    finding_suffix="no_draft",
                    draft_id=ctx.draft_id,
                )
            )
            return findings

        version_ids = {v.id for v in versions}
        sources = [s for s in ctx.draft_sources if s.draft_version_id in version_ids]
        if not sources:
            findings.append(
                make_finding(
                    rule_id=self.rule_id,
                    rule_name=self.name,
                    category=self.category,
                    severity=ComplianceSeverity.warning,
                    status=ComplianceFindingStatus.unknown,
                    message="无草稿来源可检查项目归属。",
                    finding_suffix="no_sources",
                    draft_id=versions[0].draft_id,
                )
            )
            return findings

        bad = 0
        for src in sources:
            problems: list[str] = []
            if getattr(src, "project_id", None) and src.project_id != ctx.project_id:
                problems.append("source_project_mismatch")
            loc = src.location_json if isinstance(src.location_json, dict) else {}
            doc_id = parse_uuid_safe(loc.get("document_id") or loc.get("doc_id"))
            if doc_id is not None:
                doc = ctx.documents_by_id.get(doc_id)
                if doc is None:
                    problems.append("document_not_in_project_index")
                elif getattr(doc, "project_id", None) != ctx.project_id:
                    problems.append("document_project_mismatch")
            if problems:
                bad += 1
                findings.append(
                    make_finding(
                        rule_id=self.rule_id,
                        rule_name=self.name,
                        category=self.category,
                        severity=self.default_severity,
                        status=ComplianceFindingStatus.fail,
                        message="草稿来源引用了其他项目的文档或归属不一致。",
                        finding_suffix=str(src.id),
                        draft_id=versions[0].draft_id,
                        requirement_id=src.requirement_id,
                        match_id=src.match_id,
                        metadata_json={"problems": problems},
                        remediation="仅允许引用本项目文档；重建来源快照。",
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
                    message="草稿来源项目归属一致。",
                    finding_suffix="ok",
                    draft_id=versions[0].draft_id,
                )
            )
        return findings


DRAFT_SAFETY_RULES: list[ComplianceRule] = [
    UnevidencedManualContentRule(),
    ForbiddenDraftClaimsRule(),
    DraftCitationIntegrityRule(),
    DraftPlaceholderRule(),
    DraftEmptyOrShortRule(),
    DraftStrongClaimWithoutSupportRule(),
    DraftCrossProjectSourceRule(),
]
