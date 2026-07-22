"""API routes for deterministic compliance rule engine."""

from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.enums import (
    ComplianceFindingStatus,
    ComplianceRuleCategory,
    ComplianceSeverity,
)
from app.schemas.compliance import (
    ComplianceFindingFilters,
    ComplianceFindingListResponse,
    ComplianceReport,
    ComplianceRuleListResponse,
    ComplianceRunRead,
    ComplianceStartRequest,
)
from app.services.compliance.service import ComplianceService

router = APIRouter()


@router.get(
    "/compliance/rules",
    response_model=ComplianceRuleListResponse,
)
@router.get(
    "/{project_id}/compliance/rules",
    response_model=ComplianceRuleListResponse,
)
def list_compliance_rules(
    project_id: UUID | None = None,
    db: Session = Depends(get_db),
) -> ComplianceRuleListResponse:
    _ = project_id  # project-scoped alias; rules are global
    return ComplianceService(db).list_rules()


@router.post(
    "/{project_id}/compliance/runs",
    response_model=ComplianceReport,
    status_code=201,
)
def start_compliance_run(
    project_id: UUID,
    payload: ComplianceStartRequest | None = None,
    db: Session = Depends(get_db),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> ComplianceReport:
    return ComplianceService(db).start_run(
        project_id,
        payload or ComplianceStartRequest(),
        idempotency_key=idempotency_key,
    )


@router.post(
    "/{project_id}/proposal-drafts/{draft_id}/compliance/runs",
    response_model=ComplianceReport,
    status_code=201,
)
def start_draft_compliance_run(
    project_id: UUID,
    draft_id: UUID,
    payload: ComplianceStartRequest | None = None,
    db: Session = Depends(get_db),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> ComplianceReport:
    body = payload or ComplianceStartRequest()
    return ComplianceService(db).start_run(
        project_id,
        ComplianceStartRequest(
            draft_id=draft_id,
            rule_ids=body.rule_ids,
            categories=body.categories
            or [ComplianceRuleCategory.draft_safety],
        ),
        idempotency_key=idempotency_key,
        draft_id=draft_id,
    )


@router.get(
    "/{project_id}/compliance/runs/{run_id}",
    response_model=ComplianceRunRead,
)
def get_compliance_run(
    project_id: UUID,
    run_id: UUID,
    db: Session = Depends(get_db),
) -> ComplianceRunRead:
    return ComplianceService(db).get_run(project_id, run_id)


@router.get(
    "/{project_id}/compliance/runs/{run_id}/report",
    response_model=ComplianceReport,
)
def get_compliance_run_report(
    project_id: UUID,
    run_id: UUID,
    db: Session = Depends(get_db),
) -> ComplianceReport:
    return ComplianceService(db).get_report(project_id, run_id)


@router.get(
    "/{project_id}/compliance/latest",
    response_model=ComplianceReport | None,
)
def get_latest_compliance(
    project_id: UUID,
    db: Session = Depends(get_db),
) -> ComplianceReport | None:
    return ComplianceService(db).get_latest(project_id)


@router.get(
    "/{project_id}/compliance/findings",
    response_model=ComplianceFindingListResponse,
)
def list_compliance_findings(
    project_id: UUID,
    severity: ComplianceSeverity | None = Query(default=None),
    category: ComplianceRuleCategory | None = Query(default=None),
    rule_id: str | None = Query(default=None),
    requirement_id: UUID | None = Query(default=None),
    draft_id: UUID | None = Query(default=None),
    status: ComplianceFindingStatus | None = Query(default=None),
    run_id: UUID | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> ComplianceFindingListResponse:
    return ComplianceService(db).list_findings(
        project_id,
        ComplianceFindingFilters(
            severity=severity,
            category=category,
            rule_id=rule_id,
            requirement_id=requirement_id,
            draft_id=draft_id,
            status=status,
            run_id=run_id,
            limit=limit,
            offset=offset,
        ),
    )
