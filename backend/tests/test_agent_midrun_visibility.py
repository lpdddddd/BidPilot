"""Mid-run event visibility: second Session sees tool_started before tool finishes."""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

from app.agent.nodes._helpers import AgentRuntime, reset_runtime, run_tool, set_runtime
from app.agent.state import empty_state
from app.models import BidProject, Organization
from app.models.agent import AgentEvent, AgentRun, AgentStep
from app.models.enums import AgentRunStatus
from app.services.agent_run.events import (
    EVENT_TOOL_COMPLETED,
    EVENT_TOOL_STARTED,
    record_node_started,
    record_tool_finished,
    record_tool_started,
)
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker


def _seed(db: Session):
    org = Organization(name=f"Org-{uuid4().hex[:6]}")
    db.add(org)
    db.flush()
    project = BidProject(
        organization_id=org.id,
        project_code=f"VIS-{uuid4().hex[:4]}",
        project_name="Visibility",
    )
    db.add(project)
    db.flush()
    return project


def test_tool_started_visible_before_tool_completes(db: Session, engine):
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    project = _seed(db)
    run = AgentRun(
        organization_id=project.organization_id,
        project_id=project.id,
        status=AgentRunStatus.running,
        intent="t",
        graph_version="bidpilot-agent-1.0.0",
        event_sequence=0,
    )
    db.add(run)
    db.flush()
    step = record_node_started(db, agent_run_id=run.id, node_name="retrieve_evidence")
    db.commit()
    run_id = run.id
    step_id = step.id

    started = threading.Event()
    release = threading.Event()

    def barrier(tool_name: str) -> None:
        if tool_name == "search_evidence":
            started.set()
            assert release.wait(timeout=10)

    def worker():
        s = SessionLocal()
        token = None
        try:
            state = empty_state(
                run_id=run_id,
                project_id=project.id,
                organization_id=project.organization_id,
            )

            def persist_start(**kw):
                return record_tool_started(s, agent_run_id=run_id, **kw)

            def persist_finish(**kw):
                return record_tool_finished(s, agent_run_id=run_id, **kw)

            runtime = AgentRuntime(
                db=s,
                commit_fn=lambda: s.commit(),
                persist_tool_start=persist_start,
                persist_tool_finish=persist_finish,
                current_step_id=step_id,
                current_node_name="retrieve_evidence",
                tool_barrier=barrier,
            )
            token = set_runtime(runtime)

            def body():
                time.sleep(0.01)
                return "hits"

            return run_tool(state, "search_evidence", body, summary_on_ok=lambda x: x)
        finally:
            if token is not None:
                reset_runtime(token)
            s.close()

    with ThreadPoolExecutor(max_workers=1) as pool:
        fut = pool.submit(worker)
        assert started.wait(timeout=10), "barrier not reached"

        peer = SessionLocal()
        try:
            types = [
                e.event_type
                for e in peer.scalars(
                    select(AgentEvent)
                    .where(AgentEvent.agent_run_id == run_id)
                    .order_by(AgentEvent.sequence.asc())
                ).all()
            ]
            assert EVENT_TOOL_STARTED in types
            assert EVENT_TOOL_COMPLETED not in types
        finally:
            peer.close()

        release.set()
        assert fut.result(timeout=15) == "hits"

    peer2 = SessionLocal()
    try:
        types = [
            e.event_type
            for e in peer2.scalars(
                select(AgentEvent)
                .where(AgentEvent.agent_run_id == run_id)
                .order_by(AgentEvent.sequence.asc())
            ).all()
        ]
        assert types.count(EVENT_TOOL_STARTED) == 1
        assert EVENT_TOOL_COMPLETED in types
        done = peer2.scalars(
            select(AgentEvent).where(
                AgentEvent.agent_run_id == run_id,
                AgentEvent.event_type == EVENT_TOOL_COMPLETED,
            )
        ).first()
        assert done is not None
        assert (done.duration_ms or 0) >= 1
        assert done.agent_step_id == step_id
    finally:
        peer2.close()


def test_record_tool_start_finish_times_differ(db: Session):
    project = _seed(db)
    run = AgentRun(
        organization_id=project.organization_id,
        project_id=project.id,
        status=AgentRunStatus.running,
        intent="t",
        graph_version="bidpilot-agent-1.0.0",
    )
    db.add(run)
    db.flush()
    row = record_tool_started(db, agent_run_id=run.id, tool_name="t", node_name="n")
    db.commit()
    time.sleep(0.02)
    record_tool_finished(db, agent_run_id=run.id, tool_call_id=row.id, status="ok", summary="done")
    db.commit()
    db.refresh(row)
    assert row.started_at is not None and row.finished_at is not None
    assert row.finished_at > row.started_at
    assert (row.duration_ms or 0) >= 1


def test_node_started_visible_before_finish(db: Session, engine):
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    project = _seed(db)
    run = AgentRun(
        organization_id=project.organization_id,
        project_id=project.id,
        status=AgentRunStatus.running,
        intent="t",
        graph_version="bidpilot-agent-1.0.0",
    )
    db.add(run)
    db.commit()
    run_id = run.id

    gate = threading.Event()

    def worker():
        s = SessionLocal()
        try:
            step = record_node_started(s, agent_run_id=run_id, node_name="match")
            s.commit()
            gate.set()
            time.sleep(0.05)
            from app.services.agent_run.events import record_node_finished

            record_node_finished(
                s,
                agent_run_id=run_id,
                node_name="match",
                status="succeeded",
                agent_step_id=step.id,
            )
            s.commit()
        finally:
            s.close()

    with ThreadPoolExecutor(max_workers=1) as pool:
        fut = pool.submit(worker)
        assert gate.wait(timeout=5)
        peer = SessionLocal()
        try:
            types = [
                e.event_type
                for e in peer.scalars(
                    select(AgentEvent).where(AgentEvent.agent_run_id == run_id)
                ).all()
            ]
            assert "node_started" in types
            assert "node_completed" not in types
            assert peer.scalar(select(AgentStep).where(AgentStep.agent_run_id == run_id))
        finally:
            peer.close()
        fut.result(timeout=10)
