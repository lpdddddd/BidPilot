"""Evaluation runner opens an isolated DB session per case."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from app.models import BidProject, Organization
from app.services.evaluation.claims import release_evaluation_claim
from app.services.evaluation.runner import execute_evaluation_run
from app.services.evaluation.service import EvaluationService
from sqlalchemy.orm import Session, sessionmaker

FIXTURE = str(Path(__file__).parent / "fixtures" / "evaluation" / "mini_suite.jsonl")


class _SessionTracker:
    def __init__(self, factory):
        self.factory = factory
        self.created_ids: list[int] = []
        self.close_counts: dict[int, int] = {}

    def __call__(self):
        session = self.factory()
        sid = id(session)
        self.created_ids.append(sid)
        original_close = session.close

        def tracked_close():
            self.close_counts[sid] = self.close_counts.get(sid, 0) + 1
            original_close()

        session.close = tracked_close  # type: ignore[method-assign]
        return session


def _project(db: Session) -> BidProject:
    org = Organization(name=f"SessOrg-{uuid4().hex[:6]}")
    db.add(org)
    db.flush()
    project = BidProject(organization_id=org.id, project_code="SESS", project_name="Session")
    db.add(project)
    db.commit()
    return project


def _prepare_run(db: Session, project: BidProject, *, seed: int, case_limit: int):
    run, claim = EvaluationService(db).create_run(
        project.id,
        {
            "target_type": "deterministic_fake",
            "fixture_path": FIXTURE,
            "seed": seed,
            "case_limit": case_limit,
        },
        execute=False,
    )
    if claim is not None:
        release_evaluation_claim(db, run.id, claim_token=claim.claim_token)
    db.commit()
    db.expire_all()
    return run


def test_parallel_workers_use_distinct_sessions(db: Session, engine, monkeypatch):
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    tracker = _SessionTracker(SessionLocal)
    monkeypatch.setattr("app.services.evaluation.runner.SessionLocal", tracker)

    project = _project(db)
    run = _prepare_run(db, project, seed=1, case_limit=4)
    execute_evaluation_run(db, run.id, max_workers=2)

    assert len(tracker.created_ids) >= 2
    assert len(set(tracker.created_ids)) >= 2
    for sid in tracker.created_ids:
        assert tracker.close_counts.get(sid, 0) >= 1


def test_sequential_runner_closes_session_per_case(db: Session, engine, monkeypatch):
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    tracker = _SessionTracker(SessionLocal)
    monkeypatch.setattr("app.services.evaluation.runner.SessionLocal", tracker)

    project = _project(db)
    run = _prepare_run(db, project, seed=2, case_limit=3)
    execute_evaluation_run(db, run.id, max_workers=1)

    assert len(tracker.created_ids) == 3
    assert len(set(tracker.created_ids)) == 3
    assert all(tracker.close_counts.get(sid, 0) == 1 for sid in tracker.created_ids)
