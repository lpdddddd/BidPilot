"""LangGraph agent state and constants for BidPilot Step 10."""

from __future__ import annotations

from datetime import datetime
from typing import Any, NotRequired, TypedDict
from uuid import UUID

from pydantic import BaseModel, Field

GRAPH_VERSION = "bidpilot-agent-1.0.0"
MAX_DRAFT_REVISE = 2
MAX_NODE_RETRIES = 2

# Config defaults (also overridable via state.metadata)
DEFAULT_BLOCK_ON_CRITICAL_QUALIFICATION = True

NODE_INITIALIZE = "initialize_run"
NODE_LOAD_CONTEXT = "load_project_context"
NODE_RETRIEVE = "retrieve_evidence"
NODE_EXTRACT = "extract_requirements"
NODE_MATCH = "match_company_evidence"
NODE_COMPLIANCE = "run_compliance_check"
NODE_DRAFT = "generate_response_draft"
NODE_VALIDATE = "validate_draft"
NODE_REVISE = "revise_draft"
NODE_FINALIZE = "finalize_run"

TERMINAL_STATUSES = frozenset(
    {
        "completed",
        "completed_with_warnings",
        "blocked",
        "failed",
        "cancelled",
    }
)


class RetrievedChunkSummary(BaseModel):
    chunk_id: str
    score: float | None = None
    page_start: int | None = None
    page_end: int | None = None
    section: str | None = None
    document_id: str | None = None
    summary: str | None = None


class RequirementSummaryItem(BaseModel):
    id: str
    title: str | None = None
    category: str | None = None
    mandatory: bool | None = None


class RequirementMatchItem(BaseModel):
    id: str
    requirement_id: str | None = None
    status: str
    review_status: str | None = None


class ToolEvent(BaseModel):
    name: str
    started_at: str | None = None
    finished_at: str | None = None
    status: str = "ok"
    summary: str | None = None
    duration_ms: int | None = None


class AgentStateModel(BaseModel):
    """Pydantic view of agent state (API / persistence / validation)."""

    run_id: str
    project_id: str | None = None
    organization_id: str | None = None
    user_request: str = ""
    requested_requirement_ids: list[str] = Field(default_factory=list)
    selected_document_ids: list[str] = Field(default_factory=list)
    current_node: str | None = None
    status: str = "pending"
    retrieved_chunks: list[RetrievedChunkSummary] = Field(default_factory=list)
    requirements: list[RequirementSummaryItem] = Field(default_factory=list)
    requirement_matches: list[RequirementMatchItem] = Field(default_factory=list)
    compliance_run_id: str | None = None
    compliance_summary: dict[str, Any] = Field(default_factory=dict)
    draft_ids: list[str] = Field(default_factory=list)
    draft_findings: list[dict[str, Any]] = Field(default_factory=list)
    citations: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    tool_events: list[ToolEvent] = Field(default_factory=list)
    started_at: str | None = None
    updated_at: str | None = None
    completed_at: str | None = None
    graph_version: str = GRAPH_VERSION
    retry_counts: dict[str, int] = Field(default_factory=dict)
    completed_nodes: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    # Routing / loop helpers
    route_decision: str | None = None
    has_documents: bool = False
    has_requirements: bool = False
    company_evidence_insufficient: bool = False
    critical_qualification: bool = False
    draft_validation_ok: bool | None = None
    draft_revise_count: int = 0
    interrupt_requested: bool = False
    last_error_retryable: bool = False
    error_code: str | None = None
    error_summary: str | None = None


class AgentState(TypedDict):
    """LangGraph TypedDict state (JSON-serializable values)."""

    run_id: str
    project_id: str | None
    organization_id: NotRequired[str | None]
    user_request: str
    requested_requirement_ids: list[str]
    selected_document_ids: list[str]
    current_node: str | None
    status: str
    retrieved_chunks: list[dict[str, Any]]
    requirements: list[dict[str, Any]]
    requirement_matches: list[dict[str, Any]]
    compliance_run_id: str | None
    compliance_summary: dict[str, Any]
    draft_ids: list[str]
    draft_findings: NotRequired[list[dict[str, Any]]]
    citations: list[dict[str, Any]]
    warnings: list[str]
    errors: list[str]
    tool_events: list[dict[str, Any]]
    started_at: str | None
    updated_at: str | None
    completed_at: str | None
    graph_version: str
    retry_counts: dict[str, int]
    completed_nodes: NotRequired[list[str]]
    metadata: dict[str, Any]
    route_decision: NotRequired[str | None]
    has_documents: NotRequired[bool]
    has_requirements: NotRequired[bool]
    company_evidence_insufficient: NotRequired[bool]
    critical_qualification: NotRequired[bool]
    draft_validation_ok: NotRequired[bool | None]
    draft_revise_count: NotRequired[int]
    interrupt_requested: NotRequired[bool]
    last_error_retryable: NotRequired[bool]
    error_code: NotRequired[str | None]
    error_summary: NotRequired[str | None]


def empty_state(
    *,
    run_id: str | UUID,
    project_id: str | UUID | None = None,
    organization_id: str | UUID | None = None,
    user_request: str = "",
    requested_requirement_ids: list[str] | None = None,
    selected_document_ids: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> AgentState:
    now = datetime.utcnow().isoformat() + "Z"
    return {
        "run_id": str(run_id),
        "project_id": str(project_id) if project_id else None,
        "organization_id": str(organization_id) if organization_id else None,
        "user_request": user_request or "",
        "requested_requirement_ids": list(requested_requirement_ids or []),
        "selected_document_ids": list(selected_document_ids or []),
        "current_node": None,
        "status": "pending",
        "retrieved_chunks": [],
        "requirements": [],
        "requirement_matches": [],
        "compliance_run_id": None,
        "compliance_summary": {},
        "draft_ids": [],
        "draft_findings": [],
        "citations": [],
        "warnings": [],
        "errors": [],
        "tool_events": [],
        "started_at": now,
        "updated_at": now,
        "completed_at": None,
        "graph_version": GRAPH_VERSION,
        "retry_counts": {},
        "completed_nodes": [],
        "metadata": dict(metadata or {}),
        "route_decision": None,
        "has_documents": False,
        "has_requirements": False,
        "company_evidence_insufficient": False,
        "critical_qualification": False,
        "draft_validation_ok": None,
        "draft_revise_count": 0,
        "interrupt_requested": False,
        "last_error_retryable": False,
        "error_code": None,
        "error_summary": None,
    }


def touch(state: AgentState) -> AgentState:
    state["updated_at"] = datetime.utcnow().isoformat() + "Z"
    return state


def append_warning(state: AgentState, message: str) -> None:
    warnings = list(state.get("warnings") or [])
    if message not in warnings:
        warnings.append(message)
    state["warnings"] = warnings


def append_error(state: AgentState, message: str) -> None:
    errors = list(state.get("errors") or [])
    if message not in errors:
        errors.append(message)
    state["errors"] = errors


def block_on_critical(state: AgentState) -> bool:
    meta = state.get("metadata") or {}
    return bool(
        meta.get(
            "block_on_critical_qualification",
            DEFAULT_BLOCK_ON_CRITICAL_QUALIFICATION,
        )
    )
