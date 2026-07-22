"""Centralized conditional routing for the BidPilot agent graph."""

from __future__ import annotations

from typing import Literal

from app.agent.state import (
    MAX_DRAFT_REVISE,
    MAX_NODE_RETRIES,
    NODE_DRAFT,
    NODE_EXTRACT,
    NODE_FINALIZE,
    NODE_LOAD_CONTEXT,
    NODE_MATCH,
    NODE_RETRIEVE,
    NODE_REVISE,
    NODE_VALIDATE,
    AgentState,
    block_on_critical,
)

RouteTarget = Literal[
    "load_project_context",
    "retrieve_evidence",
    "extract_requirements",
    "match_company_evidence",
    "run_compliance_check",
    "generate_response_draft",
    "validate_draft",
    "revise_draft",
    "finalize_run",
    "failed",
    "blocked",
    "completed_with_warnings",
    "completed",
    "retry_node",
    "__end__",
]


def after_initialize(state: AgentState) -> str:
    """missing project / authz → failed; else load context."""
    if state.get("status") == "failed":
        return NODE_FINALIZE
    if not state.get("project_id"):
        state["status"] = "failed"
        state["error_code"] = "missing_project"
        state["error_summary"] = "project_id is required"
        state["route_decision"] = "failed"
        return NODE_FINALIZE
    if state.get("metadata", {}).get("authz_denied"):
        state["status"] = "failed"
        state["error_code"] = "authz_denied"
        state["error_summary"] = "authorization denied"
        state["route_decision"] = "failed"
        return NODE_FINALIZE
    return NODE_LOAD_CONTEXT


def after_load_context(state: AgentState) -> str:
    if state.get("status") == "failed":
        return NODE_FINALIZE
    if not state.get("has_documents"):
        state["status"] = "blocked"
        state["error_code"] = "no_documents"
        state["error_summary"] = "project has no documents"
        state["route_decision"] = "blocked"
        return NODE_FINALIZE
    return NODE_RETRIEVE


def after_retrieve(state: AgentState) -> str:
    if _should_fail(state):
        return NODE_FINALIZE
    if _should_retry(state, NODE_RETRIEVE):
        return NODE_RETRIEVE
    if _should_fail(state):
        return NODE_FINALIZE
    return NODE_EXTRACT


def after_extract(state: AgentState) -> str:
    if _should_fail(state):
        return NODE_FINALIZE
    if _should_retry(state, NODE_EXTRACT):
        return NODE_EXTRACT
    if _should_fail(state):
        return NODE_FINALIZE
    if not state.get("has_requirements") and not state.get("requirements"):
        # Prefer completed_with_warnings when soft; blocked when metadata says so.
        if state.get("metadata", {}).get("block_on_no_requirements"):
            state["status"] = "blocked"
            state["error_code"] = "no_requirements"
            state["error_summary"] = "no requirements after extract"
            state["route_decision"] = "blocked"
        else:
            state["status"] = "completed_with_warnings"
            warnings = list(state.get("warnings") or [])
            msg = "no requirements after extract"
            if msg not in warnings:
                warnings.append(msg)
            state["warnings"] = warnings
            state["route_decision"] = "completed_with_warnings"
        return NODE_FINALIZE
    return NODE_MATCH


def after_match(state: AgentState) -> str:
    if _should_fail(state):
        return NODE_FINALIZE
    if _should_retry(state, NODE_MATCH):
        return NODE_MATCH
    if _should_fail(state):
        return NODE_FINALIZE
    # company evidence insufficient → continue with warnings (set in node)
    return "run_compliance_check"


def after_compliance(state: AgentState) -> str:
    if _should_fail(state):
        return NODE_FINALIZE
    if _should_retry(state, "run_compliance_check"):
        return "run_compliance_check"
    if _should_fail(state):
        return NODE_FINALIZE
    if state.get("critical_qualification") and block_on_critical(state):
        state["status"] = "blocked"
        state["error_code"] = "critical_qualification"
        state["error_summary"] = (
            "critical qualification finding; block_on_critical_qualification=true"
        )
        state["route_decision"] = "blocked"
        # Still allow draft node to skip / risk-only is handled if flag false.
        return NODE_FINALIZE
    return NODE_DRAFT


def after_draft(state: AgentState) -> str:
    if _should_fail(state):
        return NODE_FINALIZE
    if _should_retry(state, NODE_DRAFT):
        return NODE_DRAFT
    if _should_fail(state):
        return NODE_FINALIZE
    if state.get("status") == "blocked":
        return NODE_FINALIZE
    return NODE_VALIDATE


def after_validate(state: AgentState) -> str:
    if _should_fail(state):
        return NODE_FINALIZE
    if state.get("draft_validation_ok"):
        state["route_decision"] = "finalize"
        return NODE_FINALIZE
    revise_count = int(state.get("draft_revise_count") or 0)
    if revise_count < MAX_DRAFT_REVISE:
        state["route_decision"] = "revise_draft"
        return NODE_REVISE
    warnings = list(state.get("warnings") or [])
    msg = f"draft validation failed after {MAX_DRAFT_REVISE} revisions"
    if msg not in warnings:
        warnings.append(msg)
    state["warnings"] = warnings
    if state.get("status") not in {"blocked", "failed"}:
        state["status"] = "completed_with_warnings"
    state["route_decision"] = "completed_with_warnings"
    return NODE_FINALIZE


def after_revise(state: AgentState) -> str:
    if _should_fail(state):
        return NODE_FINALIZE
    return NODE_VALIDATE


def _should_fail(state: AgentState) -> bool:
    if state.get("status") == "failed":
        return True
    if state.get("last_error_retryable"):
        return False
    if state.get("error_code") and state.get("status") == "failed":
        return True
    return False


def _should_retry(state: AgentState, node: str) -> bool:
    if not state.get("last_error_retryable"):
        return False
    counts = dict(state.get("retry_counts") or {})
    n = int(counts.get(node, 0))
    if n < MAX_NODE_RETRIES:
        counts[node] = n + 1
        state["retry_counts"] = counts
        state["last_error_retryable"] = False
        return True
    state["status"] = "failed"
    state["error_code"] = state.get("error_code") or "retry_exhausted"
    state["error_summary"] = state.get("error_summary") or f"retries exhausted for {node}"
    return False
