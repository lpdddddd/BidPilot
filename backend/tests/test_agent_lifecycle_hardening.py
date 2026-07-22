"""Deterministic lifecycle, visibility, safety, and attempt tests (no fixed sleeps)."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from app.agent.nodes._helpers import (
    AgentRuntime,
    EventPersistError,
    begin_node,
    finish_node,
    mark_retryable_error,
    reset_runtime,
    run_tool,
    set_runtime,
)
from app.agent.state import NODE_RETRIEVE, empty_state
from app.models import BidProject, Organization
from app.models.agent import AgentEvent, AgentRun, ToolCall
from app.models.enums import AgentRunStatus
from app.services.agent_run.events import (
    EVENT_NODE_COMPLETED,
    EVENT_NODE_FAILED,
    EVENT_NODE_STARTED,
    EVENT_TOOL_COMPLETED,
    EVENT_TOOL_FAILED,
    EVENT_TOOL_STARTED,
    record_node_finished,
    record_node_started,
    record_tool_finished,
    record_tool_started,
)
from app.services.agent_run.safe_errors import safe_error_summary
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

# Reuse the well-formed fake retrieval used across agent tests.
from tests.test_agent_nodes import _fake_retrieval


def _seed(db: Session):
    org = Organization(name=f"Org-{uuid4().hex[:6]}")
    db.add(org)
    db.flush()
    project = BidProject(
        organization_id=org.id,
        project_code=f"LC-{uuid4().hex[:4]}",
        project_name="Lifecycle",
    )
    db.add(project)
    db.flush()
    return project


def test_tool_started_persist_failure_does_not_call_tool(db: Session):
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
    state = empty_state(
        run_id=run.id,
        project_id=project.id,
        organization_id=project.organization_id,
    )
    calls = {"n": 0}

    def boom(**_kw):
        raise RuntimeError("db write failed")

    runtime = AgentRuntime(
        db=db,
        commit_fn=lambda: None,
        rollback_fn=lambda: None,
        persist_tool_start=boom,
        current_node_name="retrieve_evidence",
        current_node_attempt=1,
    )
    token = set_runtime(runtime)
    try:

        def _body():
            calls["n"] += 1
            return "x"

        with pytest.raises(EventPersistError):
            run_tool(state, "search_evidence", _body)
        assert calls["n"] == 0
        assert state.get("status") == "failed"
        assert state.get("error_code") == "event_persist_failed"
    finally:
        reset_runtime(token)


def test_node_started_persist_failure_raises(db: Session):
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
    state = empty_state(
        run_id=run.id,
        project_id=project.id,
        organization_id=project.organization_id,
    )

    def boom(**_kw):
        raise RuntimeError("node start failed")

    runtime = AgentRuntime(db=db, commit_fn=lambda: None, persist_node_start=boom)
    token = set_runtime(runtime)
    try:
        with pytest.raises(EventPersistError):
            begin_node(state, NODE_RETRIEVE)
        assert state.get("error_code") == "event_persist_failed"
    finally:
        reset_runtime(token)


def test_failed_attempt_emits_node_failed_not_completed(db: Session):
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
    db.commit()
    state = empty_state(
        run_id=run.id,
        project_id=project.id,
        organization_id=project.organization_id,
    )

    def persist_start(**kw):
        return record_node_started(db, agent_run_id=run.id, **kw)

    def persist_finish(**kw):
        return record_node_finished(db, agent_run_id=run.id, **kw)

    runtime = AgentRuntime(
        db=db,
        commit_fn=lambda: db.commit(),
        persist_node_start=persist_start,
        persist_node_finish=persist_finish,
    )
    token = set_runtime(runtime)
    try:
        state, skipped = begin_node(state, NODE_RETRIEVE)
        assert not skipped
        mark_retryable_error(state, "transient", "retrieve_error")
        # Simulate service _after_node outcome handling
        assert runtime.node_attempt_outcome == "failed"
        persist_finish(
            node_name=NODE_RETRIEVE,
            status="failed",
            agent_step_id=runtime.current_step_id,
            attempt=runtime.current_node_attempt,
            error_message=state.get("error_summary"),
        )
        db.commit()
    finally:
        reset_runtime(token)

    types = [
        e.event_type
        for e in db.scalars(
            select(AgentEvent)
            .where(AgentEvent.agent_run_id == run.id)
            .order_by(AgentEvent.sequence.asc())
        ).all()
    ]
    assert EVENT_NODE_STARTED in types
    assert EVENT_NODE_FAILED in types
    assert EVENT_NODE_COMPLETED not in types


def test_retry_attempts_share_logical_call_id(db: Session):
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
    db.commit()
    state = empty_state(
        run_id=run.id,
        project_id=project.id,
        organization_id=project.organization_id,
    )
    call_ids: list[str] = []
    attempts: list[int] = []
    step_ids: list = []

    def persist_node_start(**kw):
        return record_node_started(db, agent_run_id=run.id, **kw)

    def persist_node_finish(**kw):
        return record_node_finished(db, agent_run_id=run.id, **kw)

    def persist_tool_start(**kw):
        row = record_tool_started(db, agent_run_id=run.id, **kw)
        call_ids.append(row.call_id or "")
        attempts.append(row.attempt)
        step_ids.append(row.agent_step_id)
        return row

    def persist_tool_finish(**kw):
        return record_tool_finished(db, agent_run_id=run.id, **kw)

    runtime = AgentRuntime(
        db=db,
        commit_fn=lambda: db.commit(),
        persist_node_start=persist_node_start,
        persist_node_finish=persist_node_finish,
        persist_tool_start=persist_tool_start,
        persist_tool_finish=persist_tool_finish,
    )
    token = set_runtime(runtime)
    try:
        # Attempt 1 — fail
        state, _ = begin_node(state, NODE_RETRIEVE)
        with pytest.raises(RuntimeError):
            run_tool(state, "search_evidence", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
        mark_retryable_error(state, "boom", "retrieve_error")
        persist_node_finish(
            node_name=NODE_RETRIEVE,
            status="failed",
            agent_step_id=runtime.current_step_id,
            attempt=1,
            error_message="boom",
        )
        db.commit()
        # Routing increments retry_counts
        state["retry_counts"] = {NODE_RETRIEVE: 1}
        state["last_error_retryable"] = False
        # Attempt 2 — succeed
        state, _ = begin_node(state, NODE_RETRIEVE)
        assert runtime.current_node_attempt == 2
        run_tool(state, "search_evidence", lambda: "ok", summary_on_ok=lambda x: x)
        finish_node(state, NODE_RETRIEVE)
        persist_node_finish(
            node_name=NODE_RETRIEVE,
            status="succeeded",
            agent_step_id=runtime.current_step_id,
            attempt=2,
        )
        db.commit()
    finally:
        reset_runtime(token)

    assert attempts == [1, 2]
    assert call_ids[0] == call_ids[1]
    assert step_ids[0] != step_ids[1]
    tools = list(
        db.scalars(
            select(ToolCall).where(ToolCall.agent_run_id == run.id).order_by(ToolCall.started_at)
        ).all()
    )
    assert len(tools) == 2
    assert tools[0].call_id == tools[1].call_id
    assert {tools[0].attempt, tools[1].attempt} == {1, 2}
    seqs = [
        e.sequence
        for e in db.scalars(
            select(AgentEvent)
            .where(AgentEvent.agent_run_id == run.id)
            .order_by(AgentEvent.sequence.asc())
        ).all()
    ]
    assert seqs == sorted(seqs)
    assert len(seqs) == len(set(seqs))


def test_safe_error_summary_redacts_secrets():
    raw = (
        "Authorization: Bearer super-secret-token-xyz "
        "postgresql+psycopg://user:pass@127.0.0.1:5432/db "
        "tool_args={'api_key': 'sk-live-abc'} path=/var/secrets/key.pem "
        "Cookie: session=abc Traceback (most recent call last): File ..."
    )
    out = safe_error_summary(raw, error_type="RuntimeError", error_code="x")
    lowered = out.lower()
    assert "super-secret-token" not in lowered
    assert "sk-live" not in lowered
    assert "postgresql" not in lowered or "[redacted]" in lowered
    assert "/var/secrets" not in out
    assert "traceback" not in lowered
    assert "bearer super-secret" not in lowered


def test_safe_errors_not_in_events_or_checkpoint(db: Session):
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
    step = record_node_started(db, agent_run_id=run.id, node_name="n", attempt=1)
    row = record_tool_started(
        db,
        agent_run_id=run.id,
        tool_name="t",
        agent_step_id=step.id,
        node_name="n",
        attempt=1,
        call_id="logical-1",
    )
    record_tool_finished(
        db,
        agent_run_id=run.id,
        tool_call_id=row.id,
        status="error",
        summary="Authorization: Bearer tok-AAA postgresql://u:p@h/db /root/secret",
        error_type="RuntimeError",
    )
    db.commit()
    ev = db.scalars(
        select(AgentEvent).where(
            AgentEvent.agent_run_id == run.id,
            AgentEvent.event_type == EVENT_TOOL_FAILED,
        )
    ).one()
    blob = (ev.safe_summary or "") + str(ev.payload_json or "")
    assert "tok-AAA" not in blob
    assert "postgresql://u:p" not in blob
    assert "/root/secret" not in blob


def test_real_graph_midrun_visibility_with_barrier(db: Session, engine):
    """Drive retrieve_evidence with real persist hooks; peer Session sees mid-run events."""
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    project = _seed(db)
    db.commit()

    started = threading.Event()
    release = threading.Event()

    s = SessionLocal()
    try:
        run = AgentRun(
            organization_id=project.organization_id,
            project_id=project.id,
            status=AgentRunStatus.running,
            intent="t",
            graph_version="bidpilot-agent-1.0.0",
            event_sequence=0,
        )
        s.add(run)
        s.flush()
        st = empty_state(
            run_id=run.id,
            project_id=project.id,
            organization_id=project.organization_id,
        )
        st["has_documents"] = True
        run.state_json = st
        s.commit()
        run_id = run.id
    finally:
        s.close()

    from app.agent.nodes.retrieve import retrieve_evidence

    def worker():
        sess = SessionLocal()
        token = None
        try:
            run_row = sess.get(AgentRun, run_id)
            assert run_row is not None
            state = dict(run_row.state_json or {})

            def persist_node_start(**kw):
                return record_node_started(sess, agent_run_id=run_id, **kw)

            def persist_node_finish(**kw):
                return record_node_finished(sess, agent_run_id=run_id, **kw)

            def persist_tool_start(**kw):
                return record_tool_started(sess, agent_run_id=run_id, **kw)

            def persist_tool_finish(**kw):
                return record_tool_finished(sess, agent_run_id=run_id, **kw)

            def barrier(tool_name: str) -> None:
                if tool_name == "search_evidence":
                    started.set()
                    assert release.wait(timeout=15)

            runtime = AgentRuntime(
                db=sess,
                retrieval_fn=_fake_retrieval,
                commit_fn=lambda: sess.commit(),
                rollback_fn=lambda: sess.rollback(),
                persist_node_start=persist_node_start,
                persist_node_finish=persist_node_finish,
                persist_tool_start=persist_tool_start,
                persist_tool_finish=persist_tool_finish,
                tool_barrier=barrier,
            )
            token = set_runtime(runtime)
            out = retrieve_evidence(state)  # type: ignore[arg-type]
            finish_status = (
                "succeeded"
                if runtime.node_attempt_outcome == "succeeded"
                or NODE_RETRIEVE in (out.get("completed_nodes") or [])
                else "failed"
            )
            persist_node_finish(
                node_name=NODE_RETRIEVE,
                status=finish_status,
                agent_step_id=runtime.current_step_id,
                attempt=runtime.current_node_attempt,
                error_message=out.get("error_summary"),
            )
            sess.commit()
            return out
        finally:
            if token is not None:
                reset_runtime(token)
            sess.close()

    with ThreadPoolExecutor(max_workers=1) as pool:
        fut = pool.submit(worker)
        assert started.wait(timeout=15), "tool barrier not reached"

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
            assert EVENT_NODE_STARTED in types
            assert EVENT_TOOL_STARTED in types
            assert EVENT_TOOL_COMPLETED not in types
            assert EVENT_NODE_COMPLETED not in types
        finally:
            peer.close()

        release.set()
        fut.result(timeout=20)

    peer2 = SessionLocal()
    try:
        events = list(
            peer2.scalars(
                select(AgentEvent)
                .where(AgentEvent.agent_run_id == run_id)
                .order_by(AgentEvent.sequence.asc())
            ).all()
        )
        types = [e.event_type for e in events]
        assert EVENT_TOOL_COMPLETED in types
        assert EVENT_NODE_COMPLETED in types
        assert types.index(EVENT_NODE_STARTED) < types.index(EVENT_TOOL_STARTED)
        assert types.index(EVENT_TOOL_STARTED) < types.index(EVENT_TOOL_COMPLETED)
        assert types.index(EVENT_TOOL_COMPLETED) < types.index(EVENT_NODE_COMPLETED)
        done = next(e for e in events if e.event_type == EVENT_TOOL_COMPLETED)
        assert (done.duration_ms or 0) >= 1
        tool = peer2.scalars(select(ToolCall).where(ToolCall.agent_run_id == run_id)).one()
        assert tool.started_at is not None and tool.finished_at is not None
        assert tool.finished_at > tool.started_at
        assert tool.agent_step_id is not None
    finally:
        peer2.close()


def test_tool_failure_lifecycle_order(db: Session):
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
    db.commit()
    state = empty_state(
        run_id=run.id,
        project_id=project.id,
        organization_id=project.organization_id,
    )

    runtime = AgentRuntime(
        db=db,
        commit_fn=lambda: db.commit(),
        persist_node_start=lambda **kw: record_node_started(db, agent_run_id=run.id, **kw),
        persist_node_finish=lambda **kw: record_node_finished(db, agent_run_id=run.id, **kw),
        persist_tool_start=lambda **kw: record_tool_started(db, agent_run_id=run.id, **kw),
        persist_tool_finish=lambda **kw: record_tool_finished(db, agent_run_id=run.id, **kw),
    )
    token = set_runtime(runtime)
    try:
        begin_node(state, NODE_RETRIEVE)
        with pytest.raises(ValueError):
            run_tool(state, "search_evidence", lambda: (_ for _ in ()).throw(ValueError("x")))
        mark_retryable_error(state, "x", "retrieve_error")
        record_node_finished(
            db,
            agent_run_id=run.id,
            node_name=NODE_RETRIEVE,
            status="failed",
            agent_step_id=runtime.current_step_id,
            attempt=1,
            error_message=state.get("error_summary"),
        )
        db.commit()
    finally:
        reset_runtime(token)

    types = [
        e.event_type
        for e in db.scalars(
            select(AgentEvent)
            .where(AgentEvent.agent_run_id == run.id)
            .order_by(AgentEvent.sequence.asc())
        ).all()
    ]
    assert types == [
        EVENT_NODE_STARTED,
        EVENT_TOOL_STARTED,
        EVENT_TOOL_FAILED,
        EVENT_NODE_FAILED,
    ]
