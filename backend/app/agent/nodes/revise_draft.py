from __future__ import annotations

from uuid import UUID

from app.agent.nodes._helpers import (
    begin_node,
    finish_node,
    get_runtime,
    mark_fatal_error,
    run_tool,
)
from app.agent.state import NODE_REVISE, NODE_VALIDATE, AgentState, append_warning, touch
from app.tools.agent_tools import GenerateProposalDraftInput, generate_proposal_draft


def revise_draft(state: AgentState) -> AgentState:
    state, skipped = begin_node(state, NODE_REVISE)
    if skipped:
        return state

    runtime = get_runtime()
    count = int(state.get("draft_revise_count") or 0) + 1
    state["draft_revise_count"] = count

    meta = dict(state.get("metadata") or {})
    findings = list(state.get("draft_findings") or [])
    remediations = [
        f.get("remediation") for f in findings if isinstance(f, dict) and f.get("remediation")
    ]
    failing_rules = [
        f.get("rule_id") for f in findings if isinstance(f, dict) and f.get("status") == "fail"
    ]
    meta["remediation_hints"] = remediations[:20]
    meta["revise_from_rule_ids"] = [r for r in failing_rules if r][:20]
    meta["forbid_satisfaction_claims"] = True
    meta["force_redraft"] = True

    # Risk-only when critical qualification or claim/safety findings demand it.
    risk_only = bool(state.get("critical_qualification")) or any(
        (f.get("severity") in {"error", "critical"})
        and (
            "claim" in (f.get("rule_id") or "").lower()
            or "strong" in (f.get("rule_id") or "").lower()
            or (f.get("rule_id") or "").startswith("D00")
            or (f.get("rule_id") or "").startswith("AGENT_SUPPLEMENT")
        )
        for f in findings
        if isinstance(f, dict)
    )
    meta["force_risk_only_draft"] = bool(meta.get("force_risk_only_draft") or risk_only)

    # Clear previous force_draft_validation fail on revise unless still forced.
    if (
        meta.get("force_draft_validation") is False
        and meta.get("revise_should_pass")
        and count >= int(meta.get("revise_pass_after", 1))
    ):
        meta["force_draft_validation"] = True

    # Reset validation so the next validate_draft re-runs formal check.
    state["draft_validation_ok"] = None
    completed = [n for n in (state.get("completed_nodes") or []) if n != NODE_VALIDATE]
    state["completed_nodes"] = completed
    state["metadata"] = meta

    append_warning(state, f"revising draft attempt={count}")

    # Synthetic revise for tests: append a new draft version event id.
    if meta.get("synthetic_revise"):
        prev = list(state.get("draft_ids") or [])
        new_id = meta.get("synthetic_draft_id_v2") or f"revised-{count}"
        meta["prior_draft_ids"] = prev
        state["draft_ids"] = [str(new_id)]
        state["metadata"] = meta
        run_tool(
            state,
            "generate_proposal_draft",
            lambda: f"revise_synthetic count={count}",
            summary_on_ok=lambda s: s,
        )
        return finish_node(state, NODE_REVISE)

    project_id = state.get("project_id")
    req_ids = [UUID(r["id"]) for r in (state.get("requirements") or []) if r.get("id")]
    if not project_id or not req_ids:
        run_tool(
            state,
            "generate_proposal_draft",
            lambda: f"revise_noop count={count}",
            summary_on_ok=lambda s: s,
        )
        return finish_node(state, NODE_REVISE)

    idem = f"agent-{state['run_id']}-draft-rev-{count}"

    def _call():
        return generate_proposal_draft(
            runtime.db,
            GenerateProposalDraftInput(
                project_id=UUID(project_id),
                requirement_ids=req_ids,
                title=f"Agent 响应准备草稿 (修订 {count})",
                idempotency_key=idem,
                risk_only=bool(
                    meta.get("force_risk_only_draft") or meta.get("forbid_satisfaction_claims")
                ),
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
    except Exception as exc:  # noqa: BLE001
        mark_fatal_error(state, f"revise failed: {exc}", "revise_error")
        return touch(state)
    new_ids = list(result.data.get("draft_ids") or [])
    if new_ids:
        # Keep prior ids in metadata; current draft_ids are the revised set so
        # the next formal validate does not re-fail on the old draft.
        prior = list(state.get("draft_ids") or [])
        meta["prior_draft_ids"] = prior
        state["draft_ids"] = list(new_ids)
    if result.data.get("content_preview"):
        meta["risk_draft_preview"] = result.data["content_preview"]
    state["metadata"] = meta
    return finish_node(state, NODE_REVISE)
