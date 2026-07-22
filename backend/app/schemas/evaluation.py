"""Pydantic schemas for evaluation center APIs."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class EvaluationTargetCapability(BaseModel):
    target_type: str
    available: bool
    reason: str | None = None


class EvaluationCapabilitiesResponse(BaseModel):
    targets: list[EvaluationTargetCapability]
    profiles: list[str]
    evaluator_version: str
    dataset: dict[str, Any]


class EvaluationSuiteRead(BaseModel):
    id: UUID
    project_id: UUID | None
    name: str
    version: str
    description: str | None = None
    dataset_hash: str
    evaluator_profile_version: str
    manifest_snapshot: dict[str, Any] | None = None
    task_family_config: dict[str, Any] | None = None
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


class EvaluationRunCreate(BaseModel):
    suite_id: UUID | None = None
    target_type: str = "deterministic_fake"
    target_config: dict[str, Any] = Field(default_factory=dict)
    split: str | None = None
    splits: list[str] | None = None
    task_family: str | None = None
    task_families: list[str] | None = None
    limit: int | None = None
    case_keys: list[str] | None = None
    seed: int = 42
    fixture_path: str | None = None
    created_by: str | None = None
    fail_case_keys: list[str] | None = None


class EvaluationRunRead(BaseModel):
    id: UUID
    project_id: UUID
    suite_id: UUID
    status: str
    target_type: str
    target_config_snapshot: dict[str, Any] | None = None
    dataset_hash: str
    evaluator_version: str
    seed: int
    total_cases: int
    completed_cases: int
    passed_cases: int
    failed_cases: int
    error_cases: int
    overall_score: float | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_ms: int | None = None
    safe_error_summary: str | None = None
    source_commit_sha: str | None = None
    summary_json: dict[str, Any] | None = None
    filter_json: dict[str, Any] | None = None
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


class EvaluationMetricRead(BaseModel):
    metric_name: str
    metric_version: str
    value: float | None
    applicable: bool
    weight: float
    threshold: float | None
    passed: bool | None
    evidence_summary: str | None
    reference_kind: str

    model_config = {"from_attributes": True}


class EvaluationCaseResultRead(BaseModel):
    id: UUID
    evaluation_run_id: UUID
    case_key: str
    case_content_hash: str
    task_family: str
    split: str
    status: str
    reference_kind: str
    score: float | None
    passed: bool | None
    hard_gate_failures: list[Any] | None = None
    safe_error_summary: str | None = None
    duration_ms: int | None = None
    agent_run_id: UUID | None = None
    input_snapshot: dict[str, Any] | None = None
    reference_summary: dict[str, Any] | None = None
    response_snapshot: dict[str, Any] | None = None
    metric_results: list[EvaluationMetricRead] | None = None

    model_config = {"from_attributes": True}
