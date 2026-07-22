from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
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
        Index("ix_agent_runs_idempotency_key", "idempotency_key"),
        Index(
            "uq_agent_runs_project_id_idempotency_key",
            "project_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
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

    current_node: Mapped[str | None] = mapped_column(String(128))
    graph_version: Mapped[str | None] = mapped_column(String(64), default="bidpilot-agent-1.0.0")
    idempotency_key: Mapped[str | None] = mapped_column(String(255))
    input_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    output_summary_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error_code: Mapped[str | None] = mapped_column(String(128))
    error_summary: Mapped[str | None] = mapped_column(Text)
    # Atomic counter for unified AgentEvent.sequence (next value to allocate).
    event_sequence: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )

    organization: Mapped[Organization] = relationship(back_populates="agent_runs")
    project: Mapped[BidProject | None] = relationship(back_populates="agent_runs")
    conversation: Mapped[Conversation | None] = relationship(back_populates="agent_runs")
    steps: Mapped[list[AgentStep]] = relationship(back_populates="agent_run")
    tool_calls: Mapped[list[ToolCall]] = relationship(back_populates="agent_run")
    checkpoints: Mapped[list[AgentCheckpoint]] = relationship(back_populates="agent_run")
    events: Mapped[list[AgentEvent]] = relationship(back_populates="agent_run")


class AgentStep(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "agent_steps"
    __table_args__ = (
        UniqueConstraint(
            "agent_run_id",
            "step_index",
            name="uq_agent_steps_agent_run_id_step_index",
        ),
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
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
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
        Index("ix_tool_calls_call_id", "call_id"),
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
    call_id: Mapped[str | None] = mapped_column(String(64))
    node_name: Mapped[str | None] = mapped_column(String(255))
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    arguments_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    result_json: Mapped[dict[str, Any] | Any | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(64), nullable=False, default="pending")
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    agent_run: Mapped[AgentRun] = relationship(back_populates="tool_calls")
    agent_step: Mapped[AgentStep | None] = relationship(back_populates="tool_calls")


class AgentEvent(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Unified, strictly ordered timeline for one AgentRun.

    ``(agent_run_id, sequence)`` is unique. Sequence is allocated at write time
    via ``AgentRun.event_sequence`` under a row lock — never guessed or offset.
    """

    __tablename__ = "agent_events"
    __table_args__ = (
        UniqueConstraint(
            "agent_run_id",
            "sequence",
            name="uq_agent_events_agent_run_id_sequence",
        ),
        Index("ix_agent_events_agent_run_id_sequence", "agent_run_id", "sequence"),
        Index("ix_agent_events_event_type", "event_type"),
        Index(
            "uq_agent_events_agent_run_id_idempotency_key",
            "agent_run_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
    )

    agent_run_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("agent_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    node_name: Mapped[str | None] = mapped_column(String(255))
    tool_name: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str | None] = mapped_column(String(64))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    safe_summary: Mapped[str | None] = mapped_column(Text)
    agent_step_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("agent_steps.id", ondelete="SET NULL"),
        index=True,
    )
    tool_call_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tool_calls.id", ondelete="SET NULL"),
        index=True,
    )
    call_id: Mapped[str | None] = mapped_column(String(64))
    attempt: Mapped[int | None] = mapped_column(Integer)
    idempotency_key: Mapped[str | None] = mapped_column(String(255))
    payload_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    agent_run: Mapped[AgentRun] = relationship(back_populates="events")
    agent_step: Mapped[AgentStep | None] = relationship()
    tool_call: Mapped[ToolCall | None] = relationship()


class AgentCheckpoint(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Custom DB checkpoint store (thread_id == run_id) for resume without PG checkpointer."""

    __tablename__ = "agent_checkpoints"
    __table_args__ = (
        Index("ix_agent_checkpoints_thread_id", "thread_id"),
        UniqueConstraint(
            "thread_id",
            "checkpoint_id",
            name="uq_agent_checkpoints_thread_id_checkpoint_id",
        ),
    )

    agent_run_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("agent_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    thread_id: Mapped[str] = mapped_column(String(64), nullable=False)
    checkpoint_id: Mapped[str] = mapped_column(String(128), nullable=False)
    node_name: Mapped[str | None] = mapped_column(String(128))
    checkpoint_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    agent_run: Mapped[AgentRun] = relationship(back_populates="checkpoints")
