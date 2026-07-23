"""Bounded cancel semantics for evaluation runner."""

from __future__ import annotations

import threading
from pathlib import Path
from uuid import uuid4

from app.models import BidProject, Organization
from app.models.enums import EvaluationRunStatus
from app.services.evaluation.claims import release_evaluation_claim
from app.services.evaluation.runner import execute_evaluation_run
from app.services.evaluation.service import EvaluationService
from app.services.evaluation.targets.fake import DeterministicFakeTarget
from sqlalchemy.orm import Session, sessionmaker

FIXTURE = str(Path(__file__).parent / "fixtures" / "evaluation" / "mini_suite.jsonl")


class _RunGate:
    def __init__(self) -> None:
        self.ready = threading.Event()
        self.release = threading.Event()
        self.started: list[str] = []
        self.lock = threading.Lock()
        self.blocking = True

    def disable_blocking(self) -> None:
        self.blocking = False


def _install_blocking_fake(gate: _RunGate):
    original = DeterministicFakeTarget.run_case

    def blocking_run_case(self, target_input, context):
        if gate.blocking:
            with gate.lock:
                gate.started.append(target_input.case_key)
                if len(gate.started) >= 2:
                    gate.ready.set()
            assert gate.release.wait(timeout=30)
        return original(self, target_input, context)

    DeterministicFakeTarget.run_case = blocking_run_case  # type: ignore[method-assign]
    return original


def _restore_blocking_fake(original) -> None:
    DeterministicFakeTarget.run_case = original  # type: ignore[method-assign]


def _project(db: Session) -> BidProject:
    org = Organization(name=f"CancelOrg-{uuid4().hex[:6]}")
    db.add(org)
    db.flush()
    project = BidProject(organization_id=org.id, project_code="CANCEL", project_name="Cancel")
    db.add(project)
    db.commit()
    return project


def test_bounded_cancel_stops_new_cases_preserves_completed(db: Session, engine, monkeypatch):
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    monkeypatch.setattr("app.services.evaluation.runner.SessionLocal", SessionLocal)
    project = _project(db)
    gate = _RunGate()
    original_run = _install_blocking_fake(gate)

    svc = EvaluationService(db)
    run, claim = svc.create_run(
        project.id,
        {
            "target_type": "deterministic_fake",
            "fixture_path": FIXTURE,
            "seed": 1,
            "case_limit": 4,
        },
        execute=False,
    )
    assert claim is not None
    run_id = run.id
    release_evaluation_claim(db, run_id, claim_token=claim.claim_token)
    db.commit()
    db.expire_all()

    done = threading.Event()

    def _execute():
        session = SessionLocal()
        try:
            execute_evaluation_run(session, run_id, max_workers=2)
        finally:
            session.close()
            done.set()

    worker = threading.Thread(target=_execute, daemon=True)
    worker.start()
    try:
        assert gate.ready.wait(timeout=30), "expected two in-flight cases before cancel"

        svc.cancel(project.id, run_id)
        gate.release.set()
        assert done.wait(timeout=30)
        worker.join(timeout=5)

        db.refresh(run)
        assert run.cancel_requested is True
        assert run.status == EvaluationRunStatus.cancelled
        assert len(gate.started) == 2
        assert run.completed_cases == 2
        assert run.total_cases == 4
    finally:
        gate.release.set()
        _restore_blocking_fake(original_run)


def test_resume_after_cancel_runs_remaining_only(db: Session, engine, monkeypatch):
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    monkeypatch.setattr("app.services.evaluation.runner.SessionLocal", SessionLocal)
    project = _project(db)
    gate = _RunGate()
    original_run = _install_blocking_fake(gate)

    svc = EvaluationService(db)
    run, claim = svc.create_run(
        project.id,
        {
            "target_type": "deterministic_fake",
            "fixture_path": FIXTURE,
            "seed": 2,
            "case_limit": 3,
        },
        execute=False,
    )
    run_id = run.id
    release_evaluation_claim(db, run_id, claim_token=claim.claim_token if claim else None)
    db.commit()
    db.expire_all()

    done = threading.Event()

    def _execute():
        session = SessionLocal()
        try:
            execute_evaluation_run(session, run_id, max_workers=1)
        finally:
            session.close()
            done.set()

    worker = threading.Thread(target=_execute, daemon=True)
    worker.start()
    started_wait = threading.Event()

    def _watch():
        while not done.is_set():
            with gate.lock:
                if len(gate.started) >= 1:
                    started_wait.set()
                    return

    watcher = threading.Thread(target=_watch, daemon=True)
    watcher.start()
    try:
        assert started_wait.wait(timeout=30)

        svc.cancel(project.id, run_id)
        gate.release.set()
        assert done.wait(timeout=30)
        worker.join(timeout=5)

        db.refresh(run)
        preserved = {r.case_key: r.id for r in run.case_results}
        before_count = len(preserved)

        gate.disable_blocking()
        run.cancel_requested = False
        run.execution_claim_token = None
        run.status = EvaluationRunStatus.partial
        db.commit()

        resumed, _ = svc.resume(project.id, run_id, execute=True)
        after_keys = {r.case_key for r in resumed.case_results}
        assert len(after_keys) == 3
        for key, row_id in preserved.items():
            row = next(r for r in resumed.case_results if r.case_key == key)
            assert row.id == row_id
        assert len(after_keys) > before_count
    finally:
        gate.release.set()
        _restore_blocking_fake(original_run)


def test_cancel_idempotent_and_terminal_noop(db: Session):
    project = _project(db)
    svc = EvaluationService(db)
    run, _ = svc.create_run(
        project.id,
        {
            "target_type": "deterministic_fake",
            "fixture_path": FIXTURE,
            "seed": 3,
            "case_limit": 1,
        },
        execute=True,
    )
    assert run.status in {EvaluationRunStatus.completed, EvaluationRunStatus.partial}

    first = svc.cancel(project.id, run.id)
    second = svc.cancel(project.id, run.id)
    assert first.status == second.status
    assert second.status in {
        EvaluationRunStatus.completed,
        EvaluationRunStatus.partial,
        EvaluationRunStatus.cancelled,
    }

    run2, _ = svc.create_run(
        project.id,
        {
            "target_type": "deterministic_fake",
            "fixture_path": FIXTURE,
            "seed": 4,
            "case_limit": 2,
        },
        execute=False,
    )
    run2.status = EvaluationRunStatus.running
    run2.cancel_requested = False
    db.commit()

    c1 = svc.cancel(project.id, run2.id)
    c2 = svc.cancel(project.id, run2.id)
    assert c1.cancel_requested is True
    assert c2.cancel_requested is True
    assert c1.status == c2.status
