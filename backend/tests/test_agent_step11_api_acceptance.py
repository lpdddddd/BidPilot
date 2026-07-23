"""Step 11 API acceptance: HTTP retry, concurrent retry, schedule failure."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from app.api.v1 import agent_runs as agent_runs_api
from app.models.agent import AgentRun, AgentStep
from app.models.enums import AgentRunStatus
from app.schemas.agent_run import AgentRunStartRequest
from app.services.agent_run.claims import ClaimOutcome, claim_run_execution
from app.services.agent_run.service import AgentRunService
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from tests.test_agent_e2e_scenarios import FakeLlm, _fake_retrieval, _seed


def _patch_agent_service(monkeypatch):
    original = AgentRunService.__init__

    def _init(self, db, llm=None, retrieval_fn=None):
        original(self, db, llm=FakeLlm(), retrieval_fn=retrieval_fn or _fake_retrieval)

    monkeypatch.setattr(AgentRunService, "__init__", _init)


def _failed_retrieve_run(db: Session, project_id):
    svc = AgentRunService(db, llm=FakeLlm(), retrieval_fn=_fake_retrieval)
    run = svc.start_run(
        project_id,
        AgentRunStartRequest(
            user_request="评测",
            metadata={"interrupt_after_node": "retrieve_evidence"},
        ),
        execute=True,
    )
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
    return run


def test_http_retry_after_real_failure_increments_attempt(
    db: Session, client: TestClient, monkeypatch
):
    _patch_agent_service(monkeypatch)
    project, _, _ = _seed(db)
    run = _failed_retrieve_run(db, project.id)
    steps_before = list(
        db.scalars(
            select(AgentStep).where(
                AgentStep.agent_run_id == run.id,
                AgentStep.node_name == "retrieve_evidence",
            )
        ).all()
    )
    max_before = max(s.attempt for s in steps_before)

    resp = client.post(
        f"/api/v1/projects/{project.id}/agent-runs/{run.id}/retry?sync=true",
    )
    assert resp.status_code == 200, resp.text
    steps_after = list(
        db.scalars(
            select(AgentStep).where(
                AgentStep.agent_run_id == run.id,
                AgentStep.node_name == "retrieve_evidence",
            )
        ).all()
    )
    assert max(s.attempt for s in steps_after) > max_before
    assert len(steps_after) > len(steps_before)


def test_http_concurrent_retry_single_claim_winner(db: Session, engine, monkeypatch):
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    project, _, _ = _seed(db)
    run = _failed_retrieve_run(db, project.id)
    run_id = run.id
    project_id = project.id

    barrier = threading.Barrier(2)
    outcomes: list[str] = []

    def worker():
        session = SessionLocal()
        try:
            barrier.wait(timeout=10)
            result = claim_run_execution(session, run_id, action="retry", project_id=project_id)
            outcomes.append(result.outcome.value)
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futs = [pool.submit(worker), pool.submit(worker)]
        for fut in futs:
            fut.result(timeout=30)

    assert outcomes.count(ClaimOutcome.claimed.value) == 1
    assert outcomes.count(ClaimOutcome.already_running.value) == 1


def test_http_background_schedule_failure_releases_claim(
    db: Session, client: TestClient, monkeypatch
):
    from app.db.session import get_db
    from app.main import app

    _patch_agent_service(monkeypatch)
    project, _, _ = _seed(db)
    run = _failed_retrieve_run(db, project.id)

    def boom(*_a, **_k):
        raise RuntimeError("schedule failed")

    def _override_get_db():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db] = _override_get_db
    with (
        patch.object(agent_runs_api.BackgroundTasks, "add_task", boom),
        TestClient(app, raise_server_exceptions=False) as tc,
    ):
        resp = tc.post(
            f"/api/v1/projects/{project.id}/agent-runs/{run.id}/retry",
        )

    assert resp.status_code == 500
    row = db.get(AgentRun, run.id)
    assert row is not None
    assert row.execution_claim_token is None
    assert row.status == AgentRunStatus.failed
