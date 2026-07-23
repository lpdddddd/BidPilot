"""Schemas for Course LoRA / Base structured clause analysis (SFT protocol)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

StructuredTaskType = Literal[
    "requirement_classify",
    "qualification_extract",
    "scoring_extract",
    "risk_detect",
    "project_info_extract",
]

# Align with data_pipeline TaxonomyCategory / RiskLevel used in course_pilot SFT.
StructuredCategory = Literal[
    "project_info",
    "qualification",
    "commercial",
    "technical",
    "scoring",
    "pricing",
    "contract",
    "delivery",
    "service",
    "personnel",
    "performance",
    "certification",
    "financial",
    "legal",
    "mandatory_rejection",
    "submission",
    "other",
]

StructuredRiskLevel = Literal["low", "medium", "high", "critical"]


class RequirementClassifyOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: StructuredCategory
    mandatory: bool
    risk_level: StructuredRiskLevel
    confidence: float = Field(ge=0.0, le=1.0)


class QualificationExtractOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requirements: list[str] = Field(min_length=1)
    mandatory: bool
    evidence_required: list[str]


class ScoringExtractOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item: str = Field(min_length=1)
    score: float | int | str
    method: str = Field(min_length=1)


class RiskDetectOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    risk_level: StructuredRiskLevel
    risk_type: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    is_rejection_clause: bool


class ProjectInfoExtractOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_name: str | None = None
    purchaser: str | None = None
    budget_cny: float | int | str | None = None
    deadline: str | None = None
    region: str | None = None
    project_code: str | None = None

    @field_validator("project_name", "purchaser", "region", "deadline", "project_code")
    @classmethod
    def _empty_to_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None


TASK_OUTPUT_MODELS: dict[str, type[BaseModel]] = {
    "requirement_classify": RequirementClassifyOutput,
    "qualification_extract": QualificationExtractOutput,
    "scoring_extract": ScoringExtractOutput,
    "risk_detect": RiskDetectOutput,
    "project_info_extract": ProjectInfoExtractOutput,
}


class StructuredClauseRequest(BaseModel):
    clause_text: str = Field(min_length=1, max_length=8000)
    task_type: StructuredTaskType = "requirement_classify"
    model_id: str | None = Field(default=None, max_length=128)
    allow_base_fallback: bool = False
    temperature: float = Field(default=0.1, ge=0.0, le=1.0)
    max_tokens: int = Field(default=512, ge=32, le=2048)


class StructuredClauseResponse(BaseModel):
    id: UUID | None = None
    project_id: UUID | None = None
    task_type: str
    clause_text: str
    raw_output: str
    parsed: dict[str, Any] | None = None
    schema_valid: bool
    required_field_coverage: float
    missing_fields: list[str] = Field(default_factory=list)
    parse_error: str | None = None
    requested_model_id: str
    resolved_model_id: str | None = None
    served_model_name: str | None = None
    model_type: str | None = None
    adapter_version: str | None = None
    dataset_version: str
    fallback_used: bool = False
    latency_ms: float
    capability: str
    created_at: datetime | None = None


class StructuredClauseListResponse(BaseModel):
    items: list[StructuredClauseResponse]
    total: int
