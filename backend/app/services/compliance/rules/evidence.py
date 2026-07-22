"""Category B — evidence integrity rules."""

from __future__ import annotations

from typing import Any

from app.models.enums import (
    ComplianceFindingStatus,
    ComplianceRuleCategory,
    ComplianceSeverity,
)
from app.schemas.compliance import ComplianceContext, ComplianceFinding
from app.services.compliance.config import (
    MIN_QUOTE_LENGTH,
    POSITIVE_MATCH_STATUSES,
    TENDER_DOCUMENT_TYPES,
)
from app.services.compliance.findings import enum_value, make_finding
from app.services.compliance.registry import ComplianceRule
from app.services.evidence_validate import quote_in_content


class QuoteGroundingRule:
    rule_id = "B001_quote_grounding"
    name = "企业引文接地"
    category = ComplianceRuleCategory.evidence
    description = "企业匹配引文必须能在对应 chunk 原文中找到（quote_in_content）。"
    default_severity = ComplianceSeverity.error

    def evaluate(self, ctx: ComplianceContext) -> list[ComplianceFinding]:
        findings: list[ComplianceFinding] = []
        links = list(ctx.company_match_links or [])
        if not links:
            findings.append(
                make_finding(
                    rule_id=self.rule_id,
                    rule_name=self.name,
                    category=self.category,
                    severity=ComplianceSeverity.warning,
                    status=ComplianceFindingStatus.unknown,
                    message="当前无企业侧匹配引文，无法做接地校验。",
                    finding_suffix="no_links",
                    remediation="先完成材料匹配以产生可校验引文。",
                )
            )
            return findings

        checked = 0
        for link in links:
            quote = (link.quote or "").strip()
            if len(quote) < MIN_QUOTE_LENGTH:
                findings.append(
                    make_finding(
                        rule_id=self.rule_id,
                        rule_name=self.name,
                        category=self.category,
                        severity=ComplianceSeverity.warning,
                        status=ComplianceFindingStatus.unknown,
                        message="企业引文过短或为空，无法可靠接地校验。",
                        finding_suffix=f"short:{link.id}",
                        match_id=link.match_id,
                        evidence_json={"quote": quote or None, "link_id": str(link.id)},
                        remediation="补充完整原文引文后再检查。",
                    )
                )
                continue
            if not link.chunk_id:
                findings.append(
                    make_finding(
                        rule_id=self.rule_id,
                        rule_name=self.name,
                        category=self.category,
                        severity=self.default_severity,
                        status=ComplianceFindingStatus.fail,
                        message="企业引文缺少 chunk_id，无法接地。",
                        finding_suffix=f"no_chunk:{link.id}",
                        match_id=link.match_id,
                        evidence_json={"quote": quote[:500], "link_id": str(link.id)},
                    )
                )
                continue
            chunk = ctx.chunks_by_id.get(link.chunk_id)
            if chunk is None:
                findings.append(
                    make_finding(
                        rule_id=self.rule_id,
                        rule_name=self.name,
                        category=self.category,
                        severity=self.default_severity,
                        status=ComplianceFindingStatus.fail,
                        message="引文引用的 chunk 不在当前项目中。",
                        finding_suffix=f"missing_chunk:{link.id}",
                        match_id=link.match_id,
                        evidence_json={
                            "quote": quote[:500],
                            "chunk_id": str(link.chunk_id),
                        },
                    )
                )
                continue
            checked += 1
            ok = quote_in_content(quote, chunk.content or "")
            doc = ctx.documents_by_id.get(link.document_id) if link.document_id else None
            location = {
                "document_id": str(link.document_id) if link.document_id else None,
                "chunk_id": str(link.chunk_id),
                "page_start": getattr(chunk, "page_start", None),
                "page_end": getattr(chunk, "page_end", None),
                "section": getattr(chunk, "section", None),
                "file_name": getattr(doc, "file_name", None) if doc else None,
            }
            if ok:
                findings.append(
                    make_finding(
                        rule_id=self.rule_id,
                        rule_name=self.name,
                        category=self.category,
                        severity=ComplianceSeverity.info,
                        status=ComplianceFindingStatus.pass_,
                        message="企业引文可在原文 chunk 中定位。",
                        finding_suffix=f"ok:{link.id}",
                        match_id=link.match_id,
                        evidence_json={"quote": quote[:500]},
                        source_location_json=location,
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
                        message="企业引文无法在对应 chunk 原文中找到（接地失败）。",
                        finding_suffix=f"ungrounded:{link.id}",
                        match_id=link.match_id,
                        evidence_json={"quote": quote[:500]},
                        source_location_json=location,
                        remediation="修正引文或重新匹配，禁止保留未接地文本。",
                    )
                )
        if checked == 0 and not any(
            f.status == ComplianceFindingStatus.fail for f in findings
        ):
            # only short/empty quotes — already reported as unknown
            pass
        return findings


class CompanyEvidenceDocumentScopeRule:
    rule_id = "B002_company_doc_scope"
    name = "企业证据文档范围"
    category = ComplianceRuleCategory.evidence
    description = "企业匹配证据不得引用招标侧文档类型。"
    default_severity = ComplianceSeverity.critical

    def evaluate(self, ctx: ComplianceContext) -> list[ComplianceFinding]:
        findings: list[ComplianceFinding] = []
        links = list(ctx.company_match_links or [])
        if not links:
            findings.append(
                make_finding(
                    rule_id=self.rule_id,
                    rule_name=self.name,
                    category=self.category,
                    severity=ComplianceSeverity.warning,
                    status=ComplianceFindingStatus.unknown,
                    message="无企业侧证据链接，跳过文档范围检查。",
                    finding_suffix="no_links",
                )
            )
            return findings

        violations = 0
        unknowns = 0
        for link in links:
            if not link.document_id:
                unknowns += 1
                findings.append(
                    make_finding(
                        rule_id=self.rule_id,
                        rule_name=self.name,
                        category=self.category,
                        severity=ComplianceSeverity.warning,
                        status=ComplianceFindingStatus.unknown,
                        message="企业证据缺少 document_id，无法判定文档类型。",
                        finding_suffix=f"no_doc:{link.id}",
                        match_id=link.match_id,
                    )
                )
                continue
            doc = ctx.documents_by_id.get(link.document_id)
            if doc is None:
                violations += 1
                findings.append(
                    make_finding(
                        rule_id=self.rule_id,
                        rule_name=self.name,
                        category=self.category,
                        severity=ComplianceSeverity.error,
                        status=ComplianceFindingStatus.fail,
                        message="企业证据引用了不存在的文档。",
                        finding_suffix=f"missing_doc:{link.id}",
                        match_id=link.match_id,
                        source_location_json={"document_id": str(link.document_id)},
                    )
                )
                continue
            dtype = enum_value(doc.document_type)
            if dtype in TENDER_DOCUMENT_TYPES:
                violations += 1
                findings.append(
                    make_finding(
                        rule_id=self.rule_id,
                        rule_name=self.name,
                        category=self.category,
                        severity=self.default_severity,
                        status=ComplianceFindingStatus.fail,
                        message=(
                            f"企业证据错误引用招标侧文档「{doc.file_name}」"
                            f"（类型 {dtype}）。"
                        ),
                        finding_suffix=f"tender_leak:{link.id}",
                        match_id=link.match_id,
                        source_location_json={
                            "document_id": str(doc.id),
                            "file_name": doc.file_name,
                            "document_type": dtype,
                        },
                        remediation="仅允许 company_profile/qualification/case/personnel/product。",
                    )
                )
        if violations == 0 and unknowns == 0:
            findings.append(
                make_finding(
                    rule_id=self.rule_id,
                    rule_name=self.name,
                    category=self.category,
                    severity=ComplianceSeverity.info,
                    status=ComplianceFindingStatus.pass_,
                    message="企业证据文档类型均在允许范围内。",
                    finding_suffix="scope_ok",
                )
            )
        return findings


class SupportedMatchNeedsQuoteRule:
    rule_id = "B003_supported_needs_quote"
    name = "正向匹配需有引文"
    category = ComplianceRuleCategory.evidence
    description = "supported / partially_supported 匹配应有非空企业引文。"
    default_severity = ComplianceSeverity.error

    def evaluate(self, ctx: ComplianceContext) -> list[ComplianceFinding]:
        findings: list[ComplianceFinding] = []
        positives = [
            m
            for m in ctx.evidence_matches
            if enum_value(m.status) in POSITIVE_MATCH_STATUSES
        ]
        if not positives:
            findings.append(
                make_finding(
                    rule_id=self.rule_id,
                    rule_name=self.name,
                    category=self.category,
                    severity=ComplianceSeverity.warning,
                    status=ComplianceFindingStatus.unknown,
                    message="无正向匹配，跳过引文完备性检查。",
                    finding_suffix="no_positive",
                )
            )
            return findings

        for match in positives:
            quote = (match.primary_company_quote or "").strip()
            links = list(match.company_links or [])
            link_quotes = [(lnk.quote or "").strip() for lnk in links]
            has_quote = bool(quote) or any(link_quotes)
            if has_quote:
                findings.append(
                    make_finding(
                        rule_id=self.rule_id,
                        rule_name=self.name,
                        category=self.category,
                        severity=ComplianceSeverity.info,
                        status=ComplianceFindingStatus.pass_,
                        message="正向匹配已附带企业引文。",
                        finding_suffix=str(match.id),
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
                        message="正向匹配缺少企业引文，证据不完整。",
                        finding_suffix=str(match.id),
                        match_id=match.id,
                        requirement_id=match.requirement_id,
                        remediation="重新匹配并保留可定位原文引文。",
                    )
                )
        return findings


class DanglingEvidenceLinkRule:
    rule_id = "B004_dangling_evidence"
    name = "悬空证据链接"
    category = ComplianceRuleCategory.evidence
    description = (
        "证据链接的 document_id/chunk_id 必须存在于项目索引；"
        "若存在页码/字符区间字段则校验其合法性。"
    )
    default_severity = ComplianceSeverity.error

    def evaluate(self, ctx: ComplianceContext) -> list[ComplianceFinding]:
        findings: list[ComplianceFinding] = []
        links: list[Any] = list(ctx.company_match_links or [])
        links.extend(list(ctx.tender_evidence_links or []))
        if not links:
            findings.append(
                make_finding(
                    rule_id=self.rule_id,
                    rule_name=self.name,
                    category=self.category,
                    severity=ComplianceSeverity.warning,
                    status=ComplianceFindingStatus.unknown,
                    message="无证据链接可检查悬空引用。",
                    finding_suffix="no_links",
                )
            )
            return findings

        bad = 0
        for link in links:
            problems: list[str] = []
            doc_id = getattr(link, "document_id", None)
            chunk_id = getattr(link, "chunk_id", None)
            match_id = getattr(link, "match_id", None)
            req_id = getattr(link, "requirement_id", None)

            if doc_id is not None and doc_id not in ctx.documents_by_id:
                problems.append("document_missing")
            if chunk_id is not None and chunk_id not in ctx.chunks_by_id:
                problems.append("chunk_missing")
            if chunk_id is not None and chunk_id in ctx.chunks_by_id and doc_id:
                chunk = ctx.chunks_by_id[chunk_id]
                if getattr(chunk, "document_id", None) != doc_id:
                    problems.append("chunk_document_mismatch")

            # Page / char range checks when fields are present
            chunk = ctx.chunks_by_id.get(chunk_id) if chunk_id else None
            page_start = getattr(chunk, "page_start", None) if chunk else None
            page_end = getattr(chunk, "page_end", None) if chunk else None
            if (
                page_start is not None
                and page_end is not None
                and int(page_start) > int(page_end)
            ):
                problems.append("invalid_page_range")

            meta: dict[str, Any] = {}
            if chunk and isinstance(getattr(chunk, "metadata_json", None), dict):
                meta = chunk.metadata_json or {}
            notes_meta = getattr(link, "notes", None)
            if isinstance(notes_meta, dict):
                meta = {**meta, **notes_meta}
            char_start = meta.get("char_start")
            char_end = meta.get("char_end")
            if char_start is not None and char_end is not None:
                try:
                    if int(char_start) > int(char_end):
                        problems.append("invalid_char_range")
                except (TypeError, ValueError):
                    problems.append("invalid_char_range")

            if problems:
                bad += 1
                findings.append(
                    make_finding(
                        rule_id=self.rule_id,
                        rule_name=self.name,
                        category=self.category,
                        severity=self.default_severity,
                        status=ComplianceFindingStatus.fail,
                        message="证据链接悬空或区间字段非法。",
                        finding_suffix=str(getattr(link, "id", id(link))),
                        match_id=match_id,
                        requirement_id=req_id,
                        metadata_json={"problems": problems},
                        source_location_json={
                            "document_id": str(doc_id) if doc_id else None,
                            "chunk_id": str(chunk_id) if chunk_id else None,
                        },
                        remediation="修复或删除无效证据链接。",
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
                    message=f"全部 {len(links)} 条证据链接索引有效。",
                    finding_suffix="ok",
                )
            )
        return findings


class ConflictingEvidenceCitationRule:
    rule_id = "B005_conflicting_evidence_citation"
    name = "冲突证据引用"
    category = ComplianceRuleCategory.evidence
    description = (
        "匹配状态为 conflicting_evidence，或企业角色链接引用招标侧文档时记冲突。"
    )
    default_severity = ComplianceSeverity.error

    def evaluate(self, ctx: ComplianceContext) -> list[ComplianceFinding]:
        findings: list[ComplianceFinding] = []
        if not ctx.evidence_matches and not ctx.company_match_links:
            findings.append(
                make_finding(
                    rule_id=self.rule_id,
                    rule_name=self.name,
                    category=self.category,
                    severity=ComplianceSeverity.warning,
                    status=ComplianceFindingStatus.unknown,
                    message="无匹配/企业证据可检查冲突引用。",
                    finding_suffix="no_data",
                )
            )
            return findings

        hits = 0
        for match in ctx.evidence_matches:
            if enum_value(match.status) == "conflicting_evidence":
                hits += 1
                findings.append(
                    make_finding(
                        rule_id=self.rule_id,
                        rule_name=self.name,
                        category=self.category,
                        severity=self.default_severity,
                        status=ComplianceFindingStatus.fail,
                        message="匹配状态为 conflicting_evidence，存在冲突证据。",
                        finding_suffix=f"status:{match.id}",
                        match_id=match.id,
                        requirement_id=match.requirement_id,
                        remediation="人工裁决冲突证据后再确认。",
                    )
                )

        for link in ctx.company_match_links:
            role = (getattr(link, "role", None) or "company_support").lower()
            if "company" not in role:
                continue
            doc_id = getattr(link, "document_id", None)
            if not doc_id:
                continue
            doc = ctx.documents_by_id.get(doc_id)
            if doc is None:
                continue
            dtype = enum_value(doc.document_type)
            if dtype in TENDER_DOCUMENT_TYPES:
                hits += 1
                findings.append(
                    make_finding(
                        rule_id=self.rule_id,
                        rule_name=self.name,
                        category=self.category,
                        severity=self.default_severity,
                        status=ComplianceFindingStatus.fail,
                        message=(
                            f"企业角色证据链接引用了招标侧文档（类型 {dtype}）。"
                        ),
                        finding_suffix=f"tender_as_company:{link.id}",
                        match_id=link.match_id,
                        source_location_json={
                            "document_id": str(doc.id),
                            "file_name": doc.file_name,
                            "document_type": dtype,
                        },
                        remediation="企业证据不得使用招标侧文档。",
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
                    message="未发现冲突证据状态或招标文档冒充企业证据。",
                    finding_suffix="ok",
                )
            )
        return findings


EVIDENCE_RULES: list[ComplianceRule] = [
    QuoteGroundingRule(),
    CompanyEvidenceDocumentScopeRule(),
    SupportedMatchNeedsQuoteRule(),
    DanglingEvidenceLinkRule(),
    ConflictingEvidenceCitationRule(),
]
