"""Tests for Step 10: auditable proposal drafting workspace."""

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
    MatchReviewStatus,
    ParseStatus,
    ProposalDraftGenerationMode,
    ProposalDraftStatus,
    ProposalDraftVersionKind,
    QualityLevel,
    RequirementCategory,
    ReviewStatus,
    RiskLevel,
)
from app.models.match_run import (
    RequirementEvidenceMatch,
    RequirementEvidenceMatchLink,
)
from app.models.proposal_draft import (
    ProposalDraft,
    ProposalDraftGenerationRun,
    ProposalDraftSource,
    ProposalDraftVersion,
)
from app.schemas.proposal_draft import (
    DISCLAIMER,
    UNEVIDENCED_MARKER,
    ProposalDraftCreateRequest,
    ProposalDraftManualRevisionRequest,
    ProposalDraftReopenRequest,
    ProposalDraftReviewRequest,
)
from app.services.llm_client import ChatResult
from app.services.proposal_draft_service import ProposalDraftService
from app.services.proposal_draft_validate import (
    DraftValidationError,
    content_has_unevidenced_manual,
)
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session


class FakeLlm:
    def __init__(self, responder=None, *, enabled: bool = True):
        self.enabled = enabled
        self.model = "fake-qwen"
        self.chat_calls: list = []
        self._responder = responder or (lambda messages: {"title": "x", "sections": []})

    def chat(self, messages, **kwargs):
        self.chat_calls.append({"messages": messages, **kwargs})
        payload = self._responder(messages)
        content = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
        return ChatResult(
            content=content,
            model=self.model,
            latency_ms=1.0,
            finish_reason="stop",
            request_id=kwargs.get("request_id") or "rid",
        )


def _org_project(db: Session, code: str = "PD-001") -> BidProject:
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
) -> DocumentChunk:
    chunk = DocumentChunk(
        document_id=document.id,
        project_id=project.id,
        chunk_index=index,
        content=content,
        section="企业资质",
        clause_id="Q.1",
        page_start=2,
        page_end=2,
    )
    db.add(chunk)
    db.flush()
    return chunk


def _requirement(
    db: Session,
    project: BidProject,
    *,
    title: str = "投标人资质要求",
) -> Requirement:
    req = Requirement(
        project_id=project.id,
        requirement_code=f"REQ-{uuid4().hex[:8]}",
        category=RequirementCategory.qualification,
        title=title,
        normalized_requirement="投标人须具备一级资质。",
        mandatory=True,
        risk_level=RiskLevel.medium,
        quality_level=QualityLevel.pending,
        review_status=ReviewStatus.unreviewed,
    )
    db.add(req)
    db.flush()
    return req


def _confirmed_match(
    db: Session,
    project: BidProject,
    req: Requirement,
    *,
    status: EvidenceMatchStatus,
    company_doc: Document,
    company_chunk: DocumentChunk,
    quote: str,
    extra_links: list[tuple[Document, DocumentChunk, str, str]] | None = None,
) -> RequirementEvidenceMatch:
    match = RequirementEvidenceMatch(
        project_id=project.id,
        requirement_id=req.id,
        status=status,
        summary="已确认材料结论",
        needs_review=False,
        risk_level=RiskLevel.medium,
        primary_company_document_id=company_doc.id,
        primary_company_chunk_id=company_chunk.id,
        primary_company_quote=quote,
        metadata_json={"source": "auto_match"},
        review_status=MatchReviewStatus.confirmed,
        is_review_protected=True,
        review_lock_version=1,
        lifecycle_status="active",
    )
    db.add(match)
    db.flush()
    db.add(
        RequirementEvidenceMatchLink(
            match_id=match.id,
            document_id=company_doc.id,
            chunk_id=company_chunk.id,
            quote=quote,
            role="company_support",
        )
    )
    for doc, chunk, q, role in extra_links or []:
        db.add(
            RequirementEvidenceMatchLink(
                match_id=match.id,
                document_id=doc.id,
                chunk_id=chunk.id,
                quote=q,
                role=role,
            )
        )
    # Tender evidence
    tender = _doc(
        db,
        project,
        document_type=DocumentType.tender,
        file_name=f"tender-{req.id.hex[:6]}.pdf",
    )
    tchunk = _chunk(db, project, tender, index=0, content="招标要求：须具备一级资质。")
    db.add(
        EvidenceLink(
            requirement_id=req.id,
            document_id=tender.id,
            chunk_id=tchunk.id,
            evidence_type="tender_clause",
            notes="招标要求：须具备一级资质。",
        )
    )
    db.flush()
    return match


def _load_whitelist_ids(svc: ProposalDraftService, project_id, req_ids):
    whitelist, _ = svc._build_whitelist(project_id, req_ids)
    return whitelist


def _good_llm_payload(whitelist, *, title: str = "响应准备草稿") -> dict:
    sections = []
    matrix = []
    warnings = []
    blocks_by_section: dict[str, list] = {"qualification": []}

    for rid, status in whitelist.requirement_match_status.items():
        cites = [c for c in whitelist.citations.values() if c.requirement_id == rid]
        company = [c for c in cites if c.source_role != "tender_requirement"]
        if status == EvidenceMatchStatus.supported:
            c0 = company[0]
            blocks_by_section["qualification"].append(
                {
                    "block_kind": "supported_response",
                    "requirement_ids": [str(rid)],
                    "content": "已具备可定位材料支撑的响应要点",
                    "citation_ids": [str(c0.citation_id)],
                    "source_quote_ids": [c0.quote_id],
                }
            )
            matrix.append(
                {
                    "requirement_id": str(rid),
                    "disposition": "responded",
                    "citation_ids": [str(c0.citation_id)],
                }
            )
        elif status == EvidenceMatchStatus.partially_supported:
            c0 = company[0]
            blocks_by_section["qualification"].append(
                {
                    "block_kind": "partial_response",
                    "requirement_ids": [str(rid)],
                    "content": "部分支撑，仍有缺口需补充有效期材料",
                    "citation_ids": [str(c0.citation_id)],
                    "source_quote_ids": [c0.quote_id],
                    "human_action": "补充证书有效期页",
                }
            )
            matrix.append(
                {
                    "requirement_id": str(rid),
                    "disposition": "partially_responded",
                    "citation_ids": [str(c0.citation_id)],
                }
            )
        elif status == EvidenceMatchStatus.insufficient_evidence:
            blocks_by_section["qualification"].append(
                {
                    "block_kind": "material_gap",
                    "requirement_ids": [str(rid)],
                    "content": "当前已确认材料不足，需补充证明",
                    "citation_ids": [],
                    "source_quote_ids": [],
                }
            )
            matrix.append(
                {
                    "requirement_id": str(rid),
                    "disposition": "material_gap",
                    "citation_ids": [],
                }
            )
            warnings.append(
                {
                    "requirement_id": str(rid),
                    "warning_type": "material_gap",
                    "content": "缺失可定位证明材料",
                    "citation_ids": [],
                }
            )
        elif status == EvidenceMatchStatus.conflicting_evidence:
            assert len(company) >= 2
            blocks_by_section["qualification"].append(
                {
                    "block_kind": "risk_item",
                    "requirement_ids": [str(rid)],
                    "content": "企业材料存在冲突，待人工核验",
                    "citation_ids": [str(company[0].citation_id), str(company[1].citation_id)],
                    "source_quote_ids": [company[0].quote_id, company[1].quote_id],
                }
            )
            matrix.append(
                {
                    "requirement_id": str(rid),
                    "disposition": "risk_review",
                    "citation_ids": [
                        str(company[0].citation_id),
                        str(company[1].citation_id),
                    ],
                }
            )
            warnings.append(
                {
                    "requirement_id": str(rid),
                    "warning_type": "conflicting_evidence",
                    "content": "冲突证据两侧均已保留",
                    "citation_ids": [
                        str(company[0].citation_id),
                        str(company[1].citation_id),
                    ],
                }
            )
        elif status == EvidenceMatchStatus.not_applicable:
            assert len(company) >= 2
            blocks_by_section["qualification"].append(
                {
                    "block_kind": "scope_item",
                    "requirement_ids": [str(rid)],
                    "content": "范围待核验，不作为正向响应",
                    "citation_ids": [str(company[0].citation_id), str(company[1].citation_id)],
                    "source_quote_ids": [company[0].quote_id, company[1].quote_id],
                }
            )
            matrix.append(
                {
                    "requirement_id": str(rid),
                    "disposition": "scope_review",
                    "citation_ids": [
                        str(company[0].citation_id),
                        str(company[1].citation_id),
                    ],
                }
            )
            warnings.append(
                {
                    "requirement_id": str(rid),
                    "warning_type": "scope_exclusion",
                    "content": "双侧范围证据已保留",
                    "citation_ids": [
                        str(company[0].citation_id),
                        str(company[1].citation_id),
                    ],
                }
            )

    for rid in whitelist.excluded_requirement_ids:
        matrix.append(
            {
                "requirement_id": str(rid),
                "disposition": "excluded",
                "citation_ids": [],
            }
        )
        warnings.append(
            {
                "requirement_id": str(rid),
                "warning_type": "pending_review",
                "content": "未确认匹配，不进入正向正文",
                "citation_ids": [],
            }
        )

    for key, blocks in blocks_by_section.items():
        if blocks:
            sections.append({"section_key": key, "title": "资格与证明材料", "blocks": blocks})
    if not sections:
        sections.append(
            {
                "section_key": "empty",
                "title": "待处理",
                "blocks": [
                    {
                        "block_kind": "material_gap",
                        "requirement_ids": [str(next(iter(whitelist.requirement_ids)))],
                        "content": "无可用正向内容",
                        "citation_ids": [],
                        "source_quote_ids": [],
                    }
                ],
            }
        )
    return {
        "title": title,
        "sections": sections,
        "compliance_matrix": matrix,
        "warnings": warnings,
    }


@pytest.fixture()
def company_pack(db: Session):
    project = _org_project(db)
    doc = _doc(db, project, document_type=DocumentType.qualification, file_name="qual.pdf")
    chunk = _chunk(db, project, doc, index=0, content="本公司具备一级资质证书。")
    doc2 = _doc(db, project, document_type=DocumentType.qualification, file_name="qual2.pdf")
    chunk2 = _chunk(db, project, doc2, index=0, content="本公司仅为二级资质。")
    return project, doc, chunk, doc2, chunk2


def test_eligibility_routing_by_match_status(db: Session, company_pack):
    project, doc, chunk, doc2, chunk2 = company_pack
    cases = [
        (EvidenceMatchStatus.supported, "positive"),
        (EvidenceMatchStatus.partially_supported, "positive"),
        (EvidenceMatchStatus.insufficient_evidence, "material_gap"),
        (EvidenceMatchStatus.conflicting_evidence, "risk"),
        (EvidenceMatchStatus.not_applicable, "scope"),
    ]
    reqs = []
    for status, _ in cases:
        req = _requirement(db, project, title=status.value)
        extra = None
        if status in (
            EvidenceMatchStatus.conflicting_evidence,
            EvidenceMatchStatus.not_applicable,
        ):
            role = (
                "company_conflict"
                if status == EvidenceMatchStatus.conflicting_evidence
                else "company_scope_exclusion"
            )
            extra = [(doc2, chunk2, chunk2.content, role)]
        _confirmed_match(
            db,
            project,
            req,
            status=status,
            company_doc=doc,
            company_chunk=chunk,
            quote=chunk.content,
            extra_links=extra,
        )
        reqs.append(req)

    pending_req = _requirement(db, project, title="pending")
    match = RequirementEvidenceMatch(
        project_id=project.id,
        requirement_id=pending_req.id,
        status=EvidenceMatchStatus.supported,
        summary="pending",
        needs_review=True,
        risk_level=RiskLevel.medium,
        review_status=MatchReviewStatus.pending,
        lifecycle_status="active",
    )
    db.add(match)
    db.commit()

    elig = ProposalDraftService(db).eligibility(project.id)
    by_id = {
        i.requirement_id: i
        for i in (
            elig.eligible + elig.excluded + elig.material_gaps + elig.risks + elig.scope_items
        )
    }
    for req, (_status, bucket) in zip(reqs, cases, strict=True):
        assert by_id[req.id].eligibility == bucket
    assert by_id[pending_req.id].eligibility == "excluded"


def test_successful_generation_atomic_persist(db: Session, company_pack, monkeypatch):
    project, doc, chunk, _, _ = company_pack
    req = _requirement(db, project)
    match = _confirmed_match(
        db,
        project,
        req,
        status=EvidenceMatchStatus.supported,
        company_doc=doc,
        company_chunk=chunk,
        quote=chunk.content,
    )
    db.commit()

    svc = ProposalDraftService(db)

    def responder(messages):
        wl, _ = svc._build_whitelist(project.id, [req.id])
        return _good_llm_payload(wl)

    llm = FakeLlm(responder)
    svc.llm = llm
    run = svc.start_generation(
        project.id,
        ProposalDraftCreateRequest(
            title="合成草稿A",
            requirement_ids=[req.id],
            mode=ProposalDraftGenerationMode.response_outline,
        ),
    )
    svc.execute_run(run.id)
    run = db.get(ProposalDraftGenerationRun, run.id)
    assert run.status == ExtractionRunStatus.succeeded
    assert run.draft_id is not None
    assert run.draft_version_id is not None
    draft = db.get(ProposalDraft, run.draft_id)
    assert draft is not None
    assert draft.status == ProposalDraftStatus.draft_pending_review
    version = db.get(ProposalDraftVersion, run.draft_version_id)
    assert version is not None
    assert version.version_kind == ProposalDraftVersionKind.generated
    assert version.is_current
    sources = list(
        db.scalars(
            select(ProposalDraftSource).where(ProposalDraftSource.draft_version_id == version.id)
        )
    )
    assert sources
    assert any(s.match_id == match.id for s in sources)
    assert DISCLAIMER in (version.content_markdown or "")


def test_forged_citation_fails_zero_version(db: Session, company_pack):
    project, doc, chunk, _, _ = company_pack
    req = _requirement(db, project)
    _confirmed_match(
        db,
        project,
        req,
        status=EvidenceMatchStatus.supported,
        company_doc=doc,
        company_chunk=chunk,
        quote=chunk.content,
    )
    db.commit()
    svc = ProposalDraftService(db)

    def responder(messages):
        return {
            "title": "bad",
            "sections": [
                {
                    "section_key": "q",
                    "title": "资格",
                    "blocks": [
                        {
                            "block_kind": "supported_response",
                            "requirement_ids": [str(req.id)],
                            "content": "伪造引用",
                            "citation_ids": [str(uuid4())],
                            "source_quote_ids": ["q_invented"],
                        }
                    ],
                }
            ],
            "compliance_matrix": [],
            "warnings": [],
        }

    svc.llm = FakeLlm(responder)
    run = svc.start_generation(
        project.id,
        ProposalDraftCreateRequest(title="坏草稿", requirement_ids=[req.id]),
    )
    before_versions = db.scalar(select(func.count()).select_from(ProposalDraftVersion))
    before_sources = db.scalar(select(func.count()).select_from(ProposalDraftSource))
    svc.execute_run(run.id)
    run = db.get(ProposalDraftGenerationRun, run.id)
    assert run.status == ExtractionRunStatus.failed
    assert "校验" in (run.error_summary or "")
    assert db.scalar(select(func.count()).select_from(ProposalDraftVersion)) == before_versions
    assert db.scalar(select(func.count()).select_from(ProposalDraftSource)) == before_sources


def test_cancel_wins_over_persist(db: Session, company_pack):
    project, doc, chunk, _, _ = company_pack
    req = _requirement(db, project)
    _confirmed_match(
        db,
        project,
        req,
        status=EvidenceMatchStatus.supported,
        company_doc=doc,
        company_chunk=chunk,
        quote=chunk.content,
    )
    db.commit()
    svc = ProposalDraftService(db)
    gate = {"cancelled": False}

    def responder(messages):
        # Simulate cancel during LLM
        svc.cancel_run(project.id, run.id)
        gate["cancelled"] = True
        wl, _ = svc._build_whitelist(project.id, [req.id])
        return _good_llm_payload(wl)

    svc.llm = FakeLlm(responder)
    run = svc.start_generation(
        project.id,
        ProposalDraftCreateRequest(title="取消竞争", requirement_ids=[req.id]),
    )
    svc.execute_run(run.id)
    run = db.get(ProposalDraftGenerationRun, run.id)
    assert gate["cancelled"]
    assert run.status == ExtractionRunStatus.cancelled
    assert run.draft_version_id is None
    assert db.scalar(select(func.count()).select_from(ProposalDraftVersion)) == 0


def test_cross_project_requirement_rejected(db: Session, company_pack):
    project, doc, chunk, _, _ = company_pack
    other = _org_project(db, code="PD-OTHER")
    req_other = _requirement(db, other)
    other_doc = _doc(db, other, document_type=DocumentType.qualification, file_name="o.pdf")
    other_chunk = _chunk(db, other, other_doc, index=0, content="other")
    _confirmed_match(
        db,
        other,
        req_other,
        status=EvidenceMatchStatus.supported,
        company_doc=other_doc,
        company_chunk=other_chunk,
        quote="other",
    )
    req = _requirement(db, project)
    _confirmed_match(
        db,
        project,
        req,
        status=EvidenceMatchStatus.supported,
        company_doc=doc,
        company_chunk=chunk,
        quote=chunk.content,
    )
    db.commit()
    svc = ProposalDraftService(db)
    with pytest.raises(DraftValidationError):
        svc._build_whitelist(project.id, [req.id, req_other.id])


def test_manual_revision_review_reopen_export(db: Session, company_pack, client: TestClient):
    project, doc, chunk, _, _ = company_pack
    req = _requirement(db, project)
    _confirmed_match(
        db,
        project,
        req,
        status=EvidenceMatchStatus.supported,
        company_doc=doc,
        company_chunk=chunk,
        quote=chunk.content,
    )
    db.commit()
    svc = ProposalDraftService(db)

    def responder(messages):
        wl, _ = svc._build_whitelist(project.id, [req.id])
        return _good_llm_payload(wl)

    svc.llm = FakeLlm(responder)
    run = svc.start_generation(
        project.id,
        ProposalDraftCreateRequest(title="审核导出", requirement_ids=[req.id]),
    )
    svc.execute_run(run.id)
    run = db.get(ProposalDraftGenerationRun, run.id)
    draft_id = run.draft_id
    assert draft_id

    detail = svc.get_draft(project.id, draft_id)
    content = dict(detail.current_version.content_json)
    # Add unevidenced manual block
    content["sections"][0]["blocks"].append(
        {
            "block_kind": "manual_unreferenced",
            "requirement_ids": [str(req.id)],
            "content": f"补充说明（{UNEVIDENCED_MARKER}）",
            "citation_ids": [],
            "source_quote_ids": [],
        }
    )
    revised = svc.create_manual_revision(
        project.id,
        draft_id,
        ProposalDraftManualRevisionRequest(content_json=content, created_by="editor-1"),
    )
    assert revised.current_version.version_number == 2
    assert revised.current_version.version_kind == ProposalDraftVersionKind.manual_revision
    assert revised.has_unevidenced_manual_content

    with pytest.raises(HTTPException) as exc:
        svc.mark_reviewed(
            project.id,
            draft_id,
            ProposalDraftReviewRequest(
                actor_label="reviewer",
                comment="确认",
                review_lock_version=revised.review_lock_version,
            ),
        )
    assert exc.value.status_code == 422

    # Remove unevidenced block and review
    clean = dict(revised.current_version.content_json)
    clean["sections"][0]["blocks"] = [
        b for b in clean["sections"][0]["blocks"] if b.get("block_kind") != "manual_unreferenced"
    ]
    clean.pop("has_unevidenced_manual_content", None)
    cleaned = svc.create_manual_revision(
        project.id,
        draft_id,
        ProposalDraftManualRevisionRequest(content_json=clean, created_by="editor-1"),
    )
    assert not content_has_unevidenced_manual(cleaned.current_version.content_json)

    reviewed = svc.mark_reviewed(
        project.id,
        draft_id,
        ProposalDraftReviewRequest(
            actor_label="reviewer",
            comment="材料已复核",
            review_lock_version=cleaned.review_lock_version,
        ),
    )
    assert reviewed.status == ProposalDraftStatus.reviewed

    with pytest.raises(HTTPException):
        svc.create_manual_revision(
            project.id,
            draft_id,
            ProposalDraftManualRevisionRequest(content_json=clean, created_by="editor-1"),
        )

    md_body, md_type, md_name = svc.export(project.id, draft_id, fmt="markdown")
    assert "text/markdown" in md_type
    assert md_name.endswith(".md")
    assert DISCLAIMER.encode("utf-8") in md_body

    docx_body, docx_type, docx_name = svc.export(project.id, draft_id, fmt="docx")
    assert "wordprocessingml" in docx_type
    assert docx_name.endswith(".docx")
    assert docx_body[:2] == b"PK"  # zip/docx magic

    reopened = svc.reopen(
        project.id,
        draft_id,
        ProposalDraftReopenRequest(
            actor_label="reviewer",
            comment="需要补充一处表述",
            review_lock_version=reviewed.review_lock_version,
        ),
    )
    assert reopened.status == ProposalDraftStatus.reopened
    versions = svc.list_versions(project.id, draft_id)
    assert versions.total >= 3


def test_snapshot_survives_match_supersede(db: Session, company_pack):
    project, doc, chunk, _, _ = company_pack
    req = _requirement(db, project)
    match = _confirmed_match(
        db,
        project,
        req,
        status=EvidenceMatchStatus.supported,
        company_doc=doc,
        company_chunk=chunk,
        quote=chunk.content,
    )
    db.commit()
    svc = ProposalDraftService(db)
    svc.llm = FakeLlm(
        lambda messages: _good_llm_payload(svc._build_whitelist(project.id, [req.id])[0])
    )
    run = svc.start_generation(
        project.id,
        ProposalDraftCreateRequest(title="快照", requirement_ids=[req.id]),
    )
    svc.execute_run(run.id)
    run = db.get(ProposalDraftGenerationRun, run.id)
    version_id = run.draft_version_id
    sources_before = [
        (s.evidence_link_id, s.source_quote)
        for s in db.scalars(
            select(ProposalDraftSource).where(ProposalDraftSource.draft_version_id == version_id)
        )
    ]
    # Supersede match (simulate step 9 reopen successor)
    match.lifecycle_status = "superseded"
    match.review_status = MatchReviewStatus.pending
    db.commit()
    sources_after = [
        (s.evidence_link_id, s.source_quote)
        for s in db.scalars(
            select(ProposalDraftSource).where(ProposalDraftSource.draft_version_id == version_id)
        )
    ]
    assert sources_before == sources_after
    assert sources_before


def test_integration_matrix_all_statuses(db: Session, company_pack):
    project, doc, chunk, doc2, chunk2 = company_pack
    statuses = [
        EvidenceMatchStatus.supported,
        EvidenceMatchStatus.partially_supported,
        EvidenceMatchStatus.insufficient_evidence,
        EvidenceMatchStatus.conflicting_evidence,
        EvidenceMatchStatus.not_applicable,
    ]
    req_ids = []
    for st in statuses:
        req = _requirement(db, project, title=st.value)
        extra = None
        if st in (
            EvidenceMatchStatus.conflicting_evidence,
            EvidenceMatchStatus.not_applicable,
        ):
            role = (
                "company_conflict"
                if st == EvidenceMatchStatus.conflicting_evidence
                else "company_scope_exclusion"
            )
            extra = [(doc2, chunk2, chunk2.content, role)]
        _confirmed_match(
            db,
            project,
            req,
            status=st,
            company_doc=doc,
            company_chunk=chunk,
            quote=chunk.content,
            extra_links=extra,
        )
        req_ids.append(req.id)

    pending = _requirement(db, project, title="pending-x")
    db.add(
        RequirementEvidenceMatch(
            project_id=project.id,
            requirement_id=pending.id,
            status=EvidenceMatchStatus.supported,
            summary="p",
            needs_review=True,
            risk_level=RiskLevel.low,
            review_status=MatchReviewStatus.pending,
            lifecycle_status="active",
        )
    )
    rejected = _requirement(db, project, title="rejected-x")
    db.add(
        RequirementEvidenceMatch(
            project_id=project.id,
            requirement_id=rejected.id,
            status=EvidenceMatchStatus.supported,
            summary="r",
            needs_review=False,
            risk_level=RiskLevel.low,
            review_status=MatchReviewStatus.rejected,
            lifecycle_status="active",
        )
    )
    db.commit()
    all_ids = req_ids + [pending.id, rejected.id]
    svc = ProposalDraftService(db)
    svc.llm = FakeLlm(
        lambda messages: _good_llm_payload(svc._build_whitelist(project.id, all_ids)[0])
    )
    run = svc.start_generation(
        project.id,
        ProposalDraftCreateRequest(
            title="矩阵草稿",
            requirement_ids=all_ids,
            mode=ProposalDraftGenerationMode.compliance_preparation_pack,
        ),
    )
    svc.execute_run(run.id)
    run = db.get(ProposalDraftGenerationRun, run.id)
    assert run.status == ExtractionRunStatus.succeeded
    version = db.get(ProposalDraftVersion, run.draft_version_id)
    kinds = {b["block_kind"] for s in version.content_json["sections"] for b in s["blocks"]}
    assert "supported_response" in kinds
    assert "partial_response" in kinds
    assert "material_gap" in kinds
    assert "risk_item" in kinds
    assert "scope_item" in kinds
    dispositions = {r["disposition"] for r in version.content_json["compliance_matrix"]}
    assert "excluded" in dispositions


def test_api_list_and_idempotency(db: Session, company_pack, client: TestClient, monkeypatch):
    project, doc, chunk, _, _ = company_pack
    req = _requirement(db, project)
    _confirmed_match(
        db,
        project,
        req,
        status=EvidenceMatchStatus.supported,
        company_doc=doc,
        company_chunk=chunk,
        quote=chunk.content,
    )
    db.commit()

    # Prevent background task; run sync in test
    monkeypatch.setattr(
        "app.services.proposal_draft_tasks.run_proposal_draft_generation",
        lambda run_id: None,
    )

    key = f"idem-{uuid4().hex}"
    payload = {
        "title": "API草稿",
        "requirement_ids": [str(req.id)],
        "mode": "response_outline",
    }
    r1 = client.post(
        f"/api/v1/projects/{project.id}/proposal-drafts",
        json=payload,
        headers={"Idempotency-Key": key},
    )
    assert r1.status_code == 201, r1.text
    r2 = client.post(
        f"/api/v1/projects/{project.id}/proposal-drafts",
        json=payload,
        headers={"Idempotency-Key": key},
    )
    assert r2.status_code == 201
    assert r1.json()["id"] == r2.json()["id"]

    r3 = client.post(
        f"/api/v1/projects/{project.id}/proposal-drafts",
        json={**payload, "title": "不同载荷"},
        headers={"Idempotency-Key": key},
    )
    assert r3.status_code == 409

    elig = client.get(f"/api/v1/projects/{project.id}/proposal-drafts/eligibility")
    assert elig.status_code == 200
    assert DISCLAIMER in elig.json()["disclaimer"]

    listing = client.get(f"/api/v1/projects/{project.id}/proposal-drafts")
    assert listing.status_code == 200


def test_wrong_project_draft_404(db: Session, company_pack, client: TestClient):
    project, doc, chunk, _, _ = company_pack
    other = _org_project(db, code="PD-ISO")
    req = _requirement(db, project)
    _confirmed_match(
        db,
        project,
        req,
        status=EvidenceMatchStatus.supported,
        company_doc=doc,
        company_chunk=chunk,
        quote=chunk.content,
    )
    db.commit()
    svc = ProposalDraftService(db)
    svc.llm = FakeLlm(
        lambda messages: _good_llm_payload(svc._build_whitelist(project.id, [req.id])[0])
    )
    run = svc.start_generation(
        project.id,
        ProposalDraftCreateRequest(title="隔离", requirement_ids=[req.id]),
    )
    svc.execute_run(run.id)
    run = db.get(ProposalDraftGenerationRun, run.id)
    resp = client.get(f"/api/v1/projects/{other.id}/proposal-drafts/{run.draft_id}")
    assert resp.status_code == 404
