"""Agent run service / checkpoint / idempotency tests."""

from __future__ import annotations

from uuid import uuid4

from app.agent.checkpoint import DbCheckpointStore
from app.models import BidProject, Document, Organization
from app.models.enums import DocumentType, ParseStatus
from app.schemas.agent_run import AgentRunStartRequest
from app.schemas.search import (
    RetrievalTrace,
    SearchRequest,
    SearchResponse,
    SearchResultItem,
    StageLatency,
)
from app.services.agent_run.service import AgentRunService
from sqlalchemy.orm import Session


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
                content="证据摘要",
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


def _project_with_doc(db: Session) -> BidProject:
    org = Organization(name=f"Org-{uuid4().hex[:6]}")
    db.add(org)
    db.flush()
    project = BidProject(
        organization_id=org.id,
        project_code=f"SVC-{uuid4().hex[:4]}",
        project_name="Agent Service",
    )
    db.add(project)
    db.flush()
    db.add(
        Document(
            project_id=project.id,
            organization_id=org.id,
            document_type=DocumentType.tender,
            file_name="a.pdf",
            storage_bucket="b",
            storage_key="k",
            parse_status=ParseStatus.success,
            is_scanned=False,
        )
    )
    db.commit()
    return project


def test_idempotency_returns_same_run(db: Session, monkeypatch):
    project = _project_with_doc(db)
    svc = AgentRunService(db, retrieval_fn=_fake_retrieval)

    # Force no requirements path quickly via metadata after load — seed has no reqs.
    key = f"idem-{uuid4().hex}"
    r1 = svc.start_run(
        project.id,
        AgentRunStartRequest(
            user_request="x",
            metadata={"allow_empty_draft": True, "force_risk_only_draft": True},
        ),
        idempotency_key=key,
    )
    r2 = svc.start_run(
        project.id,
        AgentRunStartRequest(user_request="x"),
        idempotency_key=key,
    )
    assert r1.id == r2.id


def test_checkpoint_save_and_resume(db: Session):
    project = _project_with_doc(db)
    svc = AgentRunService(db, retrieval_fn=_fake_retrieval)
    run = svc.start_run(
        project.id,
        AgentRunStartRequest(
            user_request="x",
            metadata={
                "interrupt_after_node": "retrieve_evidence",
                "allow_empty_draft": True,
                "force_risk_only_draft": True,
            },
        ),
        idempotency_key=f"cp-{uuid4().hex}",
    )
    assert run.status.value == "waiting_for_user"
    store = DbCheckpointStore(db)
    assert store.latest(str(run.id)) is not None
    resumed = svc.resume_run(run.id)
    assert resumed.status.value in {
        "completed",
        "completed_with_warnings",
        "blocked",
        "waiting_for_user",
    }
