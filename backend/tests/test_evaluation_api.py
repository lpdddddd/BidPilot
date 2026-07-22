"""Evaluation API and permission tests."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from app.models import BidProject, Organization
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


def test_evaluation_api_flow(client: TestClient, db: Session):
    p1, p2 = _seed(db)
    caps = client.get(f"/api/v1/projects/{p1.id}/evaluation-capabilities")
    assert caps.status_code == 200
    body = caps.json()
    assert body["dataset"]["stats"]["total_cases"] == 140
    assert any(t["target_type"] == "deterministic_fake" and t["available"] for t in body["targets"])

    suites = client.get(f"/api/v1/projects/{p1.id}/evaluation-suites")
    assert suites.status_code == 200
    assert suites.json()

    created = client.post(
        f"/api/v1/projects/{p1.id}/evaluation-runs",
        json={"target_type": "deterministic_fake", "fixture_path": FIXTURE, "seed": 9},
        headers={"Idempotency-Key": "api-1"},
    )
    assert created.status_code == 201, created.text
    run = created.json()
    run_id = run["id"]
    assert run["status"] in {"completed", "partial"}

    detail = client.get(f"/api/v1/projects/{p1.id}/evaluation-runs/{run_id}")
    assert detail.status_code == 200
    results = client.get(f"/api/v1/projects/{p1.id}/evaluation-runs/{run_id}/results")
    assert results.status_code == 200
    assert results.json()
    # test split must not leak reference_output
    for row in results.json():
        summary = row.get("reference_summary") or {}
        assert "reference_output" not in summary

    export = client.get(f"/api/v1/projects/{p1.id}/evaluation-runs/{run_id}/export?format=json")
    assert export.status_code == 200
    text = export.text.lower()
    assert "authorization" not in text or "[redacted]" in text
    assert "api_key" not in text

    # cross project 404
    assert client.get(f"/api/v1/projects/{p2.id}/evaluation-runs/{run_id}").status_code == 404

    # compare with second run
    created2 = client.post(
        f"/api/v1/projects/{p1.id}/evaluation-runs",
        json={"target_type": "deterministic_fake", "fixture_path": FIXTURE, "seed": 9},
        headers={"Idempotency-Key": "api-2"},
    )
    right = created2.json()["id"]
    cmp_ = client.get(
        f"/api/v1/projects/{p1.id}/evaluation-runs/compare?left={run_id}&right={right}"
    )
    assert cmp_.status_code == 200
    assert "common_cases" in cmp_.json()


def test_export_sensitive_scan(client: TestClient, db: Session):
    p1, _ = _seed(db)
    created = client.post(
        f"/api/v1/projects/{p1.id}/evaluation-runs",
        json={"target_type": "deterministic_fake", "fixture_path": FIXTURE, "seed": 1},
    )
    run_id = created.json()["id"]
    for fmt in ("json", "csv", "markdown"):
        resp = client.get(f"/api/v1/projects/{p1.id}/evaluation-runs/{run_id}/export?format={fmt}")
        assert resp.status_code == 200
        lowered = resp.text.lower()
        assert "bearer " not in lowered
        assert "postgresql://" not in lowered
