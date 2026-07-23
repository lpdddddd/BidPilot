"""PostgreSQL: persisted attempts, atomic claims, full Graph+Service midrun visibility."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

from app.models.agent import AgentEvent, AgentRun, AgentStep, ToolCall
from app.models.enums import AgentRunStatus
from app.schemas.agent_run import AgentRunStartRequest
from app.services.agent_run.claims import ClaimOutcome, allocate_node_attempt, claim_run_execution
from app.services.agent_run.events import (
    EVENT_NODE_COMPLETED,
    EVENT_NODE_FAILED,
    EVENT_NODE_STARTED,
    EVENT_RUN_COMPLETED,
    EVENT_TOOL_COMPLETED,
    EVENT_TOOL_FAILED,
    EVENT_TOOL_STARTED,
    record_node_started,
)
from app.services.agent_run.service import AgentRunService
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from tests.test_agent_e2e_scenarios import FakeLlm, _fake_retrieval, _seed


def test_allocate_node_attempt_increments_and_concurrency(db: Session, engine):
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    project, _, _ = _seed(db)
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
    run_id = run.id

    a1 = allocate_node_attempt(db, run_id, "retrieve_evidence")
    assert a1 == 1
    step = record_node_started(db, agent_run_id=run_id, node_name="retrieve_evidence")
    db.commit()
    assert step.attempt == 1

    a2 = allocate_node_attempt(db, run_id, "retrieve_evidence")
    assert a2 == 2
    # allocate holds FOR UPDATE until commit — release before concurrent workers.
    db.commit()

    # Concurrent allocate must not duplicate.
    results: list[int] = []
    barrier = threading.Barrier(2)

    def worker():
        s = SessionLocal()
        try:
            barrier.wait(timeout=10)
            step = record_node_started(s, agent_run_id=run_id, node_name="match_company_evidence")
            s.commit()
            results.append(step.attempt)
        finally:
            s.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futs = [pool.submit(worker), pool.submit(worker)]
        for f in futs:
            f.result(timeout=20)
    assert sorted(results) == [1, 2]
    attempts = [
        s.attempt
        for s in db.scalars(
            select(AgentStep).where(
                AgentStep.agent_run_id == run_id,
                AgentStep.node_name == "match_company_evidence",
            )
        ).all()
    ]
    assert sorted(attempts) == [1, 2]


def test_api_retry_continues_persisted_attempt(db: Session):
    project, _, _ = _seed(db)
    svc = AgentRunService(db, llm=FakeLlm(), retrieval_fn=_fake_retrieval)
    # First run with interrupt after retrieve so we have steps.
    run = svc.start_run(
        project.id,
        AgentRunStartRequest(
            user_request="评测",
            metadata={"interrupt_after_node": "retrieve_evidence"},
        ),
        execute=True,
    )
    steps = list(
        db.scalars(
            select(AgentStep)
            .where(AgentStep.agent_run_id == run.id, AgentStep.node_name == "retrieve_evidence")
            .order_by(AgentStep.attempt.asc())
        ).all()
    )
    assert steps
    first_max = max(s.attempt for s in steps)

    # Force failed for retry
    row = db.get(AgentRun, run.id)
    assert row is not None
    row.status = AgentRunStatus.failed
    row.execution_claim_token = None
    state = dict(row.state_json or {})
    state["status"] = "failed"
    state["current_node"] = "retrieve_evidence"
    # Remove retrieve from completed so it re-runs
    state["completed_nodes"] = [
        n for n in (state.get("completed_nodes") or []) if n != "retrieve_evidence"
    ]
    state["metadata"] = {
        **(state.get("metadata") or {}),
        "interrupt_after_node": "retrieve_evidence",
    }
    row.state_json = state
    db.commit()

    svc2 = AgentRunService(db, llm=FakeLlm(), retrieval_fn=_fake_retrieval)
    svc2.retry_run(run.id, execute=True)
    steps2 = list(
        db.scalars(
            select(AgentStep)
            .where(AgentStep.agent_run_id == run.id, AgentStep.node_name == "retrieve_evidence")
            .order_by(AgentStep.attempt.asc())
        ).all()
    )
    assert max(s.attempt for s in steps2) == first_max + 1
    # Old rows preserved
    assert len(steps2) >= len(steps) + 1


def test_claim_concurrent_resume_only_one(db: Session, engine):
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    project, _, _ = _seed(db)
    run = AgentRun(
        organization_id=project.organization_id,
        project_id=project.id,
        status=AgentRunStatus.waiting_for_user,
        intent="t",
        graph_version="bidpilot-agent-1.0.0",
        state_json={"status": "waiting_for_user", "run_id": str(uuid4())},
    )
    db.add(run)
    db.commit()
    run_id = run.id
    outcomes: list[str] = []
    barrier = threading.Barrier(2)

    def worker():
        s = SessionLocal()
        try:
            barrier.wait(timeout=10)
            res = claim_run_execution(s, run_id, action="resume", project_id=project.id)
            outcomes.append(res.outcome.value)
        finally:
            s.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futs = [pool.submit(worker), pool.submit(worker)]
        for f in futs:
            f.result(timeout=20)
    assert outcomes.count(ClaimOutcome.claimed.value) == 1
    assert outcomes.count(ClaimOutcome.already_running.value) == 1


def test_claim_completed_rejected_and_cross_project(db: Session):
    project, _, _ = _seed(db)
    other, _, _ = _seed(db)
    run = AgentRun(
        organization_id=project.organization_id,
        project_id=project.id,
        status=AgentRunStatus.completed,
        intent="t",
        graph_version="bidpilot-agent-1.0.0",
    )
    db.add(run)
    db.commit()

    bad = claim_run_execution(db, run.id, action="retry", project_id=project.id)
    assert bad.outcome == ClaimOutcome.invalid_state

    resume_bad = claim_run_execution(db, run.id, action="resume", project_id=project.id)
    assert resume_bad.outcome == ClaimOutcome.invalid_state

    cross = claim_run_execution(db, run.id, action="resume", project_id=other.id)
    assert cross.outcome == ClaimOutcome.not_found_or_forbidden


def test_claim_resume_vs_retry_race(db: Session, engine):
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    project, _, _ = _seed(db)
    run = AgentRun(
        organization_id=project.organization_id,
        project_id=project.id,
        status=AgentRunStatus.failed,
        intent="t",
        graph_version="bidpilot-agent-1.0.0",
        state_json={"status": "failed"},
    )
    db.add(run)
    db.commit()
    run_id = run.id
    outcomes: list[str] = []
    barrier = threading.Barrier(2)

    def worker(action: str):
        s = SessionLocal()
        try:
            barrier.wait(timeout=10)
            res = claim_run_execution(s, run_id, action=action, project_id=project.id)
            outcomes.append(res.outcome.value)
        finally:
            s.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futs = [pool.submit(worker, "resume"), pool.submit(worker, "retry")]
        for f in futs:
            f.result(timeout=20)
    assert outcomes.count(ClaimOutcome.claimed.value) == 1
    assert outcomes.count(ClaimOutcome.already_running.value) == 1


def test_schedule_failure_releases_claim(db: Session):
    """Mirrors API _schedule_or_release: failed registration must not leave claim."""
    from app.models.enums import AgentRunStatus as S
    from app.services.agent_run.claims import release_execution_claim

    project, _, _ = _seed(db)
    run = AgentRun(
        organization_id=project.organization_id,
        project_id=project.id,
        status=AgentRunStatus.waiting_for_user,
        intent="t",
        graph_version="bidpilot-agent-1.0.0",
    )
    db.add(run)
    db.commit()
    claimed = claim_run_execution(db, run.id, action="resume", project_id=project.id)
    assert claimed.outcome == ClaimOutcome.claimed
    release_execution_claim(
        db,
        run.id,
        claim_token=claimed.claim_token,
        restore_status=S.waiting_for_user,
        error_summary="background task registration failed",
    )
    row = db.get(AgentRun, run.id)
    assert row is not None
    assert row.execution_claim_token is None
    assert row.status == AgentRunStatus.waiting_for_user
    assert row.error_summary


def test_full_graph_service_midrun_visibility(db: Session, engine):
    """True E2E: AgentRunService + build_graph, barrier inside tool, peer Session."""
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    project, _, _ = _seed(db)
    db.commit()

    started = threading.Event()
    release = threading.Event()
    run_id_box: dict[str, object] = {}

    def barrier(tool_name: str) -> None:
        if tool_name == "search_evidence":
            started.set()
            assert release.wait(timeout=30)

    def worker():
        s = SessionLocal()
        try:
            svc = AgentRunService(
                s,
                llm=FakeLlm(),
                retrieval_fn=_fake_retrieval,
                tool_barrier=barrier,
            )
            read = svc.start_run(
                project.id,
                AgentRunStartRequest(
                    user_request="招标资格要求",
                    metadata={"interrupt_after_node": "retrieve_evidence"},
                ),
                execute=True,
            )
            run_id_box["id"] = read.id
            return read
        finally:
            s.close()

    with ThreadPoolExecutor(max_workers=1) as pool:
        fut = pool.submit(worker)
        assert started.wait(timeout=30), "tool barrier never reached via full Graph"

        run_id = run_id_box.get("id")
        # run_id may not be set until start_run returns — but barrier is mid-execute,
        # so run must already be persisted. Discover by latest running project run.
        peer = SessionLocal()
        try:
            if run_id is None:
                run_row = peer.scalar(
                    select(AgentRun)
                    .where(AgentRun.project_id == project.id)
                    .order_by(AgentRun.created_at.desc())
                    .limit(1)
                )
                assert run_row is not None
                run_id = run_row.id
                run_id_box["id"] = run_id
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
            # load_context may already have completed; only search_evidence must
            # still be mid-flight while the barrier is held.
            tools = list(
                peer.scalars(select(ToolCall).where(ToolCall.agent_run_id == run_id)).all()
            )
            search = [t for t in tools if t.tool_name == "search_evidence"]
            assert search, f"expected search_evidence tool row, got {[t.tool_name for t in tools]}"
            assert all(t.status == "running" for t in search)
            assert all(t.finished_at is None for t in search)
            assert all(t.agent_step_id is not None for t in search)
            retrieve_done = [
                e
                for e in peer.scalars(
                    select(AgentEvent).where(
                        AgentEvent.agent_run_id == run_id,
                        AgentEvent.event_type == EVENT_NODE_COMPLETED,
                        AgentEvent.node_name == "retrieve_evidence",
                    )
                ).all()
            ]
            assert not retrieve_done
            assert EVENT_RUN_COMPLETED not in types
        finally:
            peer.close()

        release.set()
        result = fut.result(timeout=60)

    peer2 = SessionLocal()
    try:
        events = list(
            peer2.scalars(
                select(AgentEvent)
                .where(AgentEvent.agent_run_id == result.id)
                .order_by(AgentEvent.sequence.asc())
            ).all()
        )
        types = [e.event_type for e in events]
        assert EVENT_TOOL_COMPLETED in types
        tool = peer2.scalars(
            select(ToolCall).where(
                ToolCall.agent_run_id == result.id,
                ToolCall.tool_name == "search_evidence",
            )
        ).one()
        assert tool.status in {"ok", "succeeded", "completed"}
        assert tool.finished_at is not None and tool.started_at is not None
        assert tool.finished_at > tool.started_at
        assert (tool.duration_ms or 0) >= 1
        seqs = [e.sequence for e in events]
        assert seqs == sorted(seqs)
        assert len(seqs) == len(set(seqs))
        started_ev = next(
            e for e in events if e.event_type == EVENT_TOOL_STARTED and e.tool_call_id == tool.id
        )
        completed_ev = next(
            e for e in events if e.event_type == EVENT_TOOL_COMPLETED and e.tool_call_id == tool.id
        )
        assert started_ev.sequence < completed_ev.sequence
    finally:
        peer2.close()


def test_full_graph_tool_failure_midrun_visibility(db: Session, engine):
    """Peer Session sees tool_started while tool is failing mid-flight."""

    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    project, _, _ = _seed(db)
    db.commit()

    started = threading.Event()
    release = threading.Event()

    def barrier(tool_name: str) -> None:
        if tool_name == "search_evidence":
            started.set()
            assert release.wait(timeout=30)

    def boom_retrieval(*_a, **_k):
        raise RuntimeError("injected retrieval failure")

    def worker():
        s = SessionLocal()
        try:
            svc = AgentRunService(
                s,
                llm=FakeLlm(),
                retrieval_fn=boom_retrieval,
                tool_barrier=barrier,
            )
            return svc.start_run(
                project.id,
                AgentRunStartRequest(user_request="资格要求"),
                execute=True,
            )
        finally:
            s.close()

    with ThreadPoolExecutor(max_workers=1) as pool:
        fut = pool.submit(worker)
        assert started.wait(timeout=30)
        peer = SessionLocal()
        try:
            run_row = peer.scalar(
                select(AgentRun)
                .where(AgentRun.project_id == project.id)
                .order_by(AgentRun.created_at.desc())
                .limit(1)
            )
            assert run_row is not None
            types = [
                e.event_type
                for e in peer.scalars(
                    select(AgentEvent)
                    .where(AgentEvent.agent_run_id == run_row.id)
                    .order_by(AgentEvent.sequence.asc())
                ).all()
            ]
            assert EVENT_NODE_STARTED in types
            assert EVENT_TOOL_STARTED in types
            assert EVENT_TOOL_FAILED not in types
            assert EVENT_NODE_FAILED not in types
            # Prior nodes may have completed tools; only search_evidence must be mid-flight.
            tools = list(
                peer.scalars(
                    select(ToolCall).where(
                        ToolCall.agent_run_id == run_row.id,
                        ToolCall.tool_name == "search_evidence",
                    )
                ).all()
            )
            assert tools and tools[0].finished_at is None
            search_events = [
                e
                for e in peer.scalars(
                    select(AgentEvent)
                    .where(
                        AgentEvent.agent_run_id == run_row.id,
                        AgentEvent.tool_call_id == tools[0].id,
                    )
                    .order_by(AgentEvent.sequence.asc())
                ).all()
            ]
            search_types = [e.event_type for e in search_events]
            assert EVENT_TOOL_STARTED in search_types
            assert EVENT_TOOL_COMPLETED not in search_types
            assert EVENT_TOOL_FAILED not in search_types
        finally:
            peer.close()
        release.set()
        result = fut.result(timeout=60)

    peer2 = SessionLocal()
    try:
        types = [
            e.event_type
            for e in peer2.scalars(
                select(AgentEvent)
                .where(AgentEvent.agent_run_id == result.id)
                .order_by(AgentEvent.sequence.asc())
            ).all()
        ]
        assert EVENT_TOOL_FAILED in types or EVENT_NODE_FAILED in types
        assert EVENT_RUN_COMPLETED not in types
    finally:
        peer2.close()


def test_http_api_retry_continues_persisted_attempt(db: Session, client: TestClient):
    project, _, _ = _seed(db)
    run = AgentRunService(db, llm=FakeLlm(), retrieval_fn=_fake_retrieval).start_run(
        project.id,
        AgentRunStartRequest(
            user_request="评测",
            metadata={"interrupt_after_node": "retrieve_evidence"},
        ),
        execute=True,
    )
    steps = list(
        db.scalars(
            select(AgentStep).where(
                AgentStep.agent_run_id == run.id,
                AgentStep.node_name == "retrieve_evidence",
            )
        ).all()
    )
    assert steps
    max_before = max(s.attempt for s in steps)
    tools_before = {
        t.id for t in db.scalars(select(ToolCall).where(ToolCall.agent_run_id == run.id)).all()
    }
    event_keys_before = {
        e.idempotency_key
        for e in db.scalars(select(AgentEvent).where(AgentEvent.agent_run_id == run.id)).all()
        if e.idempotency_key
    }

    row = db.get(AgentRun, run.id)
    assert row is not None
    row.status = AgentRunStatus.failed
    row.execution_claim_token = None
    state = dict(row.state_json or {})
    state["status"] = "failed"
    state["current_node"] = "retrieve_evidence"
    state["completed_nodes"] = [
        n for n in (state.get("completed_nodes") or []) if n != "retrieve_evidence"
    ]
    state["metadata"] = {
        **(state.get("metadata") or {}),
        "interrupt_after_node": "retrieve_evidence",
    }
    row.state_json = state
    db.commit()

    resp = client.post(
        f"/api/v1/projects/{project.id}/agent-runs/{run.id}/retry?sync=true",
    )
    assert resp.status_code == 200, resp.text
    steps2 = list(
        db.scalars(
            select(AgentStep).where(
                AgentStep.agent_run_id == run.id,
                AgentStep.node_name == "retrieve_evidence",
            )
        ).all()
    )
    max_after = max(s.attempt for s in steps2)
    assert max_after > max_before
    assert len(steps2) > len(steps)
    tools_after = list(db.scalars(select(ToolCall).where(ToolCall.agent_run_id == run.id)).all())
    assert {t.id for t in tools_after} - tools_before
    event_keys_after = {
        e.idempotency_key
        for e in db.scalars(select(AgentEvent).where(AgentEvent.agent_run_id == run.id)).all()
        if e.idempotency_key
    }
    assert event_keys_after - event_keys_before


def test_concurrent_retry_claim_single_winner(db: Session, engine):
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    project, _, _ = _seed(db)
    project_id = project.id
    svc = AgentRunService(db, llm=FakeLlm(), retrieval_fn=_fake_retrieval)
    run = svc.start_run(
        project_id,
        AgentRunStartRequest(user_request="x", metadata={"interrupt_after_node": "initialize_run"}),
        execute=True,
    )
    row = db.get(AgentRun, run.id)
    assert row is not None
    row.status = AgentRunStatus.failed
    row.execution_claim_token = None
    db.commit()
    run_id = run.id
    barrier = threading.Barrier(2)
    outcomes: list[str] = []

    def worker():
        s = SessionLocal()
        try:
            barrier.wait(timeout=10)
            result = claim_run_execution(s, run_id, action="retry", project_id=project_id)
            outcomes.append(result.outcome.value)
        finally:
            s.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futs = [pool.submit(worker), pool.submit(worker)]
        for f in futs:
            f.result(timeout=20)
    assert outcomes.count(ClaimOutcome.claimed.value) == 1
    assert outcomes.count(ClaimOutcome.already_running.value) == 1


def test_api_background_schedule_failure_releases_claim(db: Session):
    from unittest.mock import patch

    import pytest
    from app.api.v1 import agent_runs as agent_runs_api
    from fastapi import BackgroundTasks

    project, _, _ = _seed(db)
    svc = AgentRunService(db, llm=FakeLlm(), retrieval_fn=_fake_retrieval)
    run = svc.start_run(
        project.id,
        AgentRunStartRequest(user_request="x"),
        execute=False,
    )
    row = db.get(AgentRun, run.id)
    assert row is not None
    row.status = AgentRunStatus.failed
    row.execution_claim_token = None
    state = dict(row.state_json or {})
    state["status"] = "failed"
    state["current_node"] = "retrieve_evidence"
    row.state_json = state
    db.commit()
    run_id = run.id

    def boom(*_a, **_k):
        raise RuntimeError("schedule failed")

    with (
        patch.object(agent_runs_api.BackgroundTasks, "add_task", boom),
        pytest.raises(RuntimeError, match="schedule failed"),
    ):
        agent_runs_api.retry_project_agent_run(
            project_id=project.id,
            run_id=run_id,
            background_tasks=BackgroundTasks(),
            db=db,
            sync=False,
        )

    row2 = db.get(AgentRun, run_id)
    assert row2 is not None
    assert row2.execution_claim_token is None
    assert row2.status != AgentRunStatus.running
