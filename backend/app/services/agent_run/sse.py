"""SSE streaming for AgentEvent timeline (Step 11)."""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from uuid import UUID

from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from app.db.session import SessionLocal
from app.models.agent import AgentRun
from app.models.enums import AgentRunStatus
from app.services.agent_run.service import AgentRunService

TERMINAL = {
    AgentRunStatus.completed,
    AgentRunStatus.completed_with_warnings,
    AgentRunStatus.blocked,
    AgentRunStatus.failed,
    AgentRunStatus.cancelled,
}

HEARTBEAT_SECONDS = 5.0
POLL_SECONDS = 0.35
SESSION_FACTORY = SessionLocal


def _safe_event_payload(item) -> dict:
    return {
        "run_id": None,  # filled by caller
        "sequence": item.sequence,
        "event_type": item.event_type,
        "node_name": item.node_name,
        "tool_name": item.tool_name,
        "status": item.status,
        "timestamp": item.timestamp.isoformat() if item.timestamp else None,
        "duration_ms": item.duration_ms,
        "safe_summary": item.safe_summary or item.summary,
        "agent_step_id": str(item.agent_step_id) if item.agent_step_id else None,
        "tool_call_id": str(item.tool_call_id) if item.tool_call_id else None,
        "attempt": item.attempt,
    }


def iter_agent_events_sse(
    run_id: UUID,
    *,
    project_id: UUID | None = None,
    after_sequence: int | None = None,
    last_event_id: str | None = None,
) -> Iterator[str]:
    """Synchronous SSE chunk iterator (also used by StreamingResponse)."""
    probe = SESSION_FACTORY()
    try:
        AgentRunService(probe).get_run(run_id, project_id=project_id)
    finally:
        probe.close()

    start_after = after_sequence
    if start_after is None and last_event_id:
        try:
            start_after = int(last_event_id)
        except ValueError:
            start_after = None
    if start_after is None:
        start_after = -1

    last_seq = start_after
    last_heartbeat = time.monotonic()
    while True:
        session = SESSION_FACTORY()
        try:
            svc = AgentRunService(session)
            run = session.get(AgentRun, run_id)
            if run is None:
                yield 'event: error\ndata: {"detail":"agent run not found"}\n\n'
                return
            if project_id is not None and run.project_id != project_id:
                yield 'event: error\ndata: {"detail":"agent run not found"}\n\n'
                return

            batch = svc.get_events(run_id, project_id=project_id, after_sequence=last_seq)
            for item in batch.items:
                payload = _safe_event_payload(item)
                payload["run_id"] = str(run_id)
                data = json.dumps(payload, ensure_ascii=False)
                yield f"id: {item.sequence}\nevent: agent_event\ndata: {data}\n\n"
                last_seq = item.sequence

            status = run.status
            if status in TERMINAL:
                trail = svc.get_events(run_id, project_id=project_id, after_sequence=last_seq)
                for item in trail.items:
                    payload = _safe_event_payload(item)
                    payload["run_id"] = str(run_id)
                    data = json.dumps(payload, ensure_ascii=False)
                    yield f"id: {item.sequence}\nevent: agent_event\ndata: {data}\n\n"
                    last_seq = item.sequence
                done = {
                    "run_id": str(run_id),
                    "status": status.value if hasattr(status, "value") else str(status),
                    "last_sequence": last_seq,
                }
                yield ("event: run_status\ndata: " + json.dumps(done, ensure_ascii=False) + "\n\n")
                yield "event: done\ndata: {}\n\n"
                return
        except HTTPException as exc:
            yield (
                "event: error\ndata: "
                + json.dumps({"detail": exc.detail}, ensure_ascii=False)
                + "\n\n"
            )
            return
        finally:
            session.close()

        now = time.monotonic()
        if now - last_heartbeat >= HEARTBEAT_SECONDS:
            yield f"event: heartbeat\ndata: {json.dumps({'ts': time.time()})}\n\n"
            last_heartbeat = now
        time.sleep(POLL_SECONDS)


def stream_agent_events_sse(
    run_id: UUID,
    *,
    project_id: UUID | None = None,
    after_sequence: int | None = None,
    last_event_id: str | None = None,
) -> StreamingResponse:
    """Live SSE: catch-up then poll with short-lived sessions; no held DB locks."""
    # Validate access before accepting the stream.
    probe = SESSION_FACTORY()
    try:
        AgentRunService(probe).get_run(run_id, project_id=project_id)
    finally:
        probe.close()

    return StreamingResponse(
        iter_agent_events_sse(
            run_id,
            project_id=project_id,
            after_sequence=after_sequence,
            last_event_id=last_event_id,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
