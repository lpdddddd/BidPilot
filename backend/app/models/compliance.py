"""ORM models for deterministic compliance rule engine runs and findings."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import (
    ComplianceFindingStatus,
    ComplianceRuleCategory,
    ComplianceSeverity,
    ExtractionRunStatus,
)
from app.models.types import (
    EnumType,
    compliance_finding_status_enum,
    compliance_rule_category_enum,
    compliance_severity_enum,
)

if TYPE_CHECKING:
    from app.models.match_run import RequirementEvidenceMatch
    from app.models.project import BidProject
    from app.models.proposal_draft import ProposalDraft
    from app.models.requirement import Requirement


class ComplianceRun(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One execution of the deterministic compliance rule engine."""

    __tablename__ = "compliance_runs"
    __table_args__ = (
        Index("ix_compliance_runs_project_id", "project_id"),
        Index("ix_compliance_runs_status", "status"),
        Index(
            "uq_compliance_runs_idempotency",
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
    draft_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("proposal_drafts.id", ondelete="SET NULL"),
    )
    total_checks: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    passed_checks: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    finding_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    severity_counts_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    category_counts_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    rule_ids_json: Mapped[list[Any] | None] = mapped_column(JSONB)
    engine_version: Mapped[str] = mapped_column(String(64), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_summary: Mapped[str | None] = mapped_column(Text)
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    config_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    project: Mapped[BidProject] = relationship(back_populates="compliance_runs")
    draft: Mapped[ProposalDraft | None] = relationship()
    findings: Mapped[list[ComplianceFinding]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="ComplianceFinding.finding_id",
    )


class ComplianceFinding(Base, UUIDPrimaryKeyMixin):
    """One structured finding produced by a compliance rule."""

    __tablename__ = "compliance_findings"
    __table_args__ = (
        Index("ix_compliance_findings_project_id", "project_id"),
        Index("ix_compliance_findings_run_id", "run_id"),
        Index("ix_compliance_findings_rule_id", "rule_id"),
        Index("ix_compliance_findings_severity", "severity"),
        Index("ix_compliance_findings_category", "category"),
        Index(
            "uq_compliance_findings_run_finding_id",
            "run_id",
            "finding_id",
            unique=True,
        ),
    )

    project_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("bid_projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    run_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("compliance_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    finding_id: Mapped[str] = mapped_column(String(256), nullable=False)
    rule_id: Mapped[str] = mapped_column(String(128), nullable=False)
    rule_name: Mapped[str] = mapped_column(String(256), nullable=False)
    category: Mapped[ComplianceRuleCategory] = mapped_column(
        compliance_rule_category_enum,
        nullable=False,
    )
    severity: Mapped[ComplianceSeverity] = mapped_column(
        compliance_severity_enum,
        nullable=False,
    )
    status: Mapped[ComplianceFindingStatus] = mapped_column(
        compliance_finding_status_enum,
        nullable=False,
    )
    message: Mapped[str] = mapped_column(Text, nullable=False)
    remediation: Mapped[str | None] = mapped_column(Text)
    requirement_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("requirements.id", ondelete="SET NULL"),
    )
    match_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("requirement_evidence_matches.id", ondelete="SET NULL"),
    )
    draft_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("proposal_drafts.id", ondelete="SET NULL"),
    )
    evidence_json: Mapped[dict[str, Any] | list[Any] | None] = mapped_column(JSONB)
    source_location_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
        nullable=False,
    )

    project: Mapped[BidProject] = relationship(back_populates="compliance_findings")
    run: Mapped[ComplianceRun] = relationship(back_populates="findings")
    requirement: Mapped[Requirement | None] = relationship()
    match: Mapped[RequirementEvidenceMatch | None] = relationship()
    draft: Mapped[ProposalDraft | None] = relationship()
