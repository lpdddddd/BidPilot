"""Unified AgentEvent sequence + ToolCall↔AgentStep association tests."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from uuid import uuid4

from app.models import BidProject, Organization
from app.models.agent import AgentEvent, AgentRun, ToolCall
from app.models.enums import AgentRunStatus
from app.services.agent_run.events import (
    EVENT_NODE_COMPLETED,
    EVENT_NODE_STARTED,
    EVENT_TOOL_COMPLETED,
    EVENT_TOOL_FAILED,
    EVENT_TOOL_STARTED,
    next_event_sequence,
    record_event,
    record_node_finished,
    record_node_started,
    record_tool_lifecycle,
)
from app.services.agent_run.service import AgentRunService
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker


def _events(db: Session, run_id):
    return list(
        db.scalars(
            select(AgentEvent)
            .where(AgentEvent.agent_run_id == run_id)
            .order_by(AgentEvent.sequence)
        )
    )


def _seed(db: Session):
    org = Organization(name=f"Org-{uuid4().hex[:6]}")
    db.add(org)
    db.flush()
    project = BidProject(
        organization_id=org.id,
        project_code=f"SEQ-{uuid4().hex[:4]}",
        project_name="Event Sequence",
    )
    db.add(project)
    db.flush()
    return project


def _make_run(db: Session, project: BidProject) -> AgentRun:
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
    return run


def test_single_node_no_tool_order(db: Session):
    project = _seed(db)
    run = _make_run(db, project)
    step = record_node_started(db, agent_run_id=run.id, node_name="retrieve")
    record_node_finished(
        db, agent_run_id=run.id, node_name="retrieve", status="succeeded", agent_step_id=step.id
    )
    types = [e.event_type for e in _events(db, run.id)]
    assert types == [EVENT_NODE_STARTED, EVENT_NODE_COMPLETED]
    seqs = [e.sequence for e in _events(db, run.id)]
    assert seqs == [0, 1]


def test_single_node_one_tool_order(db: Session):
    project = _seed(db)
    run = _make_run(db, project)
    step = record_node_started(db, agent_run_id=run.id, node_name="retrieve")
    tool = record_tool_lifecycle(
        db,
        agent_run_id=run.id,
        tool_name="search_evidence",
        status="ok",
        summary="2 hits",
        agent_step_id=step.id,
        node_name="retrieve",
    )
    record_node_finished(
        db, agent_run_id=run.id, node_name="retrieve", status="succeeded", agent_step_id=step.id
    )
    types = [e.event_type for e in _events(db, run.id)]
    assert types == [
        EVENT_NODE_STARTED,
        EVENT_TOOL_STARTED,
        EVENT_TOOL_COMPLETED,
        EVENT_NODE_COMPLETED,
    ]
    assert tool.agent_step_id == step.id
    assert tool.node_name == "retrieve"
    assert tool.call_id


def test_tool_failed_order(db: Session):
    project = _seed(db)
    run = _make_run(db, project)
    step = record_node_started(db, agent_run_id=run.id, node_name="match")
    record_tool_lifecycle(
        db,
        agent_run_id=run.id,
        tool_name="match_company_evidence",
        status="error",
        summary="timeout",
        agent_step_id=step.id,
    )
    record_node_finished(
        db,
        agent_run_id=run.id,
        node_name="match",
        status="failed",
        agent_step_id=step.id,
        error_message="timeout",
    )
    types = [e.event_type for e in _events(db, run.id)]
    assert types == [
        EVENT_NODE_STARTED,
        EVENT_TOOL_STARTED,
        EVENT_TOOL_FAILED,
        "node_failed",
    ]


def test_multi_tool_and_multi_node_sequences(db: Session):
    project = _seed(db)
    run = _make_run(db, project)
    s1 = record_node_started(db, agent_run_id=run.id, node_name="n1")
    record_tool_lifecycle(db, agent_run_id=run.id, tool_name="t1", status="ok", agent_step_id=s1.id)
    record_tool_lifecycle(db, agent_run_id=run.id, tool_name="t2", status="ok", agent_step_id=s1.id)
    record_node_finished(
        db, agent_run_id=run.id, node_name="n1", status="succeeded", agent_step_id=s1.id
    )
    s2 = record_node_started(db, agent_run_id=run.id, node_name="n2")
    record_node_finished(
        db, agent_run_id=run.id, node_name="n2", status="succeeded", agent_step_id=s2.id
    )
    events = _events(db, run.id)
    seqs = [e.sequence for e in events]
    assert seqs == list(range(len(seqs)))
    assert len(set(seqs)) == len(seqs)
    assert events[0].event_type == EVENT_NODE_STARTED
    assert events[-1].event_type == EVENT_NODE_COMPLETED
    assert events[-1].node_name == "n2"


def test_sequence_zero_then_one(db: Session):
    project = _seed(db)
    run = _make_run(db, project)
    assert run.event_sequence == 0
    assert next_event_sequence(db, run.id) == 0
    db.refresh(run)
    assert run.event_sequence == 1
    assert next_event_sequence(db, run.id) == 1


def test_resume_continues_event_sequence(db: Session):
    project = _seed(db)
    run = _make_run(db, project)
    s = record_node_started(db, agent_run_id=run.id, node_name="a")
    record_node_finished(
        db, agent_run_id=run.id, node_name="a", status="succeeded", agent_step_id=s.id
    )
    db.refresh(run)
    before = run.event_sequence
    record_event(db, agent_run_id=run.id, event_type="run_resumed", status="running")
    s2 = record_node_started(db, agent_run_id=run.id, node_name="b")
    record_node_finished(
        db, agent_run_id=run.id, node_name="b", status="succeeded", agent_step_id=s2.id
    )
    events = _events(db, run.id)
    assert events[0].sequence == 0
    assert all(events[i].sequence + 1 == events[i + 1].sequence for i in range(len(events) - 1))
    assert run.event_sequence >= before + 1
    # No reset to 0 after resume marker.
    assert events[-1].sequence > before - 1


def test_new_session_sequence_continues(db: Session, engine):
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    s1 = SessionLocal()
    try:
        project = _seed(s1)
        run = _make_run(s1, project)
        step = record_node_started(s1, agent_run_id=run.id, node_name="n1")
        record_node_finished(
            s1, agent_run_id=run.id, node_name="n1", status="succeeded", agent_step_id=step.id
        )
        s1.commit()
        run_id = run.id
        counter = run.event_sequence
    finally:
        s1.close()

    s2 = SessionLocal()
    try:
        step2 = record_node_started(s2, agent_run_id=run_id, node_name="n2")
        record_node_finished(
            s2, agent_run_id=run_id, node_name="n2", status="succeeded", agent_step_id=step2.id
        )
        s2.commit()
        events = list(
            s2.scalars(
                select(AgentEvent)
                .where(AgentEvent.agent_run_id == run_id)
                .order_by(AgentEvent.sequence)
            )
        )
        assert events[0].sequence == 0
        assert events[-1].sequence >= counter
        assert [e.sequence for e in events] == list(range(len(events)))
    finally:
        s2.close()


def test_concurrent_event_sequences_unique(db: Session, engine):
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    seed = SessionLocal()
    try:
        project = _seed(seed)
        run = _make_run(seed, project)
        seed.commit()
        run_id = run.id
    finally:
        seed.close()

    def _worker(n: int) -> int:
        s = SessionLocal()
        try:
            ev = record_event(
                s,
                agent_run_id=run_id,
                event_type="node_started",
                node_name=f"w{n}",
                status="running",
            )
            s.commit()
            return ev.sequence
        finally:
            s.close()

    with ThreadPoolExecutor(max_workers=8) as pool:
        futs = [pool.submit(_worker, i) for i in range(16)]
        indices = [f.result() for f in as_completed(futs)]

    assert len(indices) == 16
    assert len(set(indices)) == 16
    assert sorted(indices) == list(range(16))


def test_tool_call_links_agent_step(db: Session):
    project = _seed(db)
    run = _make_run(db, project)
    step = record_node_started(db, agent_run_id=run.id, node_name="draft")
    tool = record_tool_lifecycle(
        db,
        agent_run_id=run.id,
        tool_name="generate_proposal_draft",
        status="ok",
        agent_step_id=step.id,
    )
    loaded = db.get(ToolCall, tool.id)
    assert loaded is not None
    assert loaded.agent_step_id == step.id
    assert loaded.agent_step is not None
    assert loaded.agent_step.node_name == "draft"


def test_events_api_stable_order(db: Session):
    project = _seed(db)
    run = _make_run(db, project)
    step = record_node_started(db, agent_run_id=run.id, node_name="retrieve")
    record_tool_lifecycle(
        db, agent_run_id=run.id, tool_name="search_evidence", status="ok", agent_step_id=step.id
    )
    record_node_finished(
        db, agent_run_id=run.id, node_name="retrieve", status="succeeded", agent_step_id=step.id
    )
    resp = AgentRunService(db).get_events(run.id, project_id=project.id)
    sequences = [e.sequence for e in resp.items]
    assert sequences == sorted(sequences)
    assert [e.event_type for e in resp.items] == [
        EVENT_NODE_STARTED,
        EVENT_TOOL_STARTED,
        EVENT_TOOL_COMPLETED,
        EVENT_NODE_COMPLETED,
    ]
    # No synthetic 10000+ offsets.
    assert all(e.sequence < 100 for e in resp.items)
    assert resp.items[1].agent_step_id == step.id
    assert resp.items[1].tool_call_id is not None


def test_no_10000_offset_anywhere(db: Session):
    project = _seed(db)
    run = _make_run(db, project)
    for i in range(3):
        s = record_node_started(db, agent_run_id=run.id, node_name=f"n{i}")
        record_tool_lifecycle(
            db, agent_run_id=run.id, tool_name=f"t{i}", status="ok", agent_step_id=s.id
        )
        record_node_finished(
            db, agent_run_id=run.id, node_name=f"n{i}", status="succeeded", agent_step_id=s.id
        )
    resp = AgentRunService(db).get_events(run.id, project_id=project.id)
    assert not any(e.sequence >= 10_000 for e in resp.items)
