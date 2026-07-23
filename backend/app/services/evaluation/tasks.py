"""Background task helpers for evaluation runs."""

from __future__ import annotations

from uuid import UUID

from app.db.session import SessionLocal
from app.models.enums import EvaluationRunStatus
from app.services.evaluation.claims import release_evaluation_claim
from app.services.evaluation.runner import execute_evaluation_run

SESSION_FACTORY = SessionLocal


def run_evaluation(run_id: UUID, claim_token: UUID | None = None) -> None:
    """Execute evaluation in an independent Session (never reuse request Session)."""
    session = SESSION_FACTORY()
    try:
        execute_evaluation_run(session, run_id)
    except Exception:
        release_evaluation_claim(
            session,
            run_id,
            claim_token=claim_token,
            restore_status=EvaluationRunStatus.failed,
            safe_error_summary="evaluation background task failed",
        )
        raise
    finally:
        release_evaluation_claim(session, run_id, claim_token=claim_token)
        session.close()
