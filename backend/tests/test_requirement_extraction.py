"""Tests for Step 7: traceable tender requirement extraction."""

from __future__ import annotations

import json
from uuid import UUID, uuid4

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
    ExtractionRunStatus,
    ParseStatus,
    QualityLevel,
    RequirementCategory,
    ReviewStatus,
    RiskLevel,
)
from app.schemas.extraction import ExtractionStartRequest
from app.services import requirement_extraction_tasks
from app.services.llm_client import ChatResult, LlmUnavailableError
from app.services.requirement_extraction_service import (
    RequirementExtractionService,
    risk_for_category,
    stable_requirement_code,
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


def _org_project(db: Session, code: str = "EXT-001") -> BidProject:
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
    section: str | None = "第三章",
    clause_id: str | None = "3.1",
    page_start: int | None = 10,
    page_end: int | None = 10,
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


def _valid_item(chunk: DocumentChunk, **overrides) -> dict:
    quote = chunk.content[: min(40, len(chunk.content))]
    base = {
        "category": "qualification",
        "title": "投标人资质要求",
        "normalized_requirement": "投标人须具备建筑工程施工总承包一级资质",
        "mandatory": True,
        "score": None,
        "source_chunk_ids": [str(chunk.id)],
        "evidence_quote": quote,
        "source_section": chunk.section,
        "source_clause_id": chunk.clause_id,
        "source_page": chunk.page_start,
        "needs_review": False,
        "potential_conflict": False,
        "conflict_note": None,
    }
    base.update(overrides)
    return base


def select_reqs(db: Session, project_id: UUID):
    return select(Requirement).where(Requirement.project_id == project_id)


@pytest.fixture()
def task_factory(monkeypatch, engine):
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    monkeypatch.setattr(requirement_extraction_tasks, "SESSION_FACTORY", factory)
    return factory


def test_risk_rules():
    assert risk_for_category(RequirementCategory.invalid_bid) == RiskLevel.critical
    assert risk_for_category(RequirementCategory.mandatory) == RiskLevel.high
    assert risk_for_category(RequirementCategory.deadline) == RiskLevel.high
    assert risk_for_category(RequirementCategory.qualification) == RiskLevel.medium
    assert risk_for_category(RequirementCategory.project_info) == RiskLevel.low
    assert (
        risk_for_category(RequirementCategory.project_info, potential_conflict=True)
        == RiskLevel.high
    )


def test_only_allowed_doc_types_scanned(db):
    project = _org_project(db, "T1")
    tender = _doc(db, project, document_type=DocumentType.tender, file_name="t.pdf")
    company = _doc(db, project, document_type=DocumentType.company_profile, file_name="c.pdf")
    t_chunk = _chunk(db, project, tender, index=0, content="投标人须具备一级资质。")
    _chunk(db, project, company, index=0, content="本公司成立于1990年。")
    db.commit()

    seen_ids: list[str] = []

    def responder(messages):
        user = messages[-1]["content"]
        payload = json.loads(user.split("：\n", 1)[-1])
        for c in payload["chunks"]:
            seen_ids.append(c["chunk_id"])
        return {"items": [_valid_item(t_chunk)]}

    llm = FakeLlm(responder)
    svc = RequirementExtractionService(db, llm=llm)
    run = svc.start_extraction(project.id, ExtractionStartRequest())
    svc.execute_run(run.id)
    assert str(t_chunk.id) in seen_ids
    assert all(cid == str(t_chunk.id) for cid in seen_ids)


def test_company_docs_excluded(db):
    project = _org_project(db, "T2")
    company = _doc(db, project, document_type=DocumentType.qualification, file_name="q.pdf")
    _chunk(db, project, company, index=0, content="资质证书复印件。")
    db.commit()

    llm = FakeLlm()
    svc = RequirementExtractionService(db, llm=llm)
    run = svc.start_extraction(project.id, ExtractionStartRequest())
    svc.execute_run(run.id)
    assert llm.chat_calls == []
    refreshed = svc.get_run(project.id, run.id)
    assert refreshed.status == ExtractionRunStatus.succeeded
    assert refreshed.total_chunks == 0


def test_project_isolation(db):
    a = _org_project(db, "A")
    b = _org_project(db, "B")
    doc_a = _doc(db, a, document_type=DocumentType.tender, file_name="a.pdf")
    doc_b = _doc(db, b, document_type=DocumentType.tender, file_name="b.pdf")
    chunk_a = _chunk(db, a, doc_a, index=0, content="项目A要求具备一级资质。")
    chunk_b = _chunk(db, b, doc_b, index=0, content="项目B要求具备特级资质。")
    db.commit()

    seen: list[str] = []

    def responder(messages):
        user = messages[-1]["content"]
        payload = json.loads(user.split("：\n", 1)[-1])
        for c in payload["chunks"]:
            seen.append(c["chunk_id"])
        return {"items": [_valid_item(chunk_a, normalized_requirement="项目A要求具备一级资质")]}

    llm = FakeLlm(responder)
    svc = RequirementExtractionService(db, llm=llm)
    run = svc.start_extraction(a.id, ExtractionStartRequest())
    svc.execute_run(run.id)
    assert str(chunk_a.id) in seen
    assert str(chunk_b.id) not in seen


def test_valid_output_creates_requirement_and_evidence(db):
    project = _org_project(db, "T4")
    doc = _doc(db, project, document_type=DocumentType.tender, file_name="t.pdf")
    chunk = _chunk(
        db,
        project,
        doc,
        index=0,
        content="投标人须具备建筑工程施工总承包一级资质。",
    )
    db.commit()

    llm = FakeLlm(lambda _m: {"items": [_valid_item(chunk)]})
    svc = RequirementExtractionService(db, llm=llm)
    run = svc.start_extraction(project.id, ExtractionStartRequest())
    svc.execute_run(run.id)

    reqs = list(db.scalars(select_reqs(db, project.id)))
    assert len(reqs) == 1
    req = reqs[0]
    assert req.quality_level == QualityLevel.pending
    assert req.review_status == ReviewStatus.unreviewed
    assert req.risk_level == RiskLevel.medium
    assert req.metadata_json["source"] == "auto_extraction"
    links = list(db.scalars(select(EvidenceLink).where(EvidenceLink.requirement_id == req.id)))
    assert len(links) == 1
    assert links[0].chunk_id == chunk.id


def test_bad_quote_rejected(db):
    project = _org_project(db, "T5")
    doc = _doc(db, project, document_type=DocumentType.tender, file_name="t.pdf")
    chunk = _chunk(db, project, doc, index=0, content="投标人须具备一级资质。")
    db.commit()

    item = _valid_item(chunk, evidence_quote="这段引文根本不存在于原文中")
    llm = FakeLlm(lambda _m: {"items": [item]})
    svc = RequirementExtractionService(db, llm=llm)
    run = svc.start_extraction(project.id, ExtractionStartRequest())
    svc.execute_run(run.id)
    assert list(db.scalars(select_reqs(db, project.id))) == []


def test_unknown_chunk_id_rejected(db):
    project = _org_project(db, "T6")
    doc = _doc(db, project, document_type=DocumentType.tender, file_name="t.pdf")
    chunk = _chunk(db, project, doc, index=0, content="投标截止时间为2026年8月1日。")
    db.commit()

    item = _valid_item(
        chunk,
        category="deadline",
        title="投标截止时间",
        normalized_requirement="投标截止时间为2026年8月1日",
        source_chunk_ids=[str(uuid4())],
        evidence_quote="投标截止时间为2026年8月1日",
    )
    llm = FakeLlm(lambda _m: {"items": [item]})
    svc = RequirementExtractionService(db, llm=llm)
    run = svc.start_extraction(project.id, ExtractionStartRequest())
    svc.execute_run(run.id)
    assert list(db.scalars(select_reqs(db, project.id))) == []


def test_fabricated_page_section_rejected(db):
    project = _org_project(db, "T7")
    doc = _doc(db, project, document_type=DocumentType.tender, file_name="t.pdf")
    chunk = _chunk(
        db,
        project,
        doc,
        index=0,
        content="废标条件：未按要求密封。",
        section="第五章",
        clause_id="5.2",
        page_start=20,
        page_end=20,
    )
    db.commit()

    item = _valid_item(
        chunk,
        category="invalid_bid",
        title="废标条件",
        normalized_requirement="未按要求密封将作废标处理",
        source_page=999,
        source_section="不存在的章节",
        source_clause_id="9.9.9",
        evidence_quote="未按要求密封",
    )
    llm = FakeLlm(lambda _m: {"items": [item]})
    svc = RequirementExtractionService(db, llm=llm)
    run = svc.start_extraction(project.id, ExtractionStartRequest())
    svc.execute_run(run.id)
    assert list(db.scalars(select_reqs(db, project.id))) == []


def test_invalid_json_and_llm_error_surface(db):
    project = _org_project(db, "T8")
    doc = _doc(db, project, document_type=DocumentType.tender, file_name="t.pdf")
    _chunk(db, project, doc, index=0, content="技术参数：功率不小于100kW。")
    db.commit()

    llm = FakeLlm(lambda _m: "这不是 JSON")
    svc = RequirementExtractionService(db, llm=llm)
    run = svc.start_extraction(project.id, ExtractionStartRequest())
    svc.execute_run(run.id)
    refreshed = svc.get_run(project.id, run.id)
    assert refreshed.status == ExtractionRunStatus.failed
    assert refreshed.error_summary
    assert refreshed.failed_chunk_count >= 1

    llm2 = FakeLlm()
    llm2.raise_error = LlmUnavailableError("大模型服务不可用", detail="down")
    project2 = _org_project(db, "T8b")
    doc2 = _doc(db, project2, document_type=DocumentType.tender, file_name="t2.pdf")
    _chunk(db, project2, doc2, index=0, content="评分标准满分100分。")
    db.commit()
    svc2 = RequirementExtractionService(db, llm=llm2)
    run2 = svc2.start_extraction(project2.id, ExtractionStartRequest())
    svc2.execute_run(run2.id)
    r2 = svc2.get_run(project2.id, run2.id)
    assert r2.status == ExtractionRunStatus.failed
    assert r2.error_summary


def test_empty_docs_no_llm_call(db):
    project = _org_project(db, "T9")
    db.commit()
    llm = FakeLlm()
    svc = RequirementExtractionService(db, llm=llm)
    run = svc.start_extraction(project.id, ExtractionStartRequest())
    svc.execute_run(run.id)
    assert llm.chat_calls == []
    r = svc.get_run(project.id, run.id)
    assert r.status == ExtractionRunStatus.succeeded
    assert r.total_chunks == 0
    assert r.created_count == 0


def test_idempotent_rerun(db):
    project = _org_project(db, "T10")
    doc = _doc(db, project, document_type=DocumentType.tender, file_name="t.pdf")
    chunk = _chunk(db, project, doc, index=0, content="投标人须具备一级资质。")
    db.commit()

    llm = FakeLlm(lambda _m: {"items": [_valid_item(chunk)]})
    svc = RequirementExtractionService(db, llm=llm)
    run1 = svc.start_extraction(project.id, ExtractionStartRequest())
    svc.execute_run(run1.id)
    assert len(list(db.scalars(select_reqs(db, project.id)))) == 1

    run2 = svc.start_extraction(project.id, ExtractionStartRequest(force=False))
    svc.execute_run(run2.id)
    assert len(list(db.scalars(select_reqs(db, project.id)))) == 1


def test_force_replaces_only_auto(db):
    project = _org_project(db, "T11")
    doc = _doc(db, project, document_type=DocumentType.tender, file_name="t.pdf")
    chunk = _chunk(db, project, doc, index=0, content="投标人须具备一级资质。")
    manual = Requirement(
        project_id=project.id,
        source_document_id=doc.id,
        requirement_code="manual-001",
        category=RequirementCategory.commercial,
        title="手工要求",
        normalized_requirement="手工录入的商务要求",
        mandatory=True,
        risk_level=RiskLevel.medium,
        quality_level=QualityLevel.gold,
        review_status=ReviewStatus.reviewed,
        metadata_json={"source": "manual"},
    )
    db.add(manual)
    db.commit()

    llm = FakeLlm(lambda _m: {"items": [_valid_item(chunk)]})
    svc = RequirementExtractionService(db, llm=llm)
    run1 = svc.start_extraction(project.id, ExtractionStartRequest())
    svc.execute_run(run1.id)

    llm2 = FakeLlm(
        lambda _m: {
            "items": [
                _valid_item(
                    chunk,
                    normalized_requirement="投标人须具备建筑工程施工总承包特级资质",
                    evidence_quote="投标人须具备一级资质",
                )
            ]
        }
    )
    svc2 = RequirementExtractionService(db, llm=llm2)
    run2 = svc2.start_extraction(project.id, ExtractionStartRequest(force=True))
    svc2.execute_run(run2.id)

    rows = list(db.scalars(select_reqs(db, project.id)))
    sources = {(r.requirement_code, (r.metadata_json or {}).get("source")) for r in rows}
    assert ("manual-001", "manual") in sources
    autos = [r for r in rows if (r.metadata_json or {}).get("source") == "auto_extraction"]
    assert len(autos) == 1
    assert db.get(Requirement, manual.id) is not None


def test_merge_same_normalized_keeps_multiple_evidence(db):
    project = _org_project(db, "T12")
    doc = _doc(db, project, document_type=DocumentType.tender, file_name="t.pdf")
    c1 = _chunk(
        db,
        project,
        doc,
        index=0,
        content="投标人须具备建筑工程施工总承包一级资质。详见本章。",
        clause_id="3.1",
    )
    c2 = _chunk(
        db,
        project,
        doc,
        index=1,
        content="再次强调：投标人须具备建筑工程施工总承包一级资质。",
        clause_id="3.2",
        page_start=11,
        page_end=11,
    )
    db.commit()

    norm = "投标人须具备建筑工程施工总承包一级资质"

    def responder(messages):
        user = messages[-1]["content"]
        payload = json.loads(user.split("：\n", 1)[-1])
        ids = {c["chunk_id"] for c in payload["chunks"]}
        items = []
        if str(c1.id) in ids:
            items.append(
                _valid_item(
                    c1,
                    normalized_requirement=norm,
                    evidence_quote="投标人须具备建筑工程施工总承包一级资质",
                )
            )
        if str(c2.id) in ids:
            items.append(
                _valid_item(
                    c2,
                    normalized_requirement=norm,
                    evidence_quote="投标人须具备建筑工程施工总承包一级资质",
                    source_page=11,
                    source_clause_id="3.2",
                )
            )
        return {"items": items}

    llm = FakeLlm(responder)
    svc = RequirementExtractionService(db, llm=llm)
    run = svc.start_extraction(project.id, ExtractionStartRequest())
    svc.execute_run(run.id)
    reqs = list(db.scalars(select_reqs(db, project.id)))
    assert len(reqs) == 1
    links = list(db.scalars(select(EvidenceLink).where(EvidenceLink.requirement_id == reqs[0].id)))
    assert len(links) == 2
    r = svc.get_run(project.id, run.id)
    assert r.merged_count >= 1


def test_conflict_marked(db):
    project = _org_project(db, "T13")
    tender = _doc(db, project, document_type=DocumentType.tender, file_name="tender.pdf")
    amend = _doc(db, project, document_type=DocumentType.amendment, file_name="amend.pdf")
    c1 = _chunk(
        db,
        project,
        tender,
        index=0,
        content="投标保证金为人民币 10 万元。",
        clause_id="4.1",
        page_start=4,
        page_end=4,
    )
    c2 = _chunk(
        db,
        project,
        amend,
        index=0,
        content="投标保证金调整为人民币 20 万元。",
        clause_id="4.1",
        page_start=1,
        page_end=1,
        section="补遗一",
    )
    db.commit()

    def responder(messages):
        user = messages[-1]["content"]
        payload = json.loads(user.split("：\n", 1)[-1])
        items = []
        for c in payload["chunks"]:
            if c["chunk_id"] == str(c1.id):
                items.append(
                    _valid_item(
                        c1,
                        category="commercial",
                        title="投标保证金",
                        normalized_requirement="投标保证金为人民币10万元",
                        evidence_quote="投标保证金为人民币 10 万元",
                        source_page=4,
                        source_clause_id="4.1",
                        source_section="第三章",
                    )
                )
            if c["chunk_id"] == str(c2.id):
                items.append(
                    _valid_item(
                        c2,
                        category="commercial",
                        title="投标保证金",
                        normalized_requirement="投标保证金为人民币20万元",
                        evidence_quote="投标保证金调整为人民币 20 万元",
                        source_page=1,
                        source_clause_id="4.1",
                        source_section="补遗一",
                    )
                )
        return {"items": items}

    llm = FakeLlm(responder)
    svc = RequirementExtractionService(db, llm=llm)
    run = svc.start_extraction(project.id, ExtractionStartRequest())
    svc.execute_run(run.id)
    reqs = list(db.scalars(select_reqs(db, project.id)))
    assert len(reqs) == 2
    assert any((r.metadata_json or {}).get("potential_conflict") for r in reqs)
    assert all(
        r.risk_level in (RiskLevel.high, RiskLevel.critical)
        for r in reqs
        if (r.metadata_json or {}).get("potential_conflict")
    )
    r = svc.get_run(project.id, run.id)
    assert r.conflict_count >= 1


def test_api_list_filters_and_detail(client, db, task_factory, monkeypatch):
    project = _org_project(db, "T14")
    doc = _doc(db, project, document_type=DocumentType.tender, file_name="api.pdf")
    chunk = _chunk(
        db,
        project,
        doc,
        index=0,
        content="评分办法中技术分满分 40 分。",
    )
    db.commit()

    llm = FakeLlm(
        lambda _m: {
            "items": [
                _valid_item(
                    chunk,
                    category="scoring",
                    title="技术分",
                    normalized_requirement="技术分满分40分",
                    evidence_quote="技术分满分 40 分",
                    score="40",
                )
            ]
        }
    )
    monkeypatch.setattr(
        "app.services.requirement_extraction_service.get_llm_client",
        lambda: llm,
    )

    svc = RequirementExtractionService(db, llm=llm)
    run = svc.start_extraction(project.id, ExtractionStartRequest())
    svc.execute_run(run.id)

    listed = client.get(
        f"/api/v1/projects/{project.id}/requirements",
        params={"category": "scoring", "mandatory": True},
    )
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert body["total"] == 1
    req_id = body["items"][0]["id"]

    detail = client.get(f"/api/v1/projects/{project.id}/requirements/{req_id}")
    assert detail.status_code == 200, detail.text
    d = detail.json()
    assert d["evidence_links"]
    link = d["evidence_links"][0]
    assert link["document_file_name"] == "api.pdf"
    assert f"documentId={doc.id}" in link["document_center_path"]
    assert f"chunkId={chunk.id}" in link["document_center_path"]
    assert link["document_center_path"].startswith(f"/projects/{project.id}?tab=documents")


def test_run_status_stats_via_api(client, db, task_factory, monkeypatch):
    project = _org_project(db, "T15")
    doc = _doc(db, project, document_type=DocumentType.tender, file_name="s.pdf")
    chunk = _chunk(db, project, doc, index=0, content="投标人须具备一级资质。")
    db.commit()

    llm = FakeLlm(lambda _m: {"items": [_valid_item(chunk)]})
    monkeypatch.setattr(
        "app.services.requirement_extraction_service.get_llm_client",
        lambda: llm,
    )

    started = client.post(
        f"/api/v1/projects/{project.id}/requirements/extractions",
        json={"document_ids": [], "document_types": ["tender"], "force": False},
    )
    assert started.status_code == 201, started.text
    run_id = started.json()["id"]
    status_resp = client.get(f"/api/v1/projects/{project.id}/requirements/extractions/{run_id}")
    assert status_resp.status_code == 200
    body = status_resp.json()
    assert body["status"] == ExtractionRunStatus.succeeded.value
    assert body["total_chunks"] == 1
    assert body["processed_chunks"] == 1
    assert body["created_count"] >= 1
    assert body["candidate_count"] >= 1


def test_stable_requirement_code_deterministic():
    a = stable_requirement_code(RequirementCategory.qualification, "投标人须具备一级资质")
    b = stable_requirement_code(RequirementCategory.qualification, "  投标人须具备一级资质  ")
    assert a == b
    assert a.startswith("auto-qualification-")
