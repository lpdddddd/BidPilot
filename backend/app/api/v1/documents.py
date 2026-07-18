from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Query, UploadFile
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.document import (
    DocumentCreate,
    DocumentDownloadResponse,
    DocumentListResponse,
    DocumentPreviewResponse,
    DocumentRead,
)
from app.services import document_tasks
from app.services.document import DocumentService

router = APIRouter()


@router.post("/{project_id}/documents", response_model=DocumentRead, status_code=201)
def create_document_metadata(
    project_id: UUID,
    payload: DocumentCreate,
    db: Session = Depends(get_db),
) -> DocumentRead:
    return DocumentService(db).create_metadata(project_id, payload)


@router.post(
    "/{project_id}/documents/upload",
    response_model=DocumentRead,
    status_code=201,
)
def upload_document(
    project_id: UUID,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    document_type: str | None = Form(default=None),
    db: Session = Depends(get_db),
) -> DocumentRead:
    document = DocumentService(db).upload_document(
        project_id,
        file,
        document_type_raw=document_type,
    )
    background_tasks.add_task(document_tasks.run_document_parse, document.id)
    return document


@router.get("/{project_id}/documents", response_model=DocumentListResponse)
def list_documents(
    project_id: UUID,
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
) -> DocumentListResponse:
    return DocumentService(db).list_documents(project_id, skip=skip, limit=limit)


@router.get("/{project_id}/documents/{document_id}", response_model=DocumentRead)
def get_document(
    project_id: UUID,
    document_id: UUID,
    db: Session = Depends(get_db),
) -> DocumentRead:
    return DocumentService(db).get_document(project_id, document_id)


@router.get(
    "/{project_id}/documents/{document_id}/preview",
    response_model=DocumentPreviewResponse,
)
def preview_document(
    project_id: UUID,
    document_id: UUID,
    max_chars: int = Query(default=5000, ge=100, le=20000),
    db: Session = Depends(get_db),
) -> DocumentPreviewResponse:
    return DocumentService(db).get_preview(project_id, document_id, max_chars=max_chars)


@router.get(
    "/{project_id}/documents/{document_id}/download",
    response_model=DocumentDownloadResponse,
)
def download_document(
    project_id: UUID,
    document_id: UUID,
    db: Session = Depends(get_db),
) -> DocumentDownloadResponse:
    return DocumentService(db).get_download(project_id, document_id)


@router.post(
    "/{project_id}/documents/{document_id}/reparse",
    response_model=DocumentRead,
)
def reparse_document(
    project_id: UUID,
    document_id: UUID,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> DocumentRead:
    document = DocumentService(db).request_reparse(project_id, document_id)
    background_tasks.add_task(document_tasks.run_document_parse, document.id)
    return document
