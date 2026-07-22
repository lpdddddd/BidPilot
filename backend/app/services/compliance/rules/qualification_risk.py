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
    name = "结构化有效期/金额阈值比对"
    category = ComplianceRuleCategory.qualification_risk
    description = (
        "从要求与匹配/企业证据结构化字段解析金额/年限/数量/等级/有效期并比对；"
        "缺企业值→warning/unknown；明确不达标或过期→error/critical；无法解析不猜测。"
    )
    default_severity = ComplianceSeverity.warning

    def evaluate(self, ctx: ComplianceContext) -> list[ComplianceFinding]:
        from datetime import date as date_cls

        from app.services.compliance.config import (
            STRUCTURED_AMOUNT_KEYS,
            STRUCTURED_EXPIRY_KEYS,
            STRUCTURED_LEVEL_KEYS,
            STRUCTURED_QUANTITY_KEYS,
            STRUCTURED_YEARS_KEYS,
        )
        from app.services.compliance.parsers import (
            dig_keys,
            parse_amount_to_yuan,
            parse_date,
            parse_level,
            parse_quantity,
            parse_years_to_years,
        )

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
        today = date_cls.today()

        for req in ctx.requirements:
            req_bags = [
                req.metadata_json if isinstance(req.metadata_json, dict) else {},
                req.evidence_required_json
                if isinstance(req.evidence_required_json, dict)
                else {},
            ]
            req_found: dict[str, Any] = {}
            for bag in req_bags:
                req_found.update(dig_keys(bag, STRUCTURED_EXPIRY_KEYS))
                req_found.update(dig_keys(bag, STRUCTURED_AMOUNT_KEYS))
                req_found.update(dig_keys(bag, STRUCTURED_YEARS_KEYS))
                req_found.update(dig_keys(bag, STRUCTURED_QUANTITY_KEYS))
                req_found.update(dig_keys(bag, STRUCTURED_LEVEL_KEYS))
                req_found.update(dig_keys(bag, STRUCTURED_CONFLICT_KEYS))
            if not req_found:
                continue
            any_structured = True

            conflict_notes = req_found.get("conflict_notes") or req_found.get("conflicts")
            if conflict_notes:
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
                        metadata_json={"fields": req_found},
                    )
                )
                continue

            matches = ctx.matches_by_requirement_id.get(req.id) or []
            company_bags: list[dict[str, Any]] = []
            for match in matches:
                meta = match.metadata_json if isinstance(match.metadata_json, dict) else {}
                company_bags.append(meta)
                for link in list(getattr(match, "company_links", None) or []):
                    loc = getattr(link, "location_json", None)
                    if isinstance(loc, dict):
                        company_bags.append(loc)
                    lmeta = getattr(link, "metadata_json", None)
                    if isinstance(lmeta, dict):
                        company_bags.append(lmeta)

            company_found: dict[str, Any] = {}
            for bag in company_bags:
                company_found.update(dig_keys(bag, STRUCTURED_EXPIRY_KEYS))
                company_found.update(dig_keys(bag, STRUCTURED_AMOUNT_KEYS))
                company_found.update(dig_keys(bag, STRUCTURED_YEARS_KEYS))
                company_found.update(dig_keys(bag, STRUCTURED_QUANTITY_KEYS))
                company_found.update(dig_keys(bag, STRUCTURED_LEVEL_KEYS))

            # --- amount ---
            req_amount_raw = next(
                (req_found[k] for k in STRUCTURED_AMOUNT_KEYS if k in req_found), None
            )
            if req_amount_raw is not None:
                req_amt = parse_amount_to_yuan(req_amount_raw)
                if req_amt is None:
                    findings.append(
                        make_finding(
                            rule_id=self.rule_id,
                            rule_name=self.name,
                            category=self.category,
                            severity=ComplianceSeverity.warning,
                            status=ComplianceFindingStatus.unknown,
                            message=f"要求「{req.title}」金额阈值无法解析，跳过比对。",
                            finding_suffix=f"amount_unparseable:{req.id}",
                            requirement_id=req.id,
                            metadata_json={"raw": req_amount_raw},
                        )
                    )
                else:
                    co_amount_raw = next(
                        (company_found[k] for k in STRUCTURED_AMOUNT_KEYS if k in company_found),
                        None,
                    )
                    if co_amount_raw is None:
                        findings.append(
                            make_finding(
                                rule_id=self.rule_id,
                                rule_name=self.name,
                                category=self.category,
                                severity=ComplianceSeverity.warning,
                                status=ComplianceFindingStatus.unknown,
                                message=(
                                    f"要求「{req.title}」声明金额阈值 "
                                    f"({req_amt}元)，但企业侧无对应金额字段。"
                                ),
                                finding_suffix=f"amount_missing:{req.id}",
                                requirement_id=req.id,
                                remediation="在匹配 metadata 中补录企业金额/注册资本等字段。",
                            )
                        )
                    else:
                        co_amt = parse_amount_to_yuan(co_amount_raw)
                        if co_amt is None:
                            findings.append(
                                make_finding(
                                    rule_id=self.rule_id,
                                    rule_name=self.name,
                                    category=self.category,
                                    severity=ComplianceSeverity.warning,
                                    status=ComplianceFindingStatus.unknown,
                                    message=f"要求「{req.title}」企业金额无法解析，跳过比对。",
                                    finding_suffix=f"amount_co_unparseable:{req.id}",
                                    requirement_id=req.id,
                                )
                            )
                        elif co_amt + 1e-6 < req_amt:
                            findings.append(
                                make_finding(
                                    rule_id=self.rule_id,
                                    rule_name=self.name,
                                    category=self.category,
                                    severity=ComplianceSeverity.critical,
                                    status=ComplianceFindingStatus.fail,
                                    message=(
                                        f"要求「{req.title}」金额不达标："
                                        f"企业 {co_amt}元 < 要求 {req_amt}元。"
                                    ),
                                    finding_suffix=f"amount_fail:{req.id}",
                                    requirement_id=req.id,
                                    match_id=matches[0].id if matches else None,
                                    metadata_json={
                                        "required_yuan": req_amt,
                                        "company_yuan": co_amt,
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
                                    message=f"要求「{req.title}」金额阈值比对通过。",
                                    finding_suffix=f"amount_ok:{req.id}",
                                    requirement_id=req.id,
                                    match_id=matches[0].id if matches else None,
                                )
                            )

            # --- years ---
            req_years_raw = next(
                (req_found[k] for k in STRUCTURED_YEARS_KEYS if k in req_found), None
            )
            if req_years_raw is not None:
                req_y = parse_years_to_years(req_years_raw)
                if req_y is None:
                    findings.append(
                        make_finding(
                            rule_id=self.rule_id,
                            rule_name=self.name,
                            category=self.category,
                            severity=ComplianceSeverity.warning,
                            status=ComplianceFindingStatus.unknown,
                            message=f"要求「{req.title}」年限阈值无法解析，跳过比对。",
                            finding_suffix=f"years_unparseable:{req.id}",
                            requirement_id=req.id,
                        )
                    )
                else:
                    co_years_raw = next(
                        (company_found[k] for k in STRUCTURED_YEARS_KEYS if k in company_found),
                        None,
                    )
                    if co_years_raw is None:
                        findings.append(
                            make_finding(
                                rule_id=self.rule_id,
                                rule_name=self.name,
                                category=self.category,
                                severity=ComplianceSeverity.warning,
                                status=ComplianceFindingStatus.unknown,
                                message=f"要求「{req.title}」声明年限阈值，但企业侧无对应字段。",
                                finding_suffix=f"years_missing:{req.id}",
                                requirement_id=req.id,
                            )
                        )
                    else:
                        co_y = parse_years_to_years(co_years_raw)
                        if co_y is None:
                            findings.append(
                                make_finding(
                                    rule_id=self.rule_id,
                                    rule_name=self.name,
                                    category=self.category,
                                    severity=ComplianceSeverity.warning,
                                    status=ComplianceFindingStatus.unknown,
                                    message=f"要求「{req.title}」企业年限无法解析。",
                                    finding_suffix=f"years_co_unparseable:{req.id}",
                                    requirement_id=req.id,
                                )
                            )
                        elif co_y + 1e-9 < req_y:
                            findings.append(
                                make_finding(
                                    rule_id=self.rule_id,
                                    rule_name=self.name,
                                    category=self.category,
                                    severity=ComplianceSeverity.error,
                                    status=ComplianceFindingStatus.fail,
                                    message=(
                                        f"要求「{req.title}」年限不达标："
                                        f"企业 {co_y}年 < 要求 {req_y}年。"
                                    ),
                                    finding_suffix=f"years_fail:{req.id}",
                                    requirement_id=req.id,
                                    match_id=matches[0].id if matches else None,
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
                                    message=f"要求「{req.title}」年限阈值比对通过。",
                                    finding_suffix=f"years_ok:{req.id}",
                                    requirement_id=req.id,
                                )
                            )

            # --- quantity ---
            req_qty_raw = next(
                (req_found[k] for k in STRUCTURED_QUANTITY_KEYS if k in req_found), None
            )
            if req_qty_raw is not None:
                req_q = parse_quantity(req_qty_raw)
                co_qty_raw = next(
                    (company_found[k] for k in STRUCTURED_QUANTITY_KEYS if k in company_found),
                    None,
                )
                if req_q is None:
                    findings.append(
                        make_finding(
                            rule_id=self.rule_id,
                            rule_name=self.name,
                            category=self.category,
                            severity=ComplianceSeverity.warning,
                            status=ComplianceFindingStatus.unknown,
                            message=f"要求「{req.title}」数量阈值无法解析。",
                            finding_suffix=f"qty_unparseable:{req.id}",
                            requirement_id=req.id,
                        )
                    )
                elif co_qty_raw is None:
                    findings.append(
                        make_finding(
                            rule_id=self.rule_id,
                            rule_name=self.name,
                            category=self.category,
                            severity=ComplianceSeverity.warning,
                            status=ComplianceFindingStatus.unknown,
                            message=f"要求「{req.title}」声明数量阈值，但企业侧无对应字段。",
                            finding_suffix=f"qty_missing:{req.id}",
                            requirement_id=req.id,
                        )
                    )
                else:
                    co_q = parse_quantity(co_qty_raw)
                    if co_q is None:
                        findings.append(
                            make_finding(
                                rule_id=self.rule_id,
                                rule_name=self.name,
                                category=self.category,
                                severity=ComplianceSeverity.warning,
                                status=ComplianceFindingStatus.unknown,
                                message=f"要求「{req.title}」企业数量无法解析。",
                                finding_suffix=f"qty_co_unparseable:{req.id}",
                                requirement_id=req.id,
                            )
                        )
                    elif co_q + 1e-9 < req_q:
                        findings.append(
                            make_finding(
                                rule_id=self.rule_id,
                                rule_name=self.name,
                                category=self.category,
                                severity=ComplianceSeverity.error,
                                status=ComplianceFindingStatus.fail,
                                message=(
                                    f"要求「{req.title}」数量不达标："
                                    f"企业 {co_q} < 要求 {req_q}。"
                                ),
                                finding_suffix=f"qty_fail:{req.id}",
                                requirement_id=req.id,
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
                                message=f"要求「{req.title}」数量阈值比对通过。",
                                finding_suffix=f"qty_ok:{req.id}",
                                requirement_id=req.id,
                            )
                        )

            # --- level ---
            req_level_raw = next(
                (req_found[k] for k in STRUCTURED_LEVEL_KEYS if k in req_found), None
            )
            if req_level_raw is not None:
                req_lv = parse_level(req_level_raw)
                co_level_raw = next(
                    (company_found[k] for k in STRUCTURED_LEVEL_KEYS if k in company_found),
                    None,
                )
                if not req_lv:
                    findings.append(
                        make_finding(
                            rule_id=self.rule_id,
                            rule_name=self.name,
                            category=self.category,
                            severity=ComplianceSeverity.warning,
                            status=ComplianceFindingStatus.unknown,
                            message=f"要求「{req.title}」等级字段无法解析。",
                            finding_suffix=f"level_unparseable:{req.id}",
                            requirement_id=req.id,
                        )
                    )
                elif co_level_raw is None:
                    findings.append(
                        make_finding(
                            rule_id=self.rule_id,
                            rule_name=self.name,
                            category=self.category,
                            severity=ComplianceSeverity.warning,
                            status=ComplianceFindingStatus.unknown,
                            message=f"要求「{req.title}」声明等级，但企业侧无对应字段。",
                            finding_suffix=f"level_missing:{req.id}",
                            requirement_id=req.id,
                        )
                    )
                else:
                    co_lv = parse_level(co_level_raw)
                    if not co_lv:
                        findings.append(
                            make_finding(
                                rule_id=self.rule_id,
                                rule_name=self.name,
                                category=self.category,
                                severity=ComplianceSeverity.warning,
                                status=ComplianceFindingStatus.unknown,
                                message=f"要求「{req.title}」企业等级无法解析。",
                                finding_suffix=f"level_co_unparseable:{req.id}",
                                requirement_id=req.id,
                            )
                        )
                    elif co_lv != req_lv and req_lv not in co_lv and co_lv not in req_lv:
                        findings.append(
                            make_finding(
                                rule_id=self.rule_id,
                                rule_name=self.name,
                                category=self.category,
                                severity=ComplianceSeverity.error,
                                status=ComplianceFindingStatus.fail,
                                message=(
                                    f"要求「{req.title}」等级不一致："
                                    f"企业「{co_lv}」vs 要求「{req_lv}」。"
                                ),
                                finding_suffix=f"level_fail:{req.id}",
                                requirement_id=req.id,
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
                                message=f"要求「{req.title}」等级字段一致。",
                                finding_suffix=f"level_ok:{req.id}",
                                requirement_id=req.id,
                            )
                        )

            # --- expiry ---
            req_exp_raw = next(
                (req_found[k] for k in STRUCTURED_EXPIRY_KEYS if k in req_found), None
            )
            co_exp_raw = next(
                (company_found[k] for k in STRUCTURED_EXPIRY_KEYS if k in company_found),
                None,
            )
            if req_exp_raw is not None or co_exp_raw is not None:
                req_d = parse_date(req_exp_raw) if req_exp_raw is not None else None
                co_d = parse_date(co_exp_raw) if co_exp_raw is not None else None
                if req_exp_raw is not None and req_d is None:
                    findings.append(
                        make_finding(
                            rule_id=self.rule_id,
                            rule_name=self.name,
                            category=self.category,
                            severity=ComplianceSeverity.warning,
                            status=ComplianceFindingStatus.unknown,
                            message=f"要求「{req.title}」有效期无法解析，跳过比对。",
                            finding_suffix=f"expiry_unparseable:{req.id}",
                            requirement_id=req.id,
                            metadata_json={"raw": req_exp_raw},
                        )
                    )
                elif co_exp_raw is not None and co_d is None:
                    findings.append(
                        make_finding(
                            rule_id=self.rule_id,
                            rule_name=self.name,
                            category=self.category,
                            severity=ComplianceSeverity.warning,
                            status=ComplianceFindingStatus.unknown,
                            message=f"要求「{req.title}」企业证书有效期无法解析。",
                            finding_suffix=f"expiry_co_unparseable:{req.id}",
                            requirement_id=req.id,
                        )
                    )
                elif co_d is not None and co_d < today:
                    findings.append(
                        make_finding(
                            rule_id=self.rule_id,
                            rule_name=self.name,
                            category=self.category,
                            severity=ComplianceSeverity.critical,
                            status=ComplianceFindingStatus.fail,
                            message=(
                                f"要求「{req.title}」关联证书已过期"
                                f"（valid_until={co_d.isoformat()}）。"
                            ),
                            finding_suffix=f"expiry_expired:{req.id}",
                            requirement_id=req.id,
                            match_id=matches[0].id if matches else None,
                        )
                    )
                elif req_d is not None and co_d is None:
                    findings.append(
                        make_finding(
                            rule_id=self.rule_id,
                            rule_name=self.name,
                            category=self.category,
                            severity=ComplianceSeverity.warning,
                            status=ComplianceFindingStatus.unknown,
                            message=f"要求「{req.title}」声明有效期要求，但企业侧无有效期字段。",
                            finding_suffix=f"expiry_missing:{req.id}",
                            requirement_id=req.id,
                        )
                    )
                elif req_d is not None and co_d is not None and co_d < req_d:
                    findings.append(
                        make_finding(
                            rule_id=self.rule_id,
                            rule_name=self.name,
                            category=self.category,
                            severity=ComplianceSeverity.error,
                            status=ComplianceFindingStatus.fail,
                            message=(
                                f"要求「{req.title}」企业有效期早于要求："
                                f"{co_d.isoformat()} < {req_d.isoformat()}。"
                            ),
                            finding_suffix=f"expiry_fail:{req.id}",
                            requirement_id=req.id,
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
                            message=f"要求「{req.title}」有效期检查通过。",
                            finding_suffix=f"expiry_ok:{req.id}",
                            requirement_id=req.id,
                        )
                    )

            # If only unstructured presence keys with empty values
            empty_keys = [k for k, v in req_found.items() if v in (None, "", [], {})]
            if empty_keys and not any(
                f.requirement_id == req.id and f.rule_id == self.rule_id for f in findings
            ):
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
                        metadata_json={"fields": req_found},
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
                        "未发现 expiry/金额/年限等结构化字段；"
                        "不编造阈值，跳过数值比对。"
                    ),
                    finding_suffix="no_structured_fields",
                    remediation=(
                        "若业务需要，在 requirement.metadata_json / "
                        "match.metadata_json 中写入真实字段。"
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
