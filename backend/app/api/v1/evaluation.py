"""Evaluation center API routes."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Header, Query, Response
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.enums import EvaluationRunStatus
from app.schemas.evaluation import (
    EvaluationCapabilitiesResponse,
    EvaluationCaseResultRead,
    EvaluationCompareResponse,
    EvaluationRunCreate,
    EvaluationRunRead,
    EvaluationSuiteRead,
    PaginatedCaseResults,
    PaginatedRuns,
    PaginatedSuites,
)
from app.services.evaluation import tasks as evaluation_tasks
from app.services.evaluation.claims import EvalClaimOutcome, release_evaluation_claim
from app.services.evaluation.service import EvaluationService

router = APIRouter()


def _schedule_or_release(
    background_tasks: BackgroundTasks,
    db: Session,
    *,
    run_id: UUID,
    claim_token: UUID | None,
    restore_status: EvaluationRunStatus,
) -> None:
    if claim_token is None:
        return
    try:
        background_tasks.add_task(evaluation_tasks.run_evaluation, run_id, claim_token)
    except Exception:
        release_evaluation_claim(
            db,
            run_id,
            claim_token=claim_token,
            restore_status=restore_status,
            safe_error_summary="failed to schedule evaluation background task",
        )
        raise


def _page_from_limit_offset(
    limit: int | None, offset: int | None, page: int, page_size: int
) -> tuple[int, int]:
    if limit is not None:
        page_size = limit
    if offset is not None and page_size > 0:
        page = (offset // page_size) + 1
    return max(1, page), max(1, min(page_size, 200))


@router.get("/{project_id}/evaluation-capabilities", response_model=EvaluationCapabilitiesResponse)
def evaluation_capabilities(project_id: UUID, db: Session = Depends(get_db)):
    return EvaluationService(db).capabilities(project_id)


@router.get("/{project_id}/evaluation-suites", response_model=PaginatedSuites)
def list_suites(
    project_id: UUID,
    db: Session = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    rows, total = EvaluationService(db).list_suites(project_id, page=page, page_size=page_size)
    return PaginatedSuites(
        items=[EvaluationSuiteRead.model_validate(r) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{project_id}/evaluation-suites/{suite_id}", response_model=EvaluationSuiteRead)
def get_suite(project_id: UUID, suite_id: UUID, db: Session = Depends(get_db)):
    return EvaluationService(db).get_suite(project_id, suite_id)


@router.post("/{project_id}/evaluation-runs", response_model=EvaluationRunRead, status_code=201)
def create_run(
    project_id: UUID,
    payload: EvaluationRunCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    sync: bool = Query(
        default=False,
        description="Test-only: execute in-request. Production default is background.",
    ),
):
    svc = EvaluationService(db)
    body = payload.model_dump()
    key = idempotency_key or body.get("idempotency_key")
    run, claim = svc.create_run(project_id, body, idempotency_key=key, execute=sync)
    if not sync and claim is not None and claim.outcome == EvalClaimOutcome.claimed:
        _schedule_or_release(
            background_tasks,
            db,
            run_id=run.id,
            claim_token=claim.claim_token,
            restore_status=EvaluationRunStatus.queued,
        )
        db.refresh(run)
    return EvaluationRunRead.model_validate(svc.run_to_read(run))


@router.get("/{project_id}/evaluation-runs", response_model=PaginatedRuns)
def list_runs(
    project_id: UUID,
    db: Session = Depends(get_db),
    status: str | None = None,
    suite_id: UUID | None = None,
    target_type: str | None = None,
    task_family: str | None = None,
    started_after: datetime | None = None,
    started_before: datetime | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    limit: int | None = Query(None, ge=1, le=200),
    offset: int | None = Query(None, ge=0),
):
    page, page_size = _page_from_limit_offset(limit, offset, page, page_size)
    svc = EvaluationService(db)
    rows, total = svc.list_runs(
        project_id,
        status=status,
        suite_id=suite_id,
        target_type=target_type,
        task_family=task_family,
        started_after=started_after,
        started_before=started_before,
        page=page,
        page_size=page_size,
    )
    return PaginatedRuns(
        items=[EvaluationRunRead.model_validate(svc.run_to_read(r)) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/{project_id}/evaluation-runs/compare",
    response_model=EvaluationCompareResponse,
)
def compare_runs(
    project_id: UUID,
    left: UUID = Query(...),
    right: UUID = Query(...),
    db: Session = Depends(get_db),
):
    return EvaluationService(db).compare(project_id, left, right)


@router.get("/{project_id}/evaluation-runs/{run_id}", response_model=EvaluationRunRead)
def get_run(project_id: UUID, run_id: UUID, db: Session = Depends(get_db)):
    svc = EvaluationService(db)
    return EvaluationRunRead.model_validate(svc.run_to_read(svc.get_run(project_id, run_id)))


@router.get(
    "/{project_id}/evaluation-runs/{run_id}/results",
    response_model=PaginatedCaseResults,
)
def list_results(
    project_id: UUID,
    run_id: UUID,
    db: Session = Depends(get_db),
    status: str | None = None,
    task_family: str | None = None,
    passed: bool | None = None,
    failed: bool | None = None,
    error: bool | None = None,
    hard_gate: bool | None = None,
    metric: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=500),
    limit: int | None = Query(None, ge=1, le=500),
    offset: int | None = Query(None, ge=0),
):
    page, page_size = _page_from_limit_offset(limit, offset, page, page_size)
    svc = EvaluationService(db)
    rows, total = svc.get_results(
        project_id,
        run_id,
        status=status,
        task_family=task_family,
        passed=passed,
        failed=failed,
        error=error,
        hard_gate=hard_gate,
        metric=metric,
        page=page,
        page_size=page_size,
    )
    items = [
        EvaluationCaseResultRead.model_validate(svc.serialize_result(project_id, r)) for r in rows
    ]
    return PaginatedCaseResults(items=items, total=total, page=page, page_size=page_size)


@router.get(
    "/{project_id}/evaluation-runs/{run_id}/results/{result_id}",
    response_model=EvaluationCaseResultRead,
)
def get_result(project_id: UUID, run_id: UUID, result_id: UUID, db: Session = Depends(get_db)):
    svc = EvaluationService(db)
    r = svc.get_result(project_id, run_id, result_id)
    return EvaluationCaseResultRead.model_validate(
        svc.serialize_result(project_id, r, include_metrics=True)
    )


@router.post("/{project_id}/evaluation-runs/{run_id}/cancel", response_model=EvaluationRunRead)
def cancel_run(project_id: UUID, run_id: UUID, db: Session = Depends(get_db)):
    svc = EvaluationService(db)
    return EvaluationRunRead.model_validate(svc.run_to_read(svc.cancel(project_id, run_id)))


@router.post("/{project_id}/evaluation-runs/{run_id}/resume", response_model=EvaluationRunRead)
def resume_run(
    project_id: UUID,
    run_id: UUID,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    sync: bool = Query(default=False, description="Test-only sync resume"),
):
    svc = EvaluationService(db)
    run, claim = svc.resume(project_id, run_id, execute=sync)
    if not sync and claim is not None and claim.outcome == EvalClaimOutcome.claimed:
        _schedule_or_release(
            background_tasks,
            db,
            run_id=run.id,
            claim_token=claim.claim_token,
            restore_status=EvaluationRunStatus.partial,
        )
        db.refresh(run)
    return EvaluationRunRead.model_validate(svc.run_to_read(run))


@router.get("/{project_id}/evaluation-runs/{run_id}/export")
def export_run(
    project_id: UUID,
    run_id: UUID,
    format: str = Query("json", pattern="^(json|csv|markdown)$"),
    db: Session = Depends(get_db),
):
    body, media = EvaluationService(db).export(project_id, run_id, fmt=format)
    return Response(content=body, media_type=media)
