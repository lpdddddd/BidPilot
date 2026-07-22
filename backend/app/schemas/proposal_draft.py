"""Schemas for auditable proposal drafting workspace."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import (
    ActorAuthn,
    EvidenceMatchStatus,
    ExtractionRunStatus,
    MatchReviewStatus,
    ProposalDraftGenerationMode,
    ProposalDraftReviewAction,
    ProposalDraftSourceRole,
    ProposalDraftStatus,
    ProposalDraftVersionKind,
    RequirementCategory,
)

DISCLAIMER = (
    "本文件为基于已审核材料生成的响应准备草稿，须经人工复核、补充、签署和法务或业务确认后方可使用，"
    "不构成投标结论或投标提交文件。"
)

UNEVIDENCED_MARKER = "人工新增，尚未提供证据"

BlockKind = Literal[
    "supported_response",
    "partial_response",
    "material_gap",
    "risk_item",
    "scope_item",
    "manual_unreferenced",
]

Disposition = Literal[
    "responded",
    "partially_responded",
    "material_gap",
    "risk_review",
    "scope_review",
    "excluded",
]

WarningType = Literal[
    "material_gap",
    "conflicting_evidence",
    "scope_exclusion",
    "pending_review",
    "rejected_match",
    "needs_more_material",
]


class ProposalDraftCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=512)
    requirement_ids: list[UUID] = Field(default_factory=list)
    mode: ProposalDraftGenerationMode = ProposalDraftGenerationMode.response_outline
    created_by: str | None = Field(default=None, max_length=64)

    @field_validator("title")
    @classmethod
    def _normalize_title(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if not cleaned:
            raise ValueError("title is required")
        return cleaned[:512]

    @field_validator("created_by")
    @classmethod
    def _normalize_created_by(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = " ".join(value.split())
        return cleaned[:64] or None


class ProposalDraftManualRevisionRequest(BaseModel):
    content_json: dict[str, Any]
    created_by: str | None = Field(default=None, max_length=64)
    comment: str | None = Field(default=None, max_length=2000)

    @field_validator("created_by")
    @classmethod
    def _normalize_created_by(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = " ".join(value.split())
        return cleaned[:64] or None


class ProposalDraftReviewRequest(BaseModel):
    action: ProposalDraftReviewAction = ProposalDraftReviewAction.mark_reviewed
    actor_label: str = Field(min_length=1, max_length=64)
    comment: str = Field(min_length=1, max_length=2000)
    review_lock_version: int = Field(ge=0)

    @field_validator("action")
    @classmethod
    def _only_mark_reviewed(cls, value: ProposalDraftReviewAction) -> ProposalDraftReviewAction:
        if value != ProposalDraftReviewAction.mark_reviewed:
            raise ValueError("use /reopen for reopen action")
        return value

    @field_validator("actor_label", "comment")
    @classmethod
    def _normalize_text(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if not cleaned:
            raise ValueError("required text cannot be empty")
        return cleaned


class ProposalDraftReopenRequest(BaseModel):
    actor_label: str = Field(min_length=1, max_length=64)
    comment: str = Field(min_length=1, max_length=2000)
    review_lock_version: int = Field(ge=0)

    @field_validator("actor_label", "comment")
    @classmethod
    def _normalize_text(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if not cleaned:
            raise ValueError("required text cannot be empty")
        return cleaned


class ProposalDraftSourceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    draft_version_id: UUID
    requirement_id: UUID | None
    match_id: UUID | None
    evidence_link_id: UUID | None
    source_role: ProposalDraftSourceRole
    source_quote: str | None
    location_json: dict[str, Any] | None = None
    created_at: datetime


class ProposalDraftVersionSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    draft_id: UUID
    parent_version_id: UUID | None
    version_number: int
    version_kind: ProposalDraftVersionKind
    generation_run_id: UUID | None
    source_snapshot_hash: str | None
    created_by: str | None
    supersedes_version_id: UUID | None
    is_current: bool
    created_at: datetime
    has_unevidenced_manual_content: bool = False


class ProposalDraftVersionDetail(ProposalDraftVersionSummary):
    content_json: dict[str, Any]
    content_markdown: str | None = None
    sources: list[ProposalDraftSourceRead] = Field(default_factory=list)


class ProposalDraftReviewRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    draft_id: UUID
    draft_version_id: UUID
    action: ProposalDraftReviewAction
    comment: str | None
    actor_id: UUID | None
    actor_label: str
    actor_authn: ActorAuthn
    idempotency_key: str | None
    created_at: datetime


class ProposalDraftRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    status: ExtractionRunStatus
    mode: ProposalDraftGenerationMode
    title: str
    requested_requirement_ids: list[Any] | None = None
    eligible_requirement_count: int
    excluded_requirement_count: int
    excluded_reason_summary: str | None = None
    draft_id: UUID | None = None
    draft_version_id: UUID | None = None
    error_summary: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    cancel_requested_at: datetime | None = None
    created_by: str | None = None
    created_at: datetime
    updated_at: datetime
    config_json: dict[str, Any] | None = None


class ProposalDraftSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    title: str
    status: ProposalDraftStatus
    current_version_id: UUID | None
    current_version_number: int | None = None
    created_by: str | None
    review_lock_version: int
    created_at: datetime
    updated_at: datetime
    last_reviewed_at: datetime | None = None
    eligible_requirement_count: int = 0
    material_gap_count: int = 0
    risk_count: int = 0
    scope_count: int = 0
    has_unevidenced_manual_content: bool = False
    export_allowed: bool = False
    disclaimer: str = DISCLAIMER


class ProposalDraftDetail(ProposalDraftSummary):
    current_version: ProposalDraftVersionDetail | None = None
    recent_reviews: list[ProposalDraftReviewRead] = Field(default_factory=list)
    latest_run: ProposalDraftRunResponse | None = None


class ProposalDraftListResponse(BaseModel):
    items: list[ProposalDraftSummary]
    total: int


class ProposalDraftVersionListResponse(BaseModel):
    items: list[ProposalDraftVersionSummary]
    total: int


class EligibilityRequirementItem(BaseModel):
    requirement_id: UUID
    title: str
    category: RequirementCategory | None = None
    match_id: UUID | None = None
    match_status: EvidenceMatchStatus | None = None
    review_status: MatchReviewStatus | None = None
    eligibility: Literal[
        "positive",
        "material_gap",
        "risk",
        "scope",
        "excluded",
        "no_match",
    ]
    reason: str
    draft_handling: str


class ProposalDraftEligibilityResponse(BaseModel):
    project_id: UUID
    eligible: list[EligibilityRequirementItem]
    excluded: list[EligibilityRequirementItem]
    material_gaps: list[EligibilityRequirementItem]
    risks: list[EligibilityRequirementItem]
    scope_items: list[EligibilityRequirementItem]
    disclaimer: str = DISCLAIMER
