"""In-process background parsing task for uploaded documents.

The task never reuses the request-scoped DB session: it opens its own session
from SESSION_FACTORY (monkeypatchable in tests, defaults to the app-wide
SessionLocal).
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models import Document
from app.models.enums import ParseStatus
from app.services.document_parser import PARSER_NAME, PARSER_VERSION, parse_document
from app.services.storage import StorageError, get_document_storage

logger = logging.getLogger("bidpilot.parse")

SESSION_FACTORY = SessionLocal


def parsed_text_key(document: Document) -> str:
    return f"projects/{document.project_id}/documents/{document.id}/parsed/extracted.txt"


def page_index_key(document: Document) -> str:
    return f"projects/{document.project_id}/documents/{document.id}/parsed/page_index.json"


def run_document_parse(document_id: UUID) -> None:
    session = SESSION_FACTORY()
    try:
        _parse_document(session, document_id)
    except Exception:
        logger.exception("Unexpected error while parsing document %s", document_id)
        session.rollback()
        _mark_failed(session, document_id, "解析任务内部错误")
    finally:
        session.close()


def _parse_document(session: Session, document_id: UUID) -> None:
    document = session.get(Document, document_id)
    if document is None:
        logger.warning("Parse task skipped: document %s no longer exists", document_id)
        return

    document.parse_status = ParseStatus.processing
    session.commit()

    storage = get_document_storage()
    try:
        content = storage.get_bytes(document.storage_key)
    except StorageError as exc:
        _apply_result_meta(document, status=ParseStatus.failed, error=str(exc))
        session.commit()
        return

    extension = document.file_name.rsplit(".", 1)[-1] if "." in document.file_name else ""
    result = parse_document(content, extension)

    text_key: str | None = None
    page_key: str | None = None
    if result.status == ParseStatus.success and result.text:
        text_key = parsed_text_key(document)
        try:
            storage.put_bytes(
                text_key,
                result.text.encode("utf-8"),
                content_type="text/plain; charset=utf-8",
            )
        except StorageError as exc:
            _apply_result_meta(document, status=ParseStatus.failed, error=str(exc))
            session.commit()
            return

        # Real page-to-character mapping exists only for PDFs; other formats
        # have no reliable page notion and get no sidecar.
        if result.page_spans:
            page_key = page_index_key(document)
            sidecar = {
                "source_sha256": document.sha256,
                "generated_by": f"{PARSER_NAME}/{PARSER_VERSION}",
                "pages": [
                    {"page": s.page, "char_start": s.char_start, "char_end": s.char_end}
                    for s in result.page_spans
                ],
            }
            try:
                storage.put_bytes(
                    page_key,
                    json.dumps(sidecar, ensure_ascii=False).encode("utf-8"),
                    content_type="application/json",
                )
            except StorageError as exc:
                _apply_result_meta(document, status=ParseStatus.failed, error=str(exc))
                session.commit()
                return

    _apply_result_meta(
        document,
        status=result.status,
        error=result.error,
        text_key=text_key,
        page_index_key_value=page_key,
        extracted_characters=result.extracted_characters,
        page_count=result.page_count,
    )
    session.commit()

    # A freshly parsed document automatically gets its chunks (re)built.
    if result.status == ParseStatus.success:
        from app.services import chunk_tasks

        chunk_tasks.run_document_chunking(document.id)


def _apply_result_meta(
    document: Document,
    *,
    status: ParseStatus,
    error: str | None = None,
    text_key: str | None = None,
    page_index_key_value: str | None = None,
    extracted_characters: int | None = None,
    page_count: int | None = None,
) -> None:
    document.parse_status = status
    document.is_scanned = status == ParseStatus.ocr_required
    if page_count is not None:
        document.page_count = page_count

    meta = dict(document.metadata_json or {})
    meta.update(
        {
            "parser_name": PARSER_NAME,
            "parser_version": PARSER_VERSION,
            "parsed_at": datetime.now(UTC).isoformat(),
            "source_sha256": document.sha256,
            "extracted_text_storage_key": text_key,
            "page_index_storage_key": page_index_key_value,
            "extracted_characters": extracted_characters,
            "parse_error": error,
        }
    )
    document.metadata_json = meta


def _mark_failed(session: Session, document_id: UUID, reason: str) -> None:
    try:
        document = session.get(Document, document_id)
        if document is not None:
            _apply_result_meta(document, status=ParseStatus.failed, error=reason)
            session.commit()
    except Exception:  # noqa: BLE001 - last-resort marker must not raise
        logger.exception("Could not mark document %s as failed", document_id)
