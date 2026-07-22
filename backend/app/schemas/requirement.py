from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import (
    QualityLevel,
    RequirementCategory,
    ReviewStatus,
    RiskLevel,
)


class EvidenceLinkRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    requirement_id: UUID
    document_id: UUID | None
    chunk_id: UUID | None
    evidence_type: str | None
    confidence: Decimal | None
    notes: str | None
    created_at: datetime
    updated_at: datetime

    # Enriched locator fields (optional on list, filled on detail)
    document_file_name: str | None = None
    document_type: str | None = None
    chunk_index: int | None = None
    section: str | None = None
    clause_id: str | None = None
    page_start: int | None = None
    page_end: int | None = None
    document_center_path: str | None = None


class RequirementSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    source_document_id: UUID | None
    requirement_code: str | None
    category: RequirementCategory
    title: str
    normalized_requirement: str | None
    mandatory: bool
    score: Decimal | None
    risk_level: RiskLevel
    source_page: int | None
    source_section: str | None
    source_clause_id: str | None
    quality_level: QualityLevel
    review_status: ReviewStatus
    metadata_json: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime
    evidence_count: int = 0
    has_conflict: bool = False
    source_document_file_name: str | None = None


class RequirementListResponse(BaseModel):
    items: list[RequirementSummary]
    total: int
    page: int
    limit: int
    offset: int


class RequirementDetail(RequirementSummary):
    evidence_required_json: dict[str, Any] | list[Any] | None = None
    evidence_links: list[EvidenceLinkRead] = Field(default_factory=list)
