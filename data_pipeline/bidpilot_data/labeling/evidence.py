from __future__ import annotations

from typing import Any

from bidpilot_data.schemas import EvidenceRecord
from bidpilot_data.utils import content_fingerprint, stable_uuid


def make_evidence(
    *,
    project_id: str,
    document_id: str,
    source_url: str,
    quote: str,
    page_number: int | None = None,
    section_path: str | None = None,
    chunk_id: str | None = None,
) -> EvidenceRecord:
    q = quote.strip()
    digest = content_fingerprint(q)
    evidence_id = str(stable_uuid(f"evidence:{project_id}:{document_id}:{digest}"))
    return EvidenceRecord(
        evidence_id=evidence_id,
        project_id=project_id,
        document_id=document_id,
        chunk_id=chunk_id,
        source_url=source_url,
        page_number=page_number,
        section_path=section_path,
        quote=q,
        content_hash=digest,
    )


def quote_supported_by_chunk(quote: str, chunk_text: str, *, min_ratio: float = 0.9) -> bool:
    q = "".join(quote.split())
    t = "".join(chunk_text.split())
    if not q or not t:
        return False
    if q in t:
        return True
    # Fallback: majority contiguous coverage via simple window check
    if len(q) < 12:
        return q in t
    from rapidfuzz import fuzz

    return fuzz.partial_ratio(q, t) >= int(min_ratio * 100)


def evidence_to_dict(ev: EvidenceRecord) -> dict[str, Any]:
    return ev.model_dump(mode="json")
