from __future__ import annotations

from uuid import UUID

from app.agent.nodes._helpers import (
    begin_node,
    finish_node,
    get_runtime,
    mark_fatal_error,
    mark_retryable_error,
    record_tool_event,
)
from app.agent.state import NODE_MATCH, AgentState, append_warning, touch
from app.services.llm_client import LlmError
from app.tools.agent_tools import MatchCompanyEvidenceInput, match_company_evidence


def match_company_evidence_node(state: AgentState) -> AgentState:
    state, skipped = begin_node(state, NODE_MATCH)
    if skipped:
        return state
    runtime = get_runtime()
    project_id = UUID(state["project_id"])  # type: ignore[arg-type]
    req_ids = [UUID(r["id"]) for r in (state.get("requirements") or []) if r.get("id")]

    try:
        result = match_company_evidence(
            runtime.db,
            MatchCompanyEvidenceInput(
                project_id=project_id,
                requirement_ids=req_ids,
                use_existing=True,
            ),
            llm=runtime.llm,
        )
    except LlmError as exc:
        mark_fatal_error(state, f"LLM schema/error: {exc}", "llm_schema_error")
        record_tool_event(
            state, name="match_company_evidence", status="error", summary=str(exc)
        )
        return touch(state)
    except Exception as exc:  # noqa: BLE001
        mark_retryable_error(state, f"{type(exc).__name__}: {exc}", "match_error")
        record_tool_event(
            state, name="match_company_evidence", status="error", summary=str(exc)
        )
        return touch(state)

    record_tool_event(
        state,
        name="match_company_evidence",
        status="ok" if result.ok else "error",
        summary=result.summary or result.detail,
    )
    if not result.ok:
        mark_fatal_error(state, result.detail or "match failed", "match_error")
        return touch(state)

    matches = list(result.data.get("matches") or [])
    state["requirement_matches"] = matches
    insufficient = int(result.data.get("insufficient_count") or 0)
    state["company_evidence_insufficient"] = insufficient > 0
    if insufficient > 0:
        append_warning(
            state,
            f"company evidence insufficient for {insufficient} match(es); "
            "continuing without inventing qualifications",
        )
    return finish_node(state, NODE_MATCH)
