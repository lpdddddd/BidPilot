from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import Boolean, ForeignKey, Index, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import (
    MatchStatus,
    QualityLevel,
    RequirementCategory,
    ReviewStatus,
    RiskLevel,
)
from app.models.types import (
    EnumType,
    quality_level_enum,
    review_status_enum,
    risk_level_enum,
)

if TYPE_CHECKING:
    from app.models.company import CompanyProfile
    from app.models.document import Document, DocumentChunk
    from app.models.project import BidProject


class Requirement(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "requirements"
    __table_args__ = (
        Index("ix_requirements_project_id_category", "project_id", "category"),
        Index("ix_requirements_project_id_risk_level", "project_id", "risk_level"),
        Index("ix_requirements_requirement_code", "requirement_code"),
        Index("ix_requirements_review_status", "review_status"),
    )

    project_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("bid_projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_document_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="SET NULL"),
        index=True,
    )
    requirement_code: Mapped[str | None] = mapped_column(String(128))
    category: Mapped[RequirementCategory] = mapped_column(
        EnumType(RequirementCategory, name="requirement_category"),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(1024), nullable=False)
    normalized_requirement: Mapped[str | None] = mapped_column(Text)
    mandatory: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    score: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    risk_level: Mapped[RiskLevel] = mapped_column(
        risk_level_enum,
        nullable=False,
        default=RiskLevel.medium,
    )
    source_page: Mapped[int | None] = mapped_column(Integer)
    source_section: Mapped[str | None] = mapped_column(String(512))
    source_clause_id: Mapped[str | None] = mapped_column(String(128))
    evidence_required_json: Mapped[dict[str, Any] | list[Any] | None] = mapped_column(JSONB)
    quality_level: Mapped[QualityLevel] = mapped_column(
        quality_level_enum,
        nullable=False,
        default=QualityLevel.pending,
    )
    review_status: Mapped[ReviewStatus] = mapped_column(
        review_status_enum,
        nullable=False,
        default=ReviewStatus.unreviewed,
    )
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    project: Mapped[BidProject] = relationship(back_populates="requirements")
    source_document: Mapped[Document | None] = relationship(back_populates="sourced_requirements")
    evidence_links: Mapped[list[EvidenceLink]] = relationship(back_populates="requirement")
    matches: Mapped[list[RequirementMatch]] = relationship(back_populates="requirement")


class EvidenceLink(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "evidence_links"
    __table_args__ = (Index("ix_evidence_links_requirement_id", "requirement_id"),)

    requirement_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("requirements.id", ondelete="CASCADE"),
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
    evidence_type: Mapped[str | None] = mapped_column(String(128))
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    notes: Mapped[str | None] = mapped_column(Text)

    requirement: Mapped[Requirement] = relationship(back_populates="evidence_links")
    document: Mapped[Document | None] = relationship(back_populates="evidence_links")
    chunk: Mapped[DocumentChunk | None] = relationship(back_populates="evidence_links")


class RequirementMatch(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "requirement_matches"
    __table_args__ = (
        Index(
            "ix_requirement_matches_requirement_id_company_profile_id",
            "requirement_id",
            "company_profile_id",
        ),
        Index("ix_requirement_matches_status", "status"),
    )

    requirement_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("requirements.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    company_profile_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("company_profiles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status: Mapped[MatchStatus] = mapped_column(
        EnumType(MatchStatus, name="match_status"),
        nullable=False,
        default=MatchStatus.uncertain,
    )
    reason: Mapped[str | None] = mapped_column(Text)
    risk_level: Mapped[RiskLevel | None] = mapped_column(risk_level_enum)
    recommended_action: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    quality_level: Mapped[QualityLevel] = mapped_column(
        quality_level_enum,
        nullable=False,
        default=QualityLevel.pending,
    )
    review_status: Mapped[ReviewStatus] = mapped_column(
        review_status_enum,
        nullable=False,
        default=ReviewStatus.unreviewed,
    )

    requirement: Mapped[Requirement] = relationship(back_populates="matches")
    company_profile: Mapped[CompanyProfile] = relationship(back_populates="matches")
    evidence: Mapped[list[RequirementMatchEvidence]] = relationship(back_populates="match")


class RequirementMatchEvidence(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "requirement_match_evidence"
    __table_args__ = (Index("ix_requirement_match_evidence_match_id", "match_id"),)

    match_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("requirement_matches.id", ondelete="CASCADE"),
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

    match: Mapped[RequirementMatch] = relationship(back_populates="evidence")
    document: Mapped[Document | None] = relationship(back_populates="match_evidence")
    chunk: Mapped[DocumentChunk | None] = relationship(back_populates="match_evidence")
