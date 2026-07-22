from __future__ import annotations

from uuid import UUID

from app.agent.nodes._helpers import (
    get_runtime,
    mark_fatal_error,
    mark_node_start,
    maybe_interrupt,
    record_tool_event,
)
from app.agent.state import NODE_REVISE, AgentState, append_warning, touch
from app.tools.agent_tools import GenerateProposalDraftInput, generate_proposal_draft


def revise_draft(state: AgentState) -> AgentState:
    state = mark_node_start(state, NODE_REVISE)
    runtime = get_runtime()
    count = int(state.get("draft_revise_count") or 0) + 1
    state["draft_revise_count"] = count

    meta = dict(state.get("metadata") or {})
    meta["forbid_satisfaction_claims"] = True
    meta["force_redraft"] = True
    # Clear previous force_draft_validation fail on revise unless still forced.
    if meta.get("force_draft_validation") is False and meta.get("revise_should_pass"):
        # After first revise, allow pass.
        if count >= int(meta.get("revise_pass_after", 1)):
            meta["force_draft_validation"] = True
    state["metadata"] = meta

    append_warning(state, f"revising draft attempt={count}")

    # Synthetic revise for tests: append a new draft version event id.
    if meta.get("synthetic_revise"):
        prev = list(state.get("draft_ids") or [])
        new_id = meta.get("synthetic_draft_id_v2") or f"revised-{count}"
        state["draft_ids"] = prev + [str(new_id)]
        record_tool_event(
            state,
            name="generate_proposal_draft",
            status="ok",
            summary=f"revise_synthetic count={count}",
        )
        maybe_interrupt(state, NODE_REVISE)
        return touch(state)

    project_id = state.get("project_id")
    req_ids = [UUID(r["id"]) for r in (state.get("requirements") or []) if r.get("id")]
    if not project_id or not req_ids:
        # Still count as a revise event for loop control.
        record_tool_event(
            state,
            name="generate_proposal_draft",
            status="ok",
            summary=f"revise_noop count={count}",
        )
        maybe_interrupt(state, NODE_REVISE)
        return touch(state)

    idem = f"agent-{state['run_id']}-draft-rev-{count}"
    try:
        result = generate_proposal_draft(
            runtime.db,
            GenerateProposalDraftInput(
                project_id=UUID(project_id),
                requirement_ids=req_ids,
                title=f"Agent 响应准备草稿 (修订 {count})",
                idempotency_key=idem,
                risk_only=bool(meta.get("forbid_satisfaction_claims")),
            ),
            llm=runtime.llm,
        )
    except Exception as exc:  # noqa: BLE001
        mark_fatal_error(state, f"revise failed: {exc}", "revise_error")
        record_tool_event(
            state, name="generate_proposal_draft", status="error", summary=str(exc)
        )
        return touch(state)

    record_tool_event(
        state,
        name="generate_proposal_draft",
        status="ok" if result.ok else "error",
        summary=result.summary or result.detail,
    )
    new_ids = list(result.data.get("draft_ids") or [])
    if new_ids:
        # Keep history of draft versions/events.
        merged = list(state.get("draft_ids") or [])
        for d in new_ids:
            if d not in merged:
                merged.append(d)
        state["draft_ids"] = merged
    if result.data.get("content_preview"):
        meta["risk_draft_preview"] = result.data["content_preview"]
        state["metadata"] = meta
    maybe_interrupt(state, NODE_REVISE)
    return touch(state)
