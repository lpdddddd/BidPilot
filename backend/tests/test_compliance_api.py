"""API tests for compliance endpoints."""

from __future__ import annotations

from uuid import uuid4

from app.models import BidProject, Organization, Requirement
from app.models.enums import (
    QualityLevel,
    RequirementCategory,
    ReviewStatus,
    RiskLevel,
)
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session


def _seed(db: Session) -> BidProject:
    org = Organization(name=f"Org-{uuid4().hex[:6]}")
    db.add(org)
    db.flush()
    project = BidProject(
        organization_id=org.id,
        project_code=f"API-{uuid4().hex[:4]}",
        project_name="Compliance API Project",
    )
    db.add(project)
    db.flush()
    db.add(
        Requirement(
            project_id=project.id,
            category=RequirementCategory.mandatory,
            title="必须响应",
            mandatory=True,
            risk_level=RiskLevel.high,
            quality_level=QualityLevel.pending,
            review_status=ReviewStatus.unreviewed,
        )
    )
    db.commit()
    return project


def test_compliance_rules_and_run_flow(client: TestClient, db: Session):
    project = _seed(db)

    rules = client.get("/api/v1/projects/compliance/rules")
    assert rules.status_code == 200
    body = rules.json()
    assert body["total"] >= 29
    assert body["engine_version"]

    scoped = client.get(f"/api/v1/projects/{project.id}/compliance/rules")
    assert scoped.status_code == 200
    assert scoped.json()["total"] == body["total"]

    latest = client.get(f"/api/v1/projects/{project.id}/compliance/latest")
    assert latest.status_code == 200
    assert latest.json() is None

    key = f"k-{uuid4().hex}"
    created = client.post(
        f"/api/v1/projects/{project.id}/compliance/runs",
        json={},
        headers={"Idempotency-Key": key},
    )
    assert created.status_code == 201
    report = created.json()
    run_id = report["run"]["id"]
    assert report["run"]["status"] == "succeeded"
    assert report["finding_count"] >= 1

    again = client.post(
        f"/api/v1/projects/{project.id}/compliance/runs",
        json={},
        headers={"Idempotency-Key": key},
    )
    assert again.status_code == 201
    assert again.json()["run"]["id"] == run_id

    conflict = client.post(
        f"/api/v1/projects/{project.id}/compliance/runs",
        json={"rule_ids": ["A001_mandatory_coverage"]},
        headers={"Idempotency-Key": key},
    )
    assert conflict.status_code == 409

    got = client.get(f"/api/v1/projects/{project.id}/compliance/runs/{run_id}")
    assert got.status_code == 200
    assert got.json()["id"] == run_id

    full = client.get(f"/api/v1/projects/{project.id}/compliance/runs/{run_id}/report")
    assert full.status_code == 200
    assert full.json()["run"]["id"] == run_id

    latest2 = client.get(f"/api/v1/projects/{project.id}/compliance/latest")
    assert latest2.status_code == 200
    assert latest2.json()["run"]["id"] == run_id

    findings = client.get(
        f"/api/v1/projects/{project.id}/compliance/findings",
        params={"severity": "error"},
    )
    assert findings.status_code == 200
    assert findings.json()["run_id"] == run_id

    # Re-run creates a new historical run
    rerun = client.post(f"/api/v1/projects/{project.id}/compliance/runs", json={})
    assert rerun.status_code == 201
    assert rerun.json()["run"]["id"] != run_id
