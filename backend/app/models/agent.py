from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import AgentRunStatus
from app.models.types import EnumType

if TYPE_CHECKING:
    from app.models.conversation import Conversation
    from app.models.organization import Organization
    from app.models.project import BidProject


class AgentRun(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "agent_runs"
    __table_args__ = (
        Index("ix_agent_runs_organization_id_project_id", "organization_id", "project_id"),
        Index("ix_agent_runs_status", "status"),
        Index("ix_agent_runs_conversation_id", "conversation_id"),
    )

    organization_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    project_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("bid_projects.id", ondelete="SET NULL"),
        index=True,
    )
    conversation_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="SET NULL"),
    )
    status: Mapped[AgentRunStatus] = mapped_column(
        EnumType(AgentRunStatus, name="agent_run_status"),
        nullable=False,
        default=AgentRunStatus.pending,
    )
    intent: Mapped[str | None] = mapped_column(String(255))
    state_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_message: Mapped[str | None] = mapped_column(Text)

    organization: Mapped[Organization] = relationship(back_populates="agent_runs")
    project: Mapped[BidProject | None] = relationship(back_populates="agent_runs")
    conversation: Mapped[Conversation | None] = relationship(back_populates="agent_runs")
    steps: Mapped[list[AgentStep]] = relationship(back_populates="agent_run")
    tool_calls: Mapped[list[ToolCall]] = relationship(back_populates="agent_run")


class AgentStep(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "agent_steps"
    __table_args__ = (
        Index("ix_agent_steps_agent_run_id_step_index", "agent_run_id", "step_index"),
    )

    agent_run_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("agent_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    step_index: Mapped[int] = mapped_column(Integer, nullable=False)
    node_name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(64), nullable=False, default="pending")
    input_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    output_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    agent_run: Mapped[AgentRun] = relationship(back_populates="steps")
    tool_calls: Mapped[list[ToolCall]] = relationship(back_populates="agent_step")


class ToolCall(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "tool_calls"
    __table_args__ = (
        Index("ix_tool_calls_agent_run_id", "agent_run_id"),
        Index("ix_tool_calls_tool_name", "tool_name"),
    )

    agent_run_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("agent_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    agent_step_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("agent_steps.id", ondelete="SET NULL"),
        index=True,
    )
    tool_name: Mapped[str] = mapped_column(String(255), nullable=False)
    arguments_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    result_json: Mapped[dict[str, Any] | Any | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(64), nullable=False, default="pending")
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    error_message: Mapped[str | None] = mapped_column(Text)

    agent_run: Mapped[AgentRun] = relationship(back_populates="tool_calls")
    agent_step: Mapped[AgentStep | None] = relationship(back_populates="tool_calls")
