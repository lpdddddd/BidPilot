from __future__ import annotations

from app.agent.nodes._helpers import begin_node, finish_node, now_iso
from app.agent.state import NODE_FINALIZE, TERMINAL_STATUSES, AgentState


def finalize_run(state: AgentState) -> AgentState:
    state, skipped = begin_node(state, NODE_FINALIZE)
    if skipped:
        return state
    status = state.get("status") or "running"

    if status in {"blocked"} or status == "failed":
        pass
    elif status == "waiting_for_user":
        return finish_node(state, NODE_FINALIZE)
    elif state.get("critical_qualification") and status not in TERMINAL_STATUSES:
        from app.agent.state import block_on_critical

        if block_on_critical(state):
            state["status"] = "blocked"
        else:
            state["status"] = "completed_with_warnings"
    elif state.get("warnings") and status not in TERMINAL_STATUSES - {"completed"}:
        if status == "running" or status == "pending" or status == "completed":
            state["status"] = "completed_with_warnings"
    elif status in {"running", "pending"}:
        state["status"] = "completed"

    if state.get("status") in TERMINAL_STATUSES and state.get("status") != "waiting_for_user":
        state["completed_at"] = now_iso()
    state["current_node"] = NODE_FINALIZE
    return finish_node(state, NODE_FINALIZE)
