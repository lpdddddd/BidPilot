"""Schemas for Course LoRA / Base structured clause analysis (SFT protocol)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

StructuredTaskType = Literal[
    "requirement_classify",
    "qualification_extract",
    "scoring_extract",
    "risk_detect",
    "project_info_extract",
]


class StructuredClauseRequest(BaseModel):
    clause_text: str = Field(min_length=1, max_length=8000)
    task_type: StructuredTaskType = "requirement_classify"
    model_id: str | None = Field(default=None, max_length=128)
    allow_base_fallback: bool = False
    temperature: float = Field(default=0.1, ge=0.0, le=1.0)
    max_tokens: int = Field(default=512, ge=32, le=2048)


class StructuredClauseResponse(BaseModel):
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
