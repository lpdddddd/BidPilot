"""Strong checkpoint resume semantics: completed nodes must not re-call services."""

from __future__ import annotations

import json
from uuid import uuid4

from app.models import BidProject, Document, Organization, Requirement
from app.models.agent import AgentRun
from app.models.compliance import ComplianceRun
from app.models.document import DocumentChunk
from app.models.enums import (
    AgentRunStatus,
    DocumentType,
    EvidenceMatchStatus,
    MatchReviewStatus,
    ParseStatus,
    QualityLevel,
    RequirementCategory,
    ReviewStatus,
    RiskLevel,
)
from app.models.match_run import RequirementEvidenceMatch
from app.schemas.agent_run import AgentRunStartRequest
from app.schemas.search import (
    RetrievalTrace,
    SearchRequest,
    SearchResponse,
    SearchResultItem,
    StageLatency,
)
from app.services.agent_run.events import next_step_index, record_step
from app.services.agent_run.service import AgentRunService
from app.services.llm_client import ChatResult
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker


class FakeLlm:
    def __init__(self):
        self.enabled = True
        self.model = "fake-qwen"
        self.chat_calls: list = []

    def chat(self, messages, **kwargs):
        self.chat_calls.append(messages)
        return ChatResult(
            content=json.dumps({"items": []}, ensure_ascii=False),
            model=self.model,
            latency_ms=1.0,
            finish_reason="stop",
            request_id="rid",
        )


def _fake_retrieval(project_id, request: SearchRequest) -> SearchResponse:
    return SearchResponse(
        query=request.query,
        results=[
            SearchResultItem(
                rank=1,
                chunk_id=str(uuid4()),
                document_id=str(uuid4()),
                file_name="t.pdf",
                document_type="tender",
                chunk_index=0,
                section="s",
                clause_id=None,
                page_start=1,
                page_end=1,
                content="证据",
                content_hash="h",
                source_sha256=None,
                chunker_version=None,
                dense_rank=1,
                dense_score=1.0,
                bm25_rank=1,
                bm25_score=1.0,
                rrf_score=0.5,
                rerank_score=0.9,
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


def _seed(db: Session):
    org = Organization(name=f"Org-{uuid4().hex[:6]}")
    db.add(org)
    db.flush()
    project = BidProject(
        organization_id=org.id,
        project_code=f"CP-{uuid4().hex[:4]}",
        project_name="Checkpoint Resume",
    )
    db.add(project)
    db.flush()
    doc = Document(
        project_id=project.id,
        organization_id=org.id,
        document_type=DocumentType.tender,
        file_name="t.pdf",
        storage_bucket="b",
        storage_key="k",
        parse_status=ParseStatus.success,
        is_scanned=False,
    )
    db.add(doc)
    db.flush()
    db.add(
        DocumentChunk(
            document_id=doc.id,
            project_id=project.id,
            chunk_index=0,
            content="须具备资质",
            content_hash="h",
        )
    )
    req = Requirement(
        project_id=project.id,
        category=RequirementCategory.qualification,
        title="资质",
        mandatory=True,
        risk_level=RiskLevel.high,
        quality_level=QualityLevel.pending,
        review_status=ReviewStatus.unreviewed,
    )
    db.add(req)
    db.flush()
    match = RequirementEvidenceMatch(
        project_id=project.id,
        requirement_id=req.id,
        status=EvidenceMatchStatus.supported,
        review_status=MatchReviewStatus.confirmed,
        lifecycle_status="active",
    )
    db.add(match)
    db.commit()
    return project, req, match


def test_resume_skips_completed_nodes(db: Session, engine, monkeypatch):
    project, req, match = _seed(db)
    project_id = project.id
    req_id = req.id

    counters = {
        "initialize_run": 0,
        "get_project_context": 0,
        "search_evidence": 0,
        "extract_requirements": 0,
        "match_company_evidence": 0,
        "run_project_compliance_check": 0,
    }

    import app.agent.nodes.compliance as compliance_mod
    import app.agent.nodes.extract as extract_mod
    import app.agent.nodes.initialize as init_mod
    import app.agent.nodes.load_context as load_mod
    import app.agent.nodes.match as match_mod
    import app.agent.nodes.retrieve as retrieve_mod

    real_init_begin = init_mod.begin_node
    real_extract_begin = extract_mod.begin_node
    real_ctx = load_mod.get_project_context
    real_search = retrieve_mod.search_evidence
    real_match = match_mod.match_company_evidence
    real_compliance = compliance_mod.run_project_compliance_check

    def count_init_begin(state, node):
        state2, skipped = real_init_begin(state, node)
        if node == "initialize_run" and not skipped:
            counters["initialize_run"] += 1
        return state2, skipped

    def count_extract_begin(state, node):
        state2, skipped = real_extract_begin(state, node)
        if node == "extract_requirements" and not skipped:
            counters["extract_requirements"] += 1
        return state2, skipped

    def count_ctx(*a, **k):
        counters["get_project_context"] += 1
        return real_ctx(*a, **k)

    def count_search(*a, **k):
        counters["search_evidence"] += 1
        return real_search(*a, **k)

    def count_match(*a, **k):
        counters["match_company_evidence"] += 1
        return real_match(*a, **k)

    def count_compliance(*a, **k):
        counters["run_project_compliance_check"] += 1
        return real_compliance(*a, **k)

    monkeypatch.setattr(init_mod, "begin_node", count_init_begin)
    monkeypatch.setattr(extract_mod, "begin_node", count_extract_begin)
    monkeypatch.setattr(load_mod, "get_project_context", count_ctx)
    monkeypatch.setattr(retrieve_mod, "search_evidence", count_search)
    monkeypatch.setattr(match_mod, "match_company_evidence", count_match)
    monkeypatch.setattr(compliance_mod, "run_project_compliance_check", count_compliance)

    svc = AgentRunService(db, llm=FakeLlm(), retrieval_fn=_fake_retrieval)
    run = svc.start_run(
        project_id,
        AgentRunStartRequest(
            user_request="分析",
            requested_requirement_ids=[req_id],
            metadata={
                "interrupt_after_node": "match_company_evidence",
                "force_critical_qualification": False,
                "force_risk_only_draft": True,
                "force_draft_validation": True,
                "allow_empty_draft": True,
            },
        ),
        idempotency_key=f"resume-skip-{uuid4().hex}",
    )
    assert run.status.value == "waiting_for_user"
    assert run.state is not None
    completed = list(run.state.completed_nodes or [])
    assert "match_company_evidence" in completed
    assert "run_compliance_check" not in completed
    snap = dict(counters)
    assert snap["initialize_run"] == 1
    assert snap["get_project_context"] == 1
    assert snap["search_evidence"] == 1
    assert snap["extract_requirements"] == 1
    assert snap["match_company_evidence"] == 1
    assert snap["run_project_compliance_check"] == 0
    compliance_id = run.state.compliance_run_id
    match_count = len(run.state.requirement_matches or [])
    req_count = len(run.state.requirements or [])
    run_id = run.id

    before_runs = db.scalar(
        select(func.count())
        .select_from(ComplianceRun)
        .where(ComplianceRun.project_id == project_id)
    )

    # Simulate process restart: NEW Session + NEW AgentRunService.
    db.commit()
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db2 = SessionLocal()
    try:
        svc2 = AgentRunService(db2, llm=FakeLlm(), retrieval_fn=_fake_retrieval)
        resumed = svc2.resume_run(run_id)
        assert resumed.status.value in {"completed", "completed_with_warnings"}
        assert resumed.state is not None

        # Early-node tools must NOT be called again on resume.
        assert counters["initialize_run"] == snap["initialize_run"]
        assert counters["get_project_context"] == snap["get_project_context"]
        assert counters["search_evidence"] == snap["search_evidence"]
        assert counters["extract_requirements"] == snap["extract_requirements"]
        assert counters["match_company_evidence"] == snap["match_company_evidence"]
        # Continued from next node (compliance).
        assert counters["run_project_compliance_check"] == 1

        skipped = [
            e for e in (resumed.state.tool_events or []) if e.get("summary") == "skipped_completed"
        ]
        # True LG continue may omit early-node skip events; START+completed_nodes
        # fallback emits them. Counters above are the hard guarantee either way.
        if skipped:
            skip_names = {e.get("name") for e in skipped}
            assert skip_names & {
                "initialize_run",
                "load_project_context",
                "retrieve_evidence",
                "extract_requirements",
                "match_company_evidence",
            }

        assert resumed.state.compliance_run_id
        if compliance_id:
            assert resumed.state.compliance_run_id == compliance_id
        assert len(resumed.state.requirement_matches or []) == match_count
        assert len(resumed.state.requirements or []) == req_count

        after_runs = db2.scalar(
            select(func.count())
            .select_from(ComplianceRun)
            .where(ComplianceRun.project_id == project_id)
        )
        # Exactly one compliance run created after resume (not on interrupt).
        assert after_runs == (before_runs or 0) + 1

        # Double resume idempotent on completed run.
        again = svc2.resume_run(run_id)
        assert again.id == resumed.id
        assert again.status.value == resumed.status.value
        assert counters["run_project_compliance_check"] == 1

        # Same Idempotency-Key start returns same run.
        key = f"idem-resume-{uuid4().hex}"
        r1 = svc2.start_run(
            project_id,
            AgentRunStartRequest(
                user_request="x",
                requested_requirement_ids=[req_id],
                metadata={
                    "force_critical_qualification": False,
                    "force_risk_only_draft": True,
                    "force_draft_validation": True,
                    "allow_empty_draft": True,
                },
            ),
            idempotency_key=key,
        )
        r2 = svc2.start_run(
            project_id,
            AgentRunStartRequest(user_request="x"),
            idempotency_key=key,
        )
        assert r1.id == r2.id
    finally:
        db2.close()


def test_next_step_index_starts_at_zero(db: Session):
    project, *_ = _seed(db)
    run = AgentRun(
        organization_id=project.organization_id,
        project_id=project.id,
        status=AgentRunStatus.running,
        intent="t",
        graph_version="bidpilot-agent-1.0.0",
    )
    db.add(run)
    db.flush()
    s0 = record_step(db, agent_run_id=run.id, node_name="a", status="succeeded")
    s1 = record_step(db, agent_run_id=run.id, node_name="b", status="succeeded")
    assert s0.step_index == 0
    assert s1.step_index == 1
    assert next_step_index(db, run.id) == 2
