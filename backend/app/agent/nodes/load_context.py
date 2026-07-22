from __future__ import annotations

from uuid import UUID

from app.agent.nodes._helpers import (
    begin_node,
    finish_node,
    get_runtime,
    mark_fatal_error,
    run_tool,
)
from app.agent.state import NODE_LOAD_CONTEXT, AgentState, append_warning, touch
from app.tools.agent_tools import GetProjectContextInput, get_project_context


def load_project_context(state: AgentState) -> AgentState:
    state, skipped = begin_node(state, NODE_LOAD_CONTEXT)
    if skipped:
        return state
    runtime = get_runtime()
    project_id = state.get("project_id")
    if not project_id:
        mark_fatal_error(state, "missing project_id", "missing_project")
        return touch(state)

    selected = [UUID(x) for x in (state.get("selected_document_ids") or [])]

    def _call():
        return get_project_context(
            runtime.db,
            GetProjectContextInput(
                project_id=UUID(project_id),
                selected_document_ids=selected,
            ),
        )

    try:
        result = run_tool(
            state,
            "get_project_context",
            _call,
            summary_on_ok=lambda r: r.summary or r.detail,
        )
    except Exception as exc:  # noqa: BLE001
        mark_fatal_error(state, f"{type(exc).__name__}: {exc}", "context_error")
        return touch(state)

    if not result.ok:
        mark_fatal_error(state, result.detail or "context load failed", "context_error")
        return touch(state)

    data = result.data
    state["organization_id"] = data.get("organization_id") or state.get("organization_id")
    state["has_documents"] = int(data.get("document_count") or 0) > 0
    if not state.get("selected_document_ids"):
        state["selected_document_ids"] = list(data.get("document_ids") or [])
    if int(data.get("requirement_count") or 0) == 0:
        append_warning(state, "project has no existing requirements yet")
    if not state["has_documents"]:
        state["status"] = "blocked"
        state["error_code"] = "no_documents"
        state["error_summary"] = "project has no documents"
        state["route_decision"] = "blocked"
    return finish_node(state, NODE_LOAD_CONTEXT)
