"""API tests for agent-runs endpoints."""

from __future__ import annotations

from uuid import uuid4

from app.agent.checkpoint import DbCheckpointStore
from app.models import BidProject, Document, Organization, Requirement
from app.models.enums import (
    DocumentType,
    ParseStatus,
    QualityLevel,
    RequirementCategory,
    ReviewStatus,
    RiskLevel,
)
from app.schemas.search import (
    RetrievalTrace,
    SearchRequest,
    SearchResponse,
    SearchResultItem,
    StageLatency,
)
from app.services.agent_run.service import AgentRunService
from fastapi.testclient import TestClient
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


def _patch_service(monkeypatch):
    original = AgentRunService.__init__

    def _init(self, db, llm=None, retrieval_fn=None):
        original(self, db, llm=llm, retrieval_fn=retrieval_fn or _fake_retrieval)

    monkeypatch.setattr(AgentRunService, "__init__", _init)
    monkeypatch.setattr("app.api.v1.agent_runs.AgentRunService", AgentRunService)


def _seed(db: Session, *, with_doc: bool = True, with_req: bool = True) -> BidProject:
    org = Organization(name=f"Org-{uuid4().hex[:6]}")
    db.add(org)
    db.flush()
    project = BidProject(
        organization_id=org.id,
        project_code=f"API-{uuid4().hex[:4]}",
        project_name="Agent API",
    )
    db.add(project)
    db.flush()
    if with_doc:
        db.add(
            Document(
                project_id=project.id,
                organization_id=org.id,
                document_type=DocumentType.tender,
                file_name="t.pdf",
                storage_bucket="b",
                storage_key="k",
                parse_status=ParseStatus.success,
                is_scanned=False,
            )
        )
    if with_req:
        db.add(
            Requirement(
                project_id=project.id,
                category=RequirementCategory.mandatory,
                title="必须",
                mandatory=True,
                risk_level=RiskLevel.high,
                quality_level=QualityLevel.pending,
                review_status=ReviewStatus.unreviewed,
            )
        )
    db.commit()
    return project


def test_agent_api_happy_and_idempotency(client: TestClient, db: Session, monkeypatch):
    _patch_service(monkeypatch)
    project = _seed(db)
    key = f"k-{uuid4().hex}"
    created = client.post(
        f"/api/v1/projects/{project.id}/agent-runs",
        json={
            "user_request": "分析",
            "metadata": {
                "allow_empty_draft": True,
                "force_risk_only_draft": True,
                "force_draft_validation": True,
            },
        },
        headers={"Idempotency-Key": key},
    )
    assert created.status_code == 201, created.text
    run_id = created.json()["id"]

    again = client.post(
        f"/api/v1/projects/{project.id}/agent-runs",
        json={"user_request": "分析"},
        headers={"Idempotency-Key": key},
    )
    assert again.status_code == 201
    assert again.json()["id"] == run_id

    got = client.get(f"/api/v1/agent-runs/{run_id}")
    assert got.status_code == 200
    events = client.get(f"/api/v1/projects/{project.id}/agent-runs/{run_id}/events")
    assert events.status_code == 200
    assert events.json()["total"] >= 1
    result = client.get(f"/api/v1/projects/{project.id}/agent-runs/{run_id}/result")
    assert result.status_code == 200
    latest = client.get(f"/api/v1/projects/{project.id}/agent-runs/latest")
    assert latest.status_code == 200
    assert latest.json()["id"] == run_id
    hist = client.get(f"/api/v1/projects/{project.id}/agent-runs")
    assert hist.status_code == 200
    assert hist.json()["total"] >= 1
    # SSE stub
    stream = client.get(f"/api/v1/agent-runs/{run_id}/events/stream")
    assert stream.status_code == 200


def test_agent_api_invalid_project(client: TestClient):
    resp = client.post(f"/api/v1/projects/{uuid4()}/agent-runs", json={})
    assert resp.status_code == 404


def test_agent_api_no_docs_blocked(client: TestClient, db: Session, monkeypatch):
    _patch_service(monkeypatch)
    project = _seed(db, with_doc=False, with_req=False)
    resp = client.post(f"/api/v1/projects/{project.id}/agent-runs", json={})
    assert resp.status_code == 201
    assert resp.json()["status"] == "blocked"


def test_agent_api_cross_project(client: TestClient, db: Session, monkeypatch):
    _patch_service(monkeypatch)
    p1 = _seed(db)
    p2 = _seed(db)
    created = client.post(
        f"/api/v1/projects/{p1.id}/agent-runs",
        json={
            "metadata": {
                "allow_empty_draft": True,
                "force_risk_only_draft": True,
                "force_draft_validation": True,
            }
        },
    )
    run_id = created.json()["id"]
    cross = client.get(f"/api/v1/projects/{p2.id}/agent-runs/{run_id}")
    assert cross.status_code == 404
