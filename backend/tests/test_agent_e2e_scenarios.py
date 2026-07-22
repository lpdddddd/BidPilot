"""Five E2E scenarios for BidPilot LangGraph agent loop (FakeLlm + fake retrieval)."""

from __future__ import annotations

import json
import sys
from uuid import uuid4

from app.models import BidProject, Document, Organization, Requirement
from app.models.document import DocumentChunk
from app.models.enums import (
    DocumentType,
    EvidenceMatchStatus,
    MatchReviewStatus,
    ParseStatus,
    QualityLevel,
    RequirementCategory,
    ReviewStatus,
    RiskLevel,
)
from app.models.match_run import RequirementEvidenceMatch, RequirementEvidenceMatchLink
from app.schemas.agent_run import AgentRunStartRequest
from app.schemas.search import (
    RetrievalTrace,
    SearchRequest,
    SearchResponse,
    SearchResultItem,
    StageLatency,
)
from app.services.agent_run.service import AgentRunService
from app.services.llm_client import ChatResult
from sqlalchemy import func, select
from sqlalchemy.orm import Session


class FakeLlm:
    def __init__(self, responder=None, *, enabled: bool = True):
        self.enabled = enabled
        self.model = "fake-qwen"
        self.chat_calls: list = []
        self._responder = responder or (lambda messages: {"items": []})

    def chat(self, messages, **kwargs):
        self.chat_calls.append({"messages": messages, **kwargs})
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


def _fake_retrieval(project_id, request: SearchRequest) -> SearchResponse:
    return SearchResponse(
        query=request.query,
        results=[
            SearchResultItem(
                rank=1,
                chunk_id=str(uuid4()),
                document_id=str(uuid4()),
                file_name="tender.pdf",
                document_type="tender",
                chunk_index=0,
                section="资格条件",
                clause_id="3.1",
                page_start=2,
                page_end=2,
                content="投标人须具备建筑施工总承包一级资质",
                content_hash="h1",
                source_sha256=None,
                chunker_version=None,
                dense_rank=1,
                dense_score=0.9,
                bm25_rank=1,
                bm25_score=1.2,
                rrf_score=0.6,
                rerank_score=0.85,
            )
        ],
        trace=RetrievalTrace(
            dense_candidate_count=1,
            bm25_candidate_count=1,
            fused_candidate_count=1,
            returned_count=1,
            embedding_model="fake",
            reranker_model=None,
            qdrant_collection="c",
            opensearch_index="i",
            rrf_k=60,
            latency=StageLatency(
                embed_ms=0, dense_ms=0, bm25_ms=0, fusion_ms=0, rerank_ms=0, total_ms=0
            ),
        ),
    )


def _seed(
    db: Session,
    *,
    match_status: EvidenceMatchStatus = EvidenceMatchStatus.supported,
) -> tuple[BidProject, Requirement, RequirementEvidenceMatch]:
    org = Organization(name=f"Org-{uuid4().hex[:6]}")
    db.add(org)
    db.flush()
    project = BidProject(
        organization_id=org.id,
        project_code=f"E2E-{uuid4().hex[:4]}",
        project_name="Agent E2E",
    )
    db.add(project)
    db.flush()
    tender = Document(
        project_id=project.id,
        organization_id=org.id,
        document_type=DocumentType.tender,
        file_name="tender.pdf",
        storage_bucket="b",
        storage_key="k1",
        parse_status=ParseStatus.success,
        is_scanned=False,
    )
    company = Document(
        project_id=project.id,
        organization_id=org.id,
        document_type=DocumentType.qualification,
        file_name="qual.pdf",
        storage_bucket="b",
        storage_key="k2",
        parse_status=ParseStatus.success,
        is_scanned=False,
    )
    db.add_all([tender, company])
    db.flush()
    db.add(
        DocumentChunk(
            document_id=tender.id,
            project_id=project.id,
            chunk_index=0,
            content="投标人须具备建筑施工总承包一级资质",
            content_hash="c1",
        )
    )
    company_chunk = DocumentChunk(
        document_id=company.id,
        project_id=project.id,
        chunk_index=0,
        content="本公司持有建筑施工总承包一级资质证书",
        content_hash="c2",
    )
    db.add(company_chunk)
    req = Requirement(
        project_id=project.id,
        category=RequirementCategory.qualification,
        title="建筑施工总承包一级资质",
        mandatory=True,
        risk_level=RiskLevel.critical,
        quality_level=QualityLevel.pending,
        review_status=ReviewStatus.unreviewed,
    )
    db.add(req)
    db.flush()
    match = RequirementEvidenceMatch(
        project_id=project.id,
        requirement_id=req.id,
        status=match_status,
        review_status=MatchReviewStatus.confirmed,
        lifecycle_status="active",
        summary="seeded match",
    )
    db.add(match)
    db.flush()
    if match_status in {
        EvidenceMatchStatus.supported,
        EvidenceMatchStatus.partially_supported,
    }:
        db.add(
            RequirementEvidenceMatchLink(
                match_id=match.id,
                document_id=company.id,
                chunk_id=company_chunk.id,
                quote="一级资质证书",
                role="company_support",
            )
        )
    db.commit()
    return project, req, match


def _svc(db: Session) -> AgentRunService:
    return AgentRunService(db, llm=FakeLlm(), retrieval_fn=_fake_retrieval)


def test_e2e_1_evidence_sufficient_completed(db: Session):
    project, req, _ = _seed(db, match_status=EvidenceMatchStatus.supported)
    run = _svc(db).start_run(
        project.id,
        AgentRunStartRequest(
            user_request="分析资质要求",
            requested_requirement_ids=[req.id],
            metadata={
                "force_critical_qualification": False,
                "force_risk_only_draft": True,
                "force_draft_validation": True,
                "allow_empty_draft": True,
            },
        ),
        idempotency_key=f"e2e1-{uuid4().hex}",
    )
    assert run.status.value in {"completed", "completed_with_warnings"}
    assert run.state is not None
    assert run.state.retrieved_chunks


def test_e2e_2_company_evidence_insufficient_warnings(db: Session):
    project, req, _ = _seed(db, match_status=EvidenceMatchStatus.insufficient_evidence)
    run = _svc(db).start_run(
        project.id,
        AgentRunStartRequest(
            user_request="匹配企业材料",
            requested_requirement_ids=[req.id],
            metadata={
                "force_critical_qualification": False,
                "force_risk_only_draft": True,
                "force_draft_validation": True,
                "allow_empty_draft": True,
            },
        ),
        idempotency_key=f"e2e2-{uuid4().hex}",
    )
    assert run.status.value == "completed_with_warnings"
    assert run.state is not None
    assert run.state.company_evidence_insufficient is True
    joined = " ".join(run.state.warnings or [])
    assert "insufficient" in joined or "不足" in joined or "invent" in joined
    # Must not invent qualifications in risk draft preview.
    preview = (run.state.metadata or {}).get("risk_draft_preview") or ""
    assert "完全满足" not in preview


def test_e2e_3_qualification_fail_no_full_satisfaction(db: Session):
    project, req, _ = _seed(db, match_status=EvidenceMatchStatus.insufficient_evidence)
    # Default block_on_critical=True → blocked
    run = _svc(db).start_run(
        project.id,
        AgentRunStartRequest(
            user_request="资格审查",
            requested_requirement_ids=[req.id],
            metadata={
                "force_critical_qualification": True,
                "block_on_critical_qualification": True,
            },
        ),
        idempotency_key=f"e2e3a-{uuid4().hex}",
    )
    assert run.status.value == "blocked"
    text = json.dumps(run.state.model_dump() if run.state else {}, ensure_ascii=False)
    assert "完全满足" not in text

    # Risk-only path when flag false
    run2 = _svc(db).start_run(
        project.id,
        AgentRunStartRequest(
            user_request="资格审查风险稿",
            requested_requirement_ids=[req.id],
            metadata={
                "force_critical_qualification": True,
                "block_on_critical_qualification": False,
                "force_draft_validation": True,
                "allow_empty_draft": True,
            },
        ),
        idempotency_key=f"e2e3b-{uuid4().hex}",
    )
    assert run2.status.value == "completed_with_warnings"
    preview = (run2.state.metadata or {}).get("risk_draft_preview") or ""
    assert "完全满足" not in preview
    assert preview


def test_e2e_4_draft_validate_revise_then_pass(db: Session, monkeypatch):
    """Formal validate → revise → validate path (real ProposalDraft + compliance)."""
    from app.models.enums import ProposalDraftStatus, ProposalDraftVersionKind
    from app.models.proposal_draft import ProposalDraft, ProposalDraftVersion
    from app.schemas.proposal_draft import UNEVIDENCED_MARKER
    from app.tools.agent_tools import ToolResult

    project, req, _ = _seed(db)
    bad_md = (
        f"{UNEVIDENCED_MARKER} 保证中标并完全满足要求。"
        "补充说明文字以使草稿超过最短长度。"
    )
    good_md = (
        "根据现有材料，本公司可按招标文件要求提供相关资质证明与响应说明，"
        "供评审参考，本稿不含满足性承诺。"
    )

    def _draft(markdown: str, title: str) -> ProposalDraft:
        draft = ProposalDraft(
            project_id=project.id,
            title=title,
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
                                "content": markdown,
                                "citation_ids": [],
                            }
                        ],
                    }
                ]
            },
            content_markdown=markdown,
            is_current=True,
        )
        db.add(version)
        db.flush()
        draft.current_version_id = version.id
        db.commit()
        db.refresh(draft)
        return draft

    bad = _draft(bad_md, "bad-v1")
    good = _draft(good_md, "good-v2")

    # First draft generation returns bad draft; revise returns clean draft.
    gen_calls = {"n": 0}

    def fake_generate(db_sess, payload, llm=None):
        gen_calls["n"] += 1
        if gen_calls["n"] == 1:
            return ToolResult(
                ok=True,
                summary="bad_draft",
                data={"draft_ids": [str(bad.id)]},
            )
        return ToolResult(
            ok=True,
            summary="good_draft",
            data={
                "draft_ids": [str(good.id)],
                "risk_only": True,
                "content_preview": good_md,
            },
        )

    monkeypatch.setattr(
        "app.agent.nodes.draft.generate_proposal_draft", fake_generate
    )

    revise_mod = sys.modules["app.agent.nodes.revise_draft"]
    monkeypatch.setattr(revise_mod, "generate_proposal_draft", fake_generate)

    run = _svc(db).start_run(
        project.id,
        AgentRunStartRequest(
            user_request="生成草稿",
            requested_requirement_ids=[req.id],
            metadata={
                "force_critical_qualification": False,
            },
        ),
        idempotency_key=f"e2e4-{uuid4().hex}",
    )
    assert run.status.value in {"completed", "completed_with_warnings"}
    assert run.state is not None
    assert run.state.draft_revise_count >= 1
    assert str(good.id) in (run.state.draft_ids or [])
    assert run.state.draft_validation_ok is True
    check_events = [
        e
        for e in (run.state.tool_events or [])
        if e.get("name") == "check_draft_compliance"
    ]
    assert check_events, "formal compliance path must run"


def test_e2e_5_interrupt_resume_no_duplicate_business(db: Session):
    project, req, _ = _seed(db)
    svc = _svc(db)
    run = svc.start_run(
        project.id,
        AgentRunStartRequest(
            user_request="可中断",
            requested_requirement_ids=[req.id],
            metadata={
                "interrupt_after_node": "run_compliance_check",
                "force_critical_qualification": False,
                "force_risk_only_draft": True,
                "force_draft_validation": True,
                "allow_empty_draft": True,
            },
        ),
        idempotency_key=f"e2e5-{uuid4().hex}",
    )
    assert run.status.value == "waiting_for_user"
    assert run.state is not None
    compliance_id = run.state.compliance_run_id
    assert compliance_id

    # Count compliance runs before resume
    from app.models.compliance import ComplianceRun

    before = db.scalar(
        select(func.count()).select_from(ComplianceRun).where(
            ComplianceRun.project_id == project.id
        )
    )

    resumed = svc.resume_run(run.id)
    assert resumed.status.value in {"completed", "completed_with_warnings"}
    assert resumed.state is not None
    assert resumed.state.compliance_run_id == compliance_id

    after = db.scalar(
        select(func.count()).select_from(ComplianceRun).where(
            ComplianceRun.project_id == project.id
        )
    )
    assert after == before
