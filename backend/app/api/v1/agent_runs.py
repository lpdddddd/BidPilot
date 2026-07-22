"""API routes for LangGraph agent business loop runs."""

from __future__ import annotations

import json
from collections.abc import Iterator
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.agent_run import (
    AgentEventsResponse,
    AgentResultResponse,
    AgentRunListResponse,
    AgentRunRead,
    AgentRunStartRequest,
)
from app.services.agent_run.service import AgentRunService

# Mounted at /projects
project_router = APIRouter()
# Mounted at /agent-runs
run_router = APIRouter()


@project_router.post(
    "/{project_id}/agent-runs",
    response_model=AgentRunRead,
    status_code=201,
)
def start_agent_run(
    project_id: UUID,
    payload: AgentRunStartRequest | None = None,
    db: Session = Depends(get_db),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> AgentRunRead:
    return AgentRunService(db).start_run(
        project_id,
        payload or AgentRunStartRequest(),
        idempotency_key=idempotency_key,
        execute=True,
    )


@project_router.get(
    "/{project_id}/agent-runs",
    response_model=AgentRunListResponse,
)
def list_agent_runs(
    project_id: UUID,
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> AgentRunListResponse:
    return AgentRunService(db).list_for_project(project_id, limit=limit)


@project_router.get(
    "/{project_id}/agent-runs/latest",
    response_model=AgentRunRead | None,
)
def get_latest_agent_run(
    project_id: UUID,
    db: Session = Depends(get_db),
) -> AgentRunRead | None:
    return AgentRunService(db).get_latest(project_id)


@project_router.get(
    "/{project_id}/agent-runs/{run_id}",
    response_model=AgentRunRead,
)
def get_project_agent_run(
    project_id: UUID,
    run_id: UUID,
    db: Session = Depends(get_db),
) -> AgentRunRead:
    return AgentRunService(db).get_run(run_id, project_id=project_id)


@project_router.get(
    "/{project_id}/agent-runs/{run_id}/events",
    response_model=AgentEventsResponse,
)
def get_project_agent_events(
    project_id: UUID,
    run_id: UUID,
    db: Session = Depends(get_db),
) -> AgentEventsResponse:
    return AgentRunService(db).get_events(run_id, project_id=project_id)


@project_router.get(
    "/{project_id}/agent-runs/{run_id}/result",
    response_model=AgentResultResponse,
)
def get_project_agent_result(
    project_id: UUID,
    run_id: UUID,
    db: Session = Depends(get_db),
) -> AgentResultResponse:
    return AgentRunService(db).get_result(run_id, project_id=project_id)


@project_router.post(
    "/{project_id}/agent-runs/{run_id}/resume",
    response_model=AgentRunRead,
)
def resume_project_agent_run(
    project_id: UUID,
    run_id: UUID,
    db: Session = Depends(get_db),
) -> AgentRunRead:
    AgentRunService(db).get_run(run_id, project_id=project_id)
    return AgentRunService(db).resume_run(run_id)


@project_router.post(
    "/{project_id}/agent-runs/{run_id}/retry",
    response_model=AgentRunRead,
)
def retry_project_agent_run(
    project_id: UUID,
    run_id: UUID,
    db: Session = Depends(get_db),
) -> AgentRunRead:
    AgentRunService(db).get_run(run_id, project_id=project_id)
    return AgentRunService(db).retry_run(run_id)


@project_router.get("/{project_id}/agent-runs/{run_id}/events/stream")
def stream_project_agent_events(
    project_id: UUID,
    run_id: UUID,
    db: Session = Depends(get_db),
) -> StreamingResponse:
    return _sse(db, run_id, project_id=project_id)


@run_router.get("/{run_id}", response_model=AgentRunRead)
def get_agent_run(run_id: UUID, db: Session = Depends(get_db)) -> AgentRunRead:
    return AgentRunService(db).get_run(run_id)


@run_router.get("/{run_id}/events", response_model=AgentEventsResponse)
def get_agent_events(run_id: UUID, db: Session = Depends(get_db)) -> AgentEventsResponse:
    return AgentRunService(db).get_events(run_id)


@run_router.get("/{run_id}/result", response_model=AgentResultResponse)
def get_agent_result(run_id: UUID, db: Session = Depends(get_db)) -> AgentResultResponse:
    return AgentRunService(db).get_result(run_id)


@run_router.post("/{run_id}/resume", response_model=AgentRunRead)
def resume_agent_run(run_id: UUID, db: Session = Depends(get_db)) -> AgentRunRead:
    return AgentRunService(db).resume_run(run_id)


@run_router.post("/{run_id}/retry", response_model=AgentRunRead)
def retry_agent_run(run_id: UUID, db: Session = Depends(get_db)) -> AgentRunRead:
    return AgentRunService(db).retry_run(run_id)


@run_router.get("/{run_id}/events/stream")
def stream_agent_events(
    run_id: UUID, db: Session = Depends(get_db)
) -> StreamingResponse:
    return _sse(db, run_id)


def _sse(
    db: Session, run_id: UUID, *, project_id: UUID | None = None
) -> StreamingResponse:
    events = AgentRunService(db).get_events(run_id, project_id=project_id)

    def gen() -> Iterator[str]:
        payload = {
            "run_id": str(events.run_id),
            "total": events.total,
            "note": "SSE stub — full realtime timeline arrives in Step 11",
        }
        yield f"event: snapshot\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
        for item in events.items:
            yield (
                "event: agent_event\ndata: "
                + json.dumps(item.model_dump(mode="json"), ensure_ascii=False)
                + "\n\n"
            )
        yield "event: done\ndata: {}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


# Back-compat alias expected by some imports
router = project_router
