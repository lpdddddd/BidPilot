"""API routes for LangGraph agent business loop runs (Step 10–11)."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Header, Query
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
from app.services.agent_run import tasks as agent_tasks
from app.services.agent_run.service import AgentRunService
from app.services.agent_run.sse import stream_agent_events_sse

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
    background_tasks: BackgroundTasks,
    payload: AgentRunStartRequest | None = None,
    db: Session = Depends(get_db),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    sync: bool = Query(
        default=False,
        description="If true, execute graph in-request (tests). Default: background.",
    ),
) -> AgentRunRead:
    svc = AgentRunService(db)
    # Persist run first; graph runs in background so clients get run_id immediately.
    run = svc.start_run(
        project_id,
        payload or AgentRunStartRequest(),
        idempotency_key=idempotency_key,
        execute=False,
    )
    if sync:
        return svc.execute_run(run.id)
    # Idempotent: do not start a second executor for the same run.
    if not agent_tasks.is_execute_running(run.id):
        background_tasks.add_task(agent_tasks.run_agent_execute, run.id)
    return run


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
    after_sequence: int | None = Query(default=None, ge=-1),
    db: Session = Depends(get_db),
) -> AgentEventsResponse:
    return AgentRunService(db).get_events(
        run_id, project_id=project_id, after_sequence=after_sequence
    )


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
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    sync: bool = Query(
        default=False,
        description="If true, resume graph in-request. Default: prepare + background.",
    ),
) -> AgentRunRead:
    svc = AgentRunService(db)
    svc.get_run(run_id, project_id=project_id)
    if sync:
        return svc.resume_run(run_id, execute=True)
    run = svc.resume_run(run_id, execute=False)
    if not agent_tasks.is_execute_running(run_id):
        background_tasks.add_task(agent_tasks.run_agent_resume, run_id)
    return run


@project_router.post(
    "/{project_id}/agent-runs/{run_id}/retry",
    response_model=AgentRunRead,
)
def retry_project_agent_run(
    project_id: UUID,
    run_id: UUID,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    sync: bool = Query(
        default=False,
        description="If true, retry graph in-request. Default: prepare + background.",
    ),
) -> AgentRunRead:
    svc = AgentRunService(db)
    svc.get_run(run_id, project_id=project_id)
    if sync:
        return svc.retry_run(run_id, execute=True)
    run = svc.retry_run(run_id, execute=False)
    if not agent_tasks.is_execute_running(run_id):
        background_tasks.add_task(agent_tasks.run_agent_retry, run_id)
    return run


@project_router.get("/{project_id}/agent-runs/{run_id}/events/stream")
def stream_project_agent_events(
    project_id: UUID,
    run_id: UUID,
    after_sequence: int | None = Query(default=None),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
) -> StreamingResponse:
    return stream_agent_events_sse(
        run_id,
        project_id=project_id,
        after_sequence=after_sequence,
        last_event_id=last_event_id,
    )


@run_router.get("/{run_id}", response_model=AgentRunRead)
def get_agent_run(
    run_id: UUID,
    project_id: UUID = Query(...),
    db: Session = Depends(get_db),
) -> AgentRunRead:
    return AgentRunService(db).get_run(run_id, project_id=project_id)


@run_router.get("/{run_id}/events", response_model=AgentEventsResponse)
def get_agent_events(
    run_id: UUID,
    project_id: UUID = Query(...),
    after_sequence: int | None = Query(default=None),
    db: Session = Depends(get_db),
) -> AgentEventsResponse:
    return AgentRunService(db).get_events(
        run_id, project_id=project_id, after_sequence=after_sequence
    )


@run_router.get("/{run_id}/result", response_model=AgentResultResponse)
def get_agent_result(
    run_id: UUID,
    project_id: UUID = Query(...),
    db: Session = Depends(get_db),
) -> AgentResultResponse:
    return AgentRunService(db).get_result(run_id, project_id=project_id)


@run_router.post("/{run_id}/resume", response_model=AgentRunRead)
def resume_agent_run(
    run_id: UUID,
    background_tasks: BackgroundTasks,
    project_id: UUID = Query(...),
    db: Session = Depends(get_db),
    sync: bool = Query(
        default=False,
        description="If true, resume graph in-request. Default: prepare + background.",
    ),
) -> AgentRunRead:
    svc = AgentRunService(db)
    svc.get_run(run_id, project_id=project_id)
    if sync:
        return svc.resume_run(run_id, execute=True)
    run = svc.resume_run(run_id, execute=False)
    if not agent_tasks.is_execute_running(run_id):
        background_tasks.add_task(agent_tasks.run_agent_resume, run_id)
    return run


@run_router.post("/{run_id}/retry", response_model=AgentRunRead)
def retry_agent_run(
    run_id: UUID,
    background_tasks: BackgroundTasks,
    project_id: UUID = Query(...),
    db: Session = Depends(get_db),
    sync: bool = Query(
        default=False,
        description="If true, retry graph in-request. Default: prepare + background.",
    ),
) -> AgentRunRead:
    svc = AgentRunService(db)
    svc.get_run(run_id, project_id=project_id)
    if sync:
        return svc.retry_run(run_id, execute=True)
    run = svc.retry_run(run_id, execute=False)
    if not agent_tasks.is_execute_running(run_id):
        background_tasks.add_task(agent_tasks.run_agent_retry, run_id)
    return run


@run_router.get("/{run_id}/events/stream")
def stream_agent_events(
    run_id: UUID,
    project_id: UUID = Query(...),
    after_sequence: int | None = Query(default=None),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
) -> StreamingResponse:
    return stream_agent_events_sse(
        run_id,
        project_id=project_id,
        after_sequence=after_sequence,
        last_event_id=last_event_id,
    )


# Back-compat alias expected by some imports
router = project_router
