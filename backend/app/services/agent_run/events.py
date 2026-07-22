"""Agent run event helpers (steps / tool_calls as timeline events)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.agent import AgentStep, ToolCall
from app.models.enums import AgentRunStatus


def _now() -> datetime:
    return datetime.now(UTC)


def next_step_index(db: Session, agent_run_id: UUID) -> int:
    current = db.scalar(
        select(func.coalesce(func.max(AgentStep.step_index), -1)).where(
            AgentStep.agent_run_id == agent_run_id
        )
    )
    # coalesce(..., -1) already handles empty rows; do not use `or -1`
    # because step_index 0 is falsy and would incorrectly reset to 0.
    return int(current) + 1


def record_step(
    db: Session,
    *,
    agent_run_id: UUID,
    node_name: str,
    status: str,
    input_json: dict[str, Any] | None = None,
    output_json: dict[str, Any] | None = None,
    error_message: str | None = None,
    step_index: int | None = None,
) -> AgentStep:
    idx = step_index if step_index is not None else next_step_index(db, agent_run_id)
    step = AgentStep(
        agent_run_id=agent_run_id,
        step_index=idx,
        node_name=node_name,
        status=status,
        input_json=input_json,
        output_json=output_json,
        error_message=error_message,
        started_at=_now(),
        finished_at=_now(),
    )
    db.add(step)
    db.flush()
    return step


def record_tool_call(
    db: Session,
    *,
    agent_run_id: UUID,
    tool_name: str,
    status: str,
    summary: str | None = None,
    duration_ms: int | None = None,
    agent_step_id: UUID | None = None,
    arguments_json: dict[str, Any] | None = None,
) -> ToolCall:
    row = ToolCall(
        agent_run_id=agent_run_id,
        agent_step_id=agent_step_id,
        tool_name=tool_name,
        status=status,
        duration_ms=duration_ms,
        arguments_json=arguments_json,
        result_json={"summary": summary} if summary else None,
        error_message=summary if status == "error" else None,
    )
    db.add(row)
    db.flush()
    return row


def status_from_str(value: str) -> AgentRunStatus:
    try:
        return AgentRunStatus(value)
    except ValueError:
        return AgentRunStatus.failed
