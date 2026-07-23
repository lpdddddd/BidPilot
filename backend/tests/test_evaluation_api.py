"""Evaluation API and permission tests."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest
from app.models import BidProject, Organization
from app.models.enums import EvaluationRunStatus
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
    assert "id" in body["profiles"][0] and "enabled_metrics" in body["profiles"][0]
    assert any(t["target_type"] == "deterministic_fake" and t["available"] for t in body["items"])

    suites = client.get(f"/api/v1/projects/{p1.id}/evaluation-suites")
    assert suites.status_code == 200
    suites_body = suites.json()
    assert "items" in suites_body and "total" in suites_body
    assert suites_body["page"] >= 1 and suites_body["page_size"] >= 1

    created = client.post(
        f"/api/v1/projects/{p1.id}/evaluation-runs?sync=true",
        json={
            "target": "deterministic_fake",
            "fixture_path": FIXTURE,
            "seed": 9,
            "case_limit": 2,
            "evaluator_profile": "default",
        },
        headers={"Idempotency-Key": "api-1"},
    )
    assert created.status_code == 201, created.text
    run = created.json()
    run_id = run["id"]
    assert run["status"] in {"completed", "partial"}
    assert run["detail_url"]
    assert run["filter_json"]["limit"] == 2
    assert run["total_cases"] <= 2

    detail = client.get(f"/api/v1/projects/{p1.id}/evaluation-runs/{run_id}")
    assert detail.status_code == 200
    results = client.get(f"/api/v1/projects/{p1.id}/evaluation-runs/{run_id}/results")
    assert results.status_code == 200
    results_body = results.json()
    assert "items" in results_body and "total" in results_body
    assert results_body["items"]
    for row in results_body["items"]:
        summary = row.get("reference_summary") or {}
        assert "reference_output" not in summary
        assert "citations" in row

    export = client.get(f"/api/v1/projects/{p1.id}/evaluation-runs/{run_id}/export?format=json")
    assert export.status_code == 200
    text = export.text.lower()
    assert "authorization" not in text or "[redacted]" in text
    assert "api_key" not in text
    assert "/root/autodl-tmp" not in text

    assert client.get(f"/api/v1/projects/{p2.id}/evaluation-runs/{run_id}").status_code == 404

    created2 = client.post(
        f"/api/v1/projects/{p1.id}/evaluation-runs?sync=true",
        json={"target_type": "deterministic_fake", "fixture_path": FIXTURE, "seed": 9},
        headers={"Idempotency-Key": "api-2"},
    )
    right = created2.json()["id"]
    cmp_ = client.get(
        f"/api/v1/projects/{p1.id}/evaluation-runs/compare?left={run_id}&right={right}"
    )
    assert cmp_.status_code == 200
    cmp_body = cmp_.json()
    assert "common_cases" in cmp_body
    assert "improved_cases" in cmp_body
    assert "left" in cmp_body and "overall_score" in cmp_body["left"]
    assert "pass_rate_delta" in cmp_body


def test_create_run_background_default_not_sync(client: TestClient, db: Session):
    p1, _ = _seed(db)
    created = client.post(
        f"/api/v1/projects/{p1.id}/evaluation-runs",
        json={"target": "deterministic_fake", "fixture_path": FIXTURE, "seed": 1, "case_limit": 1},
        headers={"Idempotency-Key": "bg-1"},
    )
    assert created.status_code == 201, created.text
    # Without sync, claim schedules background; status is running or already finished via TestClient
    assert created.json()["status"] in {
        "queued",
        "running",
        "completed",
        "partial",
        "failed",
    }
    assert created.json()["detail_url"]


def test_background_schedule_failure_releases_claim(client: TestClient, db: Session):
    p1, _ = _seed(db)

    def boom(*_a, **_k):
        raise RuntimeError("schedule failed")

    with (
        patch("app.api.v1.evaluation.BackgroundTasks.add_task", side_effect=boom),
        pytest.raises(RuntimeError, match="schedule failed"),
    ):
        # Call route function directly so we assert release even if HTTP maps to 500
        from app.api.v1 import evaluation as evaluation_api
        from fastapi import BackgroundTasks

        bg = BackgroundTasks()
        evaluation_api.create_run(
            project_id=p1.id,
            payload=__import__(
                "app.schemas.evaluation", fromlist=["EvaluationRunCreate"]
            ).EvaluationRunCreate(
                target="deterministic_fake",
                fixture_path=FIXTURE,
                seed=1,
                case_limit=1,
            ),
            background_tasks=bg,
            db=db,
            idempotency_key="sched-fail",
            sync=False,
        )

    from app.models.evaluation import EvaluationRun
    from sqlalchemy import select

    run = db.scalar(select(EvaluationRun).where(EvaluationRun.idempotency_key == "sched-fail"))
    assert run is not None
    assert run.execution_claim_token is None
    assert run.status == EvaluationRunStatus.queued
    assert run.safe_error_summary


def test_export_sensitive_scan(client: TestClient, db: Session):
    p1, _ = _seed(db)
    created = client.post(
        f"/api/v1/projects/{p1.id}/evaluation-runs?sync=true",
        json={"target_type": "deterministic_fake", "fixture_path": FIXTURE, "seed": 1},
    )
    run_id = created.json()["id"]
    for fmt in ("json", "csv", "markdown"):
        resp = client.get(f"/api/v1/projects/{p1.id}/evaluation-runs/{run_id}/export?format={fmt}")
        assert resp.status_code == 200
        lowered = resp.text.lower()
        assert "bearer " not in lowered
        assert "postgresql://" not in lowered
