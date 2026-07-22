from __future__ import annotations

from app.agent.nodes._helpers import begin_node, finish_node, now_iso
from app.agent.state import NODE_INITIALIZE, AgentState, GRAPH_VERSION, touch


def initialize_run(state: AgentState) -> AgentState:
    state, skipped = begin_node(state, NODE_INITIALIZE)
    if skipped:
        return state
    state["graph_version"] = GRAPH_VERSION
    if not state.get("started_at"):
        state["started_at"] = now_iso()
    state["status"] = "running"
    if not state.get("project_id"):
        state["status"] = "failed"
        state["error_code"] = "missing_project"
        state["error_summary"] = "project_id is required"
    return finish_node(state, NODE_INITIALIZE)
