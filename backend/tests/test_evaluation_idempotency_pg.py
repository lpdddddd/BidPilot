"""PostgreSQL concurrent idempotency for evaluation run creation."""

from __future__ import annotations

import threading
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from app.db.session import get_db
from app.main import app
from app.models import BidProject, Organization
from app.models.evaluation import EvaluationRun
from fastapi import BackgroundTasks
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

pytestmark = pytest.mark.postgres


@pytest.fixture()
def threaded_client(engine, monkeypatch) -> Generator[TestClient, None, None]:
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    monkeypatch.setattr("app.services.evaluation.tasks.SESSION_FACTORY", SessionLocal)

    def _override_get_db() -> Generator[Session, None, None]:
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _seed(db: Session) -> BidProject:
    org = Organization(name=f"IdemOrg-{uuid4().hex[:6]}")
    db.add(org)
    db.flush()
    project = BidProject(organization_id=org.id, project_code="IDEM", project_name="Idem")
    db.add(project)
    db.commit()
    return project


def test_concurrent_same_idempotency_key_same_run(threaded_client: TestClient, engine, monkeypatch):
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    seed_db = SessionLocal()
    try:
        project = _seed(seed_db)
        project_id = str(project.id)
    finally:
        seed_db.close()

    scheduled: list[tuple] = []
    schedule_lock = threading.Lock()
    key = f"eval-idem-{uuid4().hex}"

    def tracking_add_task(self, func, *args, **kwargs):
        with schedule_lock:
            scheduled.append((func, args, kwargs))

    barrier = threading.Barrier(2)
    responses: list = []
    errors: list[Exception] = []

    def post_once():
        try:
            barrier.wait(timeout=10)
            resp = threaded_client.post(
                f"/api/v1/projects/{project_id}/evaluation-runs",
                json={"target": "deterministic_fake", "case_limit": 1, "seed": 1},
                headers={"Idempotency-Key": key},
            )
            responses.append(resp)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    monkeypatch.setattr(BackgroundTasks, "add_task", tracking_add_task)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futs = [pool.submit(post_once), pool.submit(post_once)]
        for fut in futs:
            fut.result(timeout=30)

    assert not errors, errors
    assert len(responses) == 2
    for resp in responses:
        assert resp.status_code == 201, resp.text

    run_ids = {resp.json()["id"] for resp in responses}
    assert len(run_ids) == 1

    verify = SessionLocal()
    try:
        count = verify.scalar(
            select(func.count())
            .select_from(EvaluationRun)
            .where(
                EvaluationRun.project_id == project_id,
                EvaluationRun.idempotency_key == key,
            )
        )
        assert count == 1
    finally:
        verify.close()

    with schedule_lock:
        assert len(scheduled) <= 1


def test_different_projects_same_key_ok(threaded_client: TestClient, engine):
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    seed_db = SessionLocal()
    try:
        p1 = _seed(seed_db)
        p2 = _seed(seed_db)
        p1_id = str(p1.id)
        p2_id = str(p2.id)
    finally:
        seed_db.close()

    key = f"shared-{uuid4().hex}"
    r1 = threaded_client.post(
        f"/api/v1/projects/{p1_id}/evaluation-runs",
        json={"target": "deterministic_fake", "case_limit": 1, "seed": 1},
        headers={"Idempotency-Key": key},
    )
    r2 = threaded_client.post(
        f"/api/v1/projects/{p2_id}/evaluation-runs",
        json={"target": "deterministic_fake", "case_limit": 1, "seed": 1},
        headers={"Idempotency-Key": key},
    )
    assert r1.status_code == 201, r1.text
    assert r2.status_code == 201, r2.text
    assert r1.json()["id"] != r2.json()["id"]


def test_different_keys_same_project_ok(threaded_client: TestClient, engine):
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    seed_db = SessionLocal()
    try:
        project = _seed(seed_db)
        project_id = str(project.id)
    finally:
        seed_db.close()

    r1 = threaded_client.post(
        f"/api/v1/projects/{project_id}/evaluation-runs",
        json={"target": "deterministic_fake", "case_limit": 1, "seed": 1},
        headers={"Idempotency-Key": f"k1-{uuid4().hex}"},
    )
    r2 = threaded_client.post(
        f"/api/v1/projects/{project_id}/evaluation-runs",
        json={"target": "deterministic_fake", "case_limit": 1, "seed": 1},
        headers={"Idempotency-Key": f"k2-{uuid4().hex}"},
    )
    assert r1.status_code == 201, r1.text
    assert r2.status_code == 201, r2.text
    assert r1.json()["id"] != r2.json()["id"]
