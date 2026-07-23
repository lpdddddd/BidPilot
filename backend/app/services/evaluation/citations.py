"""Citation deep-link validation for evaluation case results."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.document import Document, DocumentChunk


def _as_uuid(value: Any) -> UUID | None:
    if value is None:
        return None
    try:
        return UUID(str(value))
    except Exception:
        return None


def extract_raw_citations(snapshot: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Extract citations from the canonical response_snapshot shape.

    Supports both flattened top-level fields and nested ``output`` for
    backwards compatibility with older rows.
    """
    if not snapshot:
        return []
    buckets: list[Any] = []
    nested = snapshot.get("output") if isinstance(snapshot.get("output"), dict) else {}
    for src in (snapshot, nested):
        if not isinstance(src, dict):
            continue
        buckets.append(src.get("citations") or [])
        for key in ("retrieved_chunk_ids", "evidence_chunk_ids"):
            buckets.append([{"chunk_id": str(c)} for c in (src.get(key) or [])])
    out: list[dict[str, Any]] = []
    for raw in buckets:
        if not isinstance(raw, list):
            continue
        for item in raw:
            if isinstance(item, dict):
                out.append(dict(item))
            elif item:
                out.append({"chunk_id": str(item)})
    return out


def validate_citation(
    db: Session,
    *,
    project_id: UUID,
    citation: dict[str, Any],
) -> dict[str, Any]:
    """Validate document/page/chunk belong to the project. Never raises 404 to caller."""
    result = {
        "document_id": citation.get("document_id"),
        "document_title": citation.get("document_title"),
        "document_name": citation.get("document_name") or citation.get("file_name"),
        "file_name": citation.get("file_name"),
        "page": citation.get("page")
        if citation.get("page") is not None
        else citation.get("page_start"),
        "page_start": citation.get("page_start"),
        "section": citation.get("section"),
        "chunk_id": citation.get("chunk_id"),
        "project_id": str(project_id),
        "valid": False,
        "validation_error": None,
        "invalid_reason": None,
        "summary": citation.get("summary"),
        "detail_url": None,
    }
    chunk_id = _as_uuid(citation.get("chunk_id"))
    document_id = _as_uuid(citation.get("document_id"))
    page = result["page"]

    if chunk_id is None and document_id is None:
        result["validation_error"] = "missing_document_or_chunk"
        result["invalid_reason"] = "missing_document_or_chunk"
        return result

    if chunk_id is not None:
        chunk = db.get(DocumentChunk, chunk_id)
        if chunk is None or chunk.project_id != project_id:
            result["validation_error"] = "chunk_not_found_or_forbidden"
            result["invalid_reason"] = "chunk_not_found_or_forbidden"
            return result
        result["chunk_id"] = str(chunk.id)
        result["document_id"] = str(chunk.document_id)
        result["page_start"] = chunk.page_start
        result["page"] = chunk.page_start if page is None else page
        result["section"] = chunk.section
        document_id = chunk.document_id
        if citation.get("document_id"):
            claimed = _as_uuid(citation.get("document_id"))
            if claimed is not None and claimed != chunk.document_id:
                result["validation_error"] = "chunk_document_mismatch"
                result["invalid_reason"] = "chunk_document_mismatch"
                return result
        if page is not None and chunk.page_start is not None and chunk.page_end is not None:
            try:
                page_i = int(page)
            except Exception:
                result["validation_error"] = "invalid_page"
                result["invalid_reason"] = "invalid_page"
                return result
            if page_i < chunk.page_start or page_i > chunk.page_end:
                result["validation_error"] = "page_out_of_range"
                result["invalid_reason"] = "page_out_of_range"
                return result
        doc = db.get(Document, document_id)
        if doc is None or doc.project_id != project_id:
            result["validation_error"] = "document_not_found_or_forbidden"
            result["invalid_reason"] = "document_not_found_or_forbidden"
            return result
        result["file_name"] = getattr(doc, "file_name", None)
        result["document_title"] = result["file_name"]
        result["document_name"] = result["file_name"]
        result["valid"] = True
        result["detail_url"] = f"/projects/{project_id}/documents/{doc.id}?chunkId={chunk.id}" + (
            f"&page={result['page']}" if result["page"] is not None else ""
        )
        return result

    doc = db.get(Document, document_id) if document_id else None
    if doc is None or doc.project_id != project_id:
        result["validation_error"] = "document_not_found_or_forbidden"
        result["invalid_reason"] = "document_not_found_or_forbidden"
        return result
    result["document_id"] = str(doc.id)
    result["file_name"] = getattr(doc, "file_name", None)
    result["document_title"] = result["file_name"]
    result["document_name"] = result["file_name"]
    result["valid"] = True
    result["detail_url"] = f"/projects/{project_id}/documents/{doc.id}"
    return result


def validate_citations_for_result(
    db: Session,
    *,
    project_id: UUID,
    response_snapshot: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for raw in extract_raw_citations(response_snapshot):
        key = f"{raw.get('chunk_id')}|{raw.get('document_id')}|{raw.get('page')}"
        if key in seen:
            continue
        seen.add(key)
        out.append(validate_citation(db, project_id=project_id, citation=raw))
    return out
