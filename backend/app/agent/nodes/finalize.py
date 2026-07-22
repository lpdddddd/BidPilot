from __future__ import annotations

from app.agent.nodes._helpers import mark_node_start, now_iso
from app.agent.state import NODE_FINALIZE, TERMINAL_STATUSES, AgentState, touch


def finalize_run(state: AgentState) -> AgentState:
    state = mark_node_start(state, NODE_FINALIZE)
    status = state.get("status") or "running"

    if status in {"blocked"}:
        pass
    elif status == "failed":
        pass
    elif status == "waiting_for_user":
        # Interrupted — leave as waiting.
        return touch(state)
    elif state.get("critical_qualification") and status not in TERMINAL_STATUSES:
        # Defensive: should already be blocked by routing when flag true.
        from app.agent.state import block_on_critical

        if block_on_critical(state):
            state["status"] = "blocked"
        else:
            state["status"] = "completed_with_warnings"
    elif state.get("warnings") and status not in TERMINAL_STATUSES - {"completed"}:
        if status == "running" or status == "pending":
            state["status"] = "completed_with_warnings"
        elif status == "completed":
            state["status"] = "completed_with_warnings"
    elif status in {"running", "pending"}:
        state["status"] = "completed"

    if state.get("status") in TERMINAL_STATUSES and state.get("status") != "waiting_for_user":
        state["completed_at"] = now_iso()
    state["current_node"] = NODE_FINALIZE
    return touch(state)
