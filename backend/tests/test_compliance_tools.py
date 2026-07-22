"""Tests for compliance tool wrappers."""

from __future__ import annotations

from uuid import uuid4

from app.models import BidProject, Organization, Requirement
from app.models.enums import (
    QualityLevel,
    RequirementCategory,
    ReviewStatus,
    RiskLevel,
)
from app.tools.compliance_tools import (
    GetReportInput,
    ProjectComplianceInput,
    RequirementCoverageInput,
    check_requirement_coverage,
    get_compliance_report,
    run_project_compliance_check,
)
from sqlalchemy.orm import Session


def _project(db: Session) -> BidProject:
    org = Organization(name=f"Org-{uuid4().hex[:6]}")
    db.add(org)
    db.flush()
    project = BidProject(
        organization_id=org.id,
        project_code=f"T-{uuid4().hex[:4]}",
        project_name="Tool Project",
    )
    db.add(project)
    db.flush()
    db.add(
        Requirement(
            project_id=project.id,
            category=RequirementCategory.technical,
            title="技术指标",
            mandatory=False,
            risk_level=RiskLevel.low,
            quality_level=QualityLevel.pending,
            review_status=ReviewStatus.unreviewed,
        )
    )
    db.commit()
    return project


def test_check_requirement_coverage_tool(db: Session):
    project = _project(db)
    result = check_requirement_coverage(
        db, RequirementCoverageInput(project_id=project.id)
    )
    assert result.ok is True
    assert result.report is not None
    assert result.report.run.status.value == "succeeded"
    assert result.metadata["tool"] == "check_requirement_coverage"


def test_run_and_get_report_tools(db: Session):
    project = _project(db)
    key = f"idem-{uuid4().hex}"
    first = run_project_compliance_check(
        db,
        ProjectComplianceInput(project_id=project.id, idempotency_key=key),
    )
    second = run_project_compliance_check(
        db,
        ProjectComplianceInput(project_id=project.id, idempotency_key=key),
    )
    assert first.report is not None and second.report is not None
    assert first.report.run.id == second.report.run.id

    got = get_compliance_report(
        db, GetReportInput(project_id=project.id, run_id=first.report.run.id)
    )
    assert got.ok is True
    assert got.report is not None
    assert got.report.run.id == first.report.run.id

    latest = get_compliance_report(db, GetReportInput(project_id=project.id))
    assert latest.report is not None
