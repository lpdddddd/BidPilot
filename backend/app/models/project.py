from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import ProjectStatus
from app.models.types import EnumType

if TYPE_CHECKING:
    from app.models.agent import AgentRun
    from app.models.conversation import Conversation
    from app.models.document import Document, DocumentChunk
    from app.models.extraction_run import RequirementExtractionRun
    from app.models.match_run import (
        RequirementEvidenceMatch,
        RequirementMatchReview,
        RequirementMatchRun,
    )
    from app.models.organization import Organization
    from app.models.proposal_draft import (
        ProposalDraft,
        ProposalDraftGenerationRun,
        ProposalDraftReview,
        ProposalDraftSource,
        ProposalDraftVersion,
    )
    from app.models.requirement import Requirement


class BidProject(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "bid_projects"
    __table_args__ = (
        Index("ix_bid_projects_organization_id_status", "organization_id", "status"),
        Index("ix_bid_projects_project_code", "project_code"),
    )

    organization_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    project_code: Mapped[str] = mapped_column(String(128), nullable=False)
    project_name: Mapped[str] = mapped_column(String(512), nullable=False)
    purchaser: Mapped[str | None] = mapped_column(String(512))
    procurement_agency: Mapped[str | None] = mapped_column(String(512))
    procurement_method: Mapped[str | None] = mapped_column(String(128))
    industry: Mapped[str | None] = mapped_column(String(128))
    region: Mapped[str | None] = mapped_column(String(128))
    budget_cny: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    price_ceiling_cny: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    bid_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[ProjectStatus] = mapped_column(
        EnumType(ProjectStatus, name="project_status"),
        nullable=False,
        default=ProjectStatus.draft,
        index=True,
    )
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    organization: Mapped[Organization] = relationship(back_populates="projects")
    documents: Mapped[list[Document]] = relationship(back_populates="project")
    chunks: Mapped[list[DocumentChunk]] = relationship(back_populates="project")
    requirements: Mapped[list[Requirement]] = relationship(back_populates="project")
    conversations: Mapped[list[Conversation]] = relationship(back_populates="project")
    agent_runs: Mapped[list[AgentRun]] = relationship(back_populates="project")
    extraction_runs: Mapped[list[RequirementExtractionRun]] = relationship(
        back_populates="project"
    )
    match_runs: Mapped[list[RequirementMatchRun]] = relationship(back_populates="project")
    evidence_matches: Mapped[list[RequirementEvidenceMatch]] = relationship(
        back_populates="project"
    )
    match_reviews: Mapped[list[RequirementMatchReview]] = relationship(
        back_populates="project"
    )
    proposal_drafts: Mapped[list[ProposalDraft]] = relationship(
        back_populates="project"
    )
    proposal_draft_versions: Mapped[list[ProposalDraftVersion]] = relationship(
        back_populates="project"
    )
    proposal_draft_sources: Mapped[list[ProposalDraftSource]] = relationship(
        back_populates="project"
    )
    proposal_draft_reviews: Mapped[list[ProposalDraftReview]] = relationship(
        back_populates="project"
    )
    proposal_draft_runs: Mapped[list[ProposalDraftGenerationRun]] = relationship(
        back_populates="project"
    )
