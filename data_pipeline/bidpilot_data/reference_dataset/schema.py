"""Pydantic schema for auto reference dataset samples."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

GENERATOR_VERSION = "bidpilot-reference-1.0.0"

TaskType = Literal["rag", "extraction", "matching", "compliance", "drafting", "unanswerable"]
LabelSource = Literal["auto_reference", "silver"]
SplitName = Literal["train", "validation", "test"]
MatchJudgment = Literal[
    "supported",
    "partially_supported",
    "insufficient_evidence",
    "conflicting",
    "not_applicable",
]

CATEGORY_BUCKETS = (
    "qualification",
    "technical",
    "commercial",
    "scoring",
    "delivery",
    "risk",
)


class EvidenceItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_id: str | None = None
    chunk_id: str | None = None
    document_id: str | None = None
    page_number: int | None = Field(default=None, ge=1)
    char_start: int | None = Field(default=None, ge=0)
    char_end: int | None = Field(default=None, ge=0)
    quote: str = ""
    source_url: str | None = None

    @model_validator(mode="after")
    def range_order(self) -> EvidenceItem:
        if self.char_start is not None and self.char_end is not None and self.char_end < self.char_start:
            raise ValueError("char_end must be >= char_start")
        return self


class CitationMetadata(BaseModel):
    model_config = ConfigDict(extra="allow")

    chunk_ids: list[str] = Field(default_factory=list)
    document_ids: list[str] = Field(default_factory=list)
    page_numbers: list[int] = Field(default_factory=list)
    source_urls: list[str] = Field(default_factory=list)
    quotes: list[str] = Field(default_factory=list)
    category: str | None = None
    notes: str | None = None


class QualityChecks(BaseModel):
    model_config = ConfigDict(extra="allow")

    schema_ok: bool = True
    ids_ok: bool = True
    quote_grounded: bool = True
    answerable_supported: bool = True
    unanswerable_ok: bool = True
    dedupe_ok: bool = True
    judge_ok: bool = True
    messages: list[str] = Field(default_factory=list)


class DataProvenance(BaseModel):
    model_config = ConfigDict(extra="allow")

    source_paths: list[str] = Field(default_factory=list)
    source_record_ids: list[str] = Field(default_factory=list)
    method: str = "deterministic_template"
    reuse_existing_rag: bool = False
    notes: str | None = None


class ReferenceSample(BaseModel):
    """One auto-reference eval/training sample (never human_gold)."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    sample_id: str
    task_type: TaskType
    project_id: str
    document_id: str
    input: dict[str, Any]
    reference_output: dict[str, Any]
    evidence: list[EvidenceItem] = Field(default_factory=list)
    citation_metadata: CitationMetadata = Field(default_factory=CitationMetadata)
    quality_checks: QualityChecks = Field(default_factory=QualityChecks)
    confidence: float = Field(ge=0.0, le=1.0)
    generation_model: str = "deterministic"
    generator_version: str = GENERATOR_VERSION
    data_provenance: DataProvenance = Field(default_factory=DataProvenance)
    label_source: LabelSource = "auto_reference"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    split: SplitName | None = None

    @field_validator("label_source")
    @classmethod
    def never_human_gold(cls, v: str) -> str:
        if v in {"human_gold", "gold"}:
            raise ValueError("label_source must be auto_reference or silver, never human_gold")
        return v

    @field_validator("confidence")
    @classmethod
    def clamp_confidence(cls, v: float) -> float:
        return float(max(0.0, min(1.0, v)))

    def to_jsonl_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


DEFAULT_TARGETS: dict[str, int] = {
    "rag": 30,
    "extraction": 30,
    "matching": 30,
    "compliance": 20,
    "drafting": 20,
    "unanswerable": 10,
}
