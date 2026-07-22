from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import DocumentType, ExtractionRunStatus, RequirementCategory

DEFAULT_EXTRACTION_DOCUMENT_TYPES: list[DocumentType] = [
    DocumentType.tender,
    DocumentType.announcement,
    DocumentType.amendment,
    DocumentType.contract,
]

EXCLUDED_EXTRACTION_DOCUMENT_TYPES: frozenset[DocumentType] = frozenset(
    {
        DocumentType.company_profile,
        DocumentType.qualification,
        DocumentType.case,
        DocumentType.personnel,
        DocumentType.product,
    }
)


class ExtractionStartRequest(BaseModel):
    document_ids: list[UUID] = Field(default_factory=list)
    document_types: list[DocumentType] = Field(
        default_factory=lambda: list(DEFAULT_EXTRACTION_DOCUMENT_TYPES)
    )
    force: bool = False

    @field_validator("document_types")
    @classmethod
    def _reject_company_types(cls, value: list[DocumentType]) -> list[DocumentType]:
        cleaned = [t for t in value if t not in EXCLUDED_EXTRACTION_DOCUMENT_TYPES]
        if not cleaned:
            return list(DEFAULT_EXTRACTION_DOCUMENT_TYPES)
        return cleaned


class ExtractionRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    status: ExtractionRunStatus
    document_ids_json: list[Any] | None = None
    document_types_json: list[Any] | None = None
    total_chunks: int
    processed_chunks: int
    candidate_count: int
    created_count: int
    merged_count: int
    conflict_count: int
    failed_chunk_count: int
    error_summary: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    config_json: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime


class ExtractionCandidateItem(BaseModel):
    category: RequirementCategory
    title: str = Field(min_length=1, max_length=1024)
    normalized_requirement: str = Field(min_length=1)
    mandatory: bool = False
    score: Decimal | None = None
    requirement_code_hint: str | None = None
    source_chunk_ids: list[UUID] = Field(min_length=1)
    evidence_quote: str = Field(min_length=1)
    source_section: str | None = None
    source_clause_id: str | None = None
    source_page: int | None = None
    needs_review: bool = False
    potential_conflict: bool = False
    conflict_note: str | None = None


class ExtractionBatchResult(BaseModel):
    items: list[ExtractionCandidateItem] = Field(default_factory=list)
