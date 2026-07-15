from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from bidpilot_data.schemas.enums import (
    BundleLevel,
    DerivationMethod,
    Difficulty,
    DocumentType,
    MatchStatus,
    ParseStatus,
    QualityLevel,
    QuestionType,
    ReviewDecision,
    ReviewStatus,
    RiskLevel,
    SFTTaskType,
    SourceStatus,
    SplitName,
    TaxonomyCategory,
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    def to_jsonl_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class SourceRecord(StrictModel):
    source_id: str
    source_url: str
    source_site: str
    project_code: str
    project_name: str
    document_type: DocumentType = DocumentType.tender
    published_at: datetime | None = None
    province: str | None = None
    industry: str | None = None
    license_or_terms: str | None = None
    collected_at: datetime | None = None
    status: SourceStatus = SourceStatus.pending
    local_path: str | None = None
    sha256: str | None = None
    error_message: str | None = None

    @field_validator("source_url")
    @classmethod
    def url_required(cls, v: str) -> str:
        if not v.startswith(("http://", "https://", "file://")):
            raise ValueError("source_url must be http(s) or file URL")
        return v


class DocumentRecord(StrictModel):
    document_id: str
    project_id: str
    source_id: str | None = None
    original_filename: str
    mime_type: str | None = None
    sha256: str
    file_size: int = Field(ge=0)
    storage_path: str
    page_count: int | None = Field(default=None, ge=0)
    parse_method: str | None = None
    parse_status: ParseStatus = ParseStatus.pending
    document_type: DocumentType = DocumentType.other
    source_url: str | None = None
    # Optional denormalized fields written by project rebuild for pairing/resume.
    project_code: str | None = None
    project_name: str | None = None
    issuing_authority: str | None = None


class TableCell(StrictModel):
    model_config = ConfigDict(extra="allow")
    text: str = ""


class ParsedPage(StrictModel):
    document_id: str
    page_number: int = Field(ge=1)
    text: str
    tables: list[list[list[str]]] = Field(default_factory=list)
    headings: list[str] = Field(default_factory=list)
    bbox_metadata: dict[str, Any] | None = None
    ocr_used: bool = False


class ChunkRecord(StrictModel):
    chunk_id: str
    project_id: str
    document_id: str
    page_start: int = Field(ge=1)
    page_end: int = Field(ge=1)
    section_path: str | None = None
    clause_number: str | None = None
    chunk_index: int = Field(ge=0)
    text: str
    normalized_text: str
    token_count: int = Field(ge=0)
    content_hash: str

    @model_validator(mode="after")
    def page_order(self) -> ChunkRecord:
        if self.page_end < self.page_start:
            raise ValueError("page_end must be >= page_start")
        if not self.text.strip():
            raise ValueError("chunk text must not be empty")
        return self


class RequirementAnnotation(StrictModel):
    annotation_id: str
    requirement_id: str
    project_id: str
    document_id: str | None = None
    chunk_id: str | None = None
    requirement_code: str | None = None
    category: TaxonomyCategory
    title: str
    original_text: str
    normalized_requirement: str
    mandatory: bool = False
    score: float | None = None
    risk_level: RiskLevel = RiskLevel.medium
    evidence_required: list[str] = Field(default_factory=list)
    source_page: int | None = Field(default=None, ge=1)
    source_section: str | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    quality_level: QualityLevel = QualityLevel.pending
    review_status: ReviewStatus = ReviewStatus.pending
    generator: str = "rules"
    reviewer: str | None = None
    reviewed_at: datetime | None = None
    prompt_version: str | None = None
    model_name: str | None = None
    source_url: str | None = None

    @model_validator(mode="after")
    def gold_requires_reviewer(self) -> RequirementAnnotation:
        if self.quality_level == QualityLevel.gold and not self.reviewer:
            raise ValueError("gold annotations require reviewer")
        if self.quality_level == QualityLevel.gold and self.review_status not in {
            ReviewStatus.reviewed,
            ReviewStatus.auto_checked,
        }:
            # auto_checked alone is not enough for gold upgrade path; force reviewed
            if self.review_status != ReviewStatus.reviewed:
                raise ValueError("gold annotations require review_status=reviewed")
        return self


class EvidenceRecord(StrictModel):
    evidence_id: str
    project_id: str
    document_id: str
    chunk_id: str | None = None
    source_url: str
    page_number: int | None = Field(default=None, ge=1)
    section_path: str | None = None
    quote: str
    content_hash: str

    @model_validator(mode="after")
    def quote_required(self) -> EvidenceRecord:
        if not self.quote.strip():
            raise ValueError("evidence quote must not be empty")
        if not self.source_url.startswith(("http://", "https://", "file://")):
            raise ValueError("evidence source_url must be http(s) or file URL")
        return self


class ProjectDocumentRef(StrictModel):
    document_type: DocumentType
    source_url: str
    sha256: str | None = None
    published_at: str | None = None
    local_path: str | None = None
    original_filename: str | None = None
    document_id: str | None = None


class ProjectBundle(StrictModel):
    project_id: str
    project_code: str
    project_name: str
    province: str | None = None
    industry: str | None = None
    purchaser: str | None = None
    procurement_agency: str | None = None
    budget_cny: float | None = None
    published_at: str | None = None
    official_project_url: str
    bundle_level: BundleLevel = BundleLevel.incomplete
    documents: list[ProjectDocumentRef] = Field(default_factory=list)
    source_domain: str | None = None
    issuing_authority: str | None = None
    collected_at: str | None = None


class DisclosedSupplierRecord(StrictModel):
    """Supplier facts disclosed in official public filings only (never synthetic)."""

    supplier_id: str
    name: str
    credit_code: str | None = None
    industry: str | None = None
    project_id: str | None = None
    source_document_ids: list[str] = Field(default_factory=list)
    source_urls: list[str] = Field(default_factory=list)
    synthetic: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def forbid_synthetic(self) -> DisclosedSupplierRecord:
        if self.synthetic:
            raise ValueError("synthetic suppliers are forbidden in official datasets")
        return self


class CompanyMaterialRecord(StrictModel):
    """Legacy shape retained for demo DB import tests; formal pipeline must not emit synthetic=true."""

    material_id: str
    company_profile_id: str
    document_id: str
    material_type: str
    title: str
    content: str
    synthetic: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class CompanyProfileSynthetic(StrictModel):
    """Deprecated. Kept only so old fixtures fail validation when synthetic=true if used in formal path."""

    company_profile_id: str
    name: str
    credit_code: str | None = None
    industry: str | None = None
    synthetic: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class RequirementMatchAnnotation(StrictModel):
    match_id: str
    requirement_id: str
    company_profile_id: str | None = None
    supplier_id: str | None = None
    status: MatchStatus
    reason: str
    evidence_ids: list[str] = Field(default_factory=list)
    evidence_document_id: str | None = None
    evidence_chunk_id: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    risk_level: RiskLevel | None = None
    recommended_action: str | None = None
    quality_level: QualityLevel = QualityLevel.silver
    review_status: ReviewStatus = ReviewStatus.pending

    @model_validator(mode="after")
    def evidence_for_positive_status(self) -> RequirementMatchAnnotation:
        if self.status in {MatchStatus.satisfied, MatchStatus.partially_satisfied, MatchStatus.missing}:
            if not self.evidence_ids and not self.evidence_document_id and not self.evidence_chunk_id:
                raise ValueError("non-unknown matches require official evidence references")
        return self


class RAGQuestion(StrictModel):
    question_id: str
    project_id: str
    question: str
    answer: str | None = None
    answerable: bool
    gold_chunk_ids: list[str] = Field(default_factory=list)
    gold_document_ids: list[str] = Field(default_factory=list)
    source_document_ids: list[str] = Field(default_factory=list)
    source_urls: list[str] = Field(default_factory=list)
    source_pages: list[int] = Field(default_factory=list)
    source_quotes: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    question_type: QuestionType
    difficulty: Difficulty = Difficulty.medium
    quality_level: QualityLevel = QualityLevel.silver
    review_status: ReviewStatus = ReviewStatus.pending

    @model_validator(mode="after")
    def evidence_for_answerable(self) -> RAGQuestion:
        if self.answerable and not self.gold_chunk_ids:
            raise ValueError("answerable RAG questions require gold_chunk_ids")
        if self.answerable and not self.source_quotes:
            raise ValueError("answerable RAG questions require source_quotes")
        if not self.question.strip():
            raise ValueError("question must not be empty")
        if self.quality_level == QualityLevel.gold and self.review_status != ReviewStatus.reviewed:
            raise ValueError("gold RAG requires reviewed status")
        return self


class AgentTask(StrictModel):
    task_id: str
    project_id: str
    user_request: str
    initial_state: dict[str, Any] = Field(default_factory=dict)
    expected_tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    expected_final_result: dict[str, Any] = Field(default_factory=dict)
    acceptance_criteria: list[str] = Field(default_factory=list)
    quality_level: QualityLevel = QualityLevel.silver
    review_status: ReviewStatus = ReviewStatus.pending


class ChatMessage(StrictModel):
    role: str
    content: str

    @field_validator("role")
    @classmethod
    def role_ok(cls, v: str) -> str:
        if v not in {"system", "user", "assistant", "tool"}:
            raise ValueError(f"invalid role: {v}")
        return v

    @field_validator("content")
    @classmethod
    def content_ok(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("empty content")
        return v


class SFTRecord(StrictModel):
    record_id: str
    project_id: str
    source_project_id: str | None = None
    source_document_ids: list[str] = Field(default_factory=list)
    source_chunk_ids: list[str] = Field(default_factory=list)
    source_urls: list[str] = Field(default_factory=list)
    derivation_method: DerivationMethod | None = None
    task_type: SFTTaskType
    quality_level: QualityLevel
    review_status: ReviewStatus
    messages: list[ChatMessage]
    split: SplitName | None = None
    is_test_project: bool = False

    @model_validator(mode="after")
    def messages_shape(self) -> SFTRecord:
        roles = [m.role for m in self.messages]
        if "assistant" not in roles:
            raise ValueError("assistant message required")
        if roles[0] == "assistant":
            raise ValueError("messages must not start with assistant")
        if self.quality_level == QualityLevel.gold and self.review_status != ReviewStatus.reviewed:
            raise ValueError("gold SFT requires reviewed status")
        if self.source_project_id is None:
            self.source_project_id = self.project_id
        return self

    @model_validator(mode="after")
    def gold_requires_pages_or_urls(self) -> SFTRecord:
        # Source URLs preferred for official traceability; chunk ids also accepted.
        if self.quality_level == QualityLevel.gold and not (self.source_urls or self.source_chunk_ids):
            raise ValueError("gold SFT requires source_urls or source_chunk_ids")
        return self


class ReviewDecisionRecord(StrictModel):
    annotation_id: str
    project_id: str
    source_url: str | None = None
    source_page: int | None = None
    original_text: str
    predicted_category: str
    predicted_normalized_requirement: str
    predicted_mandatory: bool
    predicted_score: float | None = None
    decision: ReviewDecision
    corrected_category: str | None = None
    corrected_normalized_requirement: str | None = None
    corrected_mandatory: bool | None = None
    corrected_score: float | None = None
    reviewer: str | None = None
    review_comment: str | None = None


class DatasetSplitManifest(StrictModel):
    seed: int = 42
    created_at: datetime
    train_project_ids: list[str]
    validation_project_ids: list[str]
    test_project_ids: list[str]
    heldout_project_ids: list[str] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=dict)

    @model_validator(mode="after")
    def no_leakage(self) -> DatasetSplitManifest:
        sets = [
            set(self.train_project_ids),
            set(self.validation_project_ids),
            set(self.test_project_ids),
        ]
        if sets[0] & sets[1] or sets[0] & sets[2] or sets[1] & sets[2]:
            raise ValueError("project_id leakage across splits")
        return self


# Avoid UUID confusion in future extensions
UUIDType = UUID
