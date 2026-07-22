"""Shared node helpers: runtime context, tool event recording, interrupt."""

from __future__ import annotations

import contextlib
import contextvars
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.agent.state import AgentState, append_error, touch
from app.tools.agent_tools import RetrievalFn


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
    persist_tool: Callable[..., Any] | None = None
    save_checkpoint: Callable[..., Any] | None = None
    config: dict[str, Any] = field(default_factory=dict)
    last_state: dict[str, Any] | None = None


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


def record_tool_event(
    state: AgentState,
    *,
    name: str,
    status: str,
    summary: str | None = None,
    duration_ms: int | None = None,
    started_at: str | None = None,
    finished_at: str | None = None,
) -> None:
    events = list(state.get("tool_events") or [])
    events.append(
        {
            "name": name,
            "status": status,
            "summary": summary,
            "duration_ms": duration_ms,
            "started_at": started_at or now_iso(),
            "finished_at": finished_at or now_iso(),
        }
    )
    state["tool_events"] = events
    runtime = _RUNTIME.get()
    if runtime and runtime.persist_tool:
        with contextlib.suppress(Exception):
            runtime.persist_tool(
                run_id=UUID(state["run_id"]),
                tool_name=name,
                status=status,
                summary=summary,
                duration_ms=duration_ms,
            )


def mark_node_start(state: AgentState, node: str) -> AgentState:
    state["current_node"] = node
    # Do not clobber terminal / interrupt statuses set by routing.
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
    """Return True when ``node`` already finished and metadata does not force re-run."""
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
    """Start a node; return ``(state, skipped)`` when already completed."""
    state = mark_node_start(state, node)
    if should_skip_completed(state, node):
        record_tool_event(
            state,
            name=node,
            status="ok",
            summary="skipped_completed",
        )
        return touch(state), True
    return state, False


def finish_node(state: AgentState, node: str) -> AgentState:
    """Mark node completed, then apply interrupt-after-node if configured."""
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
        # Do not raise here — graph wrappers / service stop after the node returns.


def run_id_uuid(state: AgentState) -> UUID:
    return UUID(state["run_id"])
