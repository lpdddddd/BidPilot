"""Shared node helpers: runtime context, instrumented tools, interrupt."""

from __future__ import annotations

import contextlib
import contextvars
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, TypeVar
from uuid import UUID

from sqlalchemy.orm import Session

from app.agent.state import AgentState, append_error, touch
from app.tools.agent_tools import RetrievalFn

T = TypeVar("T")


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
    save_checkpoint: Callable[..., Any] | None = None
    config: dict[str, Any] = field(default_factory=dict)
    last_state: dict[str, Any] | None = None
    current_step_id: UUID | None = None
    current_node_name: str | None = None
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


def _commit(runtime: AgentRuntime | None) -> None:
    if runtime and runtime.commit_fn:
        with contextlib.suppress(Exception):
            runtime.commit_fn()


def run_tool(
    state: AgentState,
    name: str,
    fn: Callable[[], T],
    *,
    attempt: int = 1,
    summary_on_ok: Callable[[T], str | None] | None = None,
) -> T:
    """Run ``fn`` with real tool_started → body → tool_completed/failed.

    ``tool_started`` is committed before ``fn`` runs so other DB sessions can see it.
    """
    started_at = now_iso()
    runtime = _RUNTIME.get()
    tool_row = None
    if runtime and runtime.persist_tool_start:
        with contextlib.suppress(Exception):
            tool_row = runtime.persist_tool_start(
                tool_name=name,
                agent_step_id=runtime.current_step_id,
                node_name=runtime.current_node_name,
                attempt=attempt,
            )
            _commit(runtime)

    if runtime and runtime.tool_barrier:
        with contextlib.suppress(Exception):
            runtime.tool_barrier(name)

    try:
        result = fn()
    except Exception as exc:
        finished_at = now_iso()
        events = list(state.get("tool_events") or [])
        events.append(
            {
                "name": name,
                "status": "error",
                "summary": f"{type(exc).__name__}: {exc}",
                "attempt": attempt,
                "started_at": started_at,
                "finished_at": finished_at,
            }
        )
        state["tool_events"] = events
        if runtime and runtime.persist_tool_finish and tool_row is not None:
            with contextlib.suppress(Exception):
                runtime.persist_tool_finish(
                    tool_call_id=getattr(tool_row, "id", tool_row),
                    status="error",
                    summary=str(exc),
                    error_type=type(exc).__name__,
                )
                _commit(runtime)
        raise

    summary = None
    if summary_on_ok is not None:
        with contextlib.suppress(Exception):
            summary = summary_on_ok(result)
    finished_at = now_iso()
    events = list(state.get("tool_events") or [])
    events.append(
        {
            "name": name,
            "status": "ok",
            "summary": summary,
            "attempt": attempt,
            "started_at": started_at,
            "finished_at": finished_at,
        }
    )
    state["tool_events"] = events
    if runtime and runtime.persist_tool_finish and tool_row is not None:
        with contextlib.suppress(Exception):
            runtime.persist_tool_finish(
                tool_call_id=getattr(tool_row, "id", tool_row),
                status="ok",
                summary=summary,
            )
            _commit(runtime)
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
    """In-memory tool marker + legacy one-shot persist (avoid for real calls).

    Prefer ``run_tool`` so start/finish bracket the real invocation.
    """
    events = list(state.get("tool_events") or [])
    events.append(
        {
            "name": name,
            "status": status,
            "summary": summary,
            "duration_ms": duration_ms,
            "attempt": attempt,
            "started_at": started_at or now_iso(),
            "finished_at": finished_at or now_iso(),
        }
    )
    state["tool_events"] = events
    runtime = _RUNTIME.get()
    if runtime and runtime.persist_tool and not runtime.persist_tool_start:
        with contextlib.suppress(Exception):
            runtime.persist_tool(
                run_id=UUID(state["run_id"]),
                tool_name=name,
                status=status,
                summary=summary,
                duration_ms=duration_ms,
                agent_step_id=runtime.current_step_id,
                node_name=runtime.current_node_name,
                attempt=attempt,
            )
            _commit(runtime)


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

    runtime = _RUNTIME.get()
    if runtime is not None:
        runtime.current_node_name = node
        if runtime.persist_node_start:
            with contextlib.suppress(Exception):
                step = runtime.persist_node_start(node_name=node)
                if step is not None and getattr(step, "id", None) is not None:
                    runtime.current_step_id = step.id
                _commit(runtime)
    return state, False


def finish_node(state: AgentState, node: str) -> AgentState:
    mark_node_completed(state, node)
    maybe_interrupt(state, node)
    return touch(state)


def mark_retryable_error(state: AgentState, message: str, code: str = "retryable") -> None:
    append_error(state, message)
    state["last_error_retryable"] = True
    state["error_code"] = code
    state["error_summary"] = message


def mark_fatal_error(state: AgentState, message: str, code: str = "fatal") -> None:
    append_error(state, message)
    state["last_error_retryable"] = False
    state["status"] = "failed"
    state["error_code"] = code
    state["error_summary"] = message


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
