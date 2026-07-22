from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import (
    ActorAuthn,
    EvidenceMatchStatus,
    ExtractionRunStatus,
    MatchReviewAction,
    MatchReviewReasonCode,
    MatchReviewStatus,
    RiskLevel,
)
from app.models.types import (
    EnumType,
    actor_authn_enum,
    evidence_match_status_enum,
    match_review_action_enum,
    match_review_reason_code_enum,
    match_review_status_enum,
    risk_level_enum,
)

if TYPE_CHECKING:
    from app.models.document import Document, DocumentChunk
    from app.models.project import BidProject
    from app.models.requirement import Requirement


class RequirementMatchRun(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Async run that matches project requirements against company-side evidence."""

    __tablename__ = "requirement_match_runs"
    __table_args__ = (
        Index("ix_requirement_match_runs_project_id", "project_id"),
        Index("ix_requirement_match_runs_status", "status"),
    )

    project_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("bid_projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[ExtractionRunStatus] = mapped_column(
        EnumType(ExtractionRunStatus, name="extraction_run_status", create_type=False),
        nullable=False,
        default=ExtractionRunStatus.queued,
    )
    requirement_ids_json: Mapped[list[Any] | None] = mapped_column(JSONB)
    document_ids_json: Mapped[list[Any] | None] = mapped_column(JSONB)
    document_types_json: Mapped[list[Any] | None] = mapped_column(JSONB)
    total_requirements: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    processed_requirements: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    matched_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    partial_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    missing_evidence_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    conflict_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_requirement_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    protected_requirement_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    skipped_reviewed_requirement_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    error_summary: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    config_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    project: Mapped[BidProject] = relationship(back_populates="match_runs")


class RequirementEvidenceMatch(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Company-evidence match for a single Requirement (auto or manual).

    Distinct from legacy RequirementMatch which requires company_profile_id.
    """

    __tablename__ = "requirement_evidence_matches"
    __table_args__ = (
        Index("ix_requirement_evidence_matches_project_id", "project_id"),
        Index("ix_requirement_evidence_matches_requirement_id", "requirement_id"),
        Index("ix_requirement_evidence_matches_status", "status"),
        Index("ix_requirement_evidence_matches_risk_level", "risk_level"),
        Index("ix_requirement_evidence_matches_review_status", "review_status"),
        Index("ix_requirement_evidence_matches_lifecycle_status", "lifecycle_status"),
    )

    project_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("bid_projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    requirement_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("requirements.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[EvidenceMatchStatus] = mapped_column(
        evidence_match_status_enum,
        nullable=False,
        default=EvidenceMatchStatus.insufficient_evidence,
    )
    confidence: Mapped[Any | None] = mapped_column(Numeric(5, 4))
    summary: Mapped[str | None] = mapped_column(Text)
    needs_review: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    risk_level: Mapped[RiskLevel] = mapped_column(
        risk_level_enum,
        nullable=False,
        default=RiskLevel.medium,
    )
    primary_company_document_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="SET NULL"),
    )
    primary_company_chunk_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("document_chunks.id", ondelete="SET NULL"),
    )
    primary_company_quote: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    review_status: Mapped[MatchReviewStatus] = mapped_column(
        match_review_status_enum,
        nullable=False,
        default=MatchReviewStatus.pending,
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_by: Mapped[str | None] = mapped_column(String(128))
    review_lock_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_review_protected: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    lifecycle_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="active"
    )
    superseded_by_match_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("requirement_evidence_matches.id", ondelete="SET NULL"),
    )
    supersedes_match_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("requirement_evidence_matches.id", ondelete="SET NULL"),
    )

    project: Mapped[BidProject] = relationship(back_populates="evidence_matches")
    requirement: Mapped[Requirement] = relationship(back_populates="evidence_matches")
    primary_company_document: Mapped[Document | None] = relationship(
        foreign_keys=[primary_company_document_id],
    )
    primary_company_chunk: Mapped[DocumentChunk | None] = relationship(
        foreign_keys=[primary_company_chunk_id],
    )
    company_links: Mapped[list[RequirementEvidenceMatchLink]] = relationship(
        back_populates="match",
        cascade="all, delete-orphan",
    )
    reviews: Mapped[list[RequirementMatchReview]] = relationship(
        back_populates="match",
        cascade="all, delete-orphan",
        order_by="RequirementMatchReview.created_at.desc()",
    )


class RequirementEvidenceMatchLink(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Company-side evidence for a RequirementEvidenceMatch (not tender EvidenceLink)."""

    __tablename__ = "requirement_evidence_match_links"
    __table_args__ = (
        Index("ix_requirement_evidence_match_links_match_id", "match_id"),
    )

    match_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("requirement_evidence_matches.id", ondelete="CASCADE"),
        nullable=False,
    )
    document_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="SET NULL"),
    )
    chunk_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("document_chunks.id", ondelete="SET NULL"),
    )
    quote: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(64), nullable=False, default="company_support")

    match: Mapped[RequirementEvidenceMatch] = relationship(back_populates="company_links")
    document: Mapped[Document | None] = relationship()
    chunk: Mapped[DocumentChunk | None] = relationship()


class RequirementMatchReview(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Immutable audit row for a human review action on a RequirementEvidenceMatch."""

    __tablename__ = "requirement_match_reviews"
    __table_args__ = (
        Index("ix_requirement_match_reviews_project_id", "project_id"),
        Index("ix_requirement_match_reviews_match_id", "match_id"),
        Index(
            "uq_requirement_match_reviews_idempotency",
            "project_id",
            "match_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
    )

    project_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("bid_projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    match_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("requirement_evidence_matches.id", ondelete="CASCADE"),
        nullable=False,
    )
    action: Mapped[MatchReviewAction] = mapped_column(
        match_review_action_enum,
        nullable=False,
    )
    from_review_status: Mapped[MatchReviewStatus] = mapped_column(
        match_review_status_enum,
        nullable=False,
    )
    to_review_status: Mapped[MatchReviewStatus] = mapped_column(
        match_review_status_enum,
        nullable=False,
    )
    comment: Mapped[str | None] = mapped_column(Text)
    reason_code: Mapped[MatchReviewReasonCode | None] = mapped_column(
        match_review_reason_code_enum,
    )
    actor_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    actor_label: Mapped[str] = mapped_column(String(128), nullable=False)
    actor_authn: Mapped[ActorAuthn] = mapped_column(
        actor_authn_enum,
        nullable=False,
        default=ActorAuthn.unverified_local_operator,
    )
    idempotency_key: Mapped[str | None] = mapped_column(String(128))

    project: Mapped[BidProject] = relationship(back_populates="match_reviews")
    match: Mapped[RequirementEvidenceMatch] = relationship(back_populates="reviews")
