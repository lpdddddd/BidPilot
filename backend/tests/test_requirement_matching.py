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
from app.models.match_run import (
    RequirementEvidenceMatch,
    RequirementEvidenceMatchLink,
    RequirementMatchRun,
)
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
    # Tender-as-company → validation reject → whole run failed, zero writes.
    assert matches == []
    assert svc.get_run(project.id, run.id).status == ExtractionRunStatus.failed
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


def test_invented_critical_tokens_fail_whole_run(db):
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
    refreshed = svc.get_run(project.id, run.id)
    assert refreshed.status == ExtractionRunStatus.failed
    assert list(db.scalars(select(RequirementEvidenceMatch))) == []
    cfg = refreshed.config_json or {}
    assert cfg.get("result_kind") == "invalid_or_incomplete_result"
    assert (cfg.get("reject_reason_counts") or {}).get("fabricated_summary", 0) >= 1


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
    assert svc.get_run(project.id, run.id).status == ExtractionRunStatus.failed
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
    assert svc2.get_run(project.id, run2.id).status == ExtractionRunStatus.failed
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
    refreshed = svc.get_run(project.id, run.id)
    assert refreshed.status == ExtractionRunStatus.failed
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


# ---------------------------------------------------------------------------
# Goal 1: not_applicable evidence rules
# ---------------------------------------------------------------------------


def _attach_tender_evidence(
    db: Session,
    project: BidProject,
    req: Requirement,
    *,
    tender: Document,
    content: str,
    section: str = "适用范围",
    clause_id: str = "S.1",
) -> DocumentChunk:
    t_chunk = _chunk(
        db,
        project,
        tender,
        index=0,
        content=content,
        section=section,
        clause_id=clause_id,
        page_start=3,
        page_end=3,
    )
    db.add(
        EvidenceLink(
            requirement_id=req.id,
            document_id=tender.id,
            chunk_id=t_chunk.id,
            evidence_type="quote",
            notes=content[:80],
        )
    )
    db.flush()
    return t_chunk


def test_not_applicable_without_evidence_rejected(db):
    project = _org_project(db, "NA1")
    company = _doc(db, project, document_type=DocumentType.qualification, file_name="q.pdf")
    _chunk(db, project, company, index=0, content="本公司具备一级资质。")
    req = _requirement(
        db,
        project,
        normalized="本包件仅适用于北京市海淀区项目。",
        title="包件范围",
    )
    db.commit()

    llm = FakeLlm(
        lambda _m: {
            "items": [
                {
                    "requirement_id": str(req.id),
                    "status": "not_applicable",
                    "summary": "当前项目不在海淀，故不适用",
                    "needs_review": True,
                    "not_applicable_basis": None,
                    "not_applicable_evidence_quote": None,
                    "not_applicable_evidence_chunk_id": None,
                }
            ]
        }
    )
    svc = RequirementMatchService(db, llm=llm)
    run = svc.start_matching(project.id, MatchStartRequest())
    svc.execute_run(run.id)
    refreshed = svc.get_run(project.id, run.id)
    # Unfounded not_applicable → all rejected → failed, zero writes
    assert refreshed.status == ExtractionRunStatus.failed
    assert list(db.scalars(select(RequirementEvidenceMatch))) == []


def test_empty_company_materials_never_not_applicable(db):
    project = _org_project(db, "NA2")
    tender = _doc(db, project, document_type=DocumentType.tender, file_name="t.pdf")
    req = _requirement(
        db,
        project,
        normalized="本包件仅适用于A标段。",
        source_document=tender,
    )
    _attach_tender_evidence(
        db, project, req, tender=tender, content="本包件仅适用于A标段，其他标段不适用。"
    )
    db.commit()

    llm = FakeLlm()
    svc = RequirementMatchService(db, llm=llm)
    run = svc.start_matching(project.id, MatchStartRequest())
    svc.execute_run(run.id)
    refreshed = svc.get_run(project.id, run.id)
    assert refreshed.status == ExtractionRunStatus.failed
    assert "企业材料为空" in (refreshed.error_summary or "")
    assert llm.chat_calls == []
    assert list(db.scalars(select(RequirementEvidenceMatch))) == []


def test_legal_not_applicable_with_tender_scope_evidence(db):
    project = _org_project(db, "NA3")
    tender = _doc(db, project, document_type=DocumentType.tender, file_name="t.pdf")
    company = _doc(db, project, document_type=DocumentType.qualification, file_name="q.pdf")
    c_chunk = _chunk(
        db,
        project,
        company,
        index=0,
        content="本项目服务范围为朝阳区，本公司具备一级资质。",
    )
    req = _requirement(
        db,
        project,
        normalized="本包件仅适用于海淀区范围内的项目。",
        title="包件适用",
        source_document=tender,
        category=RequirementCategory.commercial,
        mandatory=False,
        risk_level=RiskLevel.low,
    )
    t_chunk = _attach_tender_evidence(
        db,
        project,
        req,
        tender=tender,
        content="本包件仅适用于海淀区范围内的项目，其他区域不适用。",
    )
    db.commit()

    llm = FakeLlm(
        lambda _m: {
            "items": [
                {
                    "requirement_id": str(req.id),
                    "status": "not_applicable",
                    "summary": "招标要求限定海淀区范围，当前对象服务范围为朝阳区，待人工审核",
                    "needs_review": True,
                    "not_applicable_basis": "requirement_scope_exclusion",
                    "requirement_scope_chunk_id": str(t_chunk.id),
                    "requirement_scope_quote": (
                        "本包件仅适用于海淀区范围内的项目，其他区域不适用"
                    ),
                    "current_scope_chunk_id": str(c_chunk.id),
                    "current_scope_quote": "本项目服务范围为朝阳区",
                    "not_applicable_note": "海淀区与朝阳区范围互斥",
                }
            ]
        }
    )
    svc = RequirementMatchService(db, llm=llm)
    run = svc.start_matching(project.id, MatchStartRequest())
    svc.execute_run(run.id)
    refreshed = svc.get_run(project.id, run.id)
    assert refreshed.status == ExtractionRunStatus.succeeded
    match = db.scalar(select(RequirementEvidenceMatch))
    assert match is not None
    assert match.status == EvidenceMatchStatus.not_applicable
    assert match.needs_review is True
    meta = match.metadata_json or {}
    assert meta.get("not_applicable_basis") == "requirement_scope_exclusion"
    assert "海淀区" in (meta.get("requirement_scope_quote") or "")
    assert meta.get("requirement_scope_chunk_id") == str(t_chunk.id)
    assert "朝阳区" in (meta.get("current_scope_quote") or "")
    assert meta.get("current_scope_chunk_id") == str(c_chunk.id)
    loc = meta.get("requirement_scope_location") or {}
    assert loc.get("section") == "适用范围"
    assert loc.get("clause_id") == "S.1"
    links = list(
        db.scalars(
            select(RequirementEvidenceMatchLink).where(
                RequirementEvidenceMatchLink.match_id == match.id
            )
        )
    )
    assert any(link.role == "company_scope_exclusion" for link in links)


def test_not_applicable_rejects_cross_project_and_tender_as_company_basis(db):
    project_a = _org_project(db, "NA4a")
    project_b = _org_project(db, "NA4b")
    tender_a = _doc(db, project_a, document_type=DocumentType.tender, file_name="ta.pdf")
    company_a = _doc(
        db, project_a, document_type=DocumentType.qualification, file_name="qa.pdf"
    )
    _chunk(db, project_a, company_a, index=0, content="A公司简介文字。")
    tender_b = _doc(db, project_b, document_type=DocumentType.tender, file_name="tb.pdf")
    t_chunk_b = _chunk(
        db,
        project_b,
        tender_b,
        index=0,
        content="本包件仅适用于B市项目。",
    )
    req = _requirement(
        db,
        project_a,
        normalized="本包件仅适用于A市项目。",
        source_document=tender_a,
    )
    # Legitimate tender evidence on A (unused by bad LLM response)
    _attach_tender_evidence(
        db, project_a, req, tender=tender_a, content="本包件仅适用于A市项目，其他城市不适用。"
    )
    db.commit()

    # Cross-project tender chunk as requirement_scope evidence → reject
    llm = FakeLlm(
        lambda _m: {
            "items": [
                {
                    "requirement_id": str(req.id),
                    "status": "not_applicable",
                    "summary": "跨项目证据，待人工审核",
                    "needs_review": True,
                    "not_applicable_basis": "requirement_scope_exclusion",
                    "requirement_scope_chunk_id": str(t_chunk_b.id),
                    "requirement_scope_quote": "本包件仅适用于B市项目",
                    "current_scope_chunk_id": str(
                        db.scalar(
                            select(DocumentChunk).where(
                                DocumentChunk.document_id == company_a.id
                            )
                        ).id
                    ),
                    "current_scope_quote": "A公司简介文字",
                }
            ]
        }
    )
    svc = RequirementMatchService(db, llm=llm)
    run = svc.start_matching(project_a.id, MatchStartRequest())
    svc.execute_run(run.id)
    assert svc.get_run(project_a.id, run.id).status == ExtractionRunStatus.failed
    assert list(db.scalars(select(RequirementEvidenceMatch))) == []

    # project_scope_exclusion citing tender-type chunk id (not in company set) → reject
    tender_chunk_a = db.scalar(
        select(DocumentChunk).where(DocumentChunk.document_id == tender_a.id)
    )
    assert tender_chunk_a is not None
    company_chunk_a = db.scalar(
        select(DocumentChunk).where(DocumentChunk.document_id == company_a.id)
    )
    llm2 = FakeLlm(
        lambda _m: {
            "items": [
                {
                    "requirement_id": str(req.id),
                    "status": "not_applicable",
                    "summary": "误用招标文件作当前范围，待人工审核",
                    "needs_review": True,
                    "not_applicable_basis": "project_scope_exclusion",
                    "requirement_scope_chunk_id": str(tender_chunk_a.id),
                    "requirement_scope_quote": "本包件仅适用于A市项目，其他城市不适用",
                    "current_scope_chunk_id": str(tender_chunk_a.id),
                    "current_scope_quote": "本包件仅适用于A市项目，其他城市不适用",
                }
            ]
        }
    )
    _ = company_chunk_a
    svc2 = RequirementMatchService(db, llm=llm2)
    run2 = svc2.start_matching(project_a.id, MatchStartRequest())
    svc2.execute_run(run2.id)
    assert svc2.get_run(project_a.id, run2.id).status == ExtractionRunStatus.failed
    assert list(db.scalars(select(RequirementEvidenceMatch))) == []


# ---------------------------------------------------------------------------
# Goal 2: conflicting_evidence dual company evidence
# ---------------------------------------------------------------------------


def test_conflicting_single_evidence_rejected(db):
    project = _org_project(db, "CF1")
    company = _doc(db, project, document_type=DocumentType.qualification, file_name="q.pdf")
    chunk = _chunk(
        db,
        project,
        company,
        index=0,
        content="本公司具备一级资质，同时声明不具备一级资质。",
    )
    req = _requirement(db, project, normalized="投标人须具备一级资质。")
    db.commit()

    llm = FakeLlm(
        lambda _m: {
            "items": [
                {
                    "requirement_id": str(req.id),
                    "status": "conflicting_evidence",
                    "summary": "材料自相矛盾，需人工确认",
                    "primary_company_chunk_id": str(chunk.id),
                    "company_evidence_quote": "本公司具备一级资质",
                    "conflicting_company_chunk_id": None,
                    "conflicting_company_evidence_quote": None,
                    "needs_review": True,
                }
            ]
        }
    )
    svc = RequirementMatchService(db, llm=llm)
    run = svc.start_matching(project.id, MatchStartRequest())
    svc.execute_run(run.id)
    refreshed = svc.get_run(project.id, run.id)
    # Illegal conflict → whole run failed, no downgrade, zero writes
    assert refreshed.status == ExtractionRunStatus.failed
    assert list(db.scalars(select(RequirementEvidenceMatch))) == []


def test_conflicting_same_chunk_same_quote_rejected(db):
    project = _org_project(db, "CF2")
    company = _doc(db, project, document_type=DocumentType.qualification, file_name="q.pdf")
    chunk = _chunk(db, project, company, index=0, content="本公司具备一级资质。")
    req = _requirement(db, project, normalized="投标人须具备一级资质。")
    db.commit()

    llm = FakeLlm(
        lambda _m: {
            "items": [
                {
                    "requirement_id": str(req.id),
                    "status": "conflicting_evidence",
                    "summary": "伪冲突，需人工确认",
                    "primary_company_chunk_id": str(chunk.id),
                    "company_evidence_quote": "本公司具备一级资质",
                    "conflicting_company_chunk_id": str(chunk.id),
                    "conflicting_company_evidence_quote": "本公司具备一级资质",
                    "conflict_note": "一级资质表述冲突",
                    "needs_review": True,
                }
            ]
        }
    )
    svc = RequirementMatchService(db, llm=llm)
    run = svc.start_matching(project.id, MatchStartRequest())
    svc.execute_run(run.id)
    assert svc.get_run(project.id, run.id).status == ExtractionRunStatus.failed
    assert list(db.scalars(select(RequirementEvidenceMatch))) == []


def test_conflicting_unrelated_quotes_rejected(db):
    """Two locatable but unrelated company quotes must not become conflicting_evidence."""
    project = _org_project(db, "CF2b")
    company = _doc(db, project, document_type=DocumentType.qualification, file_name="q.pdf")
    chunk_a = _chunk(
        db, project, company, index=0, content="本公司具备一级建筑业企业资质。"
    )
    chunk_b = _chunk(
        db, project, company, index=1, content="本公司办公地址位于朝阳区望京街道。"
    )
    req = _requirement(db, project, normalized="投标人须具备一级建筑业企业资质。")
    db.commit()

    llm = FakeLlm(
        lambda _m: {
            "items": [
                {
                    "requirement_id": str(req.id),
                    "status": "conflicting_evidence",
                    "summary": "资质与地址无关却标为冲突，需人工确认",
                    "primary_company_chunk_id": str(chunk_a.id),
                    "company_evidence_quote": "本公司具备一级建筑业企业资质",
                    "conflicting_company_chunk_id": str(chunk_b.id),
                    "conflicting_company_evidence_quote": "本公司办公地址位于朝阳区望京街道",
                    "conflict_dimension": "qualification_level",
                    "conflict_subject": "建筑业企业资质",
                    "primary_claim_value": "一级",
                    "conflicting_claim_value": "朝阳区",
                    "conflict_note": "无关材料",
                    "needs_review": True,
                }
            ]
        }
    )
    svc = RequirementMatchService(db, llm=llm)
    run = svc.start_matching(project.id, MatchStartRequest())
    svc.execute_run(run.id)
    assert svc.get_run(project.id, run.id).status == ExtractionRunStatus.failed
    assert list(db.scalars(select(RequirementEvidenceMatch))) == []


def test_conflicting_two_chunks_persists_dual_links(db):
    project = _org_project(db, "CF3")
    company = _doc(db, project, document_type=DocumentType.qualification, file_name="q.pdf")
    chunk_a = _chunk(
        db, project, company, index=0, content="本公司具备一级建筑业企业资质。"
    )
    chunk_b = _chunk(
        db, project, company, index=1, content="本公司仅具备二级建筑业企业资质。"
    )
    req = _requirement(db, project, normalized="投标人须具备一级建筑业企业资质。")
    db.commit()

    llm = FakeLlm(
        lambda _m: {
            "items": [
                {
                    "requirement_id": str(req.id),
                    "status": "conflicting_evidence",
                    "summary": "企业材料中一级与二级资质表述冲突，需人工确认",
                    "primary_company_chunk_id": str(chunk_a.id),
                    "company_evidence_quote": "本公司具备一级建筑业企业资质",
                    "conflicting_company_chunk_id": str(chunk_b.id),
                    "conflicting_company_evidence_quote": "本公司仅具备二级建筑业企业资质",
                    "conflict_dimension": "qualification_level",
                    "conflict_subject": "建筑业企业资质",
                    "primary_claim_value": "一级",
                    "conflicting_claim_value": "二级",
                    "conflict_note": "一级与二级资质冲突",
                    "needs_review": True,
                }
            ]
        }
    )
    svc = RequirementMatchService(db, llm=llm)
    run = svc.start_matching(project.id, MatchStartRequest())
    svc.execute_run(run.id)
    refreshed = svc.get_run(project.id, run.id)
    assert refreshed.status == ExtractionRunStatus.succeeded
    assert refreshed.conflict_count == 1
    match = db.scalar(select(RequirementEvidenceMatch))
    assert match is not None
    assert match.status == EvidenceMatchStatus.conflicting_evidence
    links = list(
        db.scalars(
            select(RequirementEvidenceMatchLink).where(
                RequirementEvidenceMatchLink.match_id == match.id
            )
        )
    )
    roles = {link.role for link in links}
    assert roles == {"company_support", "company_conflict"}
    by_role = {link.role: link for link in links}
    assert by_role["company_support"].chunk_id == chunk_a.id
    assert by_role["company_conflict"].chunk_id == chunk_b.id
    meta = match.metadata_json or {}
    assert meta.get("conflict_dimension") == "qualification_level"
    assert meta.get("primary_claim_value") == "一级"
    assert meta.get("conflicting_claim_value") == "二级"


def test_conflicting_unlocatable_quote_not_persisted_as_conflict(db):
    project = _org_project(db, "CF4")
    company = _doc(db, project, document_type=DocumentType.qualification, file_name="q.pdf")
    chunk_a = _chunk(db, project, company, index=0, content="本公司具备一级资质。")
    chunk_b = _chunk(db, project, company, index=1, content="本公司具备二级资质。")
    req = _requirement(db, project, normalized="投标人须具备一级资质。")
    db.commit()

    llm = FakeLlm(
        lambda _m: {
            "items": [
                {
                    "requirement_id": str(req.id),
                    "status": "conflicting_evidence",
                    "summary": "冲突引文无法定位，需人工确认",
                    "primary_company_chunk_id": str(chunk_a.id),
                    "company_evidence_quote": "本公司具备一级资质",
                    "conflicting_company_chunk_id": str(chunk_b.id),
                    "conflicting_company_evidence_quote": "这段话完全不存在于原文XYZ",
                    "needs_review": True,
                }
            ]
        }
    )
    svc = RequirementMatchService(db, llm=llm)
    run = svc.start_matching(project.id, MatchStartRequest())
    svc.execute_run(run.id)
    assert svc.get_run(project.id, run.id).status == ExtractionRunStatus.failed
    assert list(db.scalars(select(RequirementEvidenceMatch))) == []


def test_conflicting_cross_project_evidence_rejected(db):
    project_a = _org_project(db, "CF5a")
    project_b = _org_project(db, "CF5b")
    company_a = _doc(
        db, project_a, document_type=DocumentType.qualification, file_name="a.pdf"
    )
    company_b = _doc(
        db, project_b, document_type=DocumentType.qualification, file_name="b.pdf"
    )
    chunk_a = _chunk(db, project_a, company_a, index=0, content="A公司具备一级资质。")
    chunk_b = _chunk(db, project_b, company_b, index=0, content="B公司具备二级资质。")
    req = _requirement(db, project_a, normalized="投标人须具备一级资质。")
    db.commit()

    llm = FakeLlm(
        lambda _m: {
            "items": [
                {
                    "requirement_id": str(req.id),
                    "status": "conflicting_evidence",
                    "summary": "跨项目冲突证据，需人工确认",
                    "primary_company_chunk_id": str(chunk_a.id),
                    "company_evidence_quote": "A公司具备一级资质",
                    "conflicting_company_chunk_id": str(chunk_b.id),
                    "conflicting_company_evidence_quote": "B公司具备二级资质",
                    "needs_review": True,
                }
            ]
        }
    )
    svc = RequirementMatchService(db, llm=llm)
    run = svc.start_matching(project_a.id, MatchStartRequest())
    svc.execute_run(run.id)
    # Conflict side not in allowed set → validation reject → whole run failed
    assert svc.get_run(project_a.id, run.id).status == ExtractionRunStatus.failed
    assert (
        list(
            db.scalars(
                select(RequirementEvidenceMatch).where(
                    RequirementEvidenceMatch.project_id == project_a.id
                )
            )
        )
        == []
    )


# ---------------------------------------------------------------------------
# Goal 3: cancellable match run
# ---------------------------------------------------------------------------


def test_cancel_queued_run(db, client):
    project = _org_project(db, "CX1")
    company = _doc(db, project, document_type=DocumentType.qualification, file_name="q.pdf")
    _chunk(db, project, company, index=0, content="本公司具备一级资质。")
    _requirement(db, project, normalized="投标人须具备一级资质。")
    db.commit()

    svc = RequirementMatchService(db, llm=FakeLlm())
    run = svc.start_matching(project.id, MatchStartRequest())
    assert run.status == ExtractionRunStatus.queued

    cancelled = svc.cancel_run(project.id, run.id)
    assert cancelled.status == ExtractionRunStatus.cancelled
    assert (cancelled.config_json or {}).get("cancel_requested") is True
    assert "取消" in (cancelled.error_summary or "")

    # execute_run should no-op
    svc.execute_run(run.id)
    assert svc.get_run(project.id, run.id).status == ExtractionRunStatus.cancelled
    assert list(db.scalars(select(RequirementEvidenceMatch))) == []

    # API path
    run2 = svc.start_matching(project.id, MatchStartRequest())
    resp = client.post(
        f"/api/v1/projects/{project.id}/requirement-matches/runs/{run2.id}/cancel"
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == ExtractionRunStatus.cancelled.value


def test_cancel_running_before_next_batch(db, monkeypatch):
    import app.services.requirement_match_service as match_mod

    monkeypatch.setattr(match_mod, "BATCH_SIZE", 1)

    project = _org_project(db, "CX2")
    company = _doc(db, project, document_type=DocumentType.qualification, file_name="q.pdf")
    chunk = _chunk(db, project, company, index=0, content="本公司具备一级资质。")
    req_a = _requirement(db, project, normalized="投标人须具备一级资质。", title="A")
    req_b = _requirement(db, project, normalized="须提交营业执照复印件。", title="B")
    db.commit()

    llm = FakeLlm()
    svc = RequirementMatchService(db, llm=llm)
    run = svc.start_matching(project.id, MatchStartRequest())

    calls = {"n": 0}
    original = svc._match_batch

    def wrapped(batch, company_chunks, project_id, *, run_id=None):
        result = original(batch, company_chunks, project_id, run_id=run_id)
        calls["n"] += 1
        if calls["n"] == 1:
            svc.cancel_run(project.id, run.id)
        return result

    svc._match_batch = wrapped  # type: ignore[method-assign]

    def responder(messages):
        payload = _match_payload(messages)
        rid = payload["requirements"][0]["requirement_id"]
        req = req_a if rid == str(req_a.id) else req_b
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

    llm._responder = responder
    svc.execute_run(run.id)
    refreshed = svc.get_run(project.id, run.id)
    assert refreshed.status == ExtractionRunStatus.cancelled
    assert calls["n"] == 1
    assert list(db.scalars(select(RequirementEvidenceMatch))) == []


def test_cancel_after_llm_before_persist(db, monkeypatch):
    import app.services.requirement_match_service as match_mod

    monkeypatch.setattr(match_mod, "BATCH_SIZE", 4)

    project = _org_project(db, "CX3")
    company = _doc(db, project, document_type=DocumentType.qualification, file_name="q.pdf")
    chunk = _chunk(db, project, company, index=0, content="本公司具备一级资质。")
    req = _requirement(db, project, normalized="投标人须具备一级资质。")
    db.commit()

    llm = FakeLlm()
    svc = RequirementMatchService(db, llm=llm)
    run = svc.start_matching(project.id, MatchStartRequest())

    def responder(_m):
        # Cancel after LLM returns, before validate/persist (checked inside _match_batch)
        svc.cancel_run(project.id, run.id)
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

    llm._responder = responder
    svc.execute_run(run.id)
    assert svc.get_run(project.id, run.id).status == ExtractionRunStatus.cancelled
    assert list(db.scalars(select(RequirementEvidenceMatch))) == []


def test_force_cancel_keeps_old_matches(db):
    project = _org_project(db, "CX4")
    company = _doc(db, project, document_type=DocumentType.qualification, file_name="q.pdf")
    chunk = _chunk(db, project, company, index=0, content="本公司具备一级资质。")
    req = _requirement(db, project, normalized="投标人须具备一级资质。")
    old = RequirementEvidenceMatch(
        project_id=project.id,
        requirement_id=req.id,
        status=EvidenceMatchStatus.supported,
        summary="保留旧匹配",
        needs_review=True,
        risk_level=RiskLevel.medium,
        primary_company_chunk_id=chunk.id,
        primary_company_document_id=company.id,
        metadata_json={"source": "auto_match", "match_key": auto_match_key(req.id)},
    )
    db.add(old)
    db.commit()

    llm = FakeLlm()
    svc = RequirementMatchService(db, llm=llm)
    run = svc.start_matching(project.id, MatchStartRequest(force=True))

    def responder(_m):
        svc.cancel_run(project.id, run.id)
        return {
            "items": [
                _supported_item(
                    req,
                    chunk,
                    company_evidence_quote="本公司具备一级资质",
                    summary="新结果，需人工确认",
                )
            ]
        }

    llm._responder = responder
    svc.execute_run(run.id)
    assert svc.get_run(project.id, run.id).status == ExtractionRunStatus.cancelled
    still = db.get(RequirementEvidenceMatch, old.id)
    assert still is not None
    assert still.summary == "保留旧匹配"


def test_terminal_cancel_returns_error(db, client):
    project = _org_project(db, "CX5")
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
                    summary="材料含一级资质引文，需人工确认",
                )
            ]
        }
    )
    svc = RequirementMatchService(db, llm=llm)
    run = svc.start_matching(project.id, MatchStartRequest())
    svc.execute_run(run.id)
    assert svc.get_run(project.id, run.id).status == ExtractionRunStatus.succeeded

    with pytest.raises(Exception) as excinfo:
        svc.cancel_run(project.id, run.id)
    assert getattr(excinfo.value, "status_code", None) == 409

    resp = client.post(
        f"/api/v1/projects/{project.id}/requirement-matches/runs/{run.id}/cancel"
    )
    assert resp.status_code == 409
    assert "无法取消" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Goal 4: global atomic failure
# ---------------------------------------------------------------------------


def test_atomic_second_batch_bad_json_zero_writes(db, monkeypatch):
    import app.services.requirement_match_service as match_mod

    monkeypatch.setattr(match_mod, "BATCH_SIZE", 1)

    project = _org_project(db, "AT1")
    company = _doc(db, project, document_type=DocumentType.qualification, file_name="q.pdf")
    chunk = _chunk(db, project, company, index=0, content="本公司具备一级资质。")
    req_a = _requirement(db, project, normalized="投标人须具备一级资质。", title="A")
    req_b = _requirement(db, project, normalized="须提交营业执照复印件。", title="B")
    db.commit()

    calls = {"n": 0}

    def responder(messages):
        calls["n"] += 1
        payload = _match_payload(messages)
        rid = payload["requirements"][0]["requirement_id"]
        if calls["n"] == 1:
            req = req_a if rid == str(req_a.id) else req_b
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
        return "not-json{{{"

    llm = FakeLlm(responder)
    svc = RequirementMatchService(db, llm=llm)
    run = svc.start_matching(project.id, MatchStartRequest(force=False))
    svc.execute_run(run.id)
    refreshed = svc.get_run(project.id, run.id)
    assert refreshed.status == ExtractionRunStatus.failed
    assert (refreshed.config_json or {}).get("result_kind") == "invalid_or_incomplete_result"
    assert list(db.scalars(select(RequirementEvidenceMatch))) == []
    # No orphan match links
    assert list(db.scalars(select(RequirementEvidenceMatchLink))) == []


def test_atomic_force_false_failure_keeps_existing(db, monkeypatch):
    import app.services.requirement_match_service as match_mod

    monkeypatch.setattr(match_mod, "BATCH_SIZE", 1)

    project = _org_project(db, "AT2")
    company = _doc(db, project, document_type=DocumentType.qualification, file_name="q.pdf")
    chunk = _chunk(db, project, company, index=0, content="本公司具备一级资质。")
    req_a = _requirement(db, project, normalized="投标人须具备一级资质。", title="A")
    req_b = _requirement(db, project, normalized="须提交营业执照复印件。", title="B")
    old = RequirementEvidenceMatch(
        project_id=project.id,
        requirement_id=req_a.id,
        status=EvidenceMatchStatus.insufficient_evidence,
        summary="旧自动匹配",
        needs_review=True,
        risk_level=RiskLevel.high,
        metadata_json={"source": "auto_match", "match_key": auto_match_key(req_a.id)},
    )
    db.add(old)
    db.commit()

    calls = {"n": 0}

    def responder(_m):
        calls["n"] += 1
        if calls["n"] == 1:
            return {
                "items": [
                    _supported_item(
                        req_a,
                        chunk,
                        company_evidence_quote="本公司具备一级资质",
                        summary="材料含一级资质引文，需人工确认",
                    )
                ]
            }
        return "BAD"

    llm = FakeLlm(responder)
    svc = RequirementMatchService(db, llm=llm)
    run = svc.start_matching(project.id, MatchStartRequest(force=False))
    svc.execute_run(run.id)
    assert svc.get_run(project.id, run.id).status == ExtractionRunStatus.failed
    still = db.get(RequirementEvidenceMatch, old.id)
    assert still is not None
    assert still.summary == "旧自动匹配"
    assert db.scalar(
        select(RequirementEvidenceMatch).where(
            RequirementEvidenceMatch.requirement_id == req_b.id
        )
    ) is None


def test_atomic_force_true_failure_keeps_scoped_old_and_links(db, monkeypatch):
    import app.services.requirement_match_service as match_mod

    monkeypatch.setattr(match_mod, "BATCH_SIZE", 1)

    project = _org_project(db, "AT3")
    company = _doc(db, project, document_type=DocumentType.qualification, file_name="q.pdf")
    chunk = _chunk(db, project, company, index=0, content="本公司具备一级资质。")
    req_a = _requirement(db, project, normalized="投标人须具备一级资质。", title="A")
    req_b = _requirement(db, project, normalized="须提交营业执照复印件。", title="B")
    old = RequirementEvidenceMatch(
        project_id=project.id,
        requirement_id=req_a.id,
        status=EvidenceMatchStatus.supported,
        summary="旧force范围",
        needs_review=True,
        risk_level=RiskLevel.medium,
        primary_company_chunk_id=chunk.id,
        primary_company_document_id=company.id,
        primary_company_quote="本公司具备一级资质",
        metadata_json={"source": "auto_match", "match_key": auto_match_key(req_a.id)},
    )
    db.add(old)
    db.flush()
    old_link = RequirementEvidenceMatchLink(
        match_id=old.id,
        document_id=company.id,
        chunk_id=chunk.id,
        quote="本公司具备一级资质",
        role="company_support",
    )
    db.add(old_link)
    db.commit()
    old_link_id = old_link.id

    calls = {"n": 0}

    def responder(_m):
        calls["n"] += 1
        if calls["n"] == 1:
            return {
                "items": [
                    _supported_item(
                        req_a,
                        chunk,
                        company_evidence_quote="本公司具备一级资质",
                        summary="新结果，需人工确认",
                    )
                ]
            }
        return "{not valid"

    llm = FakeLlm(responder)
    svc = RequirementMatchService(db, llm=llm)
    run = svc.start_matching(project.id, MatchStartRequest(force=True))
    svc.execute_run(run.id)
    assert svc.get_run(project.id, run.id).status == ExtractionRunStatus.failed
    still = db.get(RequirementEvidenceMatch, old.id)
    assert still is not None
    assert still.summary == "旧force范围"
    assert db.get(RequirementEvidenceMatchLink, old_link_id) is not None
    _ = req_b


def test_atomic_all_ok_mix_supported_insufficient(db, monkeypatch):
    import app.services.requirement_match_service as match_mod

    monkeypatch.setattr(match_mod, "BATCH_SIZE", 1)

    project = _org_project(db, "AT4")
    company = _doc(db, project, document_type=DocumentType.qualification, file_name="q.pdf")
    chunk = _chunk(db, project, company, index=0, content="本公司具备一级资质。")
    req_a = _requirement(db, project, normalized="投标人须具备一级资质。", title="A")
    req_b = _requirement(db, project, normalized="须具备特级资质。", title="B")
    db.commit()

    def responder(messages):
        payload = _match_payload(messages)
        rid = payload["requirements"][0]["requirement_id"]
        if rid == str(req_a.id):
            return {
                "items": [
                    _supported_item(
                        req_a,
                        chunk,
                        company_evidence_quote="本公司具备一级资质",
                        summary="材料含一级资质引文，需人工确认",
                    )
                ]
            }
        return {
            "items": [
                {
                    "requirement_id": str(req_b.id),
                    "status": "insufficient_evidence",
                    "summary": "当前材料未找到充分证据",
                    "needs_review": True,
                }
            ]
        }

    llm = FakeLlm(responder)
    svc = RequirementMatchService(db, llm=llm)
    run = svc.start_matching(project.id, MatchStartRequest())
    svc.execute_run(run.id)
    refreshed = svc.get_run(project.id, run.id)
    assert refreshed.status == ExtractionRunStatus.succeeded
    assert refreshed.matched_count == 1
    assert refreshed.missing_evidence_count == 1
    matches = list(db.scalars(select(RequirementEvidenceMatch)))
    assert len(matches) == 2
    statuses = {m.status for m in matches}
    assert EvidenceMatchStatus.supported in statuses
    assert EvidenceMatchStatus.insufficient_evidence in statuses


def test_atomic_all_insufficient_succeeds(db):
    project = _org_project(db, "AT5")
    company = _doc(db, project, document_type=DocumentType.case, file_name="c.pdf")
    _chunk(db, project, company, index=0, content="近三年完成若干信息化项目。")
    req = _requirement(db, project, normalized="投标人须具备特级资质。")
    db.commit()

    llm = FakeLlm(
        lambda _m: {
            "items": [
                {
                    "requirement_id": str(req.id),
                    "status": "insufficient_evidence",
                    "summary": "当前材料未找到充分证据",
                    "needs_review": True,
                }
            ]
        }
    )
    svc = RequirementMatchService(db, llm=llm)
    run = svc.start_matching(project.id, MatchStartRequest())
    svc.execute_run(run.id)
    refreshed = svc.get_run(project.id, run.id)
    assert refreshed.status == ExtractionRunStatus.succeeded
    assert (refreshed.config_json or {}).get("result_kind") == (
        "valid_empty_or_insufficient_result"
    )
    match = db.scalar(select(RequirementEvidenceMatch))
    assert match is not None
    assert match.status == EvidenceMatchStatus.insufficient_evidence


def test_all_rejected_now_fails_both_force_modes(db):
    project = _org_project(db, "AT6")
    company = _doc(db, project, document_type=DocumentType.qualification, file_name="q.pdf")
    chunk = _chunk(db, project, company, index=0, content="本公司具备一级资质。")
    req = _requirement(db, project, normalized="投标人须具备一级资质。")
    db.commit()

    def bad(_m):
        return {
            "items": [
                _supported_item(
                    req,
                    chunk,
                    company_evidence_quote="完全不存在的引文",
                    summary="坏引文，需人工确认",
                )
            ]
        }

    for force in (False, True):
        llm = FakeLlm(bad)
        svc = RequirementMatchService(db, llm=llm)
        run = svc.start_matching(project.id, MatchStartRequest(force=force))
        svc.execute_run(run.id)
        refreshed = svc.get_run(project.id, run.id)
        assert refreshed.status == ExtractionRunStatus.failed
        assert (refreshed.config_json or {}).get("result_kind") == (
            "invalid_or_incomplete_result"
        )


# ---------------------------------------------------------------------------
# Gap coverage: mixed batch / missing / race / mock integration
# ---------------------------------------------------------------------------


def test_mixed_valid_invalid_fails_whole_run_zero_writes(db, monkeypatch):
    import app.services.requirement_match_service as match_mod

    monkeypatch.setattr(match_mod, "BATCH_SIZE", 4)
    project = _org_project(db, "MIX1")
    company = _doc(db, project, document_type=DocumentType.qualification, file_name="q.pdf")
    chunk = _chunk(db, project, company, index=0, content="本公司具备一级资质。")
    req_ok = _requirement(db, project, normalized="投标人须具备一级资质。", title="OK")
    req_bad = _requirement(db, project, normalized="须提交营业执照复印件。", title="BAD")
    db.commit()

    def responder(_m):
        return {
            "items": [
                _supported_item(
                    req_ok,
                    chunk,
                    company_evidence_quote="本公司具备一级资质",
                    summary="材料含一级资质引文，需人工确认",
                ),
                _supported_item(
                    req_bad,
                    chunk,
                    company_evidence_quote="完全不存在的引文XYZ",
                    summary="材料支持，需人工确认",
                ),
            ]
        }

    llm = FakeLlm(responder)
    svc = RequirementMatchService(db, llm=llm)
    run = svc.start_matching(project.id, MatchStartRequest())
    svc.execute_run(run.id)
    refreshed = svc.get_run(project.id, run.id)
    assert refreshed.status == ExtractionRunStatus.failed
    assert list(db.scalars(select(RequirementEvidenceMatch))) == []
    counts = (refreshed.config_json or {}).get("reject_reason_counts") or {}
    assert sum(counts.values()) >= 1


def test_missing_requirement_result_fails_whole_run(db):
    project = _org_project(db, "MISS1")
    company = _doc(db, project, document_type=DocumentType.qualification, file_name="q.pdf")
    chunk = _chunk(db, project, company, index=0, content="本公司具备一级资质。")
    req_a = _requirement(db, project, normalized="投标人须具备一级资质。", title="A")
    req_b = _requirement(db, project, normalized="须提交营业执照复印件。", title="B")
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
                # req_b omitted on purpose
            ]
        }
    )
    svc = RequirementMatchService(db, llm=llm)
    run = svc.start_matching(project.id, MatchStartRequest())
    svc.execute_run(run.id)
    refreshed = svc.get_run(project.id, run.id)
    assert refreshed.status == ExtractionRunStatus.failed
    assert list(db.scalars(select(RequirementEvidenceMatch))) == []
    counts = (refreshed.config_json or {}).get("reject_reason_counts") or {}
    assert counts.get("missing_requirement_result", 0) >= 1
    _ = req_b


def test_legacy_single_evidence_not_applicable_rejected(db):
    project = _org_project(db, "NA5")
    tender = _doc(db, project, document_type=DocumentType.tender, file_name="t.pdf")
    company = _doc(db, project, document_type=DocumentType.qualification, file_name="q.pdf")
    _chunk(db, project, company, index=0, content="本项目服务范围为朝阳区。")
    req = _requirement(
        db,
        project,
        normalized="本包件仅适用于海淀区范围内的项目。",
        source_document=tender,
    )
    t_chunk = _attach_tender_evidence(
        db,
        project,
        req,
        tender=tender,
        content="本包件仅适用于海淀区范围内的项目，其他区域不适用。",
    )
    db.commit()

    llm = FakeLlm(
        lambda _m: {
            "items": [
                {
                    "requirement_id": str(req.id),
                    "status": "not_applicable",
                    "summary": "旧单侧字段，待人工审核",
                    "needs_review": True,
                    "not_applicable_basis": "requirement_scope_exclusion",
                    "not_applicable_evidence_quote": (
                        "本包件仅适用于海淀区范围内的项目，其他区域不适用"
                    ),
                    "not_applicable_evidence_chunk_id": str(t_chunk.id),
                }
            ]
        }
    )
    svc = RequirementMatchService(db, llm=llm)
    run = svc.start_matching(project.id, MatchStartRequest())
    svc.execute_run(run.id)
    assert svc.get_run(project.id, run.id).status == ExtractionRunStatus.failed
    assert list(db.scalars(select(RequirementEvidenceMatch))) == []


def test_cancel_vs_persist_race_zero_writes(db):
    """Cancel wins during persist lock path → aborted, zero Match writes."""
    project = _org_project(db, "RACE1")
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
                    summary="材料含一级资质引文，需人工确认",
                )
            ]
        }
    )
    svc = RequirementMatchService(db, llm=llm)
    run = svc.start_matching(project.id, MatchStartRequest())

    real_persist = RequirementMatchService._persist_matches

    def racing_persist(self, *args, **kwargs):
        locked = self.db.get(RequirementMatchRun, run.id)
        assert locked is not None
        cfg = dict(locked.config_json or {})
        cfg["cancel_requested"] = True
        locked.config_json = cfg
        locked.status = ExtractionRunStatus.cancelled
        self.db.flush()
        return real_persist(self, *args, **kwargs)

    svc._persist_matches = racing_persist.__get__(svc, RequirementMatchService)  # type: ignore[method-assign]
    svc.execute_run(run.id)
    refreshed = svc.get_run(project.id, run.id)
    assert refreshed.status == ExtractionRunStatus.cancelled
    assert list(db.scalars(select(RequirementEvidenceMatch))) == []


def test_mock_llm_integration_matrix(db):
    """Mock-LLM coverage: supported / partial / conflict / NA / fabricated / mixed."""
    project = _org_project(db, "INT1")
    tender = _doc(db, project, document_type=DocumentType.tender, file_name="t.pdf")
    company = _doc(db, project, document_type=DocumentType.qualification, file_name="q.pdf")
    chunk_a = _chunk(
        db, project, company, index=0, content="本公司具备一级建筑业企业资质。"
    )
    chunk_b = _chunk(
        db, project, company, index=1, content="本公司仅具备二级建筑业企业资质。"
    )
    chunk_scope = _chunk(
        db, project, company, index=2, content="本项目服务范围为朝阳区。"
    )

    req_supported = _requirement(
        db, project, normalized="投标人须具备一级建筑业企业资质。", title="SUP"
    )
    req_partial = _requirement(
        db, project, normalized="投标人须具备特级建筑业企业资质。", title="PART"
    )
    req_conflict = _requirement(
        db, project, normalized="投标人须具备一级建筑业企业资质。", title="CF"
    )
    req_na = _requirement(
        db,
        project,
        normalized="本包件仅适用于海淀区范围内的项目。",
        title="NA",
        source_document=tender,
        category=RequirementCategory.commercial,
        mandatory=False,
        risk_level=RiskLevel.low,
    )
    t_chunk = _attach_tender_evidence(
        db,
        project,
        req_na,
        tender=tender,
        content="本包件仅适用于海淀区范围内的项目，其他区域不适用。",
    )
    db.commit()

    # --- supported ---
    llm = FakeLlm(
        lambda _m: {
            "items": [
                _supported_item(
                    req_supported,
                    chunk_a,
                    company_evidence_quote="本公司具备一级建筑业企业资质",
                    summary="材料含一级建筑业企业资质引文，需人工确认",
                )
            ]
        }
    )
    svc = RequirementMatchService(db, llm=llm)
    run = svc.start_matching(
        project.id, MatchStartRequest(requirement_ids=[req_supported.id], force=True)
    )
    svc.execute_run(run.id)
    assert svc.get_run(project.id, run.id).status == ExtractionRunStatus.succeeded
    m = db.scalar(
        select(RequirementEvidenceMatch).where(
            RequirementEvidenceMatch.requirement_id == req_supported.id
        )
    )
    assert m is not None and m.status == EvidenceMatchStatus.supported

    # --- grade partial ---
    llm = FakeLlm(
        lambda _m: {
            "items": [
                _supported_item(
                    req_partial,
                    chunk_a,
                    company_evidence_quote="本公司具备一级建筑业企业资质",
                    summary="材料显示一级建筑业企业资质，需人工确认",
                )
            ]
        }
    )
    svc = RequirementMatchService(db, llm=llm)
    run = svc.start_matching(
        project.id, MatchStartRequest(requirement_ids=[req_partial.id], force=True)
    )
    svc.execute_run(run.id)
    assert svc.get_run(project.id, run.id).status == ExtractionRunStatus.succeeded
    m = db.scalar(
        select(RequirementEvidenceMatch).where(
            RequirementEvidenceMatch.requirement_id == req_partial.id
        )
    )
    assert m is not None and m.status == EvidenceMatchStatus.partially_supported

    # --- legal conflict ---
    llm = FakeLlm(
        lambda _m: {
            "items": [
                {
                    "requirement_id": str(req_conflict.id),
                    "status": "conflicting_evidence",
                    "summary": "企业材料中一级与二级资质表述冲突，需人工确认",
                    "primary_company_chunk_id": str(chunk_a.id),
                    "company_evidence_quote": "本公司具备一级建筑业企业资质",
                    "conflicting_company_chunk_id": str(chunk_b.id),
                    "conflicting_company_evidence_quote": "本公司仅具备二级建筑业企业资质",
                    "conflict_dimension": "qualification_level",
                    "conflict_subject": "建筑业企业资质",
                    "primary_claim_value": "一级",
                    "conflicting_claim_value": "二级",
                    "needs_review": True,
                }
            ]
        }
    )
    svc = RequirementMatchService(db, llm=llm)
    run = svc.start_matching(
        project.id, MatchStartRequest(requirement_ids=[req_conflict.id], force=True)
    )
    svc.execute_run(run.id)
    assert svc.get_run(project.id, run.id).status == ExtractionRunStatus.succeeded
    m = db.scalar(
        select(RequirementEvidenceMatch).where(
            RequirementEvidenceMatch.requirement_id == req_conflict.id
        )
    )
    assert m is not None and m.status == EvidenceMatchStatus.conflicting_evidence

    # --- legal dual-scope NA ---
    llm = FakeLlm(
        lambda _m: {
            "items": [
                {
                    "requirement_id": str(req_na.id),
                    "status": "not_applicable",
                    "summary": "招标要求限定海淀区，当前对象服务范围为朝阳区，待人工审核",
                    "needs_review": True,
                    "not_applicable_basis": "requirement_scope_exclusion",
                    "requirement_scope_chunk_id": str(t_chunk.id),
                    "requirement_scope_quote": (
                        "本包件仅适用于海淀区范围内的项目，其他区域不适用"
                    ),
                    "current_scope_chunk_id": str(chunk_scope.id),
                    "current_scope_quote": "本项目服务范围为朝阳区",
                }
            ]
        }
    )
    svc = RequirementMatchService(db, llm=llm)
    run = svc.start_matching(
        project.id, MatchStartRequest(requirement_ids=[req_na.id], force=True)
    )
    svc.execute_run(run.id)
    assert svc.get_run(project.id, run.id).status == ExtractionRunStatus.succeeded
    m = db.scalar(
        select(RequirementEvidenceMatch).where(
            RequirementEvidenceMatch.requirement_id == req_na.id
        )
    )
    assert m is not None and m.status == EvidenceMatchStatus.not_applicable

    # --- fabricated quote fail ---
    before = list(db.scalars(select(RequirementEvidenceMatch)))
    before_ids = {row.id for row in before}
    llm = FakeLlm(
        lambda _m: {
            "items": [
                _supported_item(
                    req_supported,
                    chunk_a,
                    company_evidence_quote="这段话根本不存在",
                    summary="材料支持，需人工确认",
                )
            ]
        }
    )
    svc = RequirementMatchService(db, llm=llm)
    run = svc.start_matching(
        project.id, MatchStartRequest(requirement_ids=[req_supported.id], force=True)
    )
    svc.execute_run(run.id)
    assert svc.get_run(project.id, run.id).status == ExtractionRunStatus.failed
    after_ids = {
        row.id
        for row in db.scalars(select(RequirementEvidenceMatch))
    }
    assert after_ids == before_ids

    # --- mixed legal+illegal fail ---
    llm = FakeLlm(
        lambda _m: {
            "items": [
                _supported_item(
                    req_supported,
                    chunk_a,
                    company_evidence_quote="本公司具备一级建筑业企业资质",
                    summary="材料含一级建筑业企业资质引文，需人工确认",
                ),
                _supported_item(
                    req_partial,
                    chunk_a,
                    company_evidence_quote="不存在的引文",
                    summary="材料支持，需人工确认",
                ),
            ]
        }
    )
    svc = RequirementMatchService(db, llm=llm)
    run = svc.start_matching(
        project.id,
        MatchStartRequest(
            requirement_ids=[req_supported.id, req_partial.id], force=True
        ),
    )
    svc.execute_run(run.id)
    assert svc.get_run(project.id, run.id).status == ExtractionRunStatus.failed
    # Prior autos for those reqs retained under force failure
    assert (
        db.scalar(
            select(RequirementEvidenceMatch).where(
                RequirementEvidenceMatch.requirement_id == req_supported.id
            )
        )
        is not None
    )
