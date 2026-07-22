"""Evaluation runner lifecycle tests."""

from __future__ import annotations

from pathlib import Path

from app.models import BidProject, Organization
from app.models.enums import EvaluationRunStatus
from app.services.evaluation.service import EvaluationService
from app.services.evaluation.suite_loader import load_jsonl
from sqlalchemy.orm import Session

FIXTURE = str(Path(__file__).parent / "fixtures" / "evaluation" / "mini_suite.jsonl")


def _project(db: Session) -> BidProject:
    org = Organization(name="EvalOrg")
    db.add(org)
    db.flush()
    project = BidProject(organization_id=org.id, project_code="EV1", project_name="Eval")
    db.add(project)
    db.commit()
    return project


def test_runner_twice_deterministic(db: Session):
    project = _project(db)
    svc = EvaluationService(db)
    payload = {
        "target_type": "deterministic_fake",
        "fixture_path": FIXTURE,
        "seed": 7,
    }
    r1 = svc.create_run(project.id, payload, idempotency_key="k1", execute=True)
    r2 = svc.create_run(project.id, {**payload}, idempotency_key="k2", execute=True)
    assert r1.status == EvaluationRunStatus.completed
    assert r2.status == EvaluationRunStatus.completed
    assert r1.overall_score == r2.overall_score
    assert r1.passed_cases == r2.passed_cases
    # business results equal ignoring timestamps
    c1 = sorted([(c.case_key, c.score, c.passed, c.status.value) for c in r1.case_results])
    c2 = sorted([(c.case_key, c.score, c.passed, c.status.value) for c in r2.case_results])
    assert c1 == c2


def test_idempotent_create(db: Session):
    project = _project(db)
    svc = EvaluationService(db)
    payload = {"target_type": "deterministic_fake", "fixture_path": FIXTURE, "seed": 1}
    a = svc.create_run(project.id, payload, idempotency_key="same", execute=True)
    b = svc.create_run(project.id, payload, idempotency_key="same", execute=True)
    assert a.id == b.id


def test_batch_continues_on_case_error(db: Session):
    project = _project(db)
    svc = EvaluationService(db)
    samples = load_jsonl(Path(FIXTURE))
    fail_key = samples[0]["sample_id"]
    run = svc.create_run(
        project.id,
        {
            "target_type": "deterministic_fake",
            "fixture_path": FIXTURE,
            "fail_case_keys": [fail_key],
            "seed": 1,
        },
        execute=True,
    )
    assert run.error_cases >= 1
    assert run.completed_cases == run.total_cases
    assert run.status in {
        EvaluationRunStatus.partial,
        EvaluationRunStatus.completed,
        EvaluationRunStatus.failed,
    }


def test_resume_skips_completed(db: Session):
    project = _project(db)
    svc = EvaluationService(db)
    run = svc.create_run(
        project.id,
        {"target_type": "deterministic_fake", "fixture_path": FIXTURE, "seed": 3},
        execute=True,
    )
    before = {c.case_key: c.id for c in run.case_results}
    # Force partial + clear claim for resume
    run.status = EvaluationRunStatus.partial
    run.execution_claim_token = None
    # delete one case to simulate incomplete
    victim = run.case_results[0]
    key = victim.case_key
    db.delete(victim)
    db.commit()
    resumed = svc.resume(project.id, run.id, execute=True)
    keys = {c.case_key for c in resumed.case_results}
    assert key in keys
    # other case ids preserved
    for k, cid in before.items():
        if k == key:
            continue
        row = next(c for c in resumed.case_results if c.case_key == k)
        assert row.id == cid


def test_reference_file_not_mutated(db: Session):
    path = Path(FIXTURE)
    before = path.read_text(encoding="utf-8")
    project = _project(db)
    EvaluationService(db).create_run(
        project.id,
        {"target_type": "deterministic_fake", "fixture_path": FIXTURE, "seed": 1},
        execute=True,
    )
    assert path.read_text(encoding="utf-8") == before
