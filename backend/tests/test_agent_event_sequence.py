"""Agent step_index / events sequence integrity (legacy + unified)."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from uuid import uuid4

from app.models import BidProject, Organization
from app.models.agent import AgentRun
from app.models.enums import AgentRunStatus
from app.services.agent_run.events import next_step_index, record_step
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
