"""Shared evidence helpers for grounded RAG and requirement extraction."""

from __future__ import annotations

import re
import unicodedata

_WS_RE = re.compile(r"\s+")
# Leading list markers / clause labels that may be stripped for soft match.
_LEADING_MARK_RE = re.compile(
    r"^(?:"
    r"[（(]?\d+[）).、．]\s*"
    r"|[一二三四五六七八九十百千]+[、．.]\s*"
    r"|[•·\-—]+\s*"
    r"|[(（][a-zA-Z0-9]+[)）]\s*"
    r")"
)
_PUNCT_MAP = str.maketrans(
    {
        "，": ",",
        "。": ".",
        "；": ";",
        "：": ":",
        "！": "!",
        "？": "?",
        "（": "(",
        "）": ")",
        "【": "[",
        "】": "]",
        "“": '"',
        "”": '"',
        "‘": "'",
        "’": "'",
        "、": ",",
        "．": ".",
        "—": "-",
        "－": "-",
        "～": "~",
        "　": " ",
    }
)

# Critical tokens that must not be invented by the model.
_CRITICAL_TOKEN_RE = re.compile(
    r"(?:"
    r"\d{4}[-/年]\d{1,2}[-/月]\d{1,2}日?"
    r"|\d+(?:\.\d+)?%"
    r"|\d+(?:\.\d+)?万元"
    r"|\d+(?:\.\d+)?元"
    r"|\d+(?:\.\d+)?"
    r"|[一二三四五六七八九十百千万亿两]+级"
    r"|特级|一级|二级|三级|四级|甲级|乙级|丙级"
    r"|不得|必须|应当|应|须|禁止|可以|可|宜|不应|不可"
    r")"
)


def normalize_whitespace(text: str) -> str:
    """Collapse all whitespace runs to a single space and strip ends."""
    if not text:
        return ""
    return _WS_RE.sub(" ", text).strip()


def soft_normalize_for_grounding(text: str) -> str:
    """Normalize whitespace/punctuation/fullwidth for contiguous grounding checks.

    Does NOT alter digits, grade words, or obligation/negation semantics.
    Spaces are removed after collapse so “人民币 10 万元” matches “人民币10万元”.
    """
    if not text:
        return ""
    t = unicodedata.normalize("NFKC", text)
    t = t.translate(_PUNCT_MAP)
    t = normalize_whitespace(t)
    # Strip repeated leading list markers (e.g. "1. 1. 条款…").
    prev = None
    while prev != t:
        prev = t
        t = _LEADING_MARK_RE.sub("", t).strip()
    t = t.replace(" ", "")
    return t


def quote_in_content(quote: str, content: str) -> bool:
    """Return True if whitespace-normalized quote is a substring of content."""
    q = normalize_whitespace(quote)
    c = normalize_whitespace(content)
    if not q or not c:
        return False
    return q in c


def extract_critical_tokens(text: str) -> list[str]:
    """Extract numbers, dates, grades, modality words that must be evidence-backed."""
    if not text:
        return []
    return [m.group(0) for m in _CRITICAL_TOKEN_RE.finditer(text)]


def critical_tokens_supported(candidate: str, evidence: str) -> bool:
    """Every critical token in candidate must appear in evidence text."""
    tokens = extract_critical_tokens(candidate)
    if not tokens:
        return True
    # Soft-normalize evidence for half/full-width digit/punct safety.
    hay = soft_normalize_for_grounding(evidence)
    for tok in tokens:
        needle = soft_normalize_for_grounding(tok)
        if needle and needle not in hay:
            return False
    return True


def grounded_requirement_text(normalized: str, chunk_content: str) -> str | None:
    """Return accepted grounded requirement text, or None if unsupported.

    Acceptance rules (deterministic):
    1. Soft-normalized requirement is a contiguous substring of soft-normalized chunk; AND
    2. All critical tokens in the requirement appear in the chunk.

    Allowed transforms are only those encoded in soft_normalize_for_grounding
    (whitespace, full/half-width punctuation, leading list markers).
    Entity / grade / amount / date / modality changes are rejected.
    """
    req = soft_normalize_for_grounding(normalized)
    chunk = soft_normalize_for_grounding(chunk_content)
    if not req or not chunk:
        return None
    if req not in chunk:
        return None
    if not critical_tokens_supported(normalized, chunk_content):
        return None
    # Persist a lightly cleaned form of the model text (whitespace only), not the
    # soft-normalized comparison key (which strips spaces / remaps punctuation).
    return normalize_whitespace(normalized)


def display_title_from_requirement(grounded: str, *, max_len: int = 80) -> str:
    """Non-factual display title truncated from grounded requirement text."""
    text = normalize_whitespace(grounded)
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"
