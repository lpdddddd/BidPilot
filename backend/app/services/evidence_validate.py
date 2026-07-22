"""Shared evidence helpers for grounded RAG and requirement extraction."""

from __future__ import annotations

import re

_WS_RE = re.compile(r"\s+")


def normalize_whitespace(text: str) -> str:
    """Collapse all whitespace runs to a single space and strip ends."""
    if not text:
        return ""
    return _WS_RE.sub(" ", text).strip()


def quote_in_content(quote: str, content: str) -> bool:
    """Return True if whitespace-normalized quote is a substring of content."""
    q = normalize_whitespace(quote)
    c = normalize_whitespace(content)
    if not q or not c:
        return False
    return q in c
