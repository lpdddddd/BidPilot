from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.search import SearchRequest, SearchResponse
from app.services import index_tasks
from app.services.document import DocumentService
from app.services.retrieval import RetrievalService

router = APIRouter()


@router.post("/{project_id}/search", response_model=SearchResponse)
def search_project(
    project_id: UUID,
    payload: SearchRequest,
    db: Session = Depends(get_db),
) -> SearchResponse:
    """Hybrid retrieval (dense + BM25 + RRF + cross-encoder rerank).

    Returns real ranked evidence only; no LLM-generated answers.
    """
    return RetrievalService(db).search(project_id, payload)


class ReindexResponse(BaseModel):
    project_id: str
    scheduled_document_count: int
    document_ids: list[str]


@router.post("/{project_id}/reindex", response_model=ReindexResponse)
def reindex_project(
    project_id: UUID,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> ReindexResponse:
    """Rebuild indexes for every document whose parse and chunking succeeded."""
    documents = DocumentService(db).list_indexable_documents(project_id)
    for document in documents:
        background_tasks.add_task(index_tasks.run_document_indexing, document.id)
    return ReindexResponse(
        project_id=str(project_id),
        scheduled_document_count=len(documents),
        document_ids=[str(d.id) for d in documents],
    )
