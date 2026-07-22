"""ORM models for BidPilot Step 12 evaluation center."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import (
    EvaluationCaseStatus,
    EvaluationReferenceKind,
    EvaluationRunStatus,
    EvaluationTargetType,
)
from app.models.types import EnumType

if TYPE_CHECKING:
    from app.models.agent import AgentRun
    from app.models.project import BidProject


class EvaluationSuite(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Versioned evaluation suite (builtin suites have null project_id)."""

    __tablename__ = "evaluation_suites"
    __table_args__ = (
        Index("ix_evaluation_suites_project_id", "project_id"),
        Index("ix_evaluation_suites_name_version", "name", "version"),
    )

    project_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("bid_projects.id", ondelete="CASCADE"),
        nullable=True,
    )
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    manifest_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    dataset_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    evaluator_profile_version: Mapped[str] = mapped_column(String(64), nullable=False)
    task_family_config: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    project: Mapped[BidProject | None] = relationship(back_populates="evaluation_suites")
    runs: Mapped[list[EvaluationRun]] = relationship(back_populates="suite")


class EvaluationRun(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One evaluation execution against a suite + target."""

    __tablename__ = "evaluation_runs"
    __table_args__ = (
        Index("ix_evaluation_runs_project_id", "project_id"),
        Index("ix_evaluation_runs_suite_id", "suite_id"),
        Index("ix_evaluation_runs_status", "status"),
        Index(
            "uq_evaluation_runs_project_idempotency",
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
    suite_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("evaluation_suites.id", ondelete="RESTRICT"),
        nullable=False,
    )
    status: Mapped[EvaluationRunStatus] = mapped_column(
        EnumType(EvaluationRunStatus, name="evaluation_run_status"),
        nullable=False,
        default=EvaluationRunStatus.queued,
    )
    target_type: Mapped[EvaluationTargetType] = mapped_column(
        EnumType(EvaluationTargetType, name="evaluation_target_type"),
        nullable=False,
    )
    target_config_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    dataset_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    evaluator_version: Mapped[str] = mapped_column(String(64), nullable=False)
    seed: Mapped[int] = mapped_column(Integer, nullable=False, default=42)
    total_cases: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completed_cases: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    passed_cases: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_cases: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_cases: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    overall_score: Mapped[float | None] = mapped_column(Float)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    safe_error_summary: Mapped[str | None] = mapped_column(Text)
    source_commit_sha: Mapped[str | None] = mapped_column(String(64))
    created_by: Mapped[str | None] = mapped_column(String(256))
    idempotency_key: Mapped[str | None] = mapped_column(String(255))
    execution_claim_token: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    summary_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    filter_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    cancel_requested: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )

    project: Mapped[BidProject] = relationship(back_populates="evaluation_runs")
    suite: Mapped[EvaluationSuite] = relationship(back_populates="runs")
    case_results: Mapped[list[EvaluationCaseResult]] = relationship(
        back_populates="evaluation_run",
        cascade="all, delete-orphan",
        order_by="EvaluationCaseResult.case_key",
    )


class EvaluationCaseResult(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Per-case outcome within an evaluation run."""

    __tablename__ = "evaluation_case_results"
    __table_args__ = (
        UniqueConstraint(
            "evaluation_run_id",
            "case_key",
            name="uq_evaluation_case_results_run_case_key",
        ),
        Index("ix_evaluation_case_results_run_id", "evaluation_run_id"),
        Index("ix_evaluation_case_results_task_family", "task_family"),
        Index("ix_evaluation_case_results_status", "status"),
        Index("ix_evaluation_case_results_split", "split"),
    )

    evaluation_run_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("evaluation_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    case_key: Mapped[str] = mapped_column(String(128), nullable=False)
    case_content_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    task_family: Mapped[str] = mapped_column(String(64), nullable=False)
    split: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[EvaluationCaseStatus] = mapped_column(
        EnumType(EvaluationCaseStatus, name="evaluation_case_status"),
        nullable=False,
        default=EvaluationCaseStatus.pending,
    )
    response_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    reference_kind: Mapped[EvaluationReferenceKind] = mapped_column(
        EnumType(EvaluationReferenceKind, name="evaluation_reference_kind"),
        nullable=False,
        default=EvaluationReferenceKind.auto_reference,
    )
    score: Mapped[float | None] = mapped_column(Float)
    passed: Mapped[bool | None] = mapped_column(Boolean)
    hard_gate_failures: Mapped[list[Any] | None] = mapped_column(JSONB)
    safe_error_summary: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    agent_run_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("agent_runs.id", ondelete="SET NULL"),
    )
    input_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    reference_summary: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    evaluation_run: Mapped[EvaluationRun] = relationship(back_populates="case_results")
    agent_run: Mapped[AgentRun | None] = relationship()
    metric_results: Mapped[list[EvaluationMetricResult]] = relationship(
        back_populates="case_result",
        cascade="all, delete-orphan",
        order_by="EvaluationMetricResult.metric_name",
    )


class EvaluationMetricResult(Base, UUIDPrimaryKeyMixin):
    """One metric observation for a case result."""

    __tablename__ = "evaluation_metric_results"
    __table_args__ = (
        UniqueConstraint(
            "case_result_id",
            "metric_name",
            "metric_version",
            name="uq_evaluation_metric_results_case_metric_version",
        ),
        Index("ix_evaluation_metric_results_case_result_id", "case_result_id"),
        Index("ix_evaluation_metric_results_metric_name", "metric_name"),
    )

    case_result_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("evaluation_case_results.id", ondelete="CASCADE"),
        nullable=False,
    )
    metric_name: Mapped[str] = mapped_column(String(128), nullable=False)
    metric_version: Mapped[str] = mapped_column(String(64), nullable=False)
    value: Mapped[float | None] = mapped_column(Float)
    applicable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    weight: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    threshold: Mapped[float | None] = mapped_column(Float)
    passed: Mapped[bool | None] = mapped_column(Boolean)
    evidence_summary: Mapped[str | None] = mapped_column(Text)
    reference_kind: Mapped[EvaluationReferenceKind] = mapped_column(
        EnumType(EvaluationReferenceKind, name="evaluation_reference_kind", create_type=False),
        nullable=False,
        default=EvaluationReferenceKind.auto_reference,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
        nullable=False,
    )

    case_result: Mapped[EvaluationCaseResult] = relationship(back_populates="metric_results")
