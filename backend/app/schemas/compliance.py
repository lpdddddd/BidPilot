"""Pydantic schemas for the deterministic compliance rule engine."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import (
    ComplianceFindingStatus,
    ComplianceRuleCategory,
    ComplianceSeverity,
    ExtractionRunStatus,
)

FindingStatusLiteral = Literal["pass", "fail", "unknown"]
SeverityLiteral = Literal["info", "warning", "error", "critical"]
CategoryLiteral = Literal[
    "coverage",
    "evidence",
    "qualification_risk",
    "draft_safety",
    "consistency",
    "engine",
]


class ComplianceStartRequest(BaseModel):
    draft_id: UUID | None = None
    rule_ids: list[str] | None = None
    categories: list[ComplianceRuleCategory] | None = None

    @field_validator("rule_ids")
    @classmethod
    def _normalize_rule_ids(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        cleaned: list[str] = []
        seen: set[str] = set()
        for item in value:
            rid = " ".join(str(item).split())
            if not rid or rid in seen:
                continue
            seen.add(rid)
            cleaned.append(rid[:128])
        return cleaned or None


class ComplianceFinding(BaseModel):
    """Structured finding returned by rules and persisted in reports."""

    model_config = ConfigDict(from_attributes=True)

    finding_id: str
    rule_id: str
    rule_name: str
    category: ComplianceRuleCategory
    severity: ComplianceSeverity
    status: ComplianceFindingStatus
    message: str
    remediation: str | None = None
    requirement_id: UUID | None = None
    match_id: UUID | None = None
    draft_id: UUID | None = None
    evidence_json: dict[str, Any] | list[Any] | None = None
    source_location_json: dict[str, Any] | None = None
    metadata_json: dict[str, Any] | None = None
    id: UUID | None = None
    project_id: UUID | None = None
    run_id: UUID | None = None
    created_at: datetime | None = None


class ComplianceRuleInfo(BaseModel):
    rule_id: str
    name: str
    category: ComplianceRuleCategory
    description: str
    default_severity: ComplianceSeverity


class ComplianceRuleListResponse(BaseModel):
    items: list[ComplianceRuleInfo]
    total: int
    engine_version: str


class ComplianceRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    status: ExtractionRunStatus
    draft_id: UUID | None = None
    total_checks: int
    passed_checks: int
    finding_count: int
    severity_counts_json: dict[str, int] | None = None
    category_counts_json: dict[str, int] | None = None
    rule_ids_json: list[str] | None = None
    engine_version: str
    error_summary: str | None = None
    idempotency_key: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    config_json: dict[str, Any] | None = None


class ComplianceReport(BaseModel):
    run: ComplianceRunRead
    findings: list[ComplianceFinding]
    engine_version: str
    total_checks: int
    passed_checks: int
    finding_count: int
    severity_counts: dict[str, int]
    category_counts: dict[str, int]


class ComplianceFindingListResponse(BaseModel):
    items: list[ComplianceFinding]
    total: int
    run_id: UUID | None = None


class ComplianceFindingFilters(BaseModel):
    severity: ComplianceSeverity | None = None
    category: ComplianceRuleCategory | None = None
    rule_id: str | None = None
    requirement_id: UUID | None = None
    draft_id: UUID | None = None
    status: ComplianceFindingStatus | None = None
    run_id: UUID | None = None
    limit: int = Field(default=100, ge=1, le=500)
    offset: int = Field(default=0, ge=0)


class ComplianceContext(BaseModel):
    """Loaded project snapshot consumed by deterministic rules (no LLM)."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    project_id: UUID
    draft_id: UUID | None = None
    project: Any = None
    requirements: list[Any] = Field(default_factory=list)
    evidence_matches: list[Any] = Field(default_factory=list)
    tender_evidence_links: list[Any] = Field(default_factory=list)
    company_match_links: list[Any] = Field(default_factory=list)
    drafts: list[Any] = Field(default_factory=list)
    draft_versions: list[Any] = Field(default_factory=list)
    draft_sources: list[Any] = Field(default_factory=list)
    documents_by_id: dict[UUID, Any] = Field(default_factory=dict)
    chunks_by_id: dict[UUID, Any] = Field(default_factory=dict)
    requirements_by_id: dict[UUID, Any] = Field(default_factory=dict)
    matches_by_id: dict[UUID, Any] = Field(default_factory=dict)
    matches_by_requirement_id: dict[UUID, list[Any]] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
