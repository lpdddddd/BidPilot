"""Tests for Step 8: requirement ↔ company evidence matching."""

from __future__ import annotations

import json
from uuid import uuid4

import pytest
from app.models import (
    BidProject,
    Document,
    DocumentChunk,
    EvidenceLink,
    Organization,
    Requirement,
)
from app.models.enums import (
    DocumentType,
    EvidenceMatchStatus,
    ExtractionRunStatus,
    ParseStatus,
    QualityLevel,
    RequirementCategory,
    ReviewStatus,
    RiskLevel,
)
from app.models.match_run import RequirementEvidenceMatch, RequirementEvidenceMatchLink
from app.schemas.match import MatchStartRequest
from app.services import requirement_match_tasks
from app.services.llm_client import ChatResult
from app.services.requirement_match_service import (
    RequirementMatchService,
    auto_match_key,
    grade_mismatch,
    risk_for_match,
)
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker


class FakeLlm:
    def __init__(self, responder=None, *, enabled: bool = True):
        self.enabled = enabled
        self.model = "fake-qwen"
        self.chat_calls: list = []
        self.raise_error: Exception | None = None
        self._responder = responder or (lambda messages: {"items": []})

    def chat(self, messages, **kwargs):
        self.chat_calls.append({"messages": messages, **kwargs})
        if self.raise_error:
            raise self.raise_error
        payload = self._responder(messages)
        content = (
            payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
        )
        return ChatResult(
            content=content,
            model=self.model,
            latency_ms=1.0,
            finish_reason="stop",
            request_id=kwargs.get("request_id") or "rid",
        )


def _org_project(db: Session, code: str = "MATCH-001") -> BidProject:
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
        is_scanned=False,
    )
    db.add(doc)
    db.flush()
    return doc


def _chunk(
    db: Session,
    project: BidProject,
    document: Document,
    *,
    index: int,
    content: str,
    section: str | None = "企业资质",
    clause_id: str | None = "Q.1",
    page_start: int | None = 2,
    page_end: int | None = 2,
) -> DocumentChunk:
    chunk = DocumentChunk(
        document_id=document.id,
        project_id=project.id,
        chunk_index=index,
        content=content,
        section=section,
        clause_id=clause_id,
        page_start=page_start,
        page_end=page_end,
    )
    db.add(chunk)
    db.flush()
    return chunk


def _requirement(
    db: Session,
    project: BidProject,
    *,
    title: str = "投标人资质要求",
    normalized: str = "投标人须具备特级资质。",
    category: RequirementCategory = RequirementCategory.qualification,
    mandatory: bool = True,
    risk_level: RiskLevel = RiskLevel.medium,
    source_document: Document | None = None,
    metadata: dict | None = None,
    review_status: ReviewStatus = ReviewStatus.unreviewed,
) -> Requirement:
    req = Requirement(
        project_id=project.id,
        source_document_id=source_document.id if source_document else None,
        requirement_code=f"REQ-{uuid4().hex[:8]}",
        category=category,
        title=title,
        normalized_requirement=normalized,
        mandatory=mandatory,
        risk_level=risk_level,
        quality_level=QualityLevel.pending,
        review_status=review_status,
        metadata_json=metadata or {"source": "auto_extraction"},
        source_page=10,
        source_section="第三章",
        source_clause_id="3.1",
    )
    db.add(req)
    db.flush()
    return req


def _match_payload(messages) -> dict:
    user = messages[-1]["content"]
    raw = user.split("<<<MATCH_INPUT>>>\n", 1)[-1]
    return json.loads(raw)


def _supported_item(req: Requirement, chunk: DocumentChunk, **overrides) -> dict:
    quote = (chunk.content or "")[:40]
    base = {
        "requirement_id": str(req.id),
        "status": "supported",
        "summary": "材料中存在与该要求相关的可定位引文，需人工确认",
        "primary_company_chunk_id": str(chunk.id),
        "company_evidence_quote": quote,
        "additional_company_chunk_ids": [],
        "needs_review": True,
        "conflict_note": None,
    }
    base.update(overrides)
    return base


@pytest.fixture()
def task_factory(monkeypatch, engine):
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    monkeypatch.setattr(requirement_match_tasks, "SESSION_FACTORY", factory)
    return factory


def test_grade_mismatch_and_risk_rules():
    assert grade_mismatch("须具备特级资质", "本公司具备一级资质") is True
    assert grade_mismatch("须具备一级资质", "本公司具备一级资质") is False
    assert grade_mismatch("须提交营业执照", "营业执照复印件") is False

    class _Req:
        risk_level = RiskLevel.critical
        mandatory = True
        category = RequirementCategory.qualification

    assert risk_for_match(_Req(), EvidenceMatchStatus.supported) in (
        RiskLevel.high,
        RiskLevel.critical,
    )
    # Floor is critical → insufficient also stays at least critical.
    assert risk_for_match(_Req(), EvidenceMatchStatus.insufficient_evidence) in (
        RiskLevel.high,
        RiskLevel.critical,
    )


def test_only_company_doc_types_used(db):
    project = _org_project(db, "M2")
    tender = _doc(db, project, document_type=DocumentType.tender, file_name="t.pdf")
    company = _doc(db, project, document_type=DocumentType.qualification, file_name="q.pdf")
    _chunk(db, project, tender, index=0, content="招标文件不得作为企业证据。")
    c_chunk = _chunk(db, project, company, index=0, content="本公司具备一级资质证书。")
    req = _requirement(db, project, normalized="投标人须具备一级资质。", title="资质")
    db.commit()

    seen_types: list[str] = []

    def responder(messages):
        payload = _match_payload(messages)
        for c in payload["company_chunks"]:
            seen_types.append(c["document_type"])
        return {"items": [_supported_item(req, c_chunk, summary="材料含一级资质引文，需人工确认")]}

    llm = FakeLlm(responder)
    svc = RequirementMatchService(db, llm=llm)
    run = svc.start_matching(project.id, MatchStartRequest())
    svc.execute_run(run.id)
    assert seen_types
    assert all(t == DocumentType.qualification.value for t in seen_types)
    assert DocumentType.tender.value not in seen_types


def test_tender_types_rejected_as_company_evidence(db):
    """Even if LLM cites a tender chunk id, it must not be in the allowed set."""
    project = _org_project(db, "M3")
    tender = _doc(db, project, document_type=DocumentType.tender, file_name="t.pdf")
    company = _doc(db, project, document_type=DocumentType.company_profile, file_name="c.pdf")
    t_chunk = _chunk(db, project, tender, index=0, content="本公司具备一级资质。")
    c_chunk = _chunk(db, project, company, index=0, content="无关简介文字内容。")
    req = _requirement(db, project, normalized="投标人须具备一级资质。")
    db.commit()

    def responder(messages):
        # Try to use tender chunk — must be rejected (not in company batch).
        return {
            "items": [
                _supported_item(
                    req,
                    t_chunk,
                    primary_company_chunk_id=str(t_chunk.id),
                    company_evidence_quote="本公司具备一级资质",
                    summary="误用招标文件，需人工确认",
                )
            ]
        }

    llm = FakeLlm(responder)
    svc = RequirementMatchService(db, llm=llm)
    run = svc.start_matching(project.id, MatchStartRequest())
    svc.execute_run(run.id)
    matches = list(
        db.scalars(
            select(RequirementEvidenceMatch).where(
                RequirementEvidenceMatch.project_id == project.id
            )
        )
    )
    # Rejected LLM item → synthesized insufficient (non-force, raw>0 but if all
    # rejected with no llm_validated, no write). Here all rejected → no write.
    assert matches == []
    # Ensure tender chunk never appears as primary on any row
    assert all(m.primary_company_chunk_id != t_chunk.id for m in matches)
    _ = c_chunk  # company chunk present but unused by bad LLM response


def test_supported_creates_match_and_link(db):
    project = _org_project(db, "M5")
    company = _doc(db, project, document_type=DocumentType.qualification, file_name="q.pdf")
    chunk = _chunk(db, project, company, index=0, content="本公司具备一级建筑业企业资质。")
    req = _requirement(db, project, normalized="投标人须具备一级建筑业企业资质。")
    db.commit()

    llm = FakeLlm(
        lambda _m: {
            "items": [
                _supported_item(
                    req,
                    chunk,
                    company_evidence_quote="本公司具备一级建筑业企业资质",
                    summary="材料含一级建筑业企业资质引文，需人工确认",
                )
            ]
        }
    )
    svc = RequirementMatchService(db, llm=llm)
    run = svc.start_matching(project.id, MatchStartRequest())
    svc.execute_run(run.id)
    refreshed = svc.get_run(project.id, run.id)
    assert refreshed.status == ExtractionRunStatus.succeeded
    assert refreshed.matched_count == 1

    match = db.scalar(
        select(RequirementEvidenceMatch).where(
            RequirementEvidenceMatch.project_id == project.id
        )
    )
    assert match is not None
    assert match.status == EvidenceMatchStatus.supported
    assert match.needs_review is True
    assert match.primary_company_chunk_id == chunk.id
    assert match.metadata_json["source"] == "auto_match"
    assert match.metadata_json["match_key"] == auto_match_key(req.id)

    links = list(
        db.scalars(
            select(RequirementEvidenceMatchLink).where(
                RequirementEvidenceMatchLink.match_id == match.id
            )
        )
    )
    assert len(links) == 1
    assert links[0].role == "company_support"
    assert links[0].chunk_id == chunk.id


def test_insufficient_evidence_wording(db):
    project = _org_project(db, "M6")
    company = _doc(db, project, document_type=DocumentType.case, file_name="case.pdf")
    chunk = _chunk(db, project, company, index=0, content="近三年完成若干信息化项目。")
    req = _requirement(db, project, normalized="投标人须具备特级资质。")
    db.commit()

    llm = FakeLlm(
        lambda _m: {
            "items": [
                {
                    "requirement_id": str(req.id),
                    "status": "insufficient_evidence",
                    "summary": "当前材料未找到充分证据",
                    "primary_company_chunk_id": None,
                    "company_evidence_quote": None,
                    "needs_review": True,
                }
            ]
        }
    )
    svc = RequirementMatchService(db, llm=llm)
    run = svc.start_matching(project.id, MatchStartRequest())
    svc.execute_run(run.id)
    match = db.scalar(select(RequirementEvidenceMatch))
    assert match is not None
    assert match.status == EvidenceMatchStatus.insufficient_evidence
    assert "不符合" not in (match.summary or "")
    assert "未找到充分证据" in (match.summary or "")
    assert match.needs_review is True
    _ = chunk


def test_grade_mismatch_rejects_supported(db):
    project = _org_project(db, "M7")
    company = _doc(db, project, document_type=DocumentType.qualification, file_name="q.pdf")
    chunk = _chunk(db, project, company, index=0, content="本公司具备一级资质。")
    req = _requirement(db, project, normalized="投标人须具备特级资质。")
    db.commit()

    llm = FakeLlm(
        lambda _m: {
            "items": [
                _supported_item(
                    req,
                    chunk,
                    company_evidence_quote="本公司具备一级资质",
                    summary="材料显示具备一级资质，需人工确认",
                )
            ]
        }
    )
    svc = RequirementMatchService(db, llm=llm)
    run = svc.start_matching(project.id, MatchStartRequest())
    svc.execute_run(run.id)
    match = db.scalar(select(RequirementEvidenceMatch))
    assert match is not None
    assert match.status == EvidenceMatchStatus.partially_supported
    assert (match.metadata_json or {}).get("grade_downgrade") is True


def test_invented_critical_tokens_sanitized(db):
    project = _org_project(db, "M8")
    company = _doc(db, project, document_type=DocumentType.qualification, file_name="q.pdf")
    chunk = _chunk(db, project, company, index=0, content="本公司具备一级资质。")
    req = _requirement(db, project, normalized="投标人须具备一级资质。")
    db.commit()

    llm = FakeLlm(
        lambda _m: {
            "items": [
                _supported_item(
                    req,
                    chunk,
                    company_evidence_quote="本公司具备一级资质",
                    # Invents amount not in req or evidence
                    summary="材料支持该要求且注册资本5000万元，需人工确认",
                )
            ]
        }
    )
    svc = RequirementMatchService(db, llm=llm)
    run = svc.start_matching(project.id, MatchStartRequest())
    svc.execute_run(run.id)
    match = db.scalar(select(RequirementEvidenceMatch))
    assert match is not None
    assert "5000" not in (match.summary or "")
    assert (match.metadata_json or {}).get("summary_sanitized") is True


def test_unknown_chunk_and_bad_quote_rejected(db):
    project = _org_project(db, "M9")
    company = _doc(db, project, document_type=DocumentType.product, file_name="p.pdf")
    chunk = _chunk(db, project, company, index=0, content="产品通过质量检测。")
    req = _requirement(db, project, normalized="产品须通过质量检测。")
    db.commit()

    fake_id = uuid4()

    def responder(_m):
        return {
            "items": [
                {
                    "requirement_id": str(req.id),
                    "status": "supported",
                    "summary": "材料支持，需人工确认",
                    "primary_company_chunk_id": str(fake_id),
                    "company_evidence_quote": "产品通过质量检测",
                    "needs_review": True,
                }
            ]
        }

    llm = FakeLlm(responder)
    svc = RequirementMatchService(db, llm=llm)
    run = svc.start_matching(project.id, MatchStartRequest())
    svc.execute_run(run.id)
    assert list(db.scalars(select(RequirementEvidenceMatch))) == []

    # Bad quote
    llm2 = FakeLlm(
        lambda _m: {
            "items": [
                _supported_item(
                    req,
                    chunk,
                    company_evidence_quote="这段话根本不存在于原文中",
                    summary="材料支持，需人工确认",
                )
            ]
        }
    )
    svc2 = RequirementMatchService(db, llm=llm2)
    run2 = svc2.start_matching(project.id, MatchStartRequest())
    svc2.execute_run(run2.id)
    assert list(db.scalars(select(RequirementEvidenceMatch))) == []


def test_project_isolation(db):
    project_a = _org_project(db, "MA")
    project_b = _org_project(db, "MB")
    company_a = _doc(db, project_a, document_type=DocumentType.qualification, file_name="a.pdf")
    company_b = _doc(db, project_b, document_type=DocumentType.qualification, file_name="b.pdf")
    chunk_a = _chunk(db, project_a, company_a, index=0, content="A公司具备一级资质。")
    chunk_b = _chunk(db, project_b, company_b, index=0, content="B公司具备特级资质。")
    req_a = _requirement(db, project_a, normalized="投标人须具备一级资质。")
    req_b = _requirement(db, project_b, normalized="投标人须具备特级资质。")
    db.commit()

    seen_chunk_ids: list[str] = []

    def responder(messages):
        payload = _match_payload(messages)
        for c in payload["company_chunks"]:
            seen_chunk_ids.append(c["chunk_id"])
        return {
            "items": [
                _supported_item(
                    req_a,
                    chunk_a,
                    company_evidence_quote="A公司具备一级资质",
                    summary="材料含一级资质引文，需人工确认",
                )
            ]
        }

    llm = FakeLlm(responder)
    svc = RequirementMatchService(db, llm=llm)
    run = svc.start_matching(project_a.id, MatchStartRequest())
    svc.execute_run(run.id)
    assert str(chunk_a.id) in seen_chunk_ids
    assert str(chunk_b.id) not in seen_chunk_ids
    matches_a = list(
        db.scalars(
            select(RequirementEvidenceMatch).where(
                RequirementEvidenceMatch.project_id == project_a.id
            )
        )
    )
    matches_b = list(
        db.scalars(
            select(RequirementEvidenceMatch).where(
                RequirementEvidenceMatch.project_id == project_b.id
            )
        )
    )
    assert len(matches_a) == 1
    assert matches_b == []
    _ = req_b


def test_empty_company_docs_no_llm(db):
    project = _org_project(db, "M11")
    tender = _doc(db, project, document_type=DocumentType.tender, file_name="t.pdf")
    _requirement(
        db,
        project,
        normalized="投标人须具备一级资质。",
        source_document=tender,
    )
    # Prior auto match should be preserved
    req = db.scalar(select(Requirement).where(Requirement.project_id == project.id))
    assert req is not None
    old = RequirementEvidenceMatch(
        project_id=project.id,
        requirement_id=req.id,
        status=EvidenceMatchStatus.supported,
        summary="旧结果",
        needs_review=True,
        risk_level=RiskLevel.medium,
        metadata_json={"source": "auto_match", "match_key": auto_match_key(req.id)},
    )
    db.add(old)
    db.commit()

    llm = FakeLlm()
    svc = RequirementMatchService(db, llm=llm)
    run = svc.start_matching(project.id, MatchStartRequest(force=True))
    svc.execute_run(run.id)
    refreshed = svc.get_run(project.id, run.id)
    assert refreshed.status == ExtractionRunStatus.failed
    assert "企业材料为空" in (refreshed.error_summary or "")
    assert llm.chat_calls == []
    still = db.get(RequirementEvidenceMatch, old.id)
    assert still is not None
    assert still.summary == "旧结果"


def test_conflict_inheritance_needs_review(db):
    project = _org_project(db, "M12")
    company = _doc(db, project, document_type=DocumentType.qualification, file_name="q.pdf")
    chunk = _chunk(db, project, company, index=0, content="本公司具备一级资质。")
    req = _requirement(
        db,
        project,
        normalized="投标人须具备一级资质。",
        metadata={"source": "auto_extraction", "potential_conflict": True},
    )
    db.commit()

    llm = FakeLlm(
        lambda _m: {
            "items": [
                _supported_item(
                    req,
                    chunk,
                    company_evidence_quote="本公司具备一级资质",
                    summary="材料含一级资质引文，需人工确认",
                )
            ]
        }
    )
    svc = RequirementMatchService(db, llm=llm)
    run = svc.start_matching(project.id, MatchStartRequest())
    svc.execute_run(run.id)
    match = db.scalar(select(RequirementEvidenceMatch))
    assert match is not None
    assert match.needs_review is True
    assert (match.metadata_json or {}).get("requirement_potential_conflict") is True
    assert "冲突" in (match.metadata_json or {}).get("conflict_inheritance_note", "")


def test_idempotent_rerun_without_force(db):
    project = _org_project(db, "M13")
    company = _doc(db, project, document_type=DocumentType.qualification, file_name="q.pdf")
    chunk = _chunk(db, project, company, index=0, content="本公司具备一级资质。")
    req = _requirement(db, project, normalized="投标人须具备一级资质。")
    db.commit()

    item = _supported_item(
        req,
        chunk,
        company_evidence_quote="本公司具备一级资质",
        summary="材料含一级资质引文，需人工确认",
    )
    llm = FakeLlm(lambda _m: {"items": [item]})
    svc = RequirementMatchService(db, llm=llm)
    r1 = svc.start_matching(project.id, MatchStartRequest(force=False))
    svc.execute_run(r1.id)
    r2 = svc.start_matching(project.id, MatchStartRequest(force=False))
    svc.execute_run(r2.id)
    matches = list(
        db.scalars(
            select(RequirementEvidenceMatch).where(
                RequirementEvidenceMatch.project_id == project.id
            )
        )
    )
    assert len(matches) == 1


def test_force_replaces_only_scoped_autos(db):
    project = _org_project(db, "M14")
    company = _doc(db, project, document_type=DocumentType.qualification, file_name="q.pdf")
    chunk = _chunk(db, project, company, index=0, content="本公司具备一级资质。")
    req_a = _requirement(db, project, normalized="投标人须具备一级资质。", title="A")
    req_b = _requirement(db, project, normalized="须提交营业执照复印件。", title="B")
    db.commit()

    # Seed autos for both
    for req, summary in ((req_a, "旧A"), (req_b, "旧B")):
        db.add(
            RequirementEvidenceMatch(
                project_id=project.id,
                requirement_id=req.id,
                status=EvidenceMatchStatus.insufficient_evidence,
                summary=summary,
                needs_review=True,
                risk_level=RiskLevel.high,
                metadata_json={
                    "source": "auto_match",
                    "match_key": auto_match_key(req.id),
                },
            )
        )
    db.commit()

    llm = FakeLlm(
        lambda _m: {
            "items": [
                _supported_item(
                    req_a,
                    chunk,
                    company_evidence_quote="本公司具备一级资质",
                    summary="材料含一级资质引文，需人工确认",
                )
            ]
        }
    )
    svc = RequirementMatchService(db, llm=llm)
    run = svc.start_matching(
        project.id,
        MatchStartRequest(requirement_ids=[req_a.id], force=True),
    )
    svc.execute_run(run.id)

    match_a = db.scalar(
        select(RequirementEvidenceMatch).where(
            RequirementEvidenceMatch.requirement_id == req_a.id
        )
    )
    match_b = db.scalar(
        select(RequirementEvidenceMatch).where(
            RequirementEvidenceMatch.requirement_id == req_b.id
        )
    )
    assert match_a is not None
    assert match_a.status == EvidenceMatchStatus.supported
    assert match_a.summary != "旧A"
    assert match_b is not None
    assert match_b.summary == "旧B"


def test_force_keeps_old_on_all_rejected(db):
    project = _org_project(db, "M14b")
    company = _doc(db, project, document_type=DocumentType.qualification, file_name="q.pdf")
    chunk = _chunk(db, project, company, index=0, content="本公司具备一级资质。")
    req = _requirement(db, project, normalized="投标人须具备一级资质。")
    old = RequirementEvidenceMatch(
        project_id=project.id,
        requirement_id=req.id,
        status=EvidenceMatchStatus.supported,
        summary="保留我",
        needs_review=True,
        risk_level=RiskLevel.medium,
        primary_company_chunk_id=chunk.id,
        primary_company_document_id=company.id,
        primary_company_quote="本公司具备一级资质",
        metadata_json={"source": "auto_match", "match_key": auto_match_key(req.id)},
    )
    db.add(old)
    db.commit()

    llm = FakeLlm(
        lambda _m: {
            "items": [
                _supported_item(
                    req,
                    chunk,
                    company_evidence_quote="完全不存在的引文XYZ",
                    summary="坏引文，需人工确认",
                )
            ]
        }
    )
    svc = RequirementMatchService(db, llm=llm)
    run = svc.start_matching(project.id, MatchStartRequest(force=True))
    svc.execute_run(run.id)
    refreshed = svc.get_run(project.id, run.id)
    assert refreshed.status == ExtractionRunStatus.failed
    still = db.get(RequirementEvidenceMatch, old.id)
    assert still is not None
    assert still.summary == "保留我"


def test_manual_imported_reviewed_never_deleted(db):
    project = _org_project(db, "M15")
    company = _doc(db, project, document_type=DocumentType.qualification, file_name="q.pdf")
    chunk = _chunk(db, project, company, index=0, content="本公司具备一级资质。")
    req = _requirement(db, project, normalized="投标人须具备一级资质。")
    protected = [
        RequirementEvidenceMatch(
            project_id=project.id,
            requirement_id=req.id,
            status=EvidenceMatchStatus.insufficient_evidence,
            summary="手工",
            needs_review=True,
            risk_level=RiskLevel.medium,
            metadata_json={"source": "manual"},
        ),
        RequirementEvidenceMatch(
            project_id=project.id,
            requirement_id=req.id,
            status=EvidenceMatchStatus.insufficient_evidence,
            summary="导入",
            needs_review=True,
            risk_level=RiskLevel.medium,
            metadata_json={"source": "imported"},
        ),
        RequirementEvidenceMatch(
            project_id=project.id,
            requirement_id=req.id,
            status=EvidenceMatchStatus.insufficient_evidence,
            summary="已审",
            needs_review=False,
            risk_level=RiskLevel.medium,
            metadata_json={
                "source": "auto_match",
                "review_status": ReviewStatus.reviewed.value,
                "match_key": auto_match_key(req.id) + "-reviewed",
            },
        ),
    ]
    for m in protected:
        db.add(m)
    db.commit()
    ids = [m.id for m in protected]

    llm = FakeLlm(
        lambda _m: {
            "items": [
                _supported_item(
                    req,
                    chunk,
                    company_evidence_quote="本公司具备一级资质",
                    summary="材料含一级资质引文，需人工确认",
                )
            ]
        }
    )
    svc = RequirementMatchService(db, llm=llm)
    run = svc.start_matching(project.id, MatchStartRequest(force=True))
    svc.execute_run(run.id)
    for mid in ids:
        assert db.get(RequirementEvidenceMatch, mid) is not None


def test_api_list_detail_and_run(client, db, task_factory):
    project = _org_project(db, "M16")
    company = _doc(db, project, document_type=DocumentType.qualification, file_name="q.pdf")
    chunk = _chunk(db, project, company, index=0, content="本公司具备一级资质。")
    tender = _doc(db, project, document_type=DocumentType.tender, file_name="t.pdf")
    t_chunk = _chunk(
        db,
        project,
        tender,
        index=0,
        content="投标人须具备一级资质。",
        section="第三章",
        clause_id="3.1",
        page_start=10,
        page_end=10,
    )
    req = _requirement(
        db,
        project,
        normalized="投标人须具备一级资质。",
        source_document=tender,
    )
    db.add(
        EvidenceLink(
            requirement_id=req.id,
            document_id=tender.id,
            chunk_id=t_chunk.id,
            evidence_type="quote",
            notes="投标人须具备一级资质",
        )
    )
    db.commit()

    def responder(_m):
        return {
            "items": [
                _supported_item(
                    req,
                    chunk,
                    company_evidence_quote="本公司具备一级资质",
                    summary="材料含一级资质引文，需人工确认",
                )
            ]
        }

    from app.services.requirement_match_service import RequirementMatchService as Svc

    # Run synchronously via service (client BackgroundTasks uses real SESSION_FACTORY
    # which we monkeypatched via task_factory for the task module).
    llm = FakeLlm(responder)
    svc = Svc(db, llm=llm)
    run = svc.start_matching(project.id, MatchStartRequest())
    svc.execute_run(run.id)

    run_resp = client.get(f"/api/v1/projects/{project.id}/requirement-matches/runs/{run.id}")
    assert run_resp.status_code == 200
    assert run_resp.json()["status"] == ExtractionRunStatus.succeeded.value
    assert run_resp.json()["matched_count"] == 1

    list_resp = client.get(
        f"/api/v1/projects/{project.id}/requirement-matches",
        params={"status": "supported", "needs_review": True},
    )
    assert list_resp.status_code == 200
    body = list_resp.json()
    assert body["total"] == 1
    match_id = body["items"][0]["id"]

    detail = client.get(f"/api/v1/projects/{project.id}/requirement-matches/{match_id}")
    assert detail.status_code == 200
    d = detail.json()
    assert d["needs_review"] is True
    assert d["primary_company_chunk_id"] == str(chunk.id)
    assert d["document_center_path"]
    assert "documentId=" in d["document_center_path"]
    assert len(d["company_links"]) >= 1
    assert len(d["tender_evidence_links"]) >= 1
    assert d["company_links"][0]["role"] == "company_support"

    # POST start endpoint creates a queued run
    post = client.post(
        f"/api/v1/projects/{project.id}/requirement-matches/runs",
        json={"force": False},
    )
    assert post.status_code == 201
    assert post.json()["status"] == ExtractionRunStatus.queued.value
    _ = task_factory


def test_banned_absolute_summary_rejected(db):
    project = _org_project(db, "M17")
    company = _doc(db, project, document_type=DocumentType.qualification, file_name="q.pdf")
    chunk = _chunk(db, project, company, index=0, content="本公司具备一级资质。")
    req = _requirement(db, project, normalized="投标人须具备一级资质。")
    db.commit()

    llm = FakeLlm(
        lambda _m: {
            "items": [
                _supported_item(
                    req,
                    chunk,
                    company_evidence_quote="本公司具备一级资质",
                    summary="企业不符合该要求",
                )
            ]
        }
    )
    svc = RequirementMatchService(db, llm=llm)
    run = svc.start_matching(project.id, MatchStartRequest())
    svc.execute_run(run.id)
    assert list(db.scalars(select(RequirementEvidenceMatch))) == []


def test_start_filters_excluded_document_types(db):
    project = _org_project(db, "M18")
    company = _doc(db, project, document_type=DocumentType.personnel, file_name="p.pdf")
    chunk = _chunk(db, project, company, index=0, content="项目经理具备高级职称。")
    req = _requirement(db, project, normalized="项目经理须具备高级职称。")
    db.commit()

    seen: list[str] = []

    def responder(messages):
        payload = _match_payload(messages)
        for c in payload["company_chunks"]:
            seen.append(c["document_type"])
        return {
            "items": [
                _supported_item(
                    req,
                    chunk,
                    company_evidence_quote="项目经理具备高级职称",
                    summary="材料含高级职称引文，需人工确认",
                )
            ]
        }

    llm = FakeLlm(responder)
    svc = RequirementMatchService(db, llm=llm)
    run = svc.start_matching(
        project.id,
        MatchStartRequest(
            document_types=[
                DocumentType.tender,
                DocumentType.announcement,
                DocumentType.personnel,
            ]
        ),
    )
    assert DocumentType.tender.value not in (run.document_types_json or [])
    assert DocumentType.personnel.value in (run.document_types_json or [])
    svc.execute_run(run.id)
    assert DocumentType.tender.value not in seen
    assert DocumentType.personnel.value in seen
