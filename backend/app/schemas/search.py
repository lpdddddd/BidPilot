from __future__ import annotations

from pydantic import BaseModel, Field


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=512)
    top_k: int = Field(default=8, ge=1, le=20)
    document_types: list[str] = Field(default_factory=list)
    document_ids: list[str] = Field(default_factory=list)


class SearchResultItem(BaseModel):
    rank: int
    chunk_id: str
    document_id: str
    file_name: str | None
    document_type: str | None
    chunk_index: int | None
    section: str | None
    clause_id: str | None
    page_start: int | None
    page_end: int | None
    content: str
    content_hash: str | None
    source_sha256: str | None
    chunker_version: str | None
    dense_rank: int | None
    dense_score: float | None
    bm25_rank: int | None
    bm25_score: float | None
    rrf_score: float
    rerank_score: float | None


class StageLatency(BaseModel):
    embed_ms: float
    dense_ms: float
    bm25_ms: float
    fusion_ms: float
    rerank_ms: float
    total_ms: float


class RetrievalTrace(BaseModel):
    dense_candidate_count: int
    bm25_candidate_count: int
    fused_candidate_count: int
    returned_count: int
    embedding_model: str
    reranker_model: str | None
    qdrant_collection: str
    opensearch_index: str
    rrf_k: int
    latency: StageLatency
    degraded: list[str] = Field(default_factory=list)


class SearchResponse(BaseModel):
    query: str
    results: list[SearchResultItem]
    trace: RetrievalTrace


class IndexSummaryResponse(BaseModel):
    document_id: str
    status: str
    indexed_chunk_count: int
    embedding_model: str | None
    embedding_dimension: int | None
    qdrant_collection: str | None
    opensearch_index: str | None
    error: str | None
    completed_at: str | None
