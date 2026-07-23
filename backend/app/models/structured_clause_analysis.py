"""Persisted Course LoRA / Base structured clause analysis rows."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import Boolean, Float, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.project import BidProject


class StructuredClauseAnalysis(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "structured_clause_analyses"
    __table_args__ = (
        Index("ix_structured_clause_analyses_project_id", "project_id"),
        Index("ix_structured_clause_analyses_task_type", "task_type"),
        Index("ix_structured_clause_analyses_created_at", "created_at"),
    )

    project_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("bid_projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    task_type: Mapped[str] = mapped_column(String(64), nullable=False)
    clause_text: Mapped[str] = mapped_column(Text, nullable=False)
    raw_output: Mapped[str] = mapped_column(Text, nullable=False, default="")
    parsed_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    schema_valid: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    required_field_coverage: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    missing_fields_json: Mapped[list[Any] | None] = mapped_column(JSONB)
    parse_error: Mapped[str | None] = mapped_column(String(512))
    requested_model_id: Mapped[str] = mapped_column(String(128), nullable=False)
    resolved_model_id: Mapped[str | None] = mapped_column(String(128))
    served_model_name: Mapped[str | None] = mapped_column(String(256))
    model_type: Mapped[str | None] = mapped_column(String(32))
    adapter_version: Mapped[str | None] = mapped_column(String(64))
    dataset_version: Mapped[str | None] = mapped_column(String(128))
    fallback_used: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    latency_ms: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    capability: Mapped[str] = mapped_column(String(64), nullable=False)

    project: Mapped[BidProject] = relationship(back_populates="structured_clause_analyses")
