"""DB-backed node attempt allocation and execution claim helpers."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.agent import AgentRun, AgentStep
from app.models.enums import AgentRunStatus
from app.services.agent_run.safe_errors import safe_error_summary


class ClaimOutcome(StrEnum):
    claimed = "claimed"
    already_running = "already_running"
    invalid_state = "invalid_state"
    not_found_or_forbidden = "not_found_or_forbidden"


@dataclass(frozen=True)
class ClaimResult:
    outcome: ClaimOutcome
    run: AgentRun | None = None
    claim_token: UUID | None = None
    detail: str | None = None


RESUME_ALLOWED = frozenset(
    {
        AgentRunStatus.waiting_for_user,
        AgentRunStatus.failed,
        AgentRunStatus.running,  # reclaim only if no active claim
    }
)
RETRY_ALLOWED = frozenset({AgentRunStatus.failed})
EXECUTE_ALLOWED = frozenset(
    {
        AgentRunStatus.pending,
        AgentRunStatus.running,
    }
)
TERMINAL_NO_RETRY = frozenset(
    {
        AgentRunStatus.completed,
        AgentRunStatus.completed_with_warnings,
        AgentRunStatus.blocked,
        AgentRunStatus.cancelled,
    }
)


def allocate_node_attempt(db: Session, agent_run_id: UUID, node_name: str) -> int:
    """Allocate next attempt for (run, node) under AgentRun row lock.

    Source of truth is AgentStep rows in the database — not checkpoint
    ``retry_counts``. Returns a strictly increasing integer starting at 1.
    """
    db.execute(select(AgentRun.id).where(AgentRun.id == agent_run_id).with_for_update())
    current = db.scalar(
        select(func.coalesce(func.max(AgentStep.attempt), 0)).where(
            AgentStep.agent_run_id == agent_run_id,
            AgentStep.node_name == node_name,
        )
    )
    return int(current or 0) + 1


def claim_run_execution(
    db: Session,
    run_id: UUID,
    *,
    action: str,
    project_id: UUID | None = None,
    prepare_state: dict[str, Any] | None = None,
) -> ClaimResult:
    """Atomically claim background execution for an AgentRun.

    Locks the run row, validates action vs status, sets ``execution_claim_token``
    and ``status=running``, then commits. Callers may register BackgroundTasks
    only when ``outcome == claimed``.
    """
    action = (action or "").strip().lower()
    if action not in {"execute", "resume", "retry"}:
        return ClaimResult(outcome=ClaimOutcome.invalid_state, detail="unknown action")

    run = db.execute(
        select(AgentRun).where(AgentRun.id == run_id).with_for_update()
    ).scalar_one_or_none()
    if run is None:
        db.rollback()
        return ClaimResult(outcome=ClaimOutcome.not_found_or_forbidden)
    if project_id is not None and run.project_id != project_id:
        db.rollback()
        return ClaimResult(outcome=ClaimOutcome.not_found_or_forbidden)

    # Active claim → already running (even if status says running).
    if run.execution_claim_token is not None and run.status == AgentRunStatus.running:
        token = run.execution_claim_token
        # Release row lock without mutating state.
        db.rollback()
        run = db.get(AgentRun, run_id)
        return ClaimResult(
            outcome=ClaimOutcome.already_running,
            run=run,
            claim_token=token,
            detail="execution already claimed",
        )

    invalid: ClaimResult | None = None
    if action == "execute":
        if run.status not in EXECUTE_ALLOWED and run.status not in {
            AgentRunStatus.pending,
            AgentRunStatus.running,
        }:
            if run.status in TERMINAL_NO_RETRY:
                invalid = ClaimResult(
                    outcome=ClaimOutcome.invalid_state, run=run, detail="terminal"
                )
            elif run.status != AgentRunStatus.pending:
                invalid = ClaimResult(outcome=ClaimOutcome.invalid_state, run=run)
    elif action == "resume":
        if run.status in TERMINAL_NO_RETRY:
            invalid = ClaimResult(outcome=ClaimOutcome.invalid_state, run=run, detail="terminal")
        elif run.status not in RESUME_ALLOWED:
            invalid = ClaimResult(outcome=ClaimOutcome.invalid_state, run=run)
    elif action == "retry":
        if run.status in TERMINAL_NO_RETRY:
            invalid = ClaimResult(outcome=ClaimOutcome.invalid_state, run=run, detail="terminal")
        elif run.status not in RETRY_ALLOWED:
            invalid = ClaimResult(outcome=ClaimOutcome.invalid_state, run=run)

    if invalid is not None:
        # Keep detached snapshot for callers; release FOR UPDATE.
        snap = invalid.run
        db.rollback()
        return ClaimResult(outcome=invalid.outcome, run=snap, detail=invalid.detail)

    token = uuid4()
    run.execution_claim_token = token
    run.execution_action = action
    run.status = AgentRunStatus.running
    if prepare_state is not None:
        run.state_json = prepare_state
    db.flush()
    db.commit()
    db.refresh(run)
    return ClaimResult(outcome=ClaimOutcome.claimed, run=run, claim_token=token)


def release_execution_claim(
    db: Session,
    run_id: UUID,
    *,
    claim_token: UUID | None = None,
    error_summary: str | None = None,
    restore_status: AgentRunStatus | None = None,
) -> None:
    """Clear claim token after background work finishes or registration fails."""
    run = db.execute(
        select(AgentRun).where(AgentRun.id == run_id).with_for_update()
    ).scalar_one_or_none()
    if run is None:
        return
    if claim_token is not None and run.execution_claim_token != claim_token:
        return
    run.execution_claim_token = None
    run.execution_action = None
    if error_summary:
        run.error_summary = safe_error_summary(error_summary)
        run.error_message = run.error_summary
    if restore_status is not None:
        run.status = restore_status
    db.commit()


def claim_or_http(
    result: ClaimResult,
    *,
    already_running_ok: bool = True,
) -> AgentRun:
    """Map ClaimResult to AgentRun or raise HTTPException."""
    if result.outcome == ClaimOutcome.claimed and result.run is not None:
        return result.run
    if result.outcome == ClaimOutcome.already_running and already_running_ok and result.run:
        return result.run
    if result.outcome == ClaimOutcome.not_found_or_forbidden:
        raise HTTPException(status_code=404, detail="agent run not found")
    raise HTTPException(status_code=409, detail=result.detail or "invalid run state for action")
