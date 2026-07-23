"""Atomic claim helpers for evaluation runs."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import EvaluationRunStatus
from app.models.evaluation import EvaluationRun


class EvalClaimOutcome(StrEnum):
    claimed = "claimed"
    already_running = "already_running"
    invalid_state = "invalid_state"
    not_found_or_forbidden = "not_found_or_forbidden"


@dataclass
class EvalClaimResult:
    outcome: EvalClaimOutcome
    run: EvaluationRun | None = None
    claim_token: UUID | None = None
    detail: str | None = None


RESUME_ALLOWED = frozenset(
    {
        EvaluationRunStatus.queued,
        EvaluationRunStatus.partial,
        EvaluationRunStatus.failed,
        EvaluationRunStatus.running,
    }
)
START_ALLOWED = frozenset({EvaluationRunStatus.queued})


def claim_evaluation_run(
    db: Session,
    run_id: UUID,
    *,
    action: str,
    project_id: UUID | None = None,
) -> EvalClaimResult:
    run = db.execute(
        select(EvaluationRun).where(EvaluationRun.id == run_id).with_for_update()
    ).scalar_one_or_none()
    if run is None:
        db.rollback()
        return EvalClaimResult(outcome=EvalClaimOutcome.not_found_or_forbidden)
    if project_id is not None and run.project_id != project_id:
        db.rollback()
        return EvalClaimResult(outcome=EvalClaimOutcome.not_found_or_forbidden)
    if run.execution_claim_token is not None and run.status == EvaluationRunStatus.running:
        token = run.execution_claim_token
        db.rollback()
        run = db.get(EvaluationRun, run_id)
        return EvalClaimResult(outcome=EvalClaimOutcome.already_running, run=run, claim_token=token)
    if (
        action == "start"
        and run.status not in START_ALLOWED | {EvaluationRunStatus.queued}
        and run.status in {EvaluationRunStatus.completed, EvaluationRunStatus.cancelled}
    ):
        db.rollback()
        return EvalClaimResult(outcome=EvalClaimOutcome.invalid_state, run=run, detail="terminal")
    if action == "resume" and run.status not in RESUME_ALLOWED:
        db.rollback()
        return EvalClaimResult(
            outcome=EvalClaimOutcome.invalid_state, run=run, detail="invalid resume state"
        )
    if (
        run.status in {EvaluationRunStatus.completed, EvaluationRunStatus.cancelled}
        and action != "start"
    ):
        db.rollback()
        return EvalClaimResult(outcome=EvalClaimOutcome.invalid_state, run=run, detail="terminal")
    token = uuid4()
    run.execution_claim_token = token
    run.status = EvaluationRunStatus.running
    db.commit()
    db.refresh(run)
    return EvalClaimResult(outcome=EvalClaimOutcome.claimed, run=run, claim_token=token)


def release_evaluation_claim(
    db: Session,
    run_id: UUID,
    *,
    claim_token: UUID | None = None,
    restore_status: EvaluationRunStatus | None = None,
    safe_error_summary: str | None = None,
) -> None:
    run = db.execute(
        select(EvaluationRun).where(EvaluationRun.id == run_id).with_for_update()
    ).scalar_one_or_none()
    if run is None:
        return
    if claim_token is not None and run.execution_claim_token != claim_token:
        db.rollback()
        return
    run.execution_claim_token = None
    if restore_status is not None:
        run.status = restore_status
    if safe_error_summary:
        run.safe_error_summary = safe_error_summary[:500]
    db.commit()
