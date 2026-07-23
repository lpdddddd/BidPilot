"""RAG evaluation adapter must scope retrieval to the authorized run project."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from app.models import BidProject, Document, DocumentChunk, Organization
from app.models.enums import DocumentType, ParseStatus
from app.schemas.search import SearchRequest, SearchResponse, StageLatency
from app.services.evaluation.case_loader import normalize_case
from app.services.evaluation.citations import validate_citation, validate_citations_for_result
from app.services.evaluation.suite_loader import load_jsonl
from app.services.evaluation.targets.adapters import RagServiceAdapter
from app.services.evaluation.targets.base import TargetResult
from app.services.evaluation.types import (
    TargetExecutionContext,
    split_case_for_evaluation,
)
from app.services.retrieval import RetrievalService
from sqlalchemy.orm import Session

FIXTURE = Path(__file__).parent / "fixtures" / "evaluation" / "mini_suite.jsonl"


def _empty_search_response(query: str) -> SearchResponse:
    from app.schemas.search import RetrievalTrace

    return SearchResponse(
        query=query,
        results=[],
        trace=RetrievalTrace(
            dense_candidate_count=0,
            bm25_candidate_count=0,
            fused_candidate_count=0,
            returned_count=0,
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


def _project(db: Session, code: str) -> BidProject:
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


def _doc_chunk(
    db: Session,
    project: BidProject,
    *,
    file_name: str,
    content: str,
    page_start: int = 1,
    page_end: int = 1,
) -> tuple[Document, DocumentChunk]:
    doc = Document(
        project_id=project.id,
        organization_id=project.organization_id,
        document_type=DocumentType.tender,
        file_name=file_name,
        storage_bucket="bidpilot-documents",
        storage_key=f"{project.id}/{file_name}",
        parse_status=ParseStatus.success,
        is_scanned=False,
    )
    db.add(doc)
    db.flush()
    chunk = DocumentChunk(
        document_id=doc.id,
        project_id=project.id,
        chunk_index=0,
        content=content,
        content_hash=f"hash-{uuid4().hex[:8]}",
        page_start=page_start,
        page_end=page_end,
        section="资质",
    )
    db.add(chunk)
    db.flush()
    return doc, chunk


def test_rag_adapter_searches_run_project_not_case_source(db: Session):
    run_project = _project(db, "RUN")
    other_project = _project(db, "OTHER")
    _doc_chunk(db, run_project, file_name="run.pdf", content="Run project qualification text")
    _doc_chunk(db, other_project, file_name="other.pdf", content="Other project secret text")
    db.commit()

    sample = next(s for s in load_jsonl(FIXTURE) if s.get("task_type") == "rag")
    case = normalize_case(sample)
    case.project_id = str(other_project.id)
    target_input, private = split_case_for_evaluation(case)
    # Gold source project stays in private only — never on TargetCaseInput.
    assert "source_project_id" not in target_input.__dataclass_fields__
    assert "source_project_id" not in target_input.task_input
    assert private.source_project_id == str(other_project.id)
    assert "context_chunk_ids" not in target_input.task_input
    assert private.context_chunk_ids or private.citation_metadata is not None

    captured: list = []

    def spy_search(self, project_id, request: SearchRequest):
        captured.append((project_id, request.query))
        return _empty_search_response(request.query)

    with patch.object(RetrievalService, "search", spy_search):
        result = RagServiceAdapter(db=db).run_case(
            target_input,
            TargetExecutionContext(project_id=run_project.id, seed=1),
        )

    assert result.ok
    assert captured
    assert captured[0][0] == run_project.id
    assert captured[0][0] != other_project.id


def test_cross_project_citation_invalid_under_run_project(db: Session):
    run_project = _project(db, "RUN-CITE")
    other_project = _project(db, "OTHER-CITE")
    _doc, run_chunk = _doc_chunk(db, run_project, file_name="run.pdf", content="visible")
    _other_doc, other_chunk = _doc_chunk(
        db, other_project, file_name="secret.pdf", content="forbidden"
    )
    db.commit()

    cross = validate_citation(
        db,
        project_id=run_project.id,
        citation={"chunk_id": str(other_chunk.id), "document_id": str(_other_doc.id)},
    )
    assert cross["valid"] is False
    assert cross["invalid_reason"] == "chunk_not_found_or_forbidden"

    valid = validate_citation(
        db,
        project_id=run_project.id,
        citation={"chunk_id": str(run_chunk.id), "page": run_chunk.page_start},
    )
    assert valid["valid"] is True
    assert valid["chunk_id"] == str(run_chunk.id)


def test_rag_result_snapshot_cross_project_citations_flagged(db: Session):
    run_project = _project(db, "RUN-SNAP")
    other_project = _project(db, "OTHER-SNAP")
    run_doc, run_chunk = _doc_chunk(db, run_project, file_name="run.pdf", content="ok")
    other_doc, other_chunk = _doc_chunk(db, other_project, file_name="x.pdf", content="nope")
    db.commit()

    snapshot = TargetResult(
        ok=True,
        output={
            "answer": "",
            "citations": [
                {"chunk_id": str(run_chunk.id), "document_id": str(run_doc.id), "page": 1},
                {"chunk_id": str(other_chunk.id), "document_id": str(other_doc.id), "page": 1},
            ],
        },
        citations=[
            {"chunk_id": str(run_chunk.id), "document_id": str(run_doc.id), "page": 1},
            {"chunk_id": str(other_chunk.id), "document_id": str(other_doc.id), "page": 1},
        ],
    ).to_response_snapshot()

    validated = validate_citations_for_result(
        db, project_id=run_project.id, response_snapshot=snapshot
    )
    by_chunk = {row["chunk_id"]: row for row in validated}
    assert by_chunk[str(run_chunk.id)]["valid"] is True
    assert by_chunk[str(other_chunk.id)]["valid"] is False
    assert by_chunk[str(other_chunk.id)]["invalid_reason"] == "chunk_not_found_or_forbidden"


def test_forged_source_project_id_does_not_change_retrieval_scope(db: Session):
    run_project = _project(db, "RUN-FORGE")
    forged = _project(db, "FORGED")
    db.commit()

    sample = next(s for s in load_jsonl(FIXTURE) if s.get("task_type") == "rag")
    case = normalize_case(sample)
    case.project_id = str(forged.id)
    target_input, private = split_case_for_evaluation(case)
    assert private.source_project_id == str(forged.id)
    assert "project_id" not in target_input.task_input

    seen: list = []

    def spy_search(self, project_id, request: SearchRequest):
        seen.append(project_id)
        return _empty_search_response(request.query)

    with patch.object(RetrievalService, "search", spy_search):
        RagServiceAdapter(db=db).run_case(
            target_input,
            TargetExecutionContext(project_id=run_project.id, seed=3),
        )

    assert seen == [run_project.id]
