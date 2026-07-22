"""Unified Agent timeline events (strictly monotonic sequence per run)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.agent import AgentEvent, AgentRun, AgentStep, ToolCall
from app.models.enums import AgentRunStatus

_MAX_SEQUENCE_RETRIES = 8

# Canonical event types for the unified timeline.
EVENT_NODE_STARTED = "node_started"
EVENT_NODE_COMPLETED = "node_completed"
EVENT_NODE_FAILED = "node_failed"
EVENT_TOOL_STARTED = "tool_started"
EVENT_TOOL_COMPLETED = "tool_completed"
EVENT_TOOL_FAILED = "tool_failed"
EVENT_RUN_RESUMED = "run_resumed"
EVENT_RUN_COMPLETED = "run_completed"
EVENT_RUN_FAILED = "run_failed"


def _now() -> datetime:
    return datetime.now(UTC)


def _safe_text(value: str | None, *, limit: int = 500) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    # Never persist secrets / connection strings / huge bodies.
    lowered = text.lower()
    secret_markers = ("password=", "secret=", "api_key", "postgresql://", "private_key")
    if any(k in lowered for k in secret_markers):
        return "[redacted]"
    return text[:limit]


def next_event_sequence(db: Session, agent_run_id: UUID) -> int:
    """Allocate the next sequence under a row lock on the agent run.

    Uses ``AgentRun.event_sequence`` as an atomic counter so concurrent writers
    cannot collide. Returns the assigned sequence (0-based).
    """
    run = db.execute(
        select(AgentRun).where(AgentRun.id == agent_run_id).with_for_update()
    ).scalar_one()
    seq = int(run.event_sequence or 0)
    run.event_sequence = seq + 1
    db.flush()
    return seq


def record_event(
    db: Session,
    *,
    agent_run_id: UUID,
    event_type: str,
    status: str = "ok",
    node_name: str | None = None,
    tool_name: str | None = None,
    safe_summary: str | None = None,
    duration_ms: int | None = None,
    agent_step_id: UUID | None = None,
    tool_call_id: UUID | None = None,
    call_id: str | None = None,
    payload_json: dict[str, Any] | None = None,
    occurred_at: datetime | None = None,
    sequence: int | None = None,
) -> AgentEvent:
    """Persist one timeline event with unique ``(run_id, sequence)``.

    Retries allocation on unique-constraint collisions (limited).
    """
    last_err: Exception | None = None
    for attempt in range(_MAX_SEQUENCE_RETRIES):
        if sequence is not None and attempt == 0:
            seq = sequence
        else:
            seq = next_event_sequence(db, agent_run_id)
        try:
            with db.begin_nested():
                row = AgentEvent(
                    agent_run_id=agent_run_id,
                    sequence=seq,
                    event_type=event_type,
                    node_name=node_name,
                    tool_name=tool_name,
                    status=status,
                    duration_ms=duration_ms,
                    safe_summary=_safe_text(safe_summary),
                    agent_step_id=agent_step_id,
                    tool_call_id=tool_call_id,
                    call_id=call_id,
                    payload_json=payload_json,
                    occurred_at=occurred_at or _now(),
                )
                db.add(row)
                db.flush()
            return row
        except IntegrityError as exc:
            last_err = exc
            if sequence is not None:
                raise
            continue
    assert last_err is not None
    raise last_err


def next_step_index(db: Session, agent_run_id: UUID) -> int:
    """Allocate the next AgentStep.step_index under the same run row lock."""
    from sqlalchemy import func

    db.execute(select(AgentRun.id).where(AgentRun.id == agent_run_id).with_for_update())
    current = db.scalar(
        select(func.coalesce(func.max(AgentStep.step_index), -1)).where(
            AgentStep.agent_run_id == agent_run_id
        )
    )
    return int(current) + 1


def record_node_started(
    db: Session,
    *,
    agent_run_id: UUID,
    node_name: str,
) -> AgentStep:
    """Create AgentStep (running) + ``node_started`` event."""
    last_err: Exception | None = None
    for _ in range(_MAX_SEQUENCE_RETRIES):
        idx = next_step_index(db, agent_run_id)
        try:
            with db.begin_nested():
                step = AgentStep(
                    agent_run_id=agent_run_id,
                    step_index=idx,
                    node_name=node_name,
                    status="running",
                    started_at=_now(),
                )
                db.add(step)
                db.flush()
            record_event(
                db,
                agent_run_id=agent_run_id,
                event_type=EVENT_NODE_STARTED,
                node_name=node_name,
                status="running",
                agent_step_id=step.id,
                safe_summary=f"node {node_name} started",
            )
            return step
        except IntegrityError as exc:
            last_err = exc
            continue
    assert last_err is not None
    raise last_err


def record_node_finished(
    db: Session,
    *,
    agent_run_id: UUID,
    node_name: str,
    status: str,
    agent_step_id: UUID | None = None,
    output_json: dict[str, Any] | None = None,
    error_message: str | None = None,
) -> AgentEvent:
    """Update AgentStep and emit ``node_completed`` / ``node_failed``."""
    step: AgentStep | None = None
    if agent_step_id is not None:
        step = db.get(AgentStep, agent_step_id)
    if step is None:
        # Fallback: latest running step for this node on the run.
        step = db.scalar(
            select(AgentStep)
            .where(
                AgentStep.agent_run_id == agent_run_id,
                AgentStep.node_name == node_name,
                AgentStep.status == "running",
            )
            .order_by(AgentStep.step_index.desc())
            .limit(1)
        )
    failed = status in {"failed", "error"} or bool(error_message and status != "succeeded")
    if failed:
        final_status = "failed"
    elif status in {"succeeded", "ok", "completed"}:
        final_status = "succeeded"
    else:
        final_status = status
    if step is not None:
        step.status = final_status
        step.output_json = output_json
        step.error_message = _safe_text(error_message)
        step.finished_at = _now()
        db.flush()
    event_type = EVENT_NODE_FAILED if final_status == "failed" else EVENT_NODE_COMPLETED
    return record_event(
        db,
        agent_run_id=agent_run_id,
        event_type=event_type,
        node_name=node_name,
        status=final_status,
        agent_step_id=step.id if step else agent_step_id,
        safe_summary=_safe_text(error_message) or f"node {node_name} {final_status}",
        payload_json={"output_status": (output_json or {}).get("status")} if output_json else None,
    )


def record_tool_lifecycle(
    db: Session,
    *,
    agent_run_id: UUID,
    tool_name: str,
    status: str,
    summary: str | None = None,
    duration_ms: int | None = None,
    agent_step_id: UUID | None = None,
    node_name: str | None = None,
    arguments_json: dict[str, Any] | None = None,
    call_id: str | None = None,
) -> ToolCall:
    """Create ToolCall + ``tool_started`` then ``tool_completed`` / ``tool_failed``.

    Call sites historically persist once at completion; we still emit both
    start and end events with consecutive sequences so the timeline is real.
    """
    cid = call_id or uuid4().hex
    now = _now()
    failed = status in {"error", "failed"}
    if failed:
        tool_status = "error"
    elif status in {"ok", "succeeded", "completed"}:
        tool_status = "ok"
    else:
        tool_status = status
    safe = _safe_text(summary)

    # Resolve node_name from step when omitted.
    if node_name is None and agent_step_id is not None:
        step = db.get(AgentStep, agent_step_id)
        if step is not None:
            node_name = step.node_name

    row = ToolCall(
        agent_run_id=agent_run_id,
        agent_step_id=agent_step_id,
        tool_name=tool_name,
        call_id=cid,
        node_name=node_name,
        status=tool_status,
        duration_ms=duration_ms,
        arguments_json=arguments_json,
        result_json={"summary": safe} if safe else None,
        error_message=safe if failed else None,
        started_at=now,
        finished_at=now,
    )
    db.add(row)
    db.flush()

    record_event(
        db,
        agent_run_id=agent_run_id,
        event_type=EVENT_TOOL_STARTED,
        node_name=node_name,
        tool_name=tool_name,
        status="running",
        agent_step_id=agent_step_id,
        tool_call_id=row.id,
        call_id=cid,
        safe_summary=f"tool {tool_name} started",
        occurred_at=now,
    )
    end_type = EVENT_TOOL_FAILED if failed else EVENT_TOOL_COMPLETED
    record_event(
        db,
        agent_run_id=agent_run_id,
        event_type=end_type,
        node_name=node_name,
        tool_name=tool_name,
        status=tool_status,
        duration_ms=duration_ms,
        agent_step_id=agent_step_id,
        tool_call_id=row.id,
        call_id=cid,
        safe_summary=safe or f"tool {tool_name} {tool_status}",
        occurred_at=now,
    )
    return row


# Back-compat aliases used by older call sites / tests.
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
    """Legacy helper: one completed step + node_started/node_completed pair."""
    del input_json  # unused; kept for signature compatibility
    if step_index is not None:
        step = AgentStep(
            agent_run_id=agent_run_id,
            step_index=step_index,
            node_name=node_name,
            status=status,
            output_json=output_json,
            error_message=_safe_text(error_message),
            started_at=_now(),
            finished_at=_now(),
        )
        db.add(step)
        db.flush()
        record_event(
            db,
            agent_run_id=agent_run_id,
            event_type=EVENT_NODE_STARTED,
            node_name=node_name,
            status="running",
            agent_step_id=step.id,
        )
        record_event(
            db,
            agent_run_id=agent_run_id,
            event_type=EVENT_NODE_FAILED if status == "failed" else EVENT_NODE_COMPLETED,
            node_name=node_name,
            status=status,
            agent_step_id=step.id,
            safe_summary=_safe_text(error_message),
        )
        return step
    step = record_node_started(db, agent_run_id=agent_run_id, node_name=node_name)
    record_node_finished(
        db,
        agent_run_id=agent_run_id,
        node_name=node_name,
        status=status,
        agent_step_id=step.id,
        output_json=output_json,
        error_message=error_message,
    )
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
    node_name: str | None = None,
    call_id: str | None = None,
) -> ToolCall:
    return record_tool_lifecycle(
        db,
        agent_run_id=agent_run_id,
        tool_name=tool_name,
        status=status,
        summary=summary,
        duration_ms=duration_ms,
        agent_step_id=agent_step_id,
        arguments_json=arguments_json,
        node_name=node_name,
        call_id=call_id,
    )


def status_from_str(value: str) -> AgentRunStatus:
    try:
        return AgentRunStatus(value)
    except ValueError:
        return AgentRunStatus.failed
