"""Hybrid retrieval: dense (Qdrant) + BM25 (OpenSearch) -> RRF -> cross-encoder.

No LLM is called anywhere in this pipeline; the API returns real ranked
evidence with per-stage scores and an honest retrieval trace. Failures of the
underlying services surface as explicit errors, never as fake results.
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from qdrant_client import models as qmodels
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.repositories.project import ProjectRepository
from app.schemas.search import (
    RetrievalTrace,
    SearchRequest,
    SearchResponse,
    SearchResultItem,
    StageLatency,
)
from app.services.embeddings import EmbeddingUnavailableError, get_embedding_service
from app.services.index_tasks import get_qdrant_client
from app.services.infra_clients import get_opensearch_client
from app.services.reranker import RerankerUnavailableError, get_reranker_service

logger = logging.getLogger("bidpilot.retrieval")


@dataclass
class _Candidate:
    chunk_id: str
    payload: dict[str, Any] = field(default_factory=dict)
    dense_rank: int | None = None
    dense_score: float | None = None
    bm25_rank: int | None = None
    bm25_score: float | None = None
    rrf_score: float = 0.0
    rerank_score: float | None = None


def _dense_search(
    query_vector: list[float],
    project_id: UUID,
    request: SearchRequest,
) -> list[tuple[str, float, dict[str, Any]]]:
    settings = get_settings()
    client = get_qdrant_client()
    must: list[qmodels.FieldCondition] = [
        qmodels.FieldCondition(key="project_id", match=qmodels.MatchValue(value=str(project_id)))
    ]
    if request.document_types:
        must.append(
            qmodels.FieldCondition(
                key="document_type", match=qmodels.MatchAny(any=request.document_types)
            )
        )
    if request.document_ids:
        must.append(
            qmodels.FieldCondition(
                key="document_id", match=qmodels.MatchAny(any=request.document_ids)
            )
        )
    response = client.query_points(
        collection_name=settings.qdrant_collection_name,
        query=query_vector,
        query_filter=qmodels.Filter(must=list(must)),
        limit=settings.retrieval_dense_top_k,
        with_payload=True,
    )
    results: list[tuple[str, float, dict[str, Any]]] = []
    for point in response.points:
        payload = point.payload or {}
        chunk_id = payload.get("chunk_id")
        if chunk_id:
            results.append((str(chunk_id), float(point.score), payload))
    return results


def _bm25_search(
    project_id: UUID,
    request: SearchRequest,
) -> list[tuple[str, float, dict[str, Any]]]:
    settings = get_settings()
    client = get_opensearch_client()
    filters: list[dict[str, Any]] = [{"term": {"project_id": str(project_id)}}]
    if request.document_types:
        filters.append({"terms": {"document_type": request.document_types}})
    if request.document_ids:
        filters.append({"terms": {"document_id": request.document_ids}})
    body = {
        "size": settings.retrieval_bm25_top_k,
        "query": {
            "bool": {
                "must": [
                    {
                        "multi_match": {
                            "query": request.query,
                            "fields": [
                                "content^2",
                                "section",
                                "clause_id",
                                "file_name",
                            ],
                            "type": "best_fields",
                        }
                    }
                ],
                "filter": filters,
            }
        },
        "_source": True,
    }
    response = client.search(index=settings.opensearch_index_name, body=body)
    results: list[tuple[str, float, dict[str, Any]]] = []
    for hit in response.get("hits", {}).get("hits", []):
        source = hit.get("_source", {})
        chunk_id = source.get("chunk_id") or hit.get("_id")
        if chunk_id:
            results.append((str(chunk_id), float(hit.get("_score") or 0.0), source))
    return results


def rrf_fuse(
    dense: list[tuple[str, float, dict[str, Any]]],
    bm25: list[tuple[str, float, dict[str, Any]]],
    *,
    rrf_k: int,
    dense_weight: float = 1.0,
    bm25_weight: float = 1.0,
) -> list[_Candidate]:
    """Reciprocal Rank Fusion over the two candidate lists (ranks start at 1)."""
    candidates: dict[str, _Candidate] = {}
    for rank, (chunk_id, score, payload) in enumerate(dense, start=1):
        candidate = candidates.setdefault(chunk_id, _Candidate(chunk_id=chunk_id))
        candidate.dense_rank = rank
        candidate.dense_score = score
        if payload:
            candidate.payload = payload
    for rank, (chunk_id, score, payload) in enumerate(bm25, start=1):
        candidate = candidates.setdefault(chunk_id, _Candidate(chunk_id=chunk_id))
        candidate.bm25_rank = rank
        candidate.bm25_score = score
        if not candidate.payload and payload:
            candidate.payload = payload
    for candidate in candidates.values():
        score = 0.0
        if candidate.dense_rank is not None:
            score += dense_weight / (rrf_k + candidate.dense_rank)
        if candidate.bm25_rank is not None:
            score += bm25_weight / (rrf_k + candidate.bm25_rank)
        candidate.rrf_score = score
    return sorted(candidates.values(), key=lambda c: (-c.rrf_score, c.chunk_id))


class RetrievalService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def search(self, project_id: UUID, request: SearchRequest) -> SearchResponse:
        settings = get_settings()
        if not request.query.strip():
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="查询内容不能为空")
        if ProjectRepository(self.db).get_by_id(project_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="项目不存在")

        degraded: list[str] = []
        total_start = time.perf_counter()

        embedder = get_embedding_service()
        embed_start = time.perf_counter()
        try:
            query_vector = embedder.embed_query(request.query)
        except EmbeddingUnavailableError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
            ) from exc
        embed_ms = (time.perf_counter() - embed_start) * 1000

        # Dense and BM25 run in parallel.
        parallel_start = time.perf_counter()
        with ThreadPoolExecutor(max_workers=2) as pool:
            dense_future = pool.submit(_dense_search, query_vector, project_id, request)
            bm25_future = pool.submit(_bm25_search, project_id, request)
            try:
                dense_hits = dense_future.result()
            except Exception as exc:  # noqa: BLE001 - explicit, no fake results
                logger.exception("Dense search failed for project %s", project_id)
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail=f"向量检索不可用（Qdrant）: {exc}",
                ) from exc
            try:
                bm25_hits = bm25_future.result()
            except Exception as exc:  # noqa: BLE001
                logger.exception("BM25 search failed for project %s", project_id)
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail=f"关键词检索不可用（OpenSearch）: {exc}",
                ) from exc
        parallel_ms = (time.perf_counter() - parallel_start) * 1000

        fusion_start = time.perf_counter()
        fused = rrf_fuse(
            dense_hits,
            bm25_hits,
            rrf_k=settings.retrieval_rrf_k,
            dense_weight=settings.retrieval_dense_weight,
            bm25_weight=settings.retrieval_bm25_weight,
        )
        shortlist = fused[: settings.retrieval_fusion_top_k]
        fusion_ms = (time.perf_counter() - fusion_start) * 1000

        # Cross-encoder rerank on the fused shortlist only. If the model is
        # unavailable we degrade explicitly: RRF order, rerank_score stays null.
        rerank_ms = 0.0
        reranker_model: str | None = get_settings().reranker_model_name
        if shortlist:
            rerank_start = time.perf_counter()
            try:
                scores = get_reranker_service().score(
                    request.query,
                    [str(c.payload.get("content") or "") for c in shortlist],
                )
                for candidate, score in zip(shortlist, scores, strict=True):
                    candidate.rerank_score = score
                shortlist.sort(key=lambda c: -(c.rerank_score or 0.0))
            except RerankerUnavailableError as exc:
                degraded.append("reranker_unavailable")
                reranker_model = None
                logger.warning("Reranker unavailable, RRF order returned: %s", exc)
            rerank_ms = (time.perf_counter() - rerank_start) * 1000

        top = shortlist[: request.top_k]
        results = [
            SearchResultItem(
                rank=rank,
                chunk_id=candidate.chunk_id,
                document_id=str(candidate.payload.get("document_id") or ""),
                file_name=candidate.payload.get("file_name"),
                document_type=candidate.payload.get("document_type"),
                chunk_index=candidate.payload.get("chunk_index"),
                section=candidate.payload.get("section"),
                clause_id=candidate.payload.get("clause_id"),
                page_start=candidate.payload.get("page_start"),
                page_end=candidate.payload.get("page_end"),
                content=str(candidate.payload.get("content") or ""),
                content_hash=candidate.payload.get("content_hash"),
                source_sha256=candidate.payload.get("source_sha256"),
                chunker_version=candidate.payload.get("chunker_version"),
                dense_rank=candidate.dense_rank,
                dense_score=candidate.dense_score,
                bm25_rank=candidate.bm25_rank,
                bm25_score=candidate.bm25_score,
                rrf_score=candidate.rrf_score,
                rerank_score=candidate.rerank_score,
            )
            for rank, candidate in enumerate(top, start=1)
        ]
        total_ms = (time.perf_counter() - total_start) * 1000

        return SearchResponse(
            query=request.query,
            results=results,
            trace=RetrievalTrace(
                dense_candidate_count=len(dense_hits),
                bm25_candidate_count=len(bm25_hits),
                fused_candidate_count=len(fused),
                returned_count=len(results),
                embedding_model=embedder.model_name,
                reranker_model=reranker_model,
                qdrant_collection=settings.qdrant_collection_name,
                opensearch_index=settings.opensearch_index_name,
                rrf_k=settings.retrieval_rrf_k,
                latency=StageLatency(
                    embed_ms=round(embed_ms, 2),
                    dense_ms=round(parallel_ms, 2),
                    bm25_ms=round(parallel_ms, 2),
                    fusion_ms=round(fusion_ms, 2),
                    rerank_ms=round(rerank_ms, 2),
                    total_ms=round(total_ms, 2),
                ),
                degraded=degraded,
            ),
        )
