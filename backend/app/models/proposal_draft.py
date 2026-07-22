from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import (
    ActorAuthn,
    ExtractionRunStatus,
    ProposalDraftGenerationMode,
    ProposalDraftReviewAction,
    ProposalDraftSourceRole,
    ProposalDraftStatus,
    ProposalDraftVersionKind,
)
from app.models.types import (
    EnumType,
    actor_authn_enum,
    proposal_draft_generation_mode_enum,
    proposal_draft_review_action_enum,
    proposal_draft_source_role_enum,
    proposal_draft_status_enum,
    proposal_draft_version_kind_enum,
)

if TYPE_CHECKING:
    from app.models.project import BidProject


class ProposalDraft(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Human-reviewable response-preparation draft for a BidProject."""

    __tablename__ = "proposal_drafts"
    __table_args__ = (
        Index("ix_proposal_drafts_project_id", "project_id"),
        Index("ix_proposal_drafts_status", "status"),
    )

    project_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("bid_projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    status: Mapped[ProposalDraftStatus] = mapped_column(
        proposal_draft_status_enum,
        nullable=False,
        default=ProposalDraftStatus.draft_pending_review,
    )
    current_version_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(
            "proposal_draft_versions.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_proposal_drafts_current_version_id",
        ),
    )
    created_by: Mapped[str | None] = mapped_column(String(128))
    review_lock_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    project: Mapped[BidProject] = relationship(back_populates="proposal_drafts")
    versions: Mapped[list[ProposalDraftVersion]] = relationship(
        back_populates="draft",
        cascade="all, delete-orphan",
        foreign_keys="ProposalDraftVersion.draft_id",
        order_by="ProposalDraftVersion.version_number.desc()",
    )
    reviews: Mapped[list[ProposalDraftReview]] = relationship(
        back_populates="draft",
        cascade="all, delete-orphan",
        order_by="ProposalDraftReview.created_at.desc()",
    )
    generation_runs: Mapped[list[ProposalDraftGenerationRun]] = relationship(
        back_populates="draft",
        foreign_keys="ProposalDraftGenerationRun.draft_id",
    )


class ProposalDraftVersion(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Immutable draft version (model-generated or manual revision)."""

    __tablename__ = "proposal_draft_versions"
    __table_args__ = (
        Index("ix_proposal_draft_versions_project_id", "project_id"),
        Index("ix_proposal_draft_versions_draft_id", "draft_id"),
        Index(
            "uq_proposal_draft_versions_draft_number",
            "draft_id",
            "version_number",
            unique=True,
        ),
    )

    project_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("bid_projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    draft_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("proposal_drafts.id", ondelete="CASCADE"),
        nullable=False,
    )
    parent_version_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("proposal_draft_versions.id", ondelete="SET NULL"),
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    version_kind: Mapped[ProposalDraftVersionKind] = mapped_column(
        proposal_draft_version_kind_enum,
        nullable=False,
        default=ProposalDraftVersionKind.generated,
    )
    generation_run_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(
            "proposal_draft_generation_runs.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_proposal_draft_versions_generation_run_id",
        ),
    )
    source_snapshot_hash: Mapped[str | None] = mapped_column(String(128))
    content_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    content_markdown: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str | None] = mapped_column(String(128))
    supersedes_version_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("proposal_draft_versions.id", ondelete="SET NULL"),
    )
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    project: Mapped[BidProject] = relationship(back_populates="proposal_draft_versions")
    draft: Mapped[ProposalDraft] = relationship(
        back_populates="versions",
        foreign_keys=[draft_id],
    )
    sources: Mapped[list[ProposalDraftSource]] = relationship(
        back_populates="draft_version",
        cascade="all, delete-orphan",
    )
    generation_run: Mapped[ProposalDraftGenerationRun | None] = relationship(
        foreign_keys=[generation_run_id],
        post_update=True,
    )


class ProposalDraftSource(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Frozen source snapshot row for a draft version."""

    __tablename__ = "proposal_draft_sources"
    __table_args__ = (
        Index("ix_proposal_draft_sources_project_id", "project_id"),
        Index("ix_proposal_draft_sources_draft_version_id", "draft_version_id"),
    )

    project_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("bid_projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    draft_version_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("proposal_draft_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    requirement_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("requirements.id", ondelete="SET NULL"),
    )
    match_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("requirement_evidence_matches.id", ondelete="SET NULL"),
    )
    # Soft reference: tender EvidenceLink.id or company RequirementEvidenceMatchLink.id
    evidence_link_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    source_role: Mapped[ProposalDraftSourceRole] = mapped_column(
        proposal_draft_source_role_enum,
        nullable=False,
    )
    source_quote: Mapped[str | None] = mapped_column(Text)
    location_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    project: Mapped[BidProject] = relationship(back_populates="proposal_draft_sources")
    draft_version: Mapped[ProposalDraftVersion] = relationship(back_populates="sources")


class ProposalDraftReview(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Immutable audit row for draft review / reopen actions."""

    __tablename__ = "proposal_draft_reviews"
    __table_args__ = (
        Index("ix_proposal_draft_reviews_project_id", "project_id"),
        Index("ix_proposal_draft_reviews_draft_id", "draft_id"),
        Index(
            "uq_proposal_draft_reviews_idempotency",
            "project_id",
            "draft_id",
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
    draft_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("proposal_drafts.id", ondelete="CASCADE"),
        nullable=False,
    )
    draft_version_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("proposal_draft_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    action: Mapped[ProposalDraftReviewAction] = mapped_column(
        proposal_draft_review_action_enum,
        nullable=False,
    )
    comment: Mapped[str | None] = mapped_column(Text)
    actor_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    actor_label: Mapped[str] = mapped_column(String(128), nullable=False)
    actor_authn: Mapped[ActorAuthn] = mapped_column(
        actor_authn_enum,
        nullable=False,
        default=ActorAuthn.unverified_local_operator,
    )
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    payload_hash: Mapped[str | None] = mapped_column(String(128))

    project: Mapped[BidProject] = relationship(back_populates="proposal_draft_reviews")
    draft: Mapped[ProposalDraft] = relationship(back_populates="reviews")


class ProposalDraftGenerationRun(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Async run that generates a ProposalDraftVersion from confirmed matches."""

    __tablename__ = "proposal_draft_generation_runs"
    __table_args__ = (
        Index("ix_proposal_draft_generation_runs_project_id", "project_id"),
        Index("ix_proposal_draft_generation_runs_status", "status"),
        Index(
            "uq_proposal_draft_generation_runs_idempotency",
            "project_id",
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
    status: Mapped[ExtractionRunStatus] = mapped_column(
        EnumType(ExtractionRunStatus, name="extraction_run_status", create_type=False),
        nullable=False,
        default=ExtractionRunStatus.queued,
    )
    mode: Mapped[ProposalDraftGenerationMode] = mapped_column(
        proposal_draft_generation_mode_enum,
        nullable=False,
        default=ProposalDraftGenerationMode.response_outline,
    )
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    requested_requirement_ids: Mapped[list[Any] | None] = mapped_column(JSONB)
    eligible_requirement_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    excluded_requirement_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    excluded_reason_summary: Mapped[str | None] = mapped_column(Text)
    draft_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("proposal_drafts.id", ondelete="SET NULL"),
    )
    draft_version_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(
            "proposal_draft_versions.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_proposal_draft_generation_runs_draft_version_id",
        ),
    )
    error_summary: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[str | None] = mapped_column(String(128))
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    config_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    project: Mapped[BidProject] = relationship(back_populates="proposal_draft_runs")
    draft: Mapped[ProposalDraft | None] = relationship(
        back_populates="generation_runs",
        foreign_keys=[draft_id],
    )
