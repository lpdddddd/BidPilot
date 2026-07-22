"""Per-node unit tests with mocked tools/services."""

from __future__ import annotations

from uuid import uuid4

import pytest
from app.agent.nodes._helpers import AgentRuntime, reset_runtime, set_runtime
from app.agent.nodes.compliance import run_compliance_check
from app.agent.nodes.draft import generate_response_draft
from app.agent.nodes.extract import extract_requirements_node
from app.agent.nodes.finalize import finalize_run
from app.agent.nodes.initialize import initialize_run
from app.agent.nodes.load_context import load_project_context
from app.agent.nodes.match import match_company_evidence_node
from app.agent.nodes.retrieve import retrieve_evidence
from app.agent.nodes.revise_draft import revise_draft
from app.agent.nodes.validate_draft import validate_draft
from app.agent.state import empty_state
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
from app.models.match_run import RequirementEvidenceMatch
from app.schemas.search import (
    RetrievalTrace,
    SearchRequest,
    SearchResponse,
    SearchResultItem,
    StageLatency,
)
from sqlalchemy.orm import Session


def _seed(db: Session):
    org = Organization(name=f"Org-{uuid4().hex[:6]}")
    db.add(org)
    db.flush()
    project = BidProject(
        organization_id=org.id,
        project_code=f"AG-{uuid4().hex[:4]}",
        project_name="Agent Node Project",
    )
    db.add(project)
    db.flush()
    doc = Document(
        project_id=project.id,
        organization_id=org.id,
        document_type=DocumentType.tender,
        file_name="tender.pdf",
        storage_bucket="b",
        storage_key="k",
        parse_status=ParseStatus.success,
        is_scanned=False,
    )
    db.add(doc)
    db.flush()
    chunk = DocumentChunk(
        document_id=doc.id,
        project_id=project.id,
        chunk_index=0,
        content="必须具备建筑施工总承包一级资质",
        content_hash="h",
    )
    db.add(chunk)
    req = Requirement(
        project_id=project.id,
        category=RequirementCategory.qualification,
        title="一级资质",
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
    return project, doc, req, match


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
                section="资格",
                clause_id=None,
                page_start=1,
                page_end=1,
                content="资质要求摘要",
                content_hash="x",
                source_sha256=None,
                chunker_version=None,
                dense_rank=1,
                dense_score=0.9,
                bm25_rank=1,
                bm25_score=1.0,
                rrf_score=0.5,
                rerank_score=0.8,
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


@pytest.fixture()
def runtime(db: Session):
    rt = AgentRuntime(db=db, retrieval_fn=_fake_retrieval)
    token = set_runtime(rt)
    yield rt
    reset_runtime(token)


def test_initialize_and_load(db: Session, runtime):
    project, *_ = _seed(db)
    state = empty_state(run_id=str(uuid4()), project_id=str(project.id))
    state = initialize_run(state)
    assert state["status"] == "running"
    state = load_project_context(state)
    assert state["has_documents"] is True


def test_retrieve_extract_match(db: Session, runtime):
    project, _, req, match = _seed(db)
    state = empty_state(
        run_id=str(uuid4()),
        project_id=str(project.id),
        requested_requirement_ids=[str(req.id)],
        user_request="资质",
    )
    state = retrieve_evidence(state)
    assert state["retrieved_chunks"]
    state = extract_requirements_node(state)
    assert state["has_requirements"]
    state = match_company_evidence_node(state)
    assert any(m["id"] == str(match.id) for m in state["requirement_matches"])


def test_compliance_draft_validate_revise_finalize(db: Session, runtime, monkeypatch):
    project, *_ = _seed(db)
    state = empty_state(
        run_id=str(uuid4()),
        project_id=str(project.id),
        metadata={
            "force_critical_qualification": False,
            "force_risk_only_draft": True,
            "force_draft_validation": False,
            "revise_should_pass": True,
            "revise_pass_after": 1,
            "synthetic_revise": True,
            "synthetic_draft_id": "d1",
            "allow_empty_draft": True,
        },
    )
    # Skip real compliance engine — patch tool.

    from app.models.enums import ExtractionRunStatus

    class FakeResult:
        ok = True
        detail = None

        class report:
            class run:
                id = uuid4()
                status = ExtractionRunStatus.succeeded

            findings = []
            finding_count = 0

    monkeypatch.setattr(
        "app.agent.nodes.compliance.run_project_compliance_check",
        lambda db, payload: FakeResult(),
    )

    state["requirements"] = [{"id": str(uuid4()), "title": "t"}]
    state = run_compliance_check(state)
    assert state["compliance_run_id"]
    state = generate_response_draft(state)
    assert state["draft_ids"]
    state = validate_draft(state)
    assert state["draft_validation_ok"] is False
    state = revise_draft(state)
    assert state["draft_revise_count"] == 1
    state = validate_draft(state)
    assert state["draft_validation_ok"] is True
    state = finalize_run(state)
    assert state["status"] in {"completed", "completed_with_warnings"}


def test_tool_exception_marks_retryable(db: Session, runtime, monkeypatch):
    project, *_ = _seed(db)
    state = empty_state(run_id=str(uuid4()), project_id=str(project.id))

    def boom(*a, **k):
        raise RuntimeError("tool boom")

    monkeypatch.setattr("app.agent.nodes.retrieve.search_evidence", boom)
    state = retrieve_evidence(state)
    assert state["last_error_retryable"] is True


def test_llm_schema_error(db: Session, runtime, monkeypatch):
    from app.services.llm_client import LlmResponseError

    project, *_ = _seed(db)
    # Clear requirements so extract path runs service
    db.query(Requirement).delete()
    db.commit()
    state = empty_state(run_id=str(uuid4()), project_id=str(project.id))

    def boom(*a, **k):
        raise LlmResponseError("bad schema")

    monkeypatch.setattr("app.agent.nodes.extract.extract_requirements", boom)
    state = extract_requirements_node(state)
    assert state["status"] == "failed"
    assert state["error_code"] == "llm_schema_error"


def test_critical_blocks_draft(db: Session, runtime):
    project, *_ = _seed(db)
    state = empty_state(
        run_id=str(uuid4()),
        project_id=str(project.id),
        metadata={"block_on_critical_qualification": True},
    )
    state["critical_qualification"] = True
    state = generate_response_draft(state)
    assert state["status"] == "blocked"
