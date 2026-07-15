from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Document
from app.schemas.document import DocumentCreate


class DocumentRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create(
        self,
        *,
        project_id: UUID,
        organization_id: UUID,
        data: DocumentCreate,
        storage_bucket: str,
        storage_key: str,
    ) -> Document:
        document = Document(
            project_id=project_id,
            organization_id=organization_id,
            document_type=data.document_type,
            file_name=data.file_name,
            mime_type=data.mime_type,
            storage_bucket=storage_bucket,
            storage_key=storage_key,
            sha256=data.sha256,
            file_size=data.file_size,
            page_count=data.page_count,
            parse_status=data.parse_status,
            is_scanned=data.is_scanned,
            metadata_json=data.metadata_json,
        )
        self.db.add(document)
        self.db.flush()
        return document

    def list_by_project(
        self,
        *,
        project_id: UUID,
        skip: int = 0,
        limit: int = 100,
    ) -> tuple[list[Document], int]:
        stmt = (
            select(Document)
            .where(Document.project_id == project_id)
            .order_by(Document.created_at.desc())
        )
        count_stmt = (
            select(func.count()).select_from(Document).where(Document.project_id == project_id)
        )
        total = self.db.scalar(count_stmt) or 0
        items = list(self.db.scalars(stmt.offset(skip).limit(limit)))
        return items, total
