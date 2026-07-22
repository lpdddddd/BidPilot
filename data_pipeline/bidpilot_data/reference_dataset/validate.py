"""Deterministic validation for reference samples."""

from __future__ import annotations

import re
from typing import Any

import orjson
from pydantic import ValidationError

from bidpilot_data.labeling.evidence import quote_supported_by_chunk
from bidpilot_data.reference_dataset.schema import ReferenceSample
from bidpilot_data.sft.dedup import normalize_user_text
from bidpilot_data.utils import content_fingerprint

# Claims that look definitive / answerable — forbidden for unanswerable samples
_DEFINITIVE_CLAIM_RE = re.compile(
    r"(明确要求|必须提供|应当具备|规定为|截止时间为|预算为|得分标准为|"
    r"废标条件为|投标保证金为|服务期限为|付款比例为|"
    r"according to the document|must provide|is required to)",
    re.IGNORECASE,
)

_ABSTAIN_OK_RE = re.compile(
    r"(无法从|不足以|未提及|未披露|不能确定|证据不足|insufficient|cannot determine|"
    r"not mentioned|no evidence|abstain|无法回答|材料中没有)",
    re.IGNORECASE,
)


def soft_normalize(text: str) -> str:
    return normalize_user_text(text or "")


def _soft_normalize_obj(obj: Any) -> Any:
    if isinstance(obj, str):
        return soft_normalize(obj)
    if isinstance(obj, dict):
        return {str(k): _soft_normalize_obj(v) for k, v in sorted(obj.items(), key=lambda kv: str(kv[0]))}
    if isinstance(obj, list):
        return [_soft_normalize_obj(v) for v in obj]
    return obj


def quote_contiguous_in_text(quote: str, text: str) -> bool:
    """Whitespace-normalized contiguous quote check (strict for citations)."""
    q = "".join((quote or "").split())
    t = "".join((text or "").split())
    if not q:
        return False
    if q in t:
        return True
    # Allow tiny OCR drift via existing helper at high ratio
    return quote_supported_by_chunk(quote, text, min_ratio=0.95)


def sample_content_hash(sample: ReferenceSample | dict[str, Any]) -> str:
    if isinstance(sample, ReferenceSample):
        payload = sample.model_dump(mode="json")
    else:
        payload = sample
    canonical = {
        "task_type": str(payload.get("task_type") or ""),
        "input": _soft_normalize_obj(payload.get("input") or {}),
        "reference_output": _soft_normalize_obj(payload.get("reference_output") or {}),
    }
    blob = orjson.dumps(canonical, option=orjson.OPT_SORT_KEYS)
    return content_fingerprint(blob.decode("utf-8"))


def _answer_text(sample: ReferenceSample) -> str:
    out = sample.reference_output or {}
    for key in ("answer", "summary", "outline", "status", "judgment", "normalized_requirement", "result"):
        if key in out and out[key] is not None:
            return str(out[key])
    return str(out)


def _citation_nonempty(cites: Any) -> bool:
    return bool(
        (cites.chunk_ids or [])
        or (cites.document_ids or [])
        or (cites.quotes or [])
        or (cites.page_numbers or [])
        or (cites.source_urls or [])
    )


def _allows_empty_evidence(parsed: ReferenceSample) -> bool:
    """Unanswerable / matching insufficient_evidence|unknown may omit evidence."""
    if parsed.task_type == "unanswerable":
        return True
    if parsed.task_type == "rag" and not (parsed.reference_output or {}).get("answerable", True):
        return True
    if parsed.task_type == "matching":
        status = str((parsed.reference_output or {}).get("status") or "")
        if status in {"insufficient_evidence", "unknown", "not_applicable"}:
            return True
    return False


def validate_sample(
    sample: ReferenceSample | dict[str, Any],
    *,
    chunk_index: dict[str, dict[str, Any]],
    document_index: dict[str, dict[str, Any]],
    require_evidence_for_answerable: bool = True,
) -> tuple[bool, list[str], ReferenceSample | None]:
    """Validate schema + citation grounding. Returns (ok, messages, parsed_sample)."""
    messages: list[str] = []
    try:
        parsed = sample if isinstance(sample, ReferenceSample) else ReferenceSample.model_validate(sample)
    except ValidationError as exc:
        return False, [f"schema: {exc}"], None

    # document_id exists
    if parsed.document_id and parsed.document_id not in document_index and document_index:
        messages.append(f"missing document_id={parsed.document_id}")

    cites = parsed.citation_metadata

    # --- Citation checks ALWAYS run when citation_metadata is present (independent of evidence) ---
    for cid in cites.chunk_ids:
        if cid and cid not in chunk_index and chunk_index:
            messages.append(f"missing chunk_id={cid}")
    for did in cites.document_ids:
        if did and did not in document_index and document_index:
            messages.append(f"missing citation document_id={did}")

    nonempty_quotes = [q for q in (cites.quotes or []) if q]
    if nonempty_quotes:
        if not cites.chunk_ids:
            messages.append("citation quotes without chunk_ids")
        elif chunk_index:
            for quote in nonempty_quotes:
                grounded = False
                for cid in cites.chunk_ids:
                    ch = chunk_index.get(cid)
                    if ch and quote_contiguous_in_text(quote, ch.get("text") or ""):
                        grounded = True
                        break
                if not grounded:
                    messages.append("citation quote not grounded in cited chunks")

    # Page numbers (if any) should fall within cited chunk page ranges when available
    if cites.page_numbers and cites.chunk_ids and chunk_index:
        for page in cites.page_numbers:
            if page is None:
                continue
            page_ok = False
            any_range = False
            for cid in cites.chunk_ids:
                ch = chunk_index.get(cid)
                if not ch:
                    continue
                ps, pe = ch.get("page_start"), ch.get("page_end")
                if ps is None or pe is None:
                    continue
                any_range = True
                if ps <= page <= pe:
                    page_ok = True
                    break
            if any_range and not page_ok:
                messages.append(f"citation page_number {page} outside cited chunk pages")

    # Evidence item checks
    for ev in parsed.evidence:
        if ev.document_id and ev.document_id not in document_index and document_index:
            messages.append(f"evidence missing document_id={ev.document_id}")
        if ev.chunk_id:
            chunk = chunk_index.get(ev.chunk_id)
            if chunk_index and chunk is None:
                messages.append(f"evidence missing chunk_id={ev.chunk_id}")
            elif chunk is not None:
                text = chunk.get("text") or chunk.get("normalized_text") or ""
                if ev.page_number is not None:
                    ps = chunk.get("page_start")
                    pe = chunk.get("page_end")
                    if ps is not None and pe is not None and not (ps <= ev.page_number <= pe):
                        messages.append(f"page_number {ev.page_number} outside chunk pages {ps}-{pe}")
                if ev.char_start is not None and ev.char_end is not None:
                    if ev.char_end > len(text) or ev.char_start > len(text):
                        messages.append("char range exceeds chunk text length")
                if ev.quote:
                    if not quote_contiguous_in_text(ev.quote, text):
                        messages.append("quote not contiguous in chunk text")

    answerable = parsed.task_type != "unanswerable"
    if parsed.task_type == "rag":
        answerable = bool((parsed.reference_output or {}).get("answerable", True))
    if parsed.task_type == "matching":
        status = str((parsed.reference_output or {}).get("status") or "")
        if status in {"insufficient_evidence", "not_applicable", "unknown"}:
            answerable = False

    allows_empty = _allows_empty_evidence(parsed)

    # Citations present but evidence empty → fail for answerable tasks
    if not parsed.evidence and _citation_nonempty(cites) and not allows_empty:
        messages.append("citations present but evidence empty")

    if require_evidence_for_answerable and answerable and parsed.task_type != "unanswerable":
        has_ev = bool(parsed.evidence) or bool(cites.chunk_ids) or bool(cites.quotes)
        if not has_ev:
            messages.append("answerable sample lacks evidence support")
        if parsed.evidence:
            any_quote = any(e.quote for e in parsed.evidence)
            if not any_quote and parsed.task_type in {"rag", "extraction", "compliance", "drafting"}:
                messages.append("answerable sample evidence missing quotes")

    if parsed.task_type == "unanswerable" or (
        parsed.task_type == "rag" and not (parsed.reference_output or {}).get("answerable", True)
    ):
        text = _answer_text(parsed)
        if _DEFINITIVE_CLAIM_RE.search(text) and not _ABSTAIN_OK_RE.search(text):
            messages.append("unanswerable sample has definitive unsupported claim")
        if (parsed.reference_output or {}).get("answerable") is True:
            messages.append("unanswerable sample marked answerable=true")

    # Drafting disclaimer
    if parsed.task_type == "drafting":
        out = parsed.reference_output or {}
        if not out.get("disclaimer") and not out.get("disclaimer_flag"):
            messages.append("drafting sample missing disclaimer flag")

    ok = not messages
    qc = parsed.quality_checks.model_copy(deep=True)
    qc.schema_ok = True
    qc.ids_ok = not any("missing" in m for m in messages)
    qc.quote_grounded = not any("quote" in m or "grounded" in m or "char range" in m or "page_number" in m for m in messages)
    qc.answerable_supported = not any("lacks evidence" in m or "missing quotes" in m for m in messages)
    qc.unanswerable_ok = not any("definitive" in m or "answerable=true" in m for m in messages)
    qc.messages = list(messages)
    parsed = parsed.model_copy(update={"quality_checks": qc})
    return ok, messages, parsed


def dedupe_samples(samples: list[ReferenceSample]) -> tuple[list[ReferenceSample], list[ReferenceSample]]:
    """Soft-normalized input+output hash dedupe. Returns (kept, rejected_duplicates)."""
    seen: set[str] = set()
    kept: list[ReferenceSample] = []
    rejected: list[ReferenceSample] = []
    for s in samples:
        h = sample_content_hash(s)
        if h in seen:
            qc = s.quality_checks.model_copy(deep=True)
            qc.dedupe_ok = False
            qc.messages = list(qc.messages) + ["duplicate soft hash"]
            rejected.append(s.model_copy(update={"quality_checks": qc}))
            continue
        seen.add(h)
        qc = s.quality_checks.model_copy(deep=True)
        qc.dedupe_ok = True
        kept.append(s.model_copy(update={"quality_checks": qc}))
    return kept, rejected
