"""In-process background chunk building for parsed documents.

Rebuilds are atomic: the new chunk plan is computed first, then old chunks are
deleted and new ones inserted in a single transaction, so a failure keeps the
previous successful chunks intact. No Qdrant, no embeddings, no LLM here;
qdrant_point_id stays null until step 5.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models import Document
from app.models.document import DocumentChunk
from app.models.enums import ParseStatus
from app.services.chunker import (
    CHUNKER_NAME,
    CHUNKER_VERSION,
    ChunkPlanResult,
    PageSpanIn,
    build_chunks,
)
from app.services.storage import StorageError, get_document_storage

logger = logging.getLogger("bidpilot.chunk")

SESSION_FACTORY = SessionLocal

CHUNKING_STATUS_PENDING = "pending"
CHUNKING_STATUS_PROCESSING = "processing"
CHUNKING_STATUS_SUCCESS = "success"
CHUNKING_STATUS_FAILED = "failed"


def run_document_chunking(document_id: UUID) -> None:
    session = SESSION_FACTORY()
    try:
        _chunk_document(session, document_id)
    except Exception:
        logger.exception("Unexpected error while chunking document %s", document_id)
        session.rollback()
        _set_chunking_meta(
            session, document_id, status=CHUNKING_STATUS_FAILED, error="切分任务内部错误"
        )
    finally:
        session.close()


def _chunk_document(session: Session, document_id: UUID) -> None:
    document = session.get(Document, document_id)
    if document is None:
        logger.warning("Chunk task skipped: document %s no longer exists", document_id)
        return
    if document.parse_status != ParseStatus.success:
        _apply_chunking_meta(
            document,
            status=CHUNKING_STATUS_FAILED,
            error=f"文档解析状态为 {document.parse_status.value}，无法切分",
        )
        session.commit()
        return

    meta = document.metadata_json or {}
    text_key = meta.get("extracted_text_storage_key")
    if not isinstance(text_key, str) or not text_key:
        _apply_chunking_meta(
            document, status=CHUNKING_STATUS_FAILED, error="缺少解析产物，无法切分"
        )
        session.commit()
        return

    _apply_chunking_meta(document, status=CHUNKING_STATUS_PROCESSING, error=None)
    session.commit()

    storage = get_document_storage()
    try:
        text = storage.get_bytes(text_key).decode("utf-8")
    except StorageError as exc:
        _apply_chunking_meta(document, status=CHUNKING_STATUS_FAILED, error=str(exc))
        session.commit()
        return

    page_spans = _load_page_spans(storage, meta)

    try:
        plan: ChunkPlanResult = build_chunks(text, page_spans=page_spans)
    except Exception as exc:  # noqa: BLE001 - report honest failure, keep old chunks
        logger.exception("Chunker failed for document %s", document_id)
        _apply_chunking_meta(document, status=CHUNKING_STATUS_FAILED, error=f"切分失败: {exc}")
        session.commit()
        return

    # Atomic swap: delete old chunks + insert new + update status in one
    # transaction. Rollback on any failure keeps the previous state.
    try:
        session.execute(delete(DocumentChunk).where(DocumentChunk.document_id == document.id))
        for planned in plan.chunks:
            session.add(
                DocumentChunk(
                    document_id=document.id,
                    project_id=document.project_id,
                    chunk_index=planned.chunk_index,
                    section=planned.section[:512] if planned.section else None,
                    clause_id=planned.clause_id[:128] if planned.clause_id else None,
                    page_start=planned.page_start,
                    page_end=planned.page_end,
                    content=planned.content,
                    content_hash=planned.content_hash,
                    token_count=planned.token_count,
                    metadata_json={
                        "chunker_name": CHUNKER_NAME,
                        "chunker_version": CHUNKER_VERSION,
                        "tokenizer": plan.tokenizer,
                        "source_sha256": document.sha256,
                        "source_char_start": planned.source_char_start,
                        "source_char_end": planned.source_char_end,
                        "core_char_start": planned.core_char_start,
                        "core_char_end": planned.core_char_end,
                        "overlap_prefix_chars": planned.overlap_prefix_chars,
                        "section_path": planned.section_path,
                        "heading_level": planned.heading_level,
                        "chunk_kind": planned.chunk_kind,
                        "extracted_text_storage_key": text_key,
                    },
                )
            )
        _apply_chunking_meta(
            document,
            status=CHUNKING_STATUS_SUCCESS,
            error=None,
            chunk_count=len(plan.chunks),
            total_tokens=plan.total_tokens,
            section_count=plan.section_count,
            tokenizer=plan.tokenizer,
        )
        session.commit()
    except Exception as exc:
        session.rollback()
        logger.exception("Chunk persistence failed for document %s", document_id)
        _set_chunking_meta(
            session,
            document_id,
            status=CHUNKING_STATUS_FAILED,
            error=f"切分结果写入失败: {exc}",
        )
        return

    # Fresh chunks invalidate any previous vector/BM25 index entries; the
    # index task deletes old entries and writes the new ones.
    _trigger_indexing(document.id)


def _trigger_indexing(document_id: UUID) -> None:
    """Kick off index rebuild after a successful chunk build (patchable in
    tests). Deferred import avoids a circular dependency."""
    from app.services import index_tasks

    index_tasks.run_document_indexing(document_id)


def _load_page_spans(storage: Any, meta: dict[str, Any]) -> list[PageSpanIn] | None:
    key = meta.get("page_index_storage_key")
    if not isinstance(key, str) or not key:
        return None
    try:
        payload = json.loads(storage.get_bytes(key).decode("utf-8"))
        pages = payload.get("pages", [])
        return [
            PageSpanIn(
                page=int(item["page"]),
                char_start=int(item["char_start"]),
                char_end=int(item["char_end"]),
            )
            for item in pages
        ]
    except Exception:  # noqa: BLE001 - a broken sidecar must not fake pages
        logger.warning("Could not load page index %s; page numbers stay null", key)
        return None


def _apply_chunking_meta(
    document: Document,
    *,
    status: str,
    error: str | None,
    chunk_count: int | None = None,
    total_tokens: int | None = None,
    section_count: int | None = None,
    tokenizer: str | None = None,
) -> None:
    meta = dict(document.metadata_json or {})
    chunking: dict[str, Any] = {
        "status": status,
        "chunker_name": CHUNKER_NAME,
        "chunker_version": CHUNKER_VERSION,
        "source_sha256": document.sha256,
        "error": error,
    }
    if status == CHUNKING_STATUS_SUCCESS:
        chunking.update(
            {
                "chunk_count": chunk_count or 0,
                "total_tokens": total_tokens or 0,
                "section_count": section_count or 0,
                "tokenizer": tokenizer,
                "completed_at": datetime.now(UTC).isoformat(),
            }
        )
    meta["chunking"] = chunking
    document.metadata_json = meta


def _set_chunking_meta(
    session: Session, document_id: UUID, *, status: str, error: str | None
) -> None:
    try:
        document = session.get(Document, document_id)
        if document is not None:
            _apply_chunking_meta(document, status=status, error=error)
            session.commit()
    except Exception:  # noqa: BLE001 - last-resort marker must not raise
        logger.exception("Could not update chunking status for %s", document_id)
