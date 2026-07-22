"""Schemas for RequirementEvidenceMatch human review workflow."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import (
    ActorAuthn,
    EvidenceMatchStatus,
    MatchReviewAction,
    MatchReviewReasonCode,
    MatchReviewStatus,
    RiskLevel,
)


class MatchReviewRequest(BaseModel):
    action: MatchReviewAction
    actor_label: str = Field(min_length=1, max_length=64)
    comment: str | None = None
    reason_code: MatchReviewReasonCode | None = None
    review_lock_version: int = Field(ge=0)

    @field_validator("action")
    @classmethod
    def _reject_reopen_on_review_endpoint(
        cls, value: MatchReviewAction
    ) -> MatchReviewAction:
        if value == MatchReviewAction.reopen:
            raise ValueError("reopen must use the /reopen endpoint")
        return value

    @field_validator("actor_label")
    @classmethod
    def _normalize_actor_label(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if not cleaned or len(cleaned) > 64:
            raise ValueError("actor_label must be 1-64 printable characters")
        if any(ord(ch) < 32 for ch in cleaned):
            raise ValueError("actor_label must be printable")
        return cleaned


class MatchReopenRequest(BaseModel):
    actor_label: str = Field(min_length=1, max_length=64)
    comment: str = Field(min_length=1, max_length=2000)
    review_lock_version: int = Field(ge=0)

    @field_validator("actor_label")
    @classmethod
    def _normalize_actor_label(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if not cleaned or len(cleaned) > 64:
            raise ValueError("actor_label must be 1-64 printable characters")
        if any(ord(ch) < 32 for ch in cleaned):
            raise ValueError("actor_label must be printable")
        return cleaned

    @field_validator("comment")
    @classmethod
    def _normalize_comment(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if not cleaned:
            raise ValueError("comment is required")
        if len(cleaned) > 2000:
            raise ValueError("comment too long")
        return cleaned


class MatchReviewRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    match_id: UUID
    action: MatchReviewAction
    from_review_status: MatchReviewStatus
    to_review_status: MatchReviewStatus
    comment: str | None = None
    reason_code: MatchReviewReasonCode | None = None
    actor_id: UUID | None = None
    actor_label: str
    actor_authn: ActorAuthn
    idempotency_key: str | None = None
    created_at: datetime
    updated_at: datetime


class MatchReviewListResponse(BaseModel):
    items: list[MatchReviewRead]
    total: int


class ReviewQueueCounts(BaseModel):
    pending: int = 0
    confirmed: int = 0
    rejected: int = 0
    needs_more_material: int = 0
    total: int = 0


class ReviewQueueItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    requirement_id: UUID
    status: EvidenceMatchStatus
    review_status: MatchReviewStatus
    risk_level: RiskLevel
    needs_review: bool
    is_review_protected: bool
    review_lock_version: int
    lifecycle_status: str
    summary: str | None = None
    reviewed_at: datetime | None = None
    reviewed_by: str | None = None
    requirement_title: str | None = None
    requirement_code: str | None = None
    created_at: datetime
    updated_at: datetime


class ReviewQueueResponse(BaseModel):
    counts: ReviewQueueCounts
    items: list[ReviewQueueItem]
    total: int
    page: int
    limit: int
    offset: int
