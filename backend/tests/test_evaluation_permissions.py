"""Cross-project permission tests for evaluation APIs."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from app.models import BidProject, Organization
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

FIXTURE = str(Path(__file__).parent / "fixtures" / "evaluation" / "mini_suite.jsonl")


def test_cross_project_blocked(client: TestClient, db: Session):
    o1 = Organization(name="A")
    o2 = Organization(name="B")
    db.add_all([o1, o2])
    db.flush()
    p1 = BidProject(organization_id=o1.id, project_code="A1", project_name="A")
    p2 = BidProject(organization_id=o2.id, project_code="B1", project_name="B")
    db.add_all([p1, p2])
    db.commit()

    created = client.post(
        f"/api/v1/projects/{p1.id}/evaluation-runs?sync=true",
        json={"target_type": "deterministic_fake", "fixture_path": FIXTURE, "seed": 1},
    )
    assert created.status_code == 201
    run_id = created.json()["id"]
    results = client.get(f"/api/v1/projects/{p1.id}/evaluation-runs/{run_id}/results").json()
    result_id = results["items"][0]["id"]

    assert client.get(f"/api/v1/projects/{p2.id}/evaluation-runs/{run_id}").status_code == 404
    assert (
        client.get(f"/api/v1/projects/{p2.id}/evaluation-runs/{run_id}/results").status_code == 404
    )
    assert (
        client.get(
            f"/api/v1/projects/{p2.id}/evaluation-runs/{run_id}/results/{result_id}"
        ).status_code
        == 404
    )
    assert (
        client.post(f"/api/v1/projects/{p2.id}/evaluation-runs/{run_id}/cancel").status_code == 404
    )
    assert client.get(f"/api/v1/projects/{p2.id}/evaluation-suites/{uuid4()}").status_code == 404
