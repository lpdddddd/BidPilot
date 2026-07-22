from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import (
    DocumentType,
    EvidenceMatchStatus,
    ExtractionRunStatus,
    RequirementCategory,
    RiskLevel,
)
from app.schemas.requirement import EvidenceLinkRead, RequirementSummary

DEFAULT_MATCH_DOCUMENT_TYPES: list[DocumentType] = [
    DocumentType.company_profile,
    DocumentType.qualification,
    DocumentType.case,
    DocumentType.personnel,
    DocumentType.product,
]

EXCLUDED_MATCH_DOCUMENT_TYPES: frozenset[DocumentType] = frozenset(
    {
        DocumentType.tender,
        DocumentType.announcement,
        DocumentType.amendment,
        DocumentType.contract,
    }
)


class MatchStartRequest(BaseModel):
    requirement_ids: list[UUID] = Field(default_factory=list)
    document_ids: list[UUID] = Field(default_factory=list)
    document_types: list[DocumentType] = Field(
        default_factory=lambda: list(DEFAULT_MATCH_DOCUMENT_TYPES)
    )
    force: bool = False

    @field_validator("document_types")
    @classmethod
    def _reject_tender_types(cls, value: list[DocumentType]) -> list[DocumentType]:
        cleaned = [t for t in value if t not in EXCLUDED_MATCH_DOCUMENT_TYPES]
        if not cleaned:
            return list(DEFAULT_MATCH_DOCUMENT_TYPES)
        return cleaned


class MatchRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    status: ExtractionRunStatus
    requirement_ids_json: list[Any] | None = None
    document_ids_json: list[Any] | None = None
    document_types_json: list[Any] | None = None
    total_requirements: int
    processed_requirements: int
    matched_count: int
    partial_count: int
    missing_evidence_count: int
    conflict_count: int
    failed_requirement_count: int
    error_summary: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    config_json: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime


class MatchCandidateItem(BaseModel):
    requirement_id: UUID
    status: EvidenceMatchStatus
    summary: str = Field(min_length=1)
    primary_company_chunk_id: UUID | None = None
    company_evidence_quote: str | None = None
    additional_company_chunk_ids: list[UUID] = Field(default_factory=list)
    needs_review: bool = True
    conflict_note: str | None = None


class MatchBatchResult(BaseModel):
    items: list[MatchCandidateItem] = Field(default_factory=list)


class CompanyEvidenceLinkRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    match_id: UUID
    document_id: UUID | None
    chunk_id: UUID | None
    quote: str | None
    notes: str | None
    role: str
    created_at: datetime
    updated_at: datetime
    document_file_name: str | None = None
    document_type: str | None = None
    chunk_index: int | None = None
    section: str | None = None
    clause_id: str | None = None
    page_start: int | None = None
    page_end: int | None = None
    document_center_path: str | None = None


class MatchSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    requirement_id: UUID
    status: EvidenceMatchStatus
    confidence: Decimal | None = None
    summary: str | None = None
    needs_review: bool
    risk_level: RiskLevel
    primary_company_document_id: UUID | None = None
    primary_company_chunk_id: UUID | None = None
    primary_company_quote: str | None = None
    metadata_json: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime
    # Nested requirement summary (list enrichment)
    requirement: RequirementSummary | None = None
    primary_company_document_file_name: str | None = None
    primary_company_document_type: str | None = None
    document_center_path: str | None = None


class MatchListResponse(BaseModel):
    items: list[MatchSummary]
    total: int
    page: int
    limit: int
    offset: int


class MatchDetail(MatchSummary):
    tender_evidence_links: list[EvidenceLinkRead] = Field(default_factory=list)
    company_links: list[CompanyEvidenceLinkRead] = Field(default_factory=list)
    requirement_category: RequirementCategory | None = None
    requirement_mandatory: bool | None = None
