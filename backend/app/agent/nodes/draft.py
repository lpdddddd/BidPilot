from __future__ import annotations

from uuid import UUID

from app.agent.nodes._helpers import (
    begin_node,
    finish_node,
    get_runtime,
    mark_fatal_error,
    mark_retryable_error,
    run_tool,
)
from app.agent.state import NODE_DRAFT, AgentState, append_warning, block_on_critical, touch
from app.services.llm_client import LlmError
from app.tools.agent_tools import GenerateProposalDraftInput, generate_proposal_draft


def generate_response_draft(state: AgentState) -> AgentState:
    state, skipped = begin_node(state, NODE_DRAFT)
    if skipped:
        return state
    runtime = get_runtime()
    project_id = UUID(str(state["project_id"]))

    if state.get("draft_ids") and not state.get("metadata", {}).get("force_redraft"):
        run_tool(
            state,
            "generate_proposal_draft",
            lambda: state["draft_ids"],
            summary_on_ok=lambda ids: f"reused drafts={ids}",
        )
        return finish_node(state, NODE_DRAFT)

    risk_only = bool(state.get("critical_qualification")) and not block_on_critical(state)
    if state.get("critical_qualification") and block_on_critical(state):
        state["status"] = "blocked"
        append_warning(state, "skipped draft due to critical qualification block")
        return finish_node(state, NODE_DRAFT)

    req_ids = [UUID(r["id"]) for r in (state.get("requirements") or []) if r.get("id")]
    idem = f"agent-{state['run_id']}-draft-{int(state.get('draft_revise_count') or 0)}"

    meta = state.get("metadata") or {}
    if meta.get("synthetic_draft_id"):
        state["draft_ids"] = [str(meta["synthetic_draft_id"])]
        run_tool(
            state,
            "generate_proposal_draft",
            lambda: "synthetic_draft",
            summary_on_ok=lambda _: "synthetic_draft",
        )
        return finish_node(state, NODE_DRAFT)

    def _call():
        return generate_proposal_draft(
            runtime.db,
            GenerateProposalDraftInput(
                project_id=project_id,
                requirement_ids=req_ids,
                title=meta.get("draft_title") or "Agent 响应准备草稿",
                idempotency_key=idem,
                risk_only=risk_only or bool(meta.get("force_risk_only_draft")),
            ),
            llm=runtime.llm,
        )

    try:
        result = run_tool(
            state,
            "generate_proposal_draft",
            _call,
            summary_on_ok=lambda r: r.summary or r.detail,
        )
    except LlmError as exc:
        mark_fatal_error(state, f"LLM schema/error: {exc}", "llm_schema_error")
        return touch(state)
    except Exception as exc:  # noqa: BLE001
        mark_retryable_error(state, f"{type(exc).__name__}: {exc}", "draft_error")
        return touch(state)

    if not result.ok:
        mark_fatal_error(state, result.detail or "draft failed", "draft_error")
        return touch(state)

    draft_ids = list(result.data.get("draft_ids") or [])
    state["draft_ids"] = draft_ids
    if result.data.get("risk_only"):
        append_warning(state, "generated risk-only draft without satisfaction claims")
        state.setdefault("metadata", {})
        meta2 = dict(state.get("metadata") or {})
        meta2["risk_draft_preview"] = result.data.get("content_preview")
        meta2["forbid_satisfaction_claims"] = True
        state["metadata"] = meta2
        if state.get("status") not in {"blocked", "failed"}:
            state["status"] = "completed_with_warnings"
    return finish_node(state, NODE_DRAFT)
