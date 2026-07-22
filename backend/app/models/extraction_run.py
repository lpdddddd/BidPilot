from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, Integer, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import ExtractionRunStatus
from app.models.types import EnumType

if TYPE_CHECKING:
    from app.models.project import BidProject


class RequirementExtractionRun(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "requirement_extraction_runs"
    __table_args__ = (
        Index("ix_requirement_extraction_runs_project_id", "project_id"),
        Index("ix_requirement_extraction_runs_status", "status"),
    )

    project_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("bid_projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[ExtractionRunStatus] = mapped_column(
        EnumType(ExtractionRunStatus, name="extraction_run_status"),
        nullable=False,
        default=ExtractionRunStatus.queued,
    )
    document_ids_json: Mapped[list[Any] | None] = mapped_column(JSONB)
    document_types_json: Mapped[list[Any] | None] = mapped_column(JSONB)
    total_chunks: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    processed_chunks: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    candidate_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    merged_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    conflict_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_summary: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    config_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    project: Mapped[BidProject] = relationship(back_populates="extraction_runs")
