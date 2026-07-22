"""Agent step_index / events sequence integrity."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.models import BidProject, Organization
from app.models.agent import AgentRun, AgentStep, ToolCall
from app.models.enums import AgentRunStatus
from app.services.agent_run.events import next_step_index, record_step
from app.services.agent_run.service import AgentRunService
from sqlalchemy.orm import Session, sessionmaker


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
    )
    db.add(run)
    db.flush()
    return run


def test_ten_events_monotonic_and_zero_then_one(db: Session):
    project = _seed(db)
    run = _make_run(db, project)
    indices: list[int] = []
    for i in range(10):
        step = record_step(db, agent_run_id=run.id, node_name=f"n{i}", status="succeeded")
        indices.append(step.step_index)
    assert indices == list(range(10))
    assert indices[0] == 0
    assert indices[1] == 1
    assert next_step_index(db, run.id) == 10


def test_resume_continues_sequence(db: Session):
    project = _seed(db)
    run = _make_run(db, project)
    for i in range(3):
        record_step(db, agent_run_id=run.id, node_name=f"a{i}", status="succeeded")
    assert next_step_index(db, run.id) == 3
    s = record_step(db, agent_run_id=run.id, node_name="resume", status="succeeded")
    assert s.step_index == 3


def test_concurrent_sequences_unique(db: Session, engine):
    """Concurrent allocators must not produce duplicate step_index values."""
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    seed_db = SessionLocal()
    try:
        project = _seed(seed_db)
        run = _make_run(seed_db, project)
        seed_db.commit()
        run_id = run.id
    finally:
        seed_db.close()

    def _worker(n: int) -> int:
        s = SessionLocal()
        try:
            step = record_step(s, agent_run_id=run_id, node_name=f"w{n}", status="succeeded")
            s.commit()
            return step.step_index
        finally:
            s.close()

    with ThreadPoolExecutor(max_workers=8) as pool:
        futs = [pool.submit(_worker, i) for i in range(12)]
        indices = [f.result() for f in as_completed(futs)]

    assert len(indices) == 12
    assert len(set(indices)) == 12
    assert sorted(indices) == list(range(12))


def test_events_api_order_stable(db: Session):
    project = _seed(db)
    run = _make_run(db, project)
    base = datetime.now(UTC)
    # Insert out of chronological order intentionally.
    s1 = AgentStep(
        agent_run_id=run.id,
        step_index=1,
        node_name="second",
        status="succeeded",
        created_at=base + timedelta(seconds=2),
        updated_at=base + timedelta(seconds=2),
        started_at=base,
        finished_at=base,
    )
    s0 = AgentStep(
        agent_run_id=run.id,
        step_index=0,
        node_name="first",
        status="succeeded",
        created_at=base + timedelta(seconds=5),
        updated_at=base + timedelta(seconds=5),
        started_at=base,
        finished_at=base,
    )
    t0 = ToolCall(
        agent_run_id=run.id,
        tool_name="tool_a",
        status="ok",
        created_at=base + timedelta(seconds=1),
        updated_at=base + timedelta(seconds=1),
    )
    db.add_all([s1, s0, t0])
    db.flush()

    events = AgentRunService(db).get_events(run.id, project_id=project.id)
    # Steps ordered by sequence first; tools use 10000+ offset.
    step_events = [e for e in events.items if e.event_type == "step"]
    assert [e.name for e in step_events] == ["first", "second"]
    assert [e.sequence for e in step_events] == [0, 1]
    # Full list: sequence, created_at, id
    sequences = [e.sequence for e in events.items]
    assert sequences == sorted(sequences)
