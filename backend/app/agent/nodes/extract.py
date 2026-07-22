from __future__ import annotations

from uuid import UUID

from sqlalchemy import select

from app.agent.nodes._helpers import (
    begin_node,
    finish_node,
    get_runtime,
    mark_fatal_error,
    mark_retryable_error,
    record_tool_event,
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
    project_id = UUID(state["project_id"])  # type: ignore[arg-type]

    requested = list(state.get("requested_requirement_ids") or [])
    if requested:
        rows = list(
            runtime.db.scalars(
                select(Requirement).where(
                    Requirement.project_id == project_id,
                    Requirement.id.in_([UUID(x) for x in requested]),
                )
            ).all()
        )
        items = [
            {
                "id": str(r.id),
                "title": r.title,
                "category": r.category.value if r.category else None,
                "mandatory": r.mandatory,
            }
            for r in rows
        ]
        state["requirements"] = items
        state["has_requirements"] = bool(items)
        record_tool_event(
            state,
            name="extract_requirements",
            status="ok",
            summary=f"requested_ids={len(items)}",
        )
        return finish_node(state, NODE_EXTRACT)

    try:
        result = extract_requirements(
            runtime.db,
            ExtractRequirementsInput(
                project_id=project_id,
                document_ids=[UUID(x) for x in (state.get("selected_document_ids") or [])],
                use_existing=True,
            ),
            llm=runtime.llm,
        )
    except LlmError as exc:
        mark_fatal_error(state, f"LLM schema/error: {exc}", "llm_schema_error")
        record_tool_event(state, name="extract_requirements", status="error", summary=str(exc))
        return touch(state)
    except Exception as exc:  # noqa: BLE001
        mark_retryable_error(state, f"{type(exc).__name__}: {exc}", "extract_error")
        record_tool_event(state, name="extract_requirements", status="error", summary=str(exc))
        return touch(state)

    record_tool_event(
        state,
        name="extract_requirements",
        status="ok" if result.ok else "error",
        summary=result.summary or result.detail,
    )
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
