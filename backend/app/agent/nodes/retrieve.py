from __future__ import annotations

from uuid import UUID

from app.agent.nodes._helpers import (
    begin_node,
    finish_node,
    get_runtime,
    mark_retryable_error,
    record_tool_event,
)
from app.agent.state import NODE_RETRIEVE, AgentState, append_warning, touch
from app.tools.agent_tools import SearchEvidenceInput, search_evidence


def retrieve_evidence(state: AgentState) -> AgentState:
    state, skipped = begin_node(state, NODE_RETRIEVE)
    if skipped:
        return state
    runtime = get_runtime()
    project_id = state.get("project_id")
    assert project_id
    query = (state.get("user_request") or "").strip() or "招标要求 资格 条款"
    try:
        result = search_evidence(
            runtime.db,
            SearchEvidenceInput(
                project_id=UUID(project_id),
                query=query,
                document_ids=list(state.get("selected_document_ids") or []),
            ),
            retrieval_fn=runtime.retrieval_fn,
        )
    except Exception as exc:  # noqa: BLE001
        mark_retryable_error(state, f"{type(exc).__name__}: {exc}", "retrieve_error")
        record_tool_event(
            state, name="search_evidence", status="error", summary=str(exc)
        )
        return touch(state)
    record_tool_event(
        state,
        name="search_evidence",
        status="ok" if result.ok else "error",
        summary=result.summary or result.detail,
    )
    if not result.ok:
        mark_retryable_error(state, result.detail or "retrieve failed", "retrieve_error")
        return touch(state)

    chunks = list(result.data.get("chunks") or [])
    state["retrieved_chunks"] = chunks
    if not chunks:
        append_warning(state, "no evidence chunks retrieved")
    state["citations"] = [
        {
            "chunk_id": c.get("chunk_id"),
            "document_id": c.get("document_id"),
            "section": c.get("section"),
            "page_start": c.get("page_start"),
            "score": c.get("score"),
        }
        for c in chunks
    ]
    return finish_node(state, NODE_RETRIEVE)
