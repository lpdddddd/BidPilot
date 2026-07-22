"""Shared node helpers: runtime context, instrumented tools, interrupt."""

from __future__ import annotations

import contextvars
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, TypeVar
from uuid import UUID, uuid5

from sqlalchemy.orm import Session

from app.agent.state import AgentState, append_error, touch
from app.services.agent_run.safe_errors import EventPersistError, safe_error_summary, safe_text
from app.tools.agent_tools import RetrievalFn

T = TypeVar("T")
logger = logging.getLogger("bidpilot.agent")

# Stable namespace for logical tool call ids (shared across attempts).
_LOGICAL_CALL_NS = UUID("b2c3d4e5-f6a7-8901-bcde-f12345678901")


class AgentInterrupt(Exception):
    """Raised to pause the graph after a node for resume testing / control."""

    def __init__(self, node: str, message: str = "interrupt") -> None:
        super().__init__(message)
        self.node = node
        self.message = message


@dataclass
class AgentRuntime:
    db: Session
    llm: Any | None = None
    retrieval_fn: RetrievalFn | None = None
    persist_step: Callable[..., Any] | None = None
    persist_tool: Callable[..., Any] | None = None  # legacy one-shot
    persist_tool_start: Callable[..., Any] | None = None
    persist_tool_finish: Callable[..., Any] | None = None
    persist_node_start: Callable[..., Any] | None = None
    persist_node_finish: Callable[..., Any] | None = None
    commit_fn: Callable[[], None] | None = None
    rollback_fn: Callable[[], None] | None = None
    save_checkpoint: Callable[..., Any] | None = None
    config: dict[str, Any] = field(default_factory=dict)
    last_state: dict[str, Any] | None = None
    current_step_id: UUID | None = None
    current_node_name: str | None = None
    current_node_attempt: int = 1
    # "running" | "succeeded" | "failed"
    node_attempt_outcome: str | None = None
    # Per logical tool name → invocation index within this node attempt.
    tool_invocation_counts: dict[str, int] = field(default_factory=dict)
    # Test hook: set before a named tool body runs (after tool_started commit).
    tool_barrier: Callable[[str], None] | None = None


_RUNTIME: contextvars.ContextVar[AgentRuntime | None] = contextvars.ContextVar(
    "bidpilot_agent_runtime", default=None
)


def set_runtime(runtime: AgentRuntime | None):
    return _RUNTIME.set(runtime)


def reset_runtime(token) -> None:
    _RUNTIME.reset(token)


def get_runtime() -> AgentRuntime:
    runtime = _RUNTIME.get()
    if runtime is None:
        raise RuntimeError("AgentRuntime not set")
    return runtime


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def logical_tool_call_id(
    *,
    run_id: UUID | str,
    node_name: str | None,
    tool_name: str,
    invocation_index: int,
) -> str:
    """Stable id shared by retries of the same logical tool invocation."""
    seed = f"{run_id}:{node_name or ''}:{tool_name}:{invocation_index}"
    return uuid5(_LOGICAL_CALL_NS, seed).hex


def _commit_required(runtime: AgentRuntime) -> None:
    if runtime.commit_fn is None:
        return
    runtime.commit_fn()


def _rollback(runtime: AgentRuntime) -> None:
    if runtime.rollback_fn is not None:
        runtime.rollback_fn()
        return
    # Fall back to session rollback when available.
    db = getattr(runtime, "db", None)
    if db is not None and hasattr(db, "rollback"):
        db.rollback()


def run_tool(
    state: AgentState,
    name: str,
    fn: Callable[[], T],
    *,
    attempt: int | None = None,
    summary_on_ok: Callable[[T], str | None] | None = None,
) -> T:
    """Run ``fn`` with real tool_started → body → tool_completed/failed.

    ``tool_started`` MUST commit successfully before ``fn`` runs. On persist
    failure the tool body is not invoked.
    """
    started_at = now_iso()
    runtime = _RUNTIME.get()
    tool_row = None
    resolved_attempt = 1
    call_id: str | None = None

    if runtime is not None:
        resolved_attempt = int(
            attempt if attempt is not None else (runtime.current_node_attempt or 1)
        )
        inv = int(runtime.tool_invocation_counts.get(name, 0)) + 1
        runtime.tool_invocation_counts[name] = inv
        call_id = logical_tool_call_id(
            run_id=state.get("run_id") or "",
            node_name=runtime.current_node_name,
            tool_name=name,
            invocation_index=inv,
        )
        if runtime.persist_tool_start:
            try:
                tool_row = runtime.persist_tool_start(
                    tool_name=name,
                    agent_step_id=runtime.current_step_id,
                    node_name=runtime.current_node_name,
                    attempt=resolved_attempt,
                    call_id=call_id,
                    idempotency_key=(
                        f"tool_started:{state.get('run_id')}:{call_id}:a{resolved_attempt}"
                    ),
                )
                _commit_required(runtime)
            except EventPersistError:
                raise
            except Exception as exc:
                _rollback(runtime)
                summary = safe_error_summary(
                    exc, error_type=type(exc).__name__, error_code="event_persist_failed"
                )
                mark_fatal_error(state, summary, "event_persist_failed")
                logger.exception("tool_started persist failed for %s", name)
                raise EventPersistError(summary) from exc

    if runtime and runtime.tool_barrier:
        runtime.tool_barrier(name)

    try:
        result = fn()
    except Exception as exc:
        finished_at = now_iso()
        summary = safe_error_summary(exc, error_type=type(exc).__name__)
        events = list(state.get("tool_events") or [])
        events.append(
            {
                "name": name,
                "status": "error",
                "summary": summary,
                "attempt": resolved_attempt,
                "call_id": call_id,
                "started_at": started_at,
                "finished_at": finished_at,
            }
        )
        state["tool_events"] = events
        if runtime and runtime.persist_tool_finish and tool_row is not None:
            try:
                runtime.persist_tool_finish(
                    tool_call_id=getattr(tool_row, "id", tool_row),
                    status="error",
                    summary=summary,
                    error_type=type(exc).__name__,
                    idempotency_key=(
                        f"tool_failed:{state.get('run_id')}:{call_id}:a{resolved_attempt}"
                    ),
                )
                _commit_required(runtime)
            except Exception as persist_exc:
                logger.exception("tool_failed persist failed for %s", name)
                mark_fatal_error(
                    state,
                    safe_error_summary(
                        persist_exc,
                        error_type=type(persist_exc).__name__,
                        error_code="event_persist_failed",
                    ),
                    "event_persist_failed",
                )
                raise EventPersistError("tool_failed event persist failed") from persist_exc
        raise

    summary = None
    if summary_on_ok is not None:
        try:
            summary = safe_text(summary_on_ok(result))
        except Exception as exc:  # noqa: BLE001
            logger.warning("summary_on_ok failed for %s: %s", name, type(exc).__name__)
            summary = None
    finished_at = now_iso()
    events = list(state.get("tool_events") or [])
    events.append(
        {
            "name": name,
            "status": "ok",
            "summary": summary,
            "attempt": resolved_attempt,
            "call_id": call_id,
            "started_at": started_at,
            "finished_at": finished_at,
        }
    )
    state["tool_events"] = events
    if runtime and runtime.persist_tool_finish and tool_row is not None:
        try:
            runtime.persist_tool_finish(
                tool_call_id=getattr(tool_row, "id", tool_row),
                status="ok",
                summary=summary,
                idempotency_key=(
                    f"tool_completed:{state.get('run_id')}:{call_id}:a{resolved_attempt}"
                ),
            )
            _commit_required(runtime)
        except Exception as persist_exc:
            logger.exception("tool_completed persist failed for %s", name)
            mark_fatal_error(
                state,
                safe_error_summary(
                    persist_exc,
                    error_type=type(persist_exc).__name__,
                    error_code="event_persist_failed",
                ),
                "event_persist_failed",
            )
            # Tool already ran — do not re-execute; surface persist failure.
            raise EventPersistError("tool_completed event persist failed") from persist_exc
    return result


def record_tool_event(
    state: AgentState,
    *,
    name: str,
    status: str,
    summary: str | None = None,
    duration_ms: int | None = None,
    started_at: str | None = None,
    finished_at: str | None = None,
    attempt: int = 1,
) -> None:
    """In-memory tool marker + legacy one-shot persist (avoid for real calls)."""
    safe = safe_text(summary)
    events = list(state.get("tool_events") or [])
    events.append(
        {
            "name": name,
            "status": status,
            "summary": safe,
            "duration_ms": duration_ms,
            "attempt": attempt,
            "started_at": started_at or now_iso(),
            "finished_at": finished_at or now_iso(),
        }
    )
    state["tool_events"] = events
    runtime = _RUNTIME.get()
    if runtime and runtime.persist_tool and not runtime.persist_tool_start:
        try:
            runtime.persist_tool(
                run_id=UUID(state["run_id"]),
                tool_name=name,
                status=status,
                summary=safe,
                duration_ms=duration_ms,
                agent_step_id=runtime.current_step_id,
                node_name=runtime.current_node_name,
                attempt=attempt,
            )
            _commit_required(runtime)
        except Exception as exc:
            logger.exception("legacy tool event persist failed")
            raise EventPersistError(safe_error_summary(exc)) from exc


def mark_node_start(state: AgentState, node: str) -> AgentState:
    state["current_node"] = node
    if state.get("status") not in {
        "blocked",
        "failed",
        "cancelled",
        "completed",
        "completed_with_warnings",
        "waiting_for_user",
    }:
        state["status"] = "running"
    state["last_error_retryable"] = False
    return touch(state)


def should_skip_completed(state: AgentState, node: str) -> bool:
    meta = state.get("metadata") or {}
    force = meta.get("force_rerun_nodes", meta.get("force_rerun_node"))
    if force is True:
        return False
    if isinstance(force, str) and force == node:
        return False
    if isinstance(force, (list, tuple, set)) and node in force:
        return False
    return node in (state.get("completed_nodes") or [])


def mark_node_completed(state: AgentState, node: str) -> None:
    done = list(state.get("completed_nodes") or [])
    if node not in done:
        done.append(node)
    state["completed_nodes"] = done


def begin_node(state: AgentState, node: str) -> tuple[AgentState, bool]:
    """Start a node; persist + commit ``node_started`` before node body runs."""
    state = mark_node_start(state, node)
    if should_skip_completed(state, node):
        events = list(state.get("tool_events") or [])
        events.append(
            {
                "name": node,
                "status": "ok",
                "summary": "skipped_completed",
                "started_at": now_iso(),
                "finished_at": now_iso(),
            }
        )
        state["tool_events"] = events
        return touch(state), True

    counts = dict(state.get("retry_counts") or {})
    attempt = int(counts.get(node, 0)) + 1

    runtime = _RUNTIME.get()
    if runtime is not None:
        runtime.current_node_name = node
        runtime.current_node_attempt = attempt
        runtime.node_attempt_outcome = "running"
        runtime.tool_invocation_counts = {}
        if runtime.persist_node_start:
            try:
                step = runtime.persist_node_start(
                    node_name=node,
                    attempt=attempt,
                    idempotency_key=f"node_started:{state.get('run_id')}:{node}:a{attempt}",
                )
                if step is not None and getattr(step, "id", None) is not None:
                    runtime.current_step_id = step.id
                _commit_required(runtime)
            except EventPersistError:
                raise
            except Exception as exc:
                _rollback(runtime)
                summary = safe_error_summary(
                    exc, error_type=type(exc).__name__, error_code="event_persist_failed"
                )
                mark_fatal_error(state, summary, "event_persist_failed")
                logger.exception("node_started persist failed for %s", node)
                raise EventPersistError(summary) from exc
    return state, False


def finish_node(state: AgentState, node: str) -> AgentState:
    runtime = _RUNTIME.get()
    if runtime is not None:
        runtime.node_attempt_outcome = "succeeded"
    mark_node_completed(state, node)
    maybe_interrupt(state, node)
    return touch(state)


def mark_retryable_error(state: AgentState, message: str, code: str = "retryable") -> None:
    summary = safe_error_summary(message, error_code=code)
    append_error(state, summary)
    state["last_error_retryable"] = True
    state["error_code"] = code
    state["error_summary"] = summary
    runtime = _RUNTIME.get()
    if runtime is not None:
        runtime.node_attempt_outcome = "failed"


def mark_fatal_error(state: AgentState, message: str, code: str = "fatal") -> None:
    summary = safe_error_summary(message, error_code=code)
    append_error(state, summary)
    state["last_error_retryable"] = False
    state["status"] = "failed"
    state["error_code"] = code
    state["error_summary"] = summary
    runtime = _RUNTIME.get()
    if runtime is not None:
        runtime.node_attempt_outcome = "failed"


def maybe_interrupt(state: AgentState, node: str) -> None:
    meta = state.get("metadata") or {}
    target = meta.get("interrupt_after_node")
    if target and target == node and not meta.get("_interrupted"):
        meta = dict(meta)
        meta["_interrupted"] = True
        state["metadata"] = meta
        state["interrupt_requested"] = True
        state["status"] = "waiting_for_user"


def run_id_uuid(state: AgentState) -> UUID:
    return UUID(state["run_id"])
