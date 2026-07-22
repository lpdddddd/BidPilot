"""Agent run event helpers — unified timeline with real tool start/finish."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.agent import AgentEvent, AgentRun, AgentStep, ToolCall
from app.models.enums import AgentRunStatus

_MAX_SEQUENCE_RETRIES = 8

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
    secret_markers = ("password=", "secret=", "api_key", "postgresql://", "private_key")
    lowered = text.lower()
    if any(k in lowered for k in secret_markers):
        return "[redacted]"
    return text[:limit]


def commit_visible(db: Session) -> None:
    """Commit so independent sessions can read mid-run events immediately."""
    db.commit()


def next_event_sequence(db: Session, agent_run_id: UUID) -> int:
    """Allocate next sequence under a row lock on the agent run (0-based)."""
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
    attempt: int | None = None,
    idempotency_key: str | None = None,
    payload_json: dict[str, Any] | None = None,
    occurred_at: datetime | None = None,
    sequence: int | None = None,
) -> AgentEvent:
    """Persist one timeline event. Retries on unique-constraint collisions."""
    if idempotency_key:
        existing = db.scalar(
            select(AgentEvent).where(
                AgentEvent.agent_run_id == agent_run_id,
                AgentEvent.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            return existing

    last_err: Exception | None = None
    for attempt_n in range(_MAX_SEQUENCE_RETRIES):
        if sequence is not None and attempt_n == 0:
            seq = sequence
        else:
            seq = next_event_sequence(db, agent_run_id)
        try:
            with db.begin_nested():
                payload = dict(payload_json or {})
                if attempt is not None:
                    payload.setdefault("attempt", attempt)
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
                    attempt=attempt,
                    idempotency_key=idempotency_key,
                    payload_json=payload or None,
                    occurred_at=occurred_at or _now(),
                )
                db.add(row)
                db.flush()
            return row
        except IntegrityError as exc:
            last_err = exc
            if sequence is not None or idempotency_key:
                # Explicit collision — for idempotency re-read; else raise.
                if idempotency_key:
                    existing = db.scalar(
                        select(AgentEvent).where(
                            AgentEvent.agent_run_id == agent_run_id,
                            AgentEvent.idempotency_key == idempotency_key,
                        )
                    )
                    if existing is not None:
                        return existing
                if sequence is not None:
                    raise
            continue
    assert last_err is not None
    raise last_err


def next_step_index(db: Session, agent_run_id: UUID) -> int:
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
    idempotency_key: str | None = None,
) -> AgentStep:
    """Create AgentStep (running) + ``node_started`` event."""
    key = idempotency_key or f"node_started:{agent_run_id}:{node_name}:{uuid4().hex[:8]}"
    # If a running step already exists for this exact idempotency, reuse.
    existing_ev = db.scalar(
        select(AgentEvent).where(
            AgentEvent.agent_run_id == agent_run_id,
            AgentEvent.idempotency_key == key,
        )
    )
    if existing_ev and existing_ev.agent_step_id:
        step = db.get(AgentStep, existing_ev.agent_step_id)
        if step is not None:
            return step

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
                idempotency_key=key,
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
    idempotency_key: str | None = None,
) -> AgentEvent:
    """Update AgentStep and emit ``node_completed`` / ``node_failed``."""
    step: AgentStep | None = None
    if agent_step_id is not None:
        step = db.get(AgentStep, agent_step_id)
    if step is None:
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
    step_id = step.id if step else agent_step_id
    key = idempotency_key or (f"{event_type}:{agent_run_id}:{step_id}" if step_id else None)
    return record_event(
        db,
        agent_run_id=agent_run_id,
        event_type=event_type,
        node_name=node_name,
        status=final_status,
        agent_step_id=step_id,
        safe_summary=_safe_text(error_message) or f"node {node_name} {final_status}",
        payload_json={"output_status": (output_json or {}).get("status")} if output_json else None,
        idempotency_key=key,
    )


def record_tool_started(
    db: Session,
    *,
    agent_run_id: UUID,
    tool_name: str,
    agent_step_id: UUID | None = None,
    node_name: str | None = None,
    call_id: str | None = None,
    attempt: int = 1,
    arguments_json: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
) -> ToolCall:
    """Create ToolCall + ``tool_started`` BEFORE the real tool invocation."""
    cid = call_id or uuid4().hex
    key = idempotency_key or f"tool_started:{agent_run_id}:{cid}"
    existing = db.scalar(
        select(AgentEvent).where(
            AgentEvent.agent_run_id == agent_run_id,
            AgentEvent.idempotency_key == key,
        )
    )
    if existing and existing.tool_call_id:
        row = db.get(ToolCall, existing.tool_call_id)
        if row is not None:
            return row

    if node_name is None and agent_step_id is not None:
        step = db.get(AgentStep, agent_step_id)
        if step is not None:
            node_name = step.node_name

    now = _now()
    row = ToolCall(
        agent_run_id=agent_run_id,
        agent_step_id=agent_step_id,
        tool_name=tool_name,
        call_id=cid,
        node_name=node_name,
        status="running",
        attempt=attempt,
        arguments_json=None,  # never persist raw sensitive args
        started_at=now,
        finished_at=None,
    )
    del arguments_json  # intentionally unused — do not store
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
        attempt=attempt,
        safe_summary=f"tool {tool_name} started",
        occurred_at=now,
        idempotency_key=key,
    )
    return row


def record_tool_finished(
    db: Session,
    *,
    agent_run_id: UUID,
    tool_call_id: UUID,
    status: str,
    summary: str | None = None,
    error_type: str | None = None,
    idempotency_key: str | None = None,
) -> ToolCall:
    """Update ToolCall + write ``tool_completed`` / ``tool_failed`` AFTER the call."""
    row = db.get(ToolCall, tool_call_id)
    if row is None:
        raise ValueError(f"tool_call not found: {tool_call_id}")

    failed = status in {"error", "failed"}
    if failed:
        tool_status = "error"
    elif status in {"ok", "succeeded", "completed"}:
        tool_status = "ok"
    else:
        tool_status = status

    now = _now()
    started = row.started_at or now
    # Ensure finished_at is strictly after started_at when clocks tie.
    if now <= started:
        from datetime import timedelta

        now = started + timedelta(milliseconds=1)
    duration_ms = max(0, int((now - started).total_seconds() * 1000))
    safe = _safe_text(summary)
    if error_type and failed:
        safe = _safe_text(f"{error_type}: {safe or ''}".strip(": "))

    row.status = tool_status
    row.finished_at = now
    row.duration_ms = duration_ms
    row.result_json = {"summary": safe} if safe else None
    row.error_message = safe if failed else None
    db.flush()

    end_type = EVENT_TOOL_FAILED if failed else EVENT_TOOL_COMPLETED
    key = idempotency_key or f"{end_type}:{agent_run_id}:{row.id}"
    record_event(
        db,
        agent_run_id=agent_run_id,
        event_type=end_type,
        node_name=row.node_name,
        tool_name=row.tool_name,
        status=tool_status,
        duration_ms=duration_ms,
        agent_step_id=row.agent_step_id,
        tool_call_id=row.id,
        call_id=row.call_id,
        attempt=row.attempt,
        safe_summary=safe or f"tool {row.tool_name} {tool_status}",
        occurred_at=now,
        idempotency_key=key,
    )
    return row


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
    attempt: int = 1,
) -> ToolCall:
    """Legacy one-shot helper (tests). Prefer start/finish around real calls."""
    del duration_ms, arguments_json
    row = record_tool_started(
        db,
        agent_run_id=agent_run_id,
        tool_name=tool_name,
        agent_step_id=agent_step_id,
        node_name=node_name,
        call_id=call_id,
        attempt=attempt,
    )
    return record_tool_finished(
        db,
        agent_run_id=agent_run_id,
        tool_call_id=row.id,
        status=status,
        summary=summary,
    )


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
    attempt: int = 1,
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
        attempt=attempt,
    )


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
    del input_json
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


def status_from_str(value: str) -> AgentRunStatus:
    try:
        return AgentRunStatus(value)
    except ValueError:
        return AgentRunStatus.failed
