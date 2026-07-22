from __future__ import annotations

from uuid import UUID

from sqlalchemy import select

from app.agent.nodes._helpers import (
    begin_node,
    finish_node,
    get_runtime,
    mark_fatal_error,
    mark_retryable_error,
    run_tool,
)
from app.agent.state import NODE_EXTRACT, AgentState, append_warning, touch
from app.models.requirement import Requirement
from app.services.llm_client import LlmError
from app.tools.agent_tools import ExtractRequirementsInput, extract_requirements


def extract_requirements_node(state: AgentState) -> AgentState:
    state, skipped = begin_node(state, NODE_EXTRACT)
    if skipped:
        return state
    runtime = get_runtime()
    project_id = UUID(str(state["project_id"]))

    requested = list(state.get("requested_requirement_ids") or [])
    if requested:

        def _load_requested():
            rows = list(
                runtime.db.scalars(
                    select(Requirement).where(
                        Requirement.project_id == project_id,
                        Requirement.id.in_([UUID(x) for x in requested]),
                    )
                ).all()
            )
            return [
                {
                    "id": str(r.id),
                    "title": r.title,
                    "category": r.category.value if r.category else None,
                    "mandatory": r.mandatory,
                }
                for r in rows
            ]

        items = run_tool(
            state,
            "extract_requirements",
            _load_requested,
            summary_on_ok=lambda items: f"requested_ids={len(items)}",
        )
        state["requirements"] = items
        state["has_requirements"] = bool(items)
        return finish_node(state, NODE_EXTRACT)

    def _call():
        return extract_requirements(
            runtime.db,
            ExtractRequirementsInput(
                project_id=project_id,
                document_ids=[UUID(x) for x in (state.get("selected_document_ids") or [])],
                use_existing=True,
            ),
            llm=runtime.llm,
        )

    try:
        result = run_tool(
            state,
            "extract_requirements",
            _call,
            summary_on_ok=lambda r: r.summary or r.detail,
        )
    except LlmError as exc:
        mark_fatal_error(state, f"LLM schema/error: {exc}", "llm_schema_error")
        return touch(state)
    except Exception as exc:  # noqa: BLE001
        mark_retryable_error(state, f"{type(exc).__name__}: {exc}", "extract_error")
        return touch(state)

    if not result.ok:
        mark_fatal_error(state, result.detail or "extract failed", "extract_error")
        return touch(state)

    items = list(result.data.get("requirements") or [])
    state["requirements"] = items
    state["has_requirements"] = bool(items)
    if not items and not state.get("requested_requirement_ids"):
        if state.get("metadata", {}).get("block_on_no_requirements"):
            state["status"] = "blocked"
            state["error_code"] = "no_requirements"
            state["error_summary"] = "no requirements after extract"
            state["route_decision"] = "blocked"
        else:
            state["status"] = "completed_with_warnings"
            append_warning(state, "no requirements after extract")
            state["route_decision"] = "completed_with_warnings"
    return finish_node(state, NODE_EXTRACT)
