"""Async resume/retry and cross-project permission tests."""

from __future__ import annotations

import threading
from uuid import uuid4

from app.api.v1 import agent_runs as agent_runs_api
from app.models import BidProject, Organization
from app.models.agent import AgentEvent, AgentRun
from app.models.enums import AgentRunStatus
from app.schemas.agent_run import AgentRunStartRequest
from app.services.agent_run import tasks as agent_tasks
from app.services.agent_run.events import EVENT_RUN_RESUMED
from app.services.agent_run.service import AgentRunService
from fastapi import BackgroundTasks
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker


def _two_projects(db: Session):
    org = Organization(name=f"Org-{uuid4().hex[:6]}")
    db.add(org)
    db.flush()
    p1 = BidProject(
        organization_id=org.id,
        project_code=f"P1-{uuid4().hex[:4]}",
        project_name="P1",
    )
    p2 = BidProject(
        organization_id=org.id,
        project_code=f"P2-{uuid4().hex[:4]}",
        project_name="P2",
    )
    db.add_all([p1, p2])
    db.flush()
    return p1, p2


def test_cross_project_denied_for_events_result_resume_retry_stream(
    db: Session, client: TestClient
):
    p1, p2 = _two_projects(db)
    svc = AgentRunService(db)
    run = svc.start_run(
        p1.id,
        AgentRunStartRequest(user_request="x", metadata={"interrupt_after_node": "initialize_run"}),
        execute=True,
    )
    db.commit()
    rid = run.id

    assert client.get(f"/api/v1/projects/{p2.id}/agent-runs/{rid}").status_code == 404
    assert client.get(f"/api/v1/projects/{p2.id}/agent-runs/{rid}/events").status_code == 404
    assert client.get(f"/api/v1/projects/{p2.id}/agent-runs/{rid}/result").status_code == 404
    assert client.post(f"/api/v1/projects/{p2.id}/agent-runs/{rid}/resume").status_code == 404
    assert client.post(f"/api/v1/projects/{p2.id}/agent-runs/{rid}/retry").status_code == 404
    assert client.get(f"/api/v1/projects/{p2.id}/agent-runs/{rid}/events/stream").status_code == 404
    assert (
        client.get(f"/api/v1/agent-runs/{rid}", params={"project_id": str(p2.id)}).status_code
        == 404
    )
    assert (
        client.get(
            f"/api/v1/agent-runs/{rid}/events",
            params={"project_id": str(p2.id)},
        ).status_code
        == 404
    )


def test_async_resume_returns_while_graph_still_running(db: Session, engine, monkeypatch):
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    monkeypatch.setattr(agent_tasks, "SESSION_FACTORY", SessionLocal)

    p1, _ = _two_projects(db)
    svc = AgentRunService(db)
    run = svc.start_run(
        p1.id,
        AgentRunStartRequest(
            user_request="x",
            metadata={"interrupt_after_node": "initialize_run"},
        ),
        execute=True,
    )
    db.commit()
    row = db.get(AgentRun, run.id)
    assert row is not None
    row.status = AgentRunStatus.waiting_for_user
    state = dict(row.state_json or {})
    state["status"] = "waiting_for_user"
    state["interrupt_requested"] = True
    row.state_json = state
    db.commit()

    entered = threading.Event()
    release = threading.Event()

    def gated(self, run_id, *, mode="resume"):
        entered.set()
        assert release.wait(timeout=15)
        return AgentRunService(self.db).get_run(run_id)

    monkeypatch.setattr(AgentRunService, "continue_prepared_run", gated)

    with agent_tasks._lock:
        agent_tasks._running.clear()

    bg = BackgroundTasks()
    result = agent_runs_api.resume_project_agent_run(
        project_id=p1.id,
        run_id=run.id,
        background_tasks=bg,
        db=db,
        sync=False,
    )
    assert str(result.id) == str(run.id)
    resumed = db.scalars(
        select(AgentEvent).where(
            AgentEvent.agent_run_id == run.id,
            AgentEvent.event_type == EVENT_RUN_RESUMED,
        )
    ).all()
    assert resumed
    assert len(bg.tasks) == 1
    assert not entered.is_set()

    func = bg.tasks[0].func
    args = bg.tasks[0].args
    kwargs = bg.tasks[0].kwargs
    threading.Thread(target=func, args=args, kwargs=kwargs, daemon=True).start()
    assert entered.wait(timeout=10)
    assert agent_tasks.is_execute_running(run.id)

    bg2 = BackgroundTasks()
    agent_runs_api.resume_project_agent_run(
        project_id=p1.id,
        run_id=run.id,
        background_tasks=bg2,
        db=db,
        sync=False,
    )
    # DB claim is authoritative — second resume must not schedule another worker.
    assert len(bg2.tasks) == 0
    release.set()
    done = threading.Event()

    def _wait_clear():
        while agent_tasks.is_execute_running(run.id):
            if not release.wait(timeout=0.05):
                continue
        done.set()

    threading.Thread(target=_wait_clear, daemon=True).start()
    assert done.wait(timeout=20)
    assert not agent_tasks.is_execute_running(run.id)


def test_async_retry_prepare_then_background(db: Session, engine, monkeypatch):
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    monkeypatch.setattr(agent_tasks, "SESSION_FACTORY", SessionLocal)

    p1, _ = _two_projects(db)
    svc = AgentRunService(db)
    run = svc.start_run(
        p1.id,
        AgentRunStartRequest(user_request="x"),
        execute=False,
    )
    row = db.get(AgentRun, run.id)
    assert row is not None
    row.status = AgentRunStatus.failed
    state = dict(row.state_json or {})
    state["status"] = "failed"
    state["current_node"] = "retrieve_evidence"
    state["completed_nodes"] = ["initialize_run", "load_project_context"]
    row.state_json = state
    db.commit()

    entered = threading.Event()
    release = threading.Event()

    def gated(self, run_id, *, mode="retry"):
        entered.set()
        assert release.wait(timeout=15)
        return AgentRunService(self.db).get_run(run_id)

    monkeypatch.setattr(AgentRunService, "continue_prepared_run", gated)

    with agent_tasks._lock:
        agent_tasks._running.clear()

    bg = BackgroundTasks()
    result = agent_runs_api.retry_project_agent_run(
        project_id=p1.id,
        run_id=run.id,
        background_tasks=bg,
        db=db,
        sync=False,
    )
    assert str(result.id) == str(run.id)
    status = result.status.value if hasattr(result.status, "value") else str(result.status)
    assert status == "running"
    assert len(bg.tasks) == 1

    func = bg.tasks[0].func
    args = bg.tasks[0].args
    kwargs = bg.tasks[0].kwargs
    threading.Thread(target=func, args=args, kwargs=kwargs, daemon=True).start()
    assert entered.wait(timeout=10)
    release.set()
