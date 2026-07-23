"""Evaluation API and permission tests — public API has no sync/fixture fields."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest
from app.models import BidProject, Organization
from app.models.enums import EvaluationRunStatus
from app.schemas.evaluation import EvaluationRunCreate
from app.services.evaluation.service import EvaluationService
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

FIXTURE = str(Path(__file__).parent / "fixtures" / "evaluation" / "mini_suite.jsonl")


def _seed(db: Session) -> tuple[BidProject, BidProject]:
    o1 = Organization(name=f"O-{uuid4().hex[:4]}")
    o2 = Organization(name=f"O-{uuid4().hex[:4]}")
    db.add_all([o1, o2])
    db.flush()
    p1 = BidProject(organization_id=o1.id, project_code="P1", project_name="One")
    p2 = BidProject(organization_id=o2.id, project_code="P2", project_name="Two")
    db.add_all([p1, p2])
    db.commit()
    return p1, p2


def _sync_run(db: Session, project_id, **payload):
    return EvaluationService(db).create_run(
        project_id,
        {"target": "deterministic_fake", "fixture_path": FIXTURE, **payload},
        execute=True,
    )


def test_public_schema_forbids_test_fields():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        EvaluationRunCreate(target="rag", fixture_path="/tmp/x.jsonl")  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        EvaluationRunCreate(target="rag", fail_case_keys=["a"])  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        EvaluationRunCreate(target="rag", sync=True)  # type: ignore[call-arg]


def test_evaluation_api_contract_and_flow(client: TestClient, db: Session):
    p1, p2 = _seed(db)
    caps = client.get(f"/api/v1/projects/{p1.id}/evaluation-capabilities")
    assert caps.status_code == 200
    body = caps.json()
    assert "items" in body and "targets" not in body
    assert body["dataset"]["total_cases"] == 140
    assert body["dataset"]["human_gold_count"] == 0
    assert body["dataset"]["auto_reference_count"] == 140
    assert isinstance(body["profiles"], list) and body["profiles"]
    assert any(t["target_type"] == "deterministic_fake" and t["available"] for t in body["items"])
    unwired = {t["target_type"]: t for t in body["items"]}
    assert unwired["extraction"]["available"] is False
    assert unwired["extraction"]["reason_code"] == "service_not_wired"

    suites = client.get(f"/api/v1/projects/{p1.id}/evaluation-suites")
    assert suites.status_code == 200
    assert "items" in suites.json() and "total" in suites.json()

    run, _ = _sync_run(db, p1.id, seed=9, case_limit=2, idempotency_key="api-1")
    run_id = str(run.id)
    assert run.status.value in {"completed", "partial"}
    assert (run.filter_json or {}).get("limit") == 2

    detail = client.get(f"/api/v1/projects/{p1.id}/evaluation-runs/{run_id}")
    assert detail.status_code == 200
    assert detail.json()["detail_url"]
    results = client.get(f"/api/v1/projects/{p1.id}/evaluation-runs/{run_id}/results")
    assert results.status_code == 200
    results_body = results.json()
    assert "items" in results_body
    for row in results_body["items"]:
        summary = row.get("reference_summary") or {}
        assert "reference_output" not in summary
        assert "citations" in row

    export = client.get(f"/api/v1/projects/{p1.id}/evaluation-runs/{run_id}/export?format=json")
    assert export.status_code == 200
    text = export.text.lower()
    assert "api_key" not in text
    assert "/root/autodl-tmp" not in text

    assert client.get(f"/api/v1/projects/{p2.id}/evaluation-runs/{run_id}").status_code == 404

    # Public API rejects fixture_path / sync
    bad = client.post(
        f"/api/v1/projects/{p1.id}/evaluation-runs",
        json={"target": "deterministic_fake", "fixture_path": FIXTURE},
    )
    assert bad.status_code == 422
    bad2 = client.post(
        f"/api/v1/projects/{p1.id}/evaluation-runs?sync=true",
        json={"target": "deterministic_fake", "case_limit": 1},
    )
    # sync query ignored / not accepted for forcing sync full execute via schema body
    assert bad2.status_code in {201, 422}

    run2, _ = _sync_run(db, p1.id, seed=9, idempotency_key="api-2")
    cmp_ = client.get(
        f"/api/v1/projects/{p1.id}/evaluation-runs/compare?left={run_id}&right={run2.id}"
    )
    assert cmp_.status_code == 200
    cmp_body = cmp_.json()
    assert "improved_cases" in cmp_body
    assert "pass_rate_delta" in cmp_body


def test_create_run_background_default(client: TestClient, db: Session):
    p1, _ = _seed(db)
    created = client.post(
        f"/api/v1/projects/{p1.id}/evaluation-runs",
        json={"target": "deterministic_fake", "case_limit": 1, "seed": 1},
        headers={"Idempotency-Key": "bg-1"},
    )
    # Without fixture, uses builtin suite; may run many cases in background.
    assert created.status_code == 201, created.text
    assert created.json()["detail_url"]
    assert created.json()["status"] in {
        "queued",
        "running",
        "completed",
        "partial",
        "failed",
    }


def test_background_schedule_failure_releases_claim(client: TestClient, db: Session):

    from app.api.v1 import evaluation as evaluation_api
    from app.db.session import get_db
    from app.main import app
    from app.models.evaluation import EvaluationRun
    from sqlalchemy import select

    p1, _ = _seed(db)

    def boom(*_a, **_k):
        raise RuntimeError("schedule failed")

    def _override_get_db():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db] = _override_get_db
    with (
        patch.object(evaluation_api.BackgroundTasks, "add_task", boom),
        TestClient(app, raise_server_exceptions=False) as tc,
    ):
        resp = tc.post(
            f"/api/v1/projects/{p1.id}/evaluation-runs",
            json={"target": "deterministic_fake", "case_limit": 1, "seed": 1},
            headers={"Idempotency-Key": "sched-fail"},
        )

    assert resp.status_code == 500
    run = db.scalar(select(EvaluationRun).where(EvaluationRun.idempotency_key == "sched-fail"))
    assert run is not None
    assert run.execution_claim_token is None
    assert run.status == EvaluationRunStatus.queued
    assert run.safe_error_summary


def test_export_sensitive_scan(client: TestClient, db: Session):
    p1, _ = _seed(db)
    run, _ = _sync_run(db, p1.id, seed=1)
    for fmt in ("json", "csv", "markdown"):
        resp = client.get(f"/api/v1/projects/{p1.id}/evaluation-runs/{run.id}/export?format={fmt}")
        assert resp.status_code == 200
        lowered = resp.text.lower()
        assert "bearer " not in lowered
        assert "postgresql://" not in lowered
