"""API schemas for LangGraph agent runs (Step 10)."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.enums import AgentRunStatus


class AgentRunStartRequest(BaseModel):
    user_request: str = ""
    intent: str | None = "bid_analysis_loop"
    requested_requirement_ids: list[UUID] = Field(default_factory=list)
    selected_document_ids: list[UUID] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentStateRead(BaseModel):
    run_id: str | None = None
    project_id: str | None = None
    organization_id: str | None = None
    user_request: str | None = None
    requested_requirement_ids: list[str] = Field(default_factory=list)
    selected_document_ids: list[str] = Field(default_factory=list)
    current_node: str | None = None
    status: str | None = None
    retrieved_chunks: list[dict[str, Any]] = Field(default_factory=list)
    requirements: list[dict[str, Any]] = Field(default_factory=list)
    requirement_matches: list[dict[str, Any]] = Field(default_factory=list)
    compliance_run_id: str | None = None
    compliance_summary: dict[str, Any] = Field(default_factory=dict)
    draft_ids: list[str] = Field(default_factory=list)
    citations: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    tool_events: list[dict[str, Any]] = Field(default_factory=list)
    started_at: str | None = None
    updated_at: str | None = None
    completed_at: str | None = None
    graph_version: str | None = None
    retry_counts: dict[str, int] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    critical_qualification: bool | None = None
    company_evidence_insufficient: bool | None = None
    draft_validation_ok: bool | None = None
    draft_revise_count: int | None = None

    model_config = {"extra": "allow"}


class AgentRunRead(BaseModel):
    id: UUID
    organization_id: UUID
    project_id: UUID | None
    status: AgentRunStatus
    intent: str | None = None
    current_node: str | None = None
    graph_version: str | None = None
    idempotency_key: str | None = None
    input_json: dict[str, Any] | None = None
    output_summary_json: dict[str, Any] | None = None
    error_code: str | None = None
    error_summary: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    state: AgentStateRead | None = None


class AgentRunListResponse(BaseModel):
    items: list[AgentRunRead]
    total: int


class AgentEventItem(BaseModel):
    id: UUID | None = None
    event_type: str
    sequence: int
    name: str
    node_name: str | None = None
    tool_name: str | None = None
    status: str
    summary: str | None = None
    safe_summary: str | None = None
    created_at: datetime | None = None
    timestamp: datetime | None = None
    duration_ms: int | None = None
    agent_step_id: UUID | None = None
    tool_call_id: UUID | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class AgentEventsResponse(BaseModel):
    run_id: UUID
    items: list[AgentEventItem]
    total: int


class AgentResultResponse(BaseModel):
    run: AgentRunRead
    summary: dict[str, Any] = Field(default_factory=dict)
    state: AgentStateRead | None = None
    citations: list[dict[str, Any]] = Field(default_factory=list)
    draft_ids: list[UUID] = Field(default_factory=list)
    compliance_run_id: UUID | None = None
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
