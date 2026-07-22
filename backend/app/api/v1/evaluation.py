"""Evaluation center API routes."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Response
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.evaluation import (
    EvaluationCapabilitiesResponse,
    EvaluationCaseResultRead,
    EvaluationRunCreate,
    EvaluationRunRead,
    EvaluationSuiteRead,
)
from app.services.evaluation.service import EvaluationService

router = APIRouter()


def _run_read(run) -> EvaluationRunRead:
    data = EvaluationRunRead.model_validate(run)
    # coerce enums to str already via from_attributes if needed
    return data.model_copy(
        update={
            "status": run.status.value if hasattr(run.status, "value") else str(run.status),
            "target_type": run.target_type.value
            if hasattr(run.target_type, "value")
            else str(run.target_type),
        }
    )


@router.get("/{project_id}/evaluation-capabilities", response_model=EvaluationCapabilitiesResponse)
def evaluation_capabilities(project_id: UUID, db: Session = Depends(get_db)):
    return EvaluationService(db).capabilities(project_id)


@router.get("/{project_id}/evaluation-suites", response_model=list[EvaluationSuiteRead])
def list_suites(project_id: UUID, db: Session = Depends(get_db)):
    return EvaluationService(db).list_suites(project_id)


@router.get("/{project_id}/evaluation-suites/{suite_id}", response_model=EvaluationSuiteRead)
def get_suite(project_id: UUID, suite_id: UUID, db: Session = Depends(get_db)):
    return EvaluationService(db).get_suite(project_id, suite_id)


@router.post("/{project_id}/evaluation-runs", response_model=EvaluationRunRead, status_code=201)
def create_run(
    project_id: UUID,
    payload: EvaluationRunCreate,
    db: Session = Depends(get_db),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    sync: bool = Query(default=True, description="Execute in-request (default true for tests)"),
):
    run = EvaluationService(db).create_run(
        project_id,
        payload.model_dump(),
        idempotency_key=idempotency_key,
        execute=sync,
    )
    return _run_read(run)


@router.get("/{project_id}/evaluation-runs", response_model=list[EvaluationRunRead])
def list_runs(
    project_id: UUID,
    db: Session = Depends(get_db),
    status: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    return [
        _run_read(r)
        for r in EvaluationService(db).list_runs(
            project_id, status=status, limit=limit, offset=offset
        )
    ]


@router.get("/{project_id}/evaluation-runs/compare")
def compare_runs(
    project_id: UUID,
    left: UUID = Query(...),
    right: UUID = Query(...),
    db: Session = Depends(get_db),
):
    return EvaluationService(db).compare(project_id, left, right)


@router.get("/{project_id}/evaluation-runs/{run_id}", response_model=EvaluationRunRead)
def get_run(project_id: UUID, run_id: UUID, db: Session = Depends(get_db)):
    return _run_read(EvaluationService(db).get_run(project_id, run_id))


@router.get(
    "/{project_id}/evaluation-runs/{run_id}/results", response_model=list[EvaluationCaseResultRead]
)
def list_results(
    project_id: UUID,
    run_id: UUID,
    db: Session = Depends(get_db),
    status: str | None = None,
    task_family: str | None = None,
    passed: bool | None = None,
):
    rows = EvaluationService(db).get_results(
        project_id, run_id, status=status, task_family=task_family, passed=passed
    )
    out = []
    for r in rows:
        item = EvaluationCaseResultRead.model_validate(r)
        out.append(
            item.model_copy(
                update={
                    "status": r.status.value,
                    "reference_kind": r.reference_kind.value,
                    # Never leak full test reference_output
                    "reference_summary": r.reference_summary,
                }
            )
        )
    return out


@router.get(
    "/{project_id}/evaluation-runs/{run_id}/results/{result_id}",
    response_model=EvaluationCaseResultRead,
)
def get_result(project_id: UUID, run_id: UUID, result_id: UUID, db: Session = Depends(get_db)):
    r = EvaluationService(db).get_result(project_id, run_id, result_id)
    metrics = [
        {
            "metric_name": m.metric_name,
            "metric_version": m.metric_version,
            "value": m.value,
            "applicable": m.applicable,
            "weight": m.weight,
            "threshold": m.threshold,
            "passed": m.passed,
            "evidence_summary": m.evidence_summary,
            "reference_kind": m.reference_kind.value,
        }
        for m in (r.metric_results or [])
    ]
    base = EvaluationCaseResultRead.model_validate(r)
    return base.model_copy(
        update={
            "status": r.status.value,
            "reference_kind": r.reference_kind.value,
            "metric_results": metrics,
        }
    )


@router.post("/{project_id}/evaluation-runs/{run_id}/cancel", response_model=EvaluationRunRead)
def cancel_run(project_id: UUID, run_id: UUID, db: Session = Depends(get_db)):
    return _run_read(EvaluationService(db).cancel(project_id, run_id))


@router.post("/{project_id}/evaluation-runs/{run_id}/resume", response_model=EvaluationRunRead)
def resume_run(
    project_id: UUID,
    run_id: UUID,
    db: Session = Depends(get_db),
    sync: bool = Query(default=True),
):
    return _run_read(EvaluationService(db).resume(project_id, run_id, execute=sync))


@router.get("/{project_id}/evaluation-runs/{run_id}/export")
def export_run(
    project_id: UUID,
    run_id: UUID,
    format: str = Query("json", pattern="^(json|csv|markdown)$"),
    db: Session = Depends(get_db),
):
    body, media = EvaluationService(db).export(project_id, run_id, fmt=format)
    return Response(content=body, media_type=media)
