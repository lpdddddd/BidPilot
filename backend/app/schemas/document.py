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


class DocumentPreviewResponse(BaseModel):
    document_id: UUID
    parse_status: ParseStatus
    page_count: int | None
    extracted_characters: int | None
    preview: str
    truncated: bool
    max_chars: int


class DocumentDownloadResponse(BaseModel):
    download_url: str
    expires_in_seconds: int
    file_name: str


class ChunkRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    document_id: UUID
    project_id: UUID
    chunk_index: int
    section: str | None
    clause_id: str | None
    page_start: int | None
    page_end: int | None
    content: str
    content_hash: str | None
    token_count: int | None
    metadata_json: dict[str, Any] | None
    qdrant_point_id: str | None
    created_at: datetime


class ChunkListResponse(BaseModel):
    items: list[ChunkRead]
    total: int


class ChunkSummaryResponse(BaseModel):
    document_id: UUID
    status: str
    chunk_count: int
    section_count: int
    total_tokens: int
    chunker_name: str | None
    chunker_version: str | None
    tokenizer: str | None
    error: str | None
    completed_at: str | None
