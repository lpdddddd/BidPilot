"""In-process background task for requirement extraction runs.

Opens its own DB session from SESSION_FACTORY (monkeypatchable in tests).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from uuid import UUID

from app.db.session import SessionLocal
from app.models.enums import ExtractionRunStatus
from app.models.extraction_run import RequirementExtractionRun
from app.services.requirement_extraction_service import RequirementExtractionService

logger = logging.getLogger("bidpilot.requirement_extraction")

SESSION_FACTORY = SessionLocal


def run_requirement_extraction(run_id: UUID) -> None:
    session = SESSION_FACTORY()
    try:
        RequirementExtractionService(session).execute_run(run_id)
    except Exception:
        logger.exception("Unexpected error in extraction run %s", run_id)
        session.rollback()
        _mark_failed(session, run_id, "抽取任务内部错误")
    finally:
        session.close()


def _mark_failed(session, run_id: UUID, reason: str) -> None:  # noqa: ANN001
    try:
        run = session.get(RequirementExtractionRun, run_id)
        if run is not None and run.status != ExtractionRunStatus.succeeded:
            run.status = ExtractionRunStatus.failed
            run.finished_at = datetime.now(UTC)
            run.error_summary = reason
            session.commit()
    except Exception:  # noqa: BLE001
        logger.exception("Could not mark extraction run %s as failed", run_id)
