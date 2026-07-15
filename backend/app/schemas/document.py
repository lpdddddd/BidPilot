from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import DocumentType, ParseStatus


class DocumentCreate(BaseModel):
    document_type: DocumentType = DocumentType.other
    file_name: str = Field(min_length=1, max_length=512)
    mime_type: str | None = None
    storage_bucket: str | None = None
    storage_key: str | None = None
    sha256: str | None = Field(default=None, max_length=64)
    file_size: int | None = Field(default=None, ge=0)
    page_count: int | None = Field(default=None, ge=0)
    parse_status: ParseStatus = ParseStatus.pending
    is_scanned: bool = False
    metadata_json: dict[str, Any] | None = None


class DocumentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    organization_id: UUID
    document_type: DocumentType
    file_name: str
    mime_type: str | None
    storage_bucket: str
    storage_key: str
    sha256: str | None
    file_size: int | None
    page_count: int | None
    parse_status: ParseStatus
    is_scanned: bool
    metadata_json: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime


class DocumentListResponse(BaseModel):
    items: list[DocumentRead]
    total: int
