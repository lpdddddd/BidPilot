from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.document import DocumentCreate, DocumentListResponse, DocumentRead
from app.services.document import DocumentService

router = APIRouter()


@router.post("/{project_id}/documents", response_model=DocumentRead, status_code=201)
def create_document_metadata(
    project_id: UUID,
    payload: DocumentCreate,
    db: Session = Depends(get_db),
) -> DocumentRead:
    return DocumentService(db).create_metadata(project_id, payload)


@router.get("/{project_id}/documents", response_model=DocumentListResponse)
def list_documents(
    project_id: UUID,
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
) -> DocumentListResponse:
    return DocumentService(db).list_documents(project_id, skip=skip, limit=limit)
