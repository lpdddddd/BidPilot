"""In-process background indexing of chunks into Qdrant and OpenSearch.

Consistency strategy for rebuilds: embeddings are computed first (no side
effects), then per store the old entries for the document are deleted and the
new ones written. Failures are recorded honestly in the document's
metadata_json.indexing block; no fake success states.

Vectors live only in Qdrant; OpenSearch stores BM25-indexed text plus the
same provenance metadata (no embeddings). PostgreSQL never stores vectors.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from qdrant_client import QdrantClient
from qdrant_client import models as qmodels
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models import Document
from app.models.document import DocumentChunk
from app.models.enums import ParseStatus
from app.services.chunk_tasks import CHUNKING_STATUS_SUCCESS
from app.services.embeddings import EmbeddingUnavailableError, get_embedding_service
from app.services.infra_clients import get_opensearch_client

logger = logging.getLogger("bidpilot.index")

SESSION_FACTORY = SessionLocal

INDEXING_STATUS_PENDING = "pending"
INDEXING_STATUS_PROCESSING = "processing"
INDEXING_STATUS_SUCCESS = "success"
INDEXING_STATUS_FAILED = "failed"

# Stable namespace for deterministic point ids (uuid5 of doc/chunk/hash).
_POINT_NAMESPACE = uuid.UUID("b5a1f0aa-9c1e-4f6e-9a44-1f2b3c4d5e6f")


def stable_point_id(document_id: UUID, chunk_index: int, content_hash: str) -> str:
    return str(uuid.uuid5(_POINT_NAMESPACE, f"{document_id}:{chunk_index}:{content_hash}"))


def get_qdrant_client() -> QdrantClient:
    return QdrantClient(url=get_settings().qdrant_url, timeout=30)


def ensure_qdrant_collection(client: QdrantClient, dimension: int) -> str:
    name = get_settings().qdrant_collection_name
    if not client.collection_exists(name):
        client.create_collection(
            collection_name=name,
            vectors_config=qmodels.VectorParams(size=dimension, distance=qmodels.Distance.COSINE),
        )
        client.create_payload_index(
            collection_name=name,
            field_name="project_id",
            field_schema=qmodels.PayloadSchemaType.KEYWORD,
        )
        client.create_payload_index(
            collection_name=name,
            field_name="document_id",
            field_schema=qmodels.PayloadSchemaType.KEYWORD,
        )
    return name


# Character n-gram analysis keeps Chinese BM25 usable without paid plugins.
OPENSEARCH_INDEX_BODY: dict[str, Any] = {
    "settings": {
        "index": {"number_of_shards": 1, "number_of_replicas": 0},
        "analysis": {
            "analyzer": {
                "zh_ngram": {
                    "type": "custom",
                    "tokenizer": "zh_ngram_tokenizer",
                    "filter": ["lowercase"],
                }
            },
            "tokenizer": {
                "zh_ngram_tokenizer": {
                    "type": "ngram",
                    "min_gram": 1,
                    "max_gram": 2,
                    "token_chars": ["letter", "digit"],
                }
            },
        },
    },
    "mappings": {
        "properties": {
            "chunk_id": {"type": "keyword"},
            "project_id": {"type": "keyword"},
            "document_id": {"type": "keyword"},
            "document_type": {"type": "keyword"},
            "chunk_index": {"type": "integer"},
            "file_name": {
                "type": "text",
                "analyzer": "zh_ngram",
                "fields": {"raw": {"type": "keyword"}},
            },
            "section": {"type": "text", "analyzer": "zh_ngram"},
            "clause_id": {
                "type": "text",
                "analyzer": "zh_ngram",
                "fields": {"raw": {"type": "keyword"}},
            },
            "content": {"type": "text", "analyzer": "zh_ngram", "similarity": "BM25"},
            "content_hash": {"type": "keyword"},
            "source_sha256": {"type": "keyword"},
            "page_start": {"type": "integer"},
            "page_end": {"type": "integer"},
            "chunker_version": {"type": "keyword"},
            "embedding_model": {"type": "keyword"},
            "indexed_at": {"type": "date"},
        }
    },
}


def ensure_opensearch_index(client: Any) -> str:
    name = get_settings().opensearch_index_name
    if not client.indices.exists(index=name):
        client.indices.create(index=name, body=OPENSEARCH_INDEX_BODY)
    return name


def run_document_indexing(document_id: UUID) -> None:
    session = SESSION_FACTORY()
    try:
        _index_document(session, document_id)
    except Exception:
        logger.exception("Unexpected error while indexing document %s", document_id)
        session.rollback()
        _set_indexing_meta(
            session, document_id, status=INDEXING_STATUS_FAILED, error="索引任务内部错误"
        )
    finally:
        session.close()


def _index_document(session: Session, document_id: UUID) -> None:
    document = session.get(Document, document_id)
    if document is None:
        logger.warning("Index task skipped: document %s no longer exists", document_id)
        return

    meta = document.metadata_json or {}
    chunking = meta.get("chunking") or {}
    if document.parse_status != ParseStatus.success:
        _apply_indexing_meta(
            document,
            status=INDEXING_STATUS_FAILED,
            error=f"文档解析状态为 {document.parse_status.value}，无法建立索引",
        )
        session.commit()
        return
    if chunking.get("status") != CHUNKING_STATUS_SUCCESS:
        _apply_indexing_meta(
            document,
            status=INDEXING_STATUS_FAILED,
            error=f"Chunk 状态为 {chunking.get('status') or '未构建'}，需先完成切分",
        )
        session.commit()
        return

    chunks = list(
        session.scalars(
            select(DocumentChunk)
            .where(DocumentChunk.document_id == document.id)
            .order_by(DocumentChunk.chunk_index)
        )
    )
    if not chunks:
        _apply_indexing_meta(document, status=INDEXING_STATUS_FAILED, error="没有可索引的 Chunk")
        session.commit()
        return

    _apply_indexing_meta(document, status=INDEXING_STATUS_PROCESSING, error=None)
    session.commit()

    embedder = get_embedding_service()
    indexed_at = datetime.now(UTC).isoformat()

    # 1) Compute embeddings first: no side effects until this succeeds.
    try:
        vectors = embedder.embed_documents([chunk.content for chunk in chunks])
        dimension = embedder.dimension
    except EmbeddingUnavailableError as exc:
        _apply_indexing_meta(document, status=INDEXING_STATUS_FAILED, error=str(exc))
        session.commit()
        return

    payloads: list[dict[str, Any]] = []
    point_ids: list[str] = []
    for chunk in chunks:
        chunk_meta = chunk.metadata_json or {}
        payloads.append(
            {
                "project_id": str(chunk.project_id),
                "document_id": str(chunk.document_id),
                "chunk_id": str(chunk.id),
                "chunk_index": chunk.chunk_index,
                "document_type": document.document_type.value,
                "file_name": document.file_name,
                "section": chunk.section,
                "clause_id": chunk.clause_id,
                "page_start": chunk.page_start,
                "page_end": chunk.page_end,
                "content": chunk.content,
                "content_hash": chunk.content_hash,
                "source_sha256": chunk_meta.get("source_sha256"),
                "chunker_version": chunk_meta.get("chunker_version"),
                "embedding_model": embedder.model_name,
                "indexed_at": indexed_at,
            }
        )
        point_ids.append(
            stable_point_id(chunk.document_id, chunk.chunk_index, chunk.content_hash or "")
        )

    # 2) Qdrant: drop old points for this document, then upsert the new ones.
    try:
        qdrant = get_qdrant_client()
        collection = ensure_qdrant_collection(qdrant, dimension)
        qdrant.delete(
            collection_name=collection,
            points_selector=qmodels.FilterSelector(
                filter=qmodels.Filter(
                    must=[
                        qmodels.FieldCondition(
                            key="document_id",
                            match=qmodels.MatchValue(value=str(document.id)),
                        )
                    ]
                )
            ),
            wait=True,
        )
        qdrant.upsert(
            collection_name=collection,
            points=[
                qmodels.PointStruct(id=point_id, vector=vector, payload=payload)
                for point_id, vector, payload in zip(point_ids, vectors, payloads, strict=True)
            ],
            wait=True,
        )
    except Exception as exc:  # noqa: BLE001 - record honest failure
        logger.exception("Qdrant indexing failed for document %s", document_id)
        _apply_indexing_meta(
            document, status=INDEXING_STATUS_FAILED, error=f"Qdrant 索引失败: {exc}"
        )
        session.commit()
        return

    # 3) OpenSearch: drop old docs for this document, then bulk index.
    try:
        opensearch = get_opensearch_client()
        index_name = ensure_opensearch_index(opensearch)
        opensearch.delete_by_query(
            index=index_name,
            body={"query": {"term": {"document_id": str(document.id)}}},
            refresh=True,
            conflicts="proceed",
        )
        bulk_lines: list[dict[str, Any]] = []
        for payload in payloads:
            # chunk_id is the stable OpenSearch _id.
            bulk_lines.append({"index": {"_index": index_name, "_id": payload["chunk_id"]}})
            bulk_lines.append(payload)
        response = opensearch.bulk(body=bulk_lines, refresh=True)
        if response.get("errors"):
            failed_items = [
                item["index"].get("error")
                for item in response.get("items", [])
                if item.get("index", {}).get("error")
            ]
            raise RuntimeError(f"bulk 写入部分失败: {failed_items[:3]}")
    except Exception as exc:  # noqa: BLE001 - record honest failure
        logger.exception("OpenSearch indexing failed for document %s", document_id)
        _apply_indexing_meta(
            document, status=INDEXING_STATUS_FAILED, error=f"OpenSearch 索引失败: {exc}"
        )
        session.commit()
        return

    _apply_indexing_meta(
        document,
        status=INDEXING_STATUS_SUCCESS,
        error=None,
        indexed_chunk_count=len(chunks),
        embedding_model=embedder.model_name,
        embedding_dimension=dimension,
    )
    session.commit()


def remove_document_from_indexes(document_id: UUID) -> None:
    """Best-effort cleanup used when chunks are rebuilt: stale index entries
    must not survive a chunk rebuild. Failures are logged, not swallowed
    silently into a fake success (callers re-run indexing afterwards)."""
    settings = get_settings()
    try:
        qdrant = get_qdrant_client()
        if qdrant.collection_exists(settings.qdrant_collection_name):
            qdrant.delete(
                collection_name=settings.qdrant_collection_name,
                points_selector=qmodels.FilterSelector(
                    filter=qmodels.Filter(
                        must=[
                            qmodels.FieldCondition(
                                key="document_id",
                                match=qmodels.MatchValue(value=str(document_id)),
                            )
                        ]
                    )
                ),
                wait=True,
            )
    except Exception:  # noqa: BLE001
        logger.exception("Could not clean Qdrant points for document %s", document_id)
    try:
        opensearch = get_opensearch_client()
        if opensearch.indices.exists(index=settings.opensearch_index_name):
            opensearch.delete_by_query(
                index=settings.opensearch_index_name,
                body={"query": {"term": {"document_id": str(document_id)}}},
                refresh=True,
                conflicts="proceed",
            )
    except Exception:  # noqa: BLE001
        logger.exception("Could not clean OpenSearch docs for document %s", document_id)


def _apply_indexing_meta(
    document: Document,
    *,
    status: str,
    error: str | None,
    indexed_chunk_count: int | None = None,
    embedding_model: str | None = None,
    embedding_dimension: int | None = None,
) -> None:
    settings = get_settings()
    meta = dict(document.metadata_json or {})
    indexing: dict[str, Any] = {
        "status": status,
        "qdrant_collection": settings.qdrant_collection_name,
        "opensearch_index": settings.opensearch_index_name,
        "error": error,
    }
    if status == INDEXING_STATUS_SUCCESS:
        indexing.update(
            {
                "indexed_chunk_count": indexed_chunk_count or 0,
                "embedding_model": embedding_model,
                "embedding_dimension": embedding_dimension,
                "completed_at": datetime.now(UTC).isoformat(),
            }
        )
    meta["indexing"] = indexing
    document.metadata_json = meta


def _set_indexing_meta(
    session: Session, document_id: UUID, *, status: str, error: str | None
) -> None:
    try:
        document = session.get(Document, document_id)
        if document is not None:
            _apply_indexing_meta(document, status=status, error=error)
            session.commit()
    except Exception:  # noqa: BLE001 - last-resort marker must not raise
        logger.exception("Could not update indexing status for %s", document_id)
