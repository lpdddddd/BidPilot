"""Background execution for Agent runs (does not block start API)."""

from __future__ import annotations

import logging
import threading
from uuid import UUID

from app.db.session import SessionLocal
from app.models.enums import AgentRunStatus
from app.services.agent_run.claims import release_execution_claim
from app.services.agent_run.safe_errors import safe_error_summary
from app.services.agent_run.service import AgentRunService

logger = logging.getLogger("bidpilot.agent")

SESSION_FACTORY = SessionLocal
_running: set[UUID] = set()
_lock = threading.Lock()


def run_agent_execute(run_id: UUID, claim_token: UUID | None = None) -> None:
    """Execute an already-persisted AgentRun in a dedicated DB session."""
    with _lock:
        if run_id in _running:
            logger.info("agent execute already running for %s — skip", run_id)
            return
        _running.add(run_id)
    session = SESSION_FACTORY()
    try:
        AgentRunService(session).execute_run(run_id, claim_token=claim_token)
    except Exception as exc:
        logger.exception("agent execute failed for %s", run_id)
        session.rollback()
        try:
            release_execution_claim(
                session,
                run_id,
                claim_token=claim_token,
                error_summary=safe_error_summary(exc),
                restore_status=AgentRunStatus.failed,
            )
        except Exception:  # noqa: BLE001
            logger.exception("failed to release execute claim for %s", run_id)
    else:
        try:
            release_execution_claim(session, run_id, claim_token=claim_token)
        except Exception:  # noqa: BLE001
            logger.exception("failed to release execute claim for %s", run_id)
    finally:
        session.close()
        with _lock:
            _running.discard(run_id)


def run_agent_resume(run_id: UUID, claim_token: UUID | None = None) -> None:
    """Continue a run prepared via ``prepare_resume``."""
    with _lock:
        if run_id in _running:
            logger.info("agent resume already running for %s — skip", run_id)
            return
        _running.add(run_id)
    session = SESSION_FACTORY()
    try:
        AgentRunService(session).continue_prepared_run(run_id, mode="resume")
    except Exception as exc:
        logger.exception("agent resume failed for %s", run_id)
        session.rollback()
        try:
            release_execution_claim(
                session,
                run_id,
                claim_token=claim_token,
                error_summary=safe_error_summary(exc),
                restore_status=AgentRunStatus.failed,
            )
        except Exception:  # noqa: BLE001
            logger.exception("failed to release resume claim for %s", run_id)
    else:
        try:
            release_execution_claim(session, run_id, claim_token=claim_token)
        except Exception:  # noqa: BLE001
            logger.exception("failed to release resume claim for %s", run_id)
    finally:
        session.close()
        with _lock:
            _running.discard(run_id)


def run_agent_retry(run_id: UUID, claim_token: UUID | None = None) -> None:
    """Continue a run prepared via ``prepare_retry``."""
    with _lock:
        if run_id in _running:
            logger.info("agent retry already running for %s — skip", run_id)
            return
        _running.add(run_id)
    session = SESSION_FACTORY()
    try:
        AgentRunService(session).continue_prepared_run(run_id, mode="retry")
    except Exception as exc:
        logger.exception("agent retry failed for %s", run_id)
        session.rollback()
        try:
            release_execution_claim(
                session,
                run_id,
                claim_token=claim_token,
                error_summary=safe_error_summary(exc),
                restore_status=AgentRunStatus.failed,
            )
        except Exception:  # noqa: BLE001
            logger.exception("failed to release retry claim for %s", run_id)
    else:
        try:
            release_execution_claim(session, run_id, claim_token=claim_token)
        except Exception:  # noqa: BLE001
            logger.exception("failed to release retry claim for %s", run_id)
    finally:
        session.close()
        with _lock:
            _running.discard(run_id)


def is_execute_running(run_id: UUID) -> bool:
    with _lock:
        return run_id in _running
