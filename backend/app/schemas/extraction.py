from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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
    title: str | None = Field(default=None, max_length=1024)
    normalized_requirement: str = Field(min_length=1)
    mandatory: bool = False
    score: Decimal | None = None
    requirement_code_hint: str | None = None
    # Primary evidence (required). Locators are ALWAYS derived from this chunk.
    source_chunk_id: UUID
    evidence_quote: str = Field(min_length=1)
    # Optional supplemental chunks (must also contain the quote if used).
    source_chunk_ids: list[UUID] = Field(default_factory=list)
    # Model-supplied locators are ignored for persistence; accepted only for
    # backward-compatible schema parsing.
    source_section: str | None = None
    source_clause_id: str | None = None
    source_page: int | None = None
    needs_review: bool = False
    potential_conflict: bool = False
    conflict_note: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _coerce_primary_chunk(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        primary = data.get("source_chunk_id")
        ids = data.get("source_chunk_ids") or []
        if primary is None and ids:
            data = {**data, "source_chunk_id": ids[0]}
            primary = ids[0]
        if primary is not None:
            # Ensure primary is included in supplemental list for downstream loops.
            id_list = list(ids) if isinstance(ids, list) else []
            primary_s = str(primary)
            if primary_s not in {str(x) for x in id_list}:
                id_list = [primary, *id_list]
            data = {**data, "source_chunk_ids": id_list}
        return data


class ExtractionBatchResult(BaseModel):
    items: list[ExtractionCandidateItem] = Field(default_factory=list)
