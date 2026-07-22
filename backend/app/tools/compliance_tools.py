"""Pydantic I/O tool wrappers for deterministic compliance checks (no LangGraph)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.models.enums import ComplianceRuleCategory
from app.schemas.compliance import (
    ComplianceFindingFilters,
    ComplianceReport,
    ComplianceStartRequest,
)
from app.services.compliance.service import ComplianceService


class ProjectIdInput(BaseModel):
    project_id: UUID


class RequirementCoverageInput(BaseModel):
    project_id: UUID
    idempotency_key: str | None = None


class EvidenceIntegrityInput(BaseModel):
    project_id: UUID
    idempotency_key: str | None = None


class DraftComplianceInput(BaseModel):
    project_id: UUID
    draft_id: UUID
    idempotency_key: str | None = None
    categories: list[ComplianceRuleCategory] | None = None


class ProjectComplianceInput(BaseModel):
    project_id: UUID
    draft_id: UUID | None = None
    rule_ids: list[str] | None = None
    categories: list[ComplianceRuleCategory] | None = None
    idempotency_key: str | None = None


DEFAULT_DRAFT_COMPLIANCE_CATEGORIES: list[ComplianceRuleCategory] = [
    ComplianceRuleCategory.draft_safety,
    ComplianceRuleCategory.consistency,
]


class GetReportInput(BaseModel):
    project_id: UUID
    run_id: UUID | None = None


class ComplianceToolResult(BaseModel):
    ok: bool = True
    report: ComplianceReport | None = None
    detail: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


def check_requirement_coverage(
    db: Session, payload: RequirementCoverageInput
) -> ComplianceToolResult:
    report = ComplianceService(db).start_run(
        payload.project_id,
        ComplianceStartRequest(categories=[ComplianceRuleCategory.coverage]),
        idempotency_key=payload.idempotency_key,
    )
    return ComplianceToolResult(
        report=report,
        metadata={"tool": "check_requirement_coverage", "categories": ["coverage"]},
    )


def check_evidence_integrity(db: Session, payload: EvidenceIntegrityInput) -> ComplianceToolResult:
    report = ComplianceService(db).start_run(
        payload.project_id,
        ComplianceStartRequest(categories=[ComplianceRuleCategory.evidence]),
        idempotency_key=payload.idempotency_key,
    )
    return ComplianceToolResult(
        report=report,
        metadata={"tool": "check_evidence_integrity", "categories": ["evidence"]},
    )


def check_draft_compliance(db: Session, payload: DraftComplianceInput) -> ComplianceToolResult:
    """Run draft-scoped compliance (D* draft_safety + E* consistency by default).

    Passes ``draft_id`` through ComplianceStartRequest so ownership checks
    (e.g. E005) apply when cross-project citations exist.
    """
    categories = list(payload.categories or DEFAULT_DRAFT_COMPLIANCE_CATEGORIES)
    report = ComplianceService(db).start_run(
        payload.project_id,
        ComplianceStartRequest(
            draft_id=payload.draft_id,
            categories=categories,
        ),
        idempotency_key=payload.idempotency_key,
        draft_id=payload.draft_id,
    )
    return ComplianceToolResult(
        report=report,
        metadata={
            "tool": "check_draft_compliance",
            "categories": [c.value for c in categories],
            "draft_id": str(payload.draft_id),
        },
    )


def run_project_compliance_check(
    db: Session, payload: ProjectComplianceInput
) -> ComplianceToolResult:
    report = ComplianceService(db).start_run(
        payload.project_id,
        ComplianceStartRequest(
            draft_id=payload.draft_id,
            rule_ids=payload.rule_ids,
            categories=payload.categories,
        ),
        idempotency_key=payload.idempotency_key,
        draft_id=payload.draft_id,
    )
    return ComplianceToolResult(
        report=report,
        metadata={"tool": "run_project_compliance_check"},
    )


def get_compliance_report(db: Session, payload: GetReportInput) -> ComplianceToolResult:
    service = ComplianceService(db)
    if payload.run_id is not None:
        report: ComplianceReport | None = service.get_report(payload.project_id, payload.run_id)
    else:
        report = service.get_latest(payload.project_id)
        if report is None:
            return ComplianceToolResult(
                ok=False,
                detail="no compliance runs for project",
                metadata={"tool": "get_compliance_report"},
            )
    return ComplianceToolResult(
        report=report,
        metadata={"tool": "get_compliance_report"},
    )


# Convenience re-export for finding filters used by callers.
__all__ = [
    "ComplianceFindingFilters",
    "ComplianceToolResult",
    "DEFAULT_DRAFT_COMPLIANCE_CATEGORIES",
    "DraftComplianceInput",
    "EvidenceIntegrityInput",
    "GetReportInput",
    "ProjectComplianceInput",
    "ProjectIdInput",
    "RequirementCoverageInput",
    "check_draft_compliance",
    "check_evidence_integrity",
    "check_requirement_coverage",
    "get_compliance_report",
    "run_project_compliance_check",
]
