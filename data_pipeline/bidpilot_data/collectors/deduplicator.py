from __future__ import annotations

from typing import Any
from urllib.parse import urlparse, urlunparse

from bidpilot_data.utils import content_fingerprint, sha256_bytes


def normalize_url(url: str) -> str:
    parsed = urlparse(url.strip())
    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()
    path = parsed.path.rstrip("/") or "/"
    # Drop tracking fragments; keep query for content identity.
    return urlunparse((scheme, netloc, path, "", parsed.query, ""))


def text_fingerprint(text: str) -> str:
    return content_fingerprint(text)


def bytes_fingerprint(data: bytes) -> str:
    return sha256_bytes(data)


def dedupe_discovery_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Dedupe by URL, project_code+document_type, and optional content hash."""
    out: list[dict[str, Any]] = []
    seen_url: set[str] = set()
    seen_code_type: set[str] = set()
    seen_hash: set[str] = set()
    for row in rows:
        url = normalize_url(str(row.get("source_url") or row.get("url") or ""))
        if url and url in seen_url:
            continue
        code = str(row.get("project_code") or "").strip()
        dtype = str(row.get("document_type") or "").strip()
        code_key = f"{code}|{dtype}" if code and dtype else ""
        if code_key and code_key in seen_code_type:
            continue
        digest = str(row.get("sha256") or row.get("content_hash") or "")
        if digest and digest in seen_hash:
            continue
        if url:
            seen_url.add(url)
        if code_key:
            seen_code_type.add(code_key)
        if digest:
            seen_hash.add(digest)
        out.append(row)
    return out


def is_duplicate_document(
    *,
    sha256: str | None,
    source_url: str | None,
    existing: list[dict[str, Any]],
) -> dict[str, Any] | None:
    norm = normalize_url(source_url) if source_url else ""
    for doc in existing:
        if sha256 and doc.get("sha256") == sha256:
            return doc
        if norm and normalize_url(str(doc.get("source_url") or "")) == norm:
            return doc
    return None
