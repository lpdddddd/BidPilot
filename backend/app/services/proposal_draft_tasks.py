"""In-process background task for proposal draft generation.

Opens its own DB session from SESSION_FACTORY (monkeypatchable in tests).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from uuid import UUID

from app.db.session import SessionLocal
from app.models.enums import ExtractionRunStatus
from app.models.proposal_draft import ProposalDraftGenerationRun
from app.services.proposal_draft_service import ProposalDraftService

logger = logging.getLogger("bidpilot.proposal_draft")

SESSION_FACTORY = SessionLocal


def run_proposal_draft_generation(run_id: UUID) -> None:
    session = SESSION_FACTORY()
    try:
        ProposalDraftService(session).execute_run(run_id)
    except Exception:
        logger.exception("Unexpected error in proposal draft run %s", run_id)
        session.rollback()
        _mark_failed(session, run_id, "草稿生成任务内部错误")
    finally:
        session.close()


def _mark_failed(session, run_id: UUID, reason: str) -> None:  # noqa: ANN001
    try:
        run = session.get(ProposalDraftGenerationRun, run_id)
        if run is not None and run.status not in (
            ExtractionRunStatus.succeeded,
            ExtractionRunStatus.cancelled,
        ):
            run.status = ExtractionRunStatus.failed
            run.finished_at = datetime.now(UTC)
            run.error_summary = reason
            session.commit()
    except Exception:  # noqa: BLE001
        logger.exception("Could not mark draft run %s as failed", run_id)
