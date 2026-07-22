"""Unit/integration tests for the deterministic compliance engine."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from app.models import BidProject, Document, DocumentChunk, Organization, Requirement
from app.models.enums import (
    ComplianceFindingStatus,
    ComplianceRuleCategory,
    DocumentType,
    EvidenceMatchStatus,
    MatchReviewStatus,
    ParseStatus,
    ProposalDraftStatus,
    ProposalDraftVersionKind,
    QualityLevel,
    RequirementCategory,
    ReviewStatus,
    RiskLevel,
)
from app.models.match_run import RequirementEvidenceMatch, RequirementEvidenceMatchLink
from app.models.proposal_draft import ProposalDraft, ProposalDraftVersion
from app.models.requirement import EvidenceLink
from app.schemas.compliance import ComplianceContext
from app.schemas.proposal_draft import UNEVIDENCED_MARKER
from app.services.compliance.adapter_reference import (
    adapt_compliance_reference_sample,
    evaluate_adapted_sample,
)
from app.services.compliance.engine import ComplianceEngine
from app.services.compliance.registry import get_default_registry
from sqlalchemy.orm import Session


def _org_project(db: Session, code: str = "CMP-001") -> BidProject:
    org = Organization(name=f"Org-{code}-{uuid4().hex[:6]}")
    db.add(org)
    db.flush()
    project = BidProject(
        organization_id=org.id,
        project_code=code,
        project_name=f"Project {code}",
    )
    db.add(project)
    db.flush()
    return project


def _req(
    db: Session,
    project: BidProject,
    *,
    title: str,
    category: RequirementCategory = RequirementCategory.technical,
    mandatory: bool = False,
) -> Requirement:
    req = Requirement(
        project_id=project.id,
        category=category,
        title=title,
        mandatory=mandatory,
        risk_level=RiskLevel.medium,
        quality_level=QualityLevel.pending,
        review_status=ReviewStatus.unreviewed,
    )
    db.add(req)
    db.flush()
    return req


def _doc(
    db: Session,
    project: BidProject,
    *,
    document_type: DocumentType,
    file_name: str,
) -> Document:
    doc = Document(
        project_id=project.id,
        organization_id=project.organization_id,
        document_type=document_type,
        file_name=file_name,
        storage_bucket="bidpilot-documents",
        storage_key=f"{project.id}/{file_name}",
        parse_status=ParseStatus.success,
    )
    db.add(doc)
    db.flush()
    return doc


def _chunk(db: Session, project: BidProject, doc: Document, content: str) -> DocumentChunk:
    chunk = DocumentChunk(
        document_id=doc.id,
        project_id=project.id,
        chunk_index=0,
        content=content,
        page_start=1,
        section="§1",
    )
    db.add(chunk)
    db.flush()
    return chunk


def _ctx_from_db(db: Session, project: BidProject, **kwargs) -> ComplianceContext:
    from app.services.compliance.context import load_compliance_context

    return load_compliance_context(db, project.id, **kwargs)


def test_registry_lists_all_categories():
    rules = get_default_registry().list_rules()
    cats = {r.category for r in rules}
    assert ComplianceRuleCategory.coverage in cats
    assert ComplianceRuleCategory.evidence in cats
    assert ComplianceRuleCategory.qualification_risk in cats
    assert ComplianceRuleCategory.draft_safety in cats
    assert ComplianceRuleCategory.consistency in cats
    ids = {r.rule_id for r in rules}
    for rid in (
        "A004_uncovered_match_status",
        "A005_high_priority_uncovered",
        "A006_draft_missing_mandatory",
        "B004_dangling_evidence",
        "B005_conflicting_evidence_citation",
        "C004_definitive_negative",
        "C005_structured_thresholds",
        "D004_placeholders",
        "D005_empty_or_short",
        "D006_strong_claim_without_support",
        "D007_cross_project_source",
        "E004_exclusive_match_statuses",
        "E005_project_ownership",
        "E006_gap_match_definitive_draft",
    ):
        assert rid in ids
    assert len(rules) >= 29


def test_coverage_pass_fail_insufficient(db: Session):
    project = _org_project(db)
    # insufficient
    findings, _ = ComplianceEngine().run(
        _ctx_from_db(db, project),
        categories=[ComplianceRuleCategory.coverage],
    )
    assert any(f.status == ComplianceFindingStatus.unknown for f in findings)

    req = _req(db, project, title="必须具备ISO认证", mandatory=True)
    db.commit()
    findings, _ = ComplianceEngine().run(
        _ctx_from_db(db, project),
        rule_ids=["A001_mandatory_coverage"],
    )
    assert any(
        f.status == ComplianceFindingStatus.fail and f.requirement_id == req.id for f in findings
    )

    match = RequirementEvidenceMatch(
        project_id=project.id,
        requirement_id=req.id,
        status=EvidenceMatchStatus.supported,
        risk_level=RiskLevel.low,
        review_status=MatchReviewStatus.pending,
        lifecycle_status="active",
        primary_company_quote="本公司已通过ISO9001认证。",
    )
    db.add(match)
    db.flush()
    link = EvidenceLink(
        requirement_id=req.id,
        evidence_type="tender_clause",
        notes="src",
    )
    db.add(link)
    db.commit()

    findings, _ = ComplianceEngine().run(
        _ctx_from_db(db, project),
        rule_ids=["A001_mandatory_coverage"],
    )
    assert any(
        f.status == ComplianceFindingStatus.pass_ and f.requirement_id == req.id for f in findings
    )


def test_evidence_quote_grounding(db: Session):
    project = _org_project(db)
    req = _req(db, project, title="业绩要求")
    company = _doc(db, project, document_type=DocumentType.qualification, file_name="q.pdf")
    chunk = _chunk(db, project, company, "本公司具备同类项目三年实施经验。")
    match = RequirementEvidenceMatch(
        project_id=project.id,
        requirement_id=req.id,
        status=EvidenceMatchStatus.supported,
        risk_level=RiskLevel.medium,
        review_status=MatchReviewStatus.pending,
        lifecycle_status="active",
        primary_company_quote="本公司具备同类项目三年实施经验。",
        primary_company_document_id=company.id,
        primary_company_chunk_id=chunk.id,
    )
    db.add(match)
    db.flush()
    ok_link = RequirementEvidenceMatchLink(
        match_id=match.id,
        document_id=company.id,
        chunk_id=chunk.id,
        quote="本公司具备同类项目三年实施经验。",
    )
    db.add(ok_link)
    db.commit()

    findings, _ = ComplianceEngine().run(
        _ctx_from_db(db, project),
        rule_ids=["B001_quote_grounding"],
    )
    assert any(f.status == ComplianceFindingStatus.pass_ for f in findings)

    bad = RequirementEvidenceMatchLink(
        match_id=match.id,
        document_id=company.id,
        chunk_id=chunk.id,
        quote="这段文字完全不在原文里会出现接地失败",
    )
    db.add(bad)
    db.commit()
    findings, _ = ComplianceEngine().run(
        _ctx_from_db(db, project),
        rule_ids=["B001_quote_grounding"],
    )
    assert any(f.status == ComplianceFindingStatus.fail for f in findings)


def test_evidence_blocks_tender_doc_as_company(db: Session):
    project = _org_project(db)
    req = _req(db, project, title="资质")
    tender = _doc(db, project, document_type=DocumentType.tender, file_name="t.pdf")
    chunk = _chunk(db, project, tender, "招标文件原文片段。")
    match = RequirementEvidenceMatch(
        project_id=project.id,
        requirement_id=req.id,
        status=EvidenceMatchStatus.supported,
        risk_level=RiskLevel.high,
        review_status=MatchReviewStatus.pending,
        lifecycle_status="active",
        primary_company_quote="招标文件原文片段。",
    )
    db.add(match)
    db.flush()
    db.add(
        RequirementEvidenceMatchLink(
            match_id=match.id,
            document_id=tender.id,
            chunk_id=chunk.id,
            quote="招标文件原文片段。",
        )
    )
    db.commit()
    findings, _ = ComplianceEngine().run(
        _ctx_from_db(db, project),
        rule_ids=["B002_company_doc_scope"],
    )
    assert any(f.status == ComplianceFindingStatus.fail for f in findings)


def test_qualification_and_invalid_bid(db: Session):
    project = _org_project(db)
    qual = _req(
        db,
        project,
        title="具备安全生产许可证",
        category=RequirementCategory.qualification,
    )
    invalid = _req(
        db,
        project,
        title="串通投标作无效投标",
        category=RequirementCategory.invalid_bid,
    )
    match = RequirementEvidenceMatch(
        project_id=project.id,
        requirement_id=qual.id,
        status=EvidenceMatchStatus.insufficient_evidence,
        risk_level=RiskLevel.high,
        review_status=MatchReviewStatus.pending,
        lifecycle_status="active",
    )
    db.add(match)
    db.commit()

    findings, _ = ComplianceEngine().run(
        _ctx_from_db(db, project),
        categories=[ComplianceRuleCategory.qualification_risk],
    )
    assert any(
        f.rule_id == "C001_qualification_insufficient" and f.status == ComplianceFindingStatus.fail
        for f in findings
    )
    assert any(
        f.rule_id == "C003_invalid_bid_attention"
        and f.requirement_id == invalid.id
        and f.status == ComplianceFindingStatus.fail
        for f in findings
    )


def test_draft_safety_forbidden_and_unevidenced(db: Session):
    project = _org_project(db)
    draft = ProposalDraft(
        project_id=project.id,
        title="响应草稿",
        status=ProposalDraftStatus.draft_pending_review,
    )
    db.add(draft)
    db.flush()
    version = ProposalDraftVersion(
        project_id=project.id,
        draft_id=draft.id,
        version_number=1,
        version_kind=ProposalDraftVersionKind.manual_revision,
        content_json={
            "sections": [
                {
                    "title": "s1",
                    "blocks": [
                        {
                            "block_kind": "manual_unreferenced",
                            "content": f"{UNEVIDENCED_MARKER} 我们保证中标",
                            "citation_ids": [],
                        }
                    ],
                }
            ],
            "has_unevidenced_manual_content": True,
        },
        content_markdown=f"{UNEVIDENCED_MARKER} 保证中标",
        is_current=True,
    )
    db.add(version)
    db.flush()
    draft.current_version_id = version.id
    db.commit()

    findings, _ = ComplianceEngine().run(
        _ctx_from_db(db, project, draft_id=draft.id),
        categories=[ComplianceRuleCategory.draft_safety],
    )
    assert any(
        f.rule_id == "D001_unevidenced_manual" and f.status.value == "fail" for f in findings
    )
    assert any(f.rule_id == "D002_forbidden_claims" and f.status.value == "fail" for f in findings)


def test_consistency_deadline(db: Session):
    project = _org_project(db)
    _req(
        db,
        project,
        title="投标截止时间",
        category=RequirementCategory.deadline,
    )
    db.commit()
    findings, _ = ComplianceEngine().run(
        _ctx_from_db(db, project),
        rule_ids=["E003_date_conflicts"],
    )
    assert any(f.status == ComplianceFindingStatus.fail for f in findings)

    project.bid_deadline = datetime(2026, 8, 1, tzinfo=UTC)
    db.commit()
    findings, _ = ComplianceEngine().run(
        _ctx_from_db(db, project),
        rule_ids=["E003_date_conflicts"],
    )
    assert any(f.status == ComplianceFindingStatus.pass_ for f in findings)


def test_offline_adapter_formal_engine_parity():
    sample = {
        "sample_id": "s1",
        "project_id": str(uuid4()),
        "document_id": str(uuid4()),
        "input": {
            "rule_type": "mandatory",
            "check_id": "mandatory_clause",
            "text": "本声明函必须提供且内容不得擅自删改，否则视为无效投标。",
            "instruction": "检查",
        },
        "reference_output": {"verdict": "fail", "rule_type": "mandatory"},
        "evidence": [
            {
                "chunk_id": str(uuid4()),
                "document_id": str(uuid4()),
                "page_number": 1,
                "quote": "本声明函必须提供且内容不得擅自删改，否则视为无效投标。",
            }
        ],
        "citation_metadata": {},
        "split": "test",
    }
    from app.services.compliance.adapter_reference import (
        evaluate_sample_online_parity,
    )

    adapted = adapt_compliance_reference_sample(sample)
    evaluated = evaluate_adapted_sample(adapted, sample=sample)
    assert evaluated["ok"] is True
    assert evaluated["engine_verdict"] in {"pass", "fail", "attention_required"}
    assert evaluated["rule_ids_executed"]
    assert evaluated["rules_executed"] == evaluated["rule_ids_executed"]
    assert evaluated["focus_rules_evaluated"] == evaluated["focus_rule_ids"]
    assert all("severity" in f and "category" in f for f in evaluated["findings"])

    a, b = evaluate_sample_online_parity(sample)
    assert [f.finding_id for f in a] == [f.finding_id for f in b]
    assert [f.rule_id for f in a] == [f.rule_id for f in b]


def test_offline_eval_fixture_honest_coverage(tmp_path):
    """Minimal versioned fixture must distinguish focus vs not_directly_evaluated."""
    from app.services.compliance.offline_eval import (
        DEFAULT_FIXTURE_REFERENCE,
        run_offline_eval,
    )
    from app.services.compliance.registry import get_default_registry

    assert DEFAULT_FIXTURE_REFERENCE.exists(), DEFAULT_FIXTURE_REFERENCE
    out = tmp_path / "offline_eval.json"
    report = run_offline_eval(DEFAULT_FIXTURE_REFERENCE, out)
    assert report["succeeded"] == report["sample_count"]
    assert report["failed"] == 0
    assert "rules_executed" in report
    assert "focus_rules_evaluated" in report
    assert "rules_without_direct_reference_coverage" in report
    assert "coverage_matrix" in report
    assert report["rules_executed_count"] == len(report["rules_executed"])
    all_ids = set(get_default_registry().all_rule_ids())
    focus = set(report["focus_rules_evaluated"])
    without = set(report["rules_without_direct_reference_coverage"])
    assert focus
    assert without == all_ids - focus
    required_fields = {
        "rule_id",
        "category",
        "description",
        "executed_sample_count",
        "focus_sample_count",
        "positive_count",
        "negative_count",
        "insufficient_evidence_count",
        "agreement_count",
        "disagreement_count",
        "agreement",
        "coverage_status",
    }
    for rid, row in report["coverage_matrix"].items():
        assert required_fields <= set(row.keys()), rid
        assert row["coverage_status"] in {
            "directly_evaluated",
            "partially_evaluated",
            "executed_without_direct_reference",
            "not_executed",
        }
        if rid not in focus:
            assert row["coverage_status"] in {
                "executed_without_direct_reference",
                "not_executed",
                "partially_evaluated",
            }
            # Must not claim a 100% rate for non-focus rules
            if row["focus_sample_count"] == 0:
                assert row["agreement"] is None
                assert row.get("rate") is None
        else:
            assert row["focus_sample_count"] >= 1
    assert report["summary_headline"]["rules_executed"] == 29 or report["summary_headline"][
        "rules_executed"
    ] == len(report["rules_executed"])
    # Reproducible on same fixture inputs (ignore ephemeral finding UUIDs)
    out2 = tmp_path / "offline_eval2.json"
    report2 = run_offline_eval(DEFAULT_FIXTURE_REFERENCE, out2)
    assert report["focus_rules_evaluated"] == report2["focus_rules_evaluated"]
    assert report["verdict_match_rate"] == report2["verdict_match_rate"]
    assert (
        report["rules_without_direct_reference_coverage"]
        == report2["rules_without_direct_reference_coverage"]
    )
    assert report["sample_count"] == report2["sample_count"]
    assert [r.get("verdict_match") for r in report["results"]] == [
        r.get("verdict_match") for r in report2["results"]
    ]
    assert report["coverage_matrix"].keys() == report2["coverage_matrix"].keys()
    for rid in report["coverage_matrix"]:
        a = report["coverage_matrix"][rid]
        b = report2["coverage_matrix"][rid]
        assert a["coverage_status"] == b["coverage_status"]
        assert a["agreement"] == b["agreement"]
        assert a["focus_sample_count"] == b["focus_sample_count"]
    # Fixed inputs → byte-identical report (stable UUIDs + sort_keys).
    assert out.read_text(encoding="utf-8") == out2.read_text(encoding="utf-8")


def test_coverage_a004_a005(db: Session):
    project = _org_project(db, "CMP-A45")
    high = _req(
        db,
        project,
        title="高风险技术指标",
        category=RequirementCategory.technical,
    )
    high.risk_level = RiskLevel.critical
    db.flush()
    match = RequirementEvidenceMatch(
        project_id=project.id,
        requirement_id=high.id,
        status=EvidenceMatchStatus.insufficient_evidence,
        risk_level=RiskLevel.critical,
        review_status=MatchReviewStatus.pending,
        lifecycle_status="active",
    )
    db.add(match)
    db.commit()

    findings, _ = ComplianceEngine().run(
        _ctx_from_db(db, project),
        rule_ids=["A004_uncovered_match_status", "A005_high_priority_uncovered"],
    )
    assert any(
        f.rule_id == "A004_uncovered_match_status" and f.status == ComplianceFindingStatus.fail
        for f in findings
    )
    assert any(
        f.rule_id == "A005_high_priority_uncovered" and f.status == ComplianceFindingStatus.fail
        for f in findings
    )

    # insufficient: no high-risk requirements → pass for A005
    project2 = _org_project(db, "CMP-A45b")
    findings2, _ = ComplianceEngine().run(
        _ctx_from_db(db, project2),
        rule_ids=["A005_high_priority_uncovered"],
    )
    assert any(f.status == ComplianceFindingStatus.pass_ for f in findings2)


def test_evidence_b004_dangling(db: Session):
    project = _org_project(db, "CMP-B4")
    req = _req(db, project, title="业绩")
    company = _doc(db, project, document_type=DocumentType.case, file_name="case.pdf")
    # Invalid page range triggers B004 without violating FKs
    chunk = DocumentChunk(
        document_id=company.id,
        project_id=project.id,
        chunk_index=0,
        content="本公司有同类项目业绩。",
        page_start=9,
        page_end=3,
    )
    db.add(chunk)
    db.flush()
    match = RequirementEvidenceMatch(
        project_id=project.id,
        requirement_id=req.id,
        status=EvidenceMatchStatus.supported,
        risk_level=RiskLevel.low,
        review_status=MatchReviewStatus.pending,
        lifecycle_status="active",
        primary_company_quote="本公司有同类项目业绩。",
    )
    db.add(match)
    db.flush()
    db.add(
        RequirementEvidenceMatchLink(
            match_id=match.id,
            document_id=company.id,
            chunk_id=chunk.id,
            quote="本公司有同类项目业绩。",
        )
    )
    db.commit()
    findings, _ = ComplianceEngine().run(
        _ctx_from_db(db, project),
        rule_ids=["B004_dangling_evidence"],
    )
    assert any(f.status == ComplianceFindingStatus.fail for f in findings)

    # Pass path: valid range
    chunk.page_end = 12
    db.commit()
    findings_ok, _ = ComplianceEngine().run(
        _ctx_from_db(db, project),
        rule_ids=["B004_dangling_evidence"],
    )
    assert any(f.status == ComplianceFindingStatus.pass_ for f in findings_ok)


def test_qualification_c004_c005(db: Session):
    project = _org_project(db, "CMP-C45")
    qual = _req(
        db,
        project,
        title="资质证书",
        category=RequirementCategory.qualification,
        mandatory=True,
    )
    match = RequirementEvidenceMatch(
        project_id=project.id,
        requirement_id=qual.id,
        status=EvidenceMatchStatus.conflicting_evidence,
        risk_level=RiskLevel.high,
        review_status=MatchReviewStatus.pending,
        lifecycle_status="active",
    )
    db.add(match)
    db.commit()
    findings, _ = ComplianceEngine().run(
        _ctx_from_db(db, project),
        rule_ids=["C004_definitive_negative"],
    )
    assert any(
        f.rule_id == "C004_definitive_negative" and f.status == ComplianceFindingStatus.fail
        for f in findings
    )

    findings_u, _ = ComplianceEngine().run(
        _ctx_from_db(db, project),
        rule_ids=["C005_structured_thresholds"],
    )
    assert any(f.status == ComplianceFindingStatus.unknown for f in findings_u)

    qual.metadata_json = {"expiry": "2027-01-01", "min_amount": "10万元"}
    db.commit()
    # Without company-side values → warning/unknown (missing), not invent pass
    findings_missing, _ = ComplianceEngine().run(
        _ctx_from_db(db, project),
        rule_ids=["C005_structured_thresholds"],
    )
    assert any(
        f.status == ComplianceFindingStatus.unknown and "missing" in f.finding_id
        for f in findings_missing
    )

    match.metadata_json = {"min_amount": "5万元", "expiry": "2025-01-01"}
    db.commit()
    findings_fail, _ = ComplianceEngine().run(
        _ctx_from_db(db, project),
        rule_ids=["C005_structured_thresholds"],
    )
    assert any(
        f.status == ComplianceFindingStatus.fail and f.severity.value in {"error", "critical"}
        for f in findings_fail
    )

    match.metadata_json = {"min_amount": "20万元", "expiry": "2028-06-01"}
    db.commit()
    findings_ok, _ = ComplianceEngine().run(
        _ctx_from_db(db, project),
        rule_ids=["C005_structured_thresholds"],
    )
    assert any(f.status == ComplianceFindingStatus.pass_ for f in findings_ok)


def test_draft_d004_d005_and_consistency_e004(db: Session):
    project = _org_project(db, "CMP-DE")
    req = _req(db, project, title="同一要求", mandatory=True)
    db.add(
        RequirementEvidenceMatch(
            project_id=project.id,
            requirement_id=req.id,
            status=EvidenceMatchStatus.supported,
            risk_level=RiskLevel.medium,
            review_status=MatchReviewStatus.pending,
            lifecycle_status="active",
            primary_company_quote="ok",
        )
    )
    db.add(
        RequirementEvidenceMatch(
            project_id=project.id,
            requirement_id=req.id,
            status=EvidenceMatchStatus.insufficient_evidence,
            risk_level=RiskLevel.medium,
            review_status=MatchReviewStatus.pending,
            lifecycle_status="active",
        )
    )
    draft = ProposalDraft(
        project_id=project.id,
        title="短草稿",
        status=ProposalDraftStatus.draft_pending_review,
    )
    db.add(draft)
    db.flush()
    version = ProposalDraftVersion(
        project_id=project.id,
        draft_id=draft.id,
        version_number=1,
        version_kind=ProposalDraftVersionKind.manual_revision,
        content_json={
            "sections": [
                {
                    "title": "s",
                    "blocks": [
                        {
                            "block_kind": "partial_response",
                            "content": "TODO 待补充",
                            "requirement_ids": [str(req.id)],
                            "citation_ids": [],
                        }
                    ],
                }
            ]
        },
        content_markdown="TODO",
        is_current=True,
    )
    db.add(version)
    db.flush()
    draft.current_version_id = version.id
    db.commit()

    findings, _ = ComplianceEngine().run(
        _ctx_from_db(db, project, draft_id=draft.id),
        rule_ids=[
            "D004_placeholders",
            "D005_empty_or_short",
            "E004_exclusive_match_statuses",
        ],
    )
    assert any(
        f.rule_id == "D004_placeholders" and f.status == ComplianceFindingStatus.fail
        for f in findings
    )
    assert any(
        f.rule_id == "D005_empty_or_short" and f.status == ComplianceFindingStatus.fail
        for f in findings
    )
    assert any(
        f.rule_id == "E004_exclusive_match_statuses" and f.status == ComplianceFindingStatus.fail
        for f in findings
    )


def test_d003_d006_d007_e005_e006(db: Session):
    project = _org_project(db, "CMP-DX")
    other = _org_project(db, "CMP-OT")
    req = _req(db, project, title="强制资质", mandatory=True)
    gap = RequirementEvidenceMatch(
        project_id=project.id,
        requirement_id=req.id,
        status=EvidenceMatchStatus.insufficient_evidence,
        risk_level=RiskLevel.medium,
        review_status=MatchReviewStatus.pending,
        lifecycle_status="active",
    )
    db.add(gap)
    db.flush()
    # Superseded match exists for FK but is not loaded as active → D003 match_not_active
    old = RequirementEvidenceMatch(
        project_id=project.id,
        requirement_id=req.id,
        status=EvidenceMatchStatus.supported,
        risk_level=RiskLevel.low,
        review_status=MatchReviewStatus.pending,
        lifecycle_status="superseded",
    )
    db.add(old)
    db.flush()

    draft = ProposalDraft(
        project_id=project.id,
        title="跨项草稿",
        status=ProposalDraftStatus.draft_pending_review,
    )
    db.add(draft)
    db.flush()
    version = ProposalDraftVersion(
        project_id=project.id,
        draft_id=draft.id,
        version_number=1,
        version_kind=ProposalDraftVersionKind.manual_revision,
        content_json={
            "sections": [
                {
                    "title": "响应",
                    "blocks": [
                        {
                            "block_kind": "partial_response",
                            "content": "本公司完全满足该要求，已具备全部资质。",
                            "requirement_ids": [str(req.id)],
                            "citation_ids": [],
                        }
                    ],
                }
            ]
        },
        content_markdown="本公司完全满足该要求，已具备全部资质。",
        is_current=True,
    )
    db.add(version)
    db.flush()
    draft.current_version_id = version.id

    from app.models.enums import ProposalDraftSourceRole
    from app.models.proposal_draft import ProposalDraftSource

    src_bad = ProposalDraftSource(
        project_id=project.id,
        draft_version_id=version.id,
        requirement_id=req.id,
        match_id=old.id,
        source_role=ProposalDraftSourceRole.company_support,
        source_quote="x",
        location_json={},
    )
    db.add(src_bad)

    foreign_doc = _doc(db, other, document_type=DocumentType.qualification, file_name="other.pdf")
    src_cross = ProposalDraftSource(
        project_id=other.id,
        draft_version_id=version.id,
        requirement_id=req.id,
        match_id=gap.id,
        source_role=ProposalDraftSourceRole.company_support,
        source_quote="外项目材料",
        location_json={"document_id": str(foreign_doc.id)},
    )
    db.add(src_cross)
    db.commit()

    ctx = _ctx_from_db(db, project, draft_id=draft.id)
    findings, _ = ComplianceEngine().run(
        ctx,
        rule_ids=[
            "D003_citation_integrity",
            "D006_strong_claim_without_support",
            "D007_cross_project_source",
            "E005_project_ownership",
            "E006_gap_match_definitive_draft",
        ],
    )
    assert any(
        f.rule_id == "D003_citation_integrity" and f.status == ComplianceFindingStatus.fail
        for f in findings
    )
    assert any(
        f.rule_id == "D006_strong_claim_without_support"
        and f.status == ComplianceFindingStatus.fail
        for f in findings
    )
    assert any(
        f.rule_id == "D007_cross_project_source" and f.status == ComplianceFindingStatus.fail
        for f in findings
    )
    assert any(
        f.rule_id == "E006_gap_match_definitive_draft" and f.status == ComplianceFindingStatus.fail
        for f in findings
    )
    assert any(
        f.rule_id == "E005_project_ownership" and f.status == ComplianceFindingStatus.fail
        for f in findings
    )


def test_e003_date_conflict_and_onesided(db: Session):
    project = _org_project(db, "CMP-E3")
    req = _req(db, project, title="交付时间", category=RequirementCategory.deadline)
    req.metadata_json = {"delivery_date": "2026-09-01"}
    db.commit()
    findings, _ = ComplianceEngine().run(
        _ctx_from_db(db, project),
        rule_ids=["E003_date_conflicts"],
    )
    assert any(f.status == ComplianceFindingStatus.unknown for f in findings)

    match = RequirementEvidenceMatch(
        project_id=project.id,
        requirement_id=req.id,
        status=EvidenceMatchStatus.partially_supported,
        risk_level=RiskLevel.low,
        review_status=MatchReviewStatus.pending,
        lifecycle_status="active",
        metadata_json={"delivery_date": "2026-10-01"},
    )
    db.add(match)
    db.commit()
    findings2, _ = ComplianceEngine().run(
        _ctx_from_db(db, project),
        rule_ids=["E003_date_conflicts"],
    )
    assert any(f.status == ComplianceFindingStatus.fail for f in findings2)
