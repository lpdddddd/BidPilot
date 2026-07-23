from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.search import RetrievalTrace, SearchResultItem


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=512)
    document_types: list[str] = Field(default_factory=list)
    document_ids: list[str] = Field(default_factory=list)
    top_k: int | None = Field(default=None, ge=1, le=20)
    stream: bool = False
    # Public model_id from /api/v1/models (e.g. qwen3-8b-base / qwen3-8b-lora-course).
    model_id: str | None = Field(default=None, max_length=128)
    # Explicit Base fallback when requested LoRA is not served (never silent).
    allow_base_fallback: bool = False


class CitationItem(BaseModel):
    source_id: str
    chunk_id: str
    document_id: str
    file_name: str | None = None
    document_type: str | None = None
    section: str | None = None
    clause_id: str | None = None
    page_start: int | None = None
    page_end: int | None = None
    excerpt: str
    content_hash: str | None = None
    rerank_score: float | None = None
    rrf_score: float | None = None
    dense_rank: int | None = None
    dense_score: float | None = None
    bm25_rank: int | None = None
    bm25_score: float | None = None
    chunk_index: int | None = None
    document_url: str | None = None


class RagRetrievalTrace(RetrievalTrace):
    """RetrievalTrace plus RAG-stage timing (ms)."""

    rag_prepare_ms: float = 0.0
    context_chunk_count: int = 0
    context_token_count: int = 0
    filtered_by_min_score: int = 0


class GenerationTrace(BaseModel):
    model: str
    context_chunk_count: int
    context_token_count: int
    latency_ms: float
    finish_reason: str | None = None
    request_id: str | None = None
    requested_model_id: str | None = None
    resolved_model_id: str | None = None
    served_model_name: str | None = None
    model_type: Literal["base", "lora"] | None = None
    adapter_version: str | None = None
    fallback_used: bool = False


class AskResponse(BaseModel):
    question: str
    answer: str
    citations: list[CitationItem]
    sources: list[CitationItem] = Field(
        default_factory=list,
        description="All evidence used as context this turn (may exceed cited ones).",
    )
    retrieval_trace: RagRetrievalTrace
    generation_trace: GenerationTrace | None = None
    status: Literal["answered", "insufficient_evidence", "llm_disabled"] = "answered"


class LlmHealthResponse(BaseModel):
    status: Literal["ok", "disabled", "error"]
    enabled: bool
    model: str
    base_url: str
    reachable: bool
    detail: str | None = None
    latency_ms: float | None = None


# Streaming event payloads (documented for OpenAPI / clients)


class AskRetrievalEvent(BaseModel):
    event: Literal["retrieval"] = "retrieval"
    sources: list[CitationItem]
    retrieval_trace: RagRetrievalTrace
    status: Literal["ok", "insufficient_evidence"]


class AskDeltaEvent(BaseModel):
    event: Literal["delta"] = "delta"
    text: str


class AskFinalEvent(BaseModel):
    event: Literal["final"] = "final"
    result: AskResponse


class AskErrorEvent(BaseModel):
    event: Literal["error"] = "error"
    message: str
    detail: Any = None


# Re-export for OpenAPI convenience
SearchResultItemRef = SearchResultItem
