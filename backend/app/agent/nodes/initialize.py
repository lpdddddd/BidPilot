from __future__ import annotations

from app.agent.nodes._helpers import mark_node_start, maybe_interrupt, now_iso
from app.agent.state import NODE_INITIALIZE, AgentState, GRAPH_VERSION, touch


def initialize_run(state: AgentState) -> AgentState:
    state = mark_node_start(state, NODE_INITIALIZE)
    state["graph_version"] = GRAPH_VERSION
    if not state.get("started_at"):
        state["started_at"] = now_iso()
    state["status"] = "running"
    if not state.get("project_id"):
        state["status"] = "failed"
        state["error_code"] = "missing_project"
        state["error_summary"] = "project_id is required"
    maybe_interrupt(state, NODE_INITIALIZE)
    return touch(state)
