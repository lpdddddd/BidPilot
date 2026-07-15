from uuid import UUID, uuid4

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.repositories.document import DocumentRepository
from app.repositories.project import ProjectRepository
from app.schemas.document import DocumentCreate, DocumentListResponse, DocumentRead


class DocumentService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.documents = DocumentRepository(db)
        self.projects = ProjectRepository(db)
        self.settings = get_settings()

    def create_metadata(self, project_id: UUID, data: DocumentCreate) -> DocumentRead:
        project = self.projects.get_by_id(project_id)
        if project is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Project not found",
            )

        if data.file_size is not None and data.file_size > self.settings.max_upload_size_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"File exceeds max size of {self.settings.max_upload_size_bytes} bytes",
            )

        if data.mime_type and data.mime_type not in self.settings.allowed_upload_mime_types:
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail=f"Unsupported mime type: {data.mime_type}",
            )

        storage_bucket = data.storage_bucket or self.settings.minio_bucket
        storage_key = (
            data.storage_key or f"projects/{project_id}/documents/{uuid4()}/{data.file_name}"
        )

        document = self.documents.create(
            project_id=project.id,
            organization_id=project.organization_id,
            data=data,
            storage_bucket=storage_bucket,
            storage_key=storage_key,
        )
        self.db.commit()
        self.db.refresh(document)
        return DocumentRead.model_validate(document)

    def list_documents(
        self,
        project_id: UUID,
        *,
        skip: int = 0,
        limit: int = 100,
    ) -> DocumentListResponse:
        project = self.projects.get_by_id(project_id)
        if project is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Project not found",
            )
        items, total = self.documents.list_by_project(
            project_id=project_id,
            skip=skip,
            limit=limit,
        )
        return DocumentListResponse(
            items=[DocumentRead.model_validate(item) for item in items],
            total=total,
        )
