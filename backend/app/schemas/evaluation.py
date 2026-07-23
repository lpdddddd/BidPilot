"""Pydantic schemas for evaluation center APIs (FE/BE contract source of truth)."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator


class EvaluationTargetCapability(BaseModel):
    target_type: str
    available: bool
    reason: str | None = None
    reason_code: str | None = None


class EvaluationProfileInfo(BaseModel):
    id: str
    name: str
    version: str
    enabled_metrics: list[str] = Field(default_factory=list)
    ai_judge_enabled: bool = False


class EvaluationDatasetInfo(BaseModel):
    name: str
    version: str
    dataset_hash: str
    hash_short: str | None = None
    total_cases: int | None = None
    task_family_counts: dict[str, int] | None = None
    split_counts: dict[str, int] | None = None
    reference_kind_counts: dict[str, int] | None = None
    direct_reference_coverage: float | None = None
    human_gold_count: int | None = None
    auto_reference_count: int | None = None
    rule_expected_count: int | None = None
    no_direct_reference_count: int | None = None
    label_policy: str | None = None


class EvaluationCapabilitiesResponse(BaseModel):
    """Unified capability payload — use `items` only (not `targets`)."""

    items: list[EvaluationTargetCapability]
    profiles: list[EvaluationProfileInfo]
    evaluator_version: str
    dataset: EvaluationDatasetInfo
    task_families: list[str] = Field(default_factory=list)
    splits: list[str] = Field(default_factory=list)


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
    updated_at: datetime | None = None

    model_config = {"from_attributes": True}


class PaginatedSuites(BaseModel):
    items: list[EvaluationSuiteRead]
    total: int
    page: int = 1
    page_size: int = 50


class EvaluationRunCreate(BaseModel):
    """Create payload — aliases keep older test fields working."""

    suite_id: UUID | None = None
    target: str | None = None
    target_type: str | None = None
    target_config: dict[str, Any] = Field(default_factory=dict)
    split: str | None = None
    splits: list[str] | None = None
    task_family: str | None = None
    task_families: list[str] | None = None
    case_limit: int | None = None
    limit: int | None = None  # legacy alias for case_limit
    case_keys: list[str] | None = None
    seed: int = 42
    evaluator_profile: str | None = None
    profile: str | None = None  # legacy alias
    fixture_path: str | None = None
    created_by: str | None = None
    fail_case_keys: list[str] | None = None
    idempotency_key: str | None = None

    @model_validator(mode="after")
    def _unify_aliases(self) -> EvaluationRunCreate:
        if not self.target_type:
            object.__setattr__(self, "target_type", self.target)
        if not self.target:
            object.__setattr__(self, "target", self.target_type)
        if self.case_limit is None and self.limit is not None:
            object.__setattr__(self, "case_limit", self.limit)
        if self.evaluator_profile is None and self.profile is not None:
            object.__setattr__(self, "evaluator_profile", self.profile)
        if self.task_families is None and self.task_family:
            object.__setattr__(self, "task_families", [self.task_family])
        return self

    @field_validator("case_limit", "limit")
    @classmethod
    def _non_negative(cls, v: int | None) -> int | None:
        if v is not None and v < 0:
            raise ValueError("case_limit must be >= 0")
        return v


class EvaluationRunRead(BaseModel):
    id: UUID
    project_id: UUID
    suite_id: UUID
    suite_name: str | None = None
    suite_version: str | None = None
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
    created_by: str | None = None
    idempotency_key: str | None = None
    cancel_requested: bool | None = None
    detail_url: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = {"from_attributes": True}


class PaginatedRuns(BaseModel):
    items: list[EvaluationRunRead]
    total: int
    page: int = 1
    page_size: int = 50


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


class EvaluationCitationRead(BaseModel):
    document_id: str | None = None
    document_title: str | None = None
    file_name: str | None = None
    page: int | None = None
    page_start: int | None = None
    section: str | None = None
    chunk_id: str | None = None
    project_id: str | None = None
    valid: bool | None = None
    validation_error: str | None = None
    summary: str | None = None


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
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_ms: int | None = None
    agent_run_id: UUID | None = None
    input_snapshot: dict[str, Any] | None = None
    reference_summary: dict[str, Any] | None = None
    response_snapshot: dict[str, Any] | None = None
    metric_results: list[EvaluationMetricRead] | None = None
    citations: list[EvaluationCitationRead] | None = None

    model_config = {"from_attributes": True}


class PaginatedCaseResults(BaseModel):
    items: list[EvaluationCaseResultRead]
    total: int
    page: int = 1
    page_size: int = 100


class EvaluationCaseCompareRow(BaseModel):
    case_key: str
    left_score: float | None = None
    right_score: float | None = None
    left_status: str | None = None
    right_status: str | None = None
    delta: float | None = None


class EvaluationCompareResponse(BaseModel):
    left: EvaluationRunRead
    right: EvaluationRunRead
    warnings: list[str]
    overall_score_delta: float | None = None
    pass_rate_delta: float | None = None
    task_family_deltas: dict[str, float | None] = Field(default_factory=dict)
    metric_deltas: dict[str, float | None] = Field(default_factory=dict)
    improved_cases: list[EvaluationCaseCompareRow] = Field(default_factory=list)
    regressed_cases: list[EvaluationCaseCompareRow] = Field(default_factory=list)
    unchanged_cases: list[EvaluationCaseCompareRow] = Field(default_factory=list)
    left_only_cases: list[str] = Field(default_factory=list)
    right_only_cases: list[str] = Field(default_factory=list)
    config_diff: dict[str, Any] | None = None
    # legacy keys kept for older tests
    common_cases: list[str] = Field(default_factory=list)
    only_left: list[str] = Field(default_factory=list)
    only_right: list[str] = Field(default_factory=list)
