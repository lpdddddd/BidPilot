"""Unified safe error summaries for Agent persistence, API, and SSE."""

from __future__ import annotations

import re
from typing import Any

# Patterns that must never appear in persisted or client-facing text.
_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)(api[_-]?key|token|secret|password|authorization)\s*[:=]\s*\S+"),
    re.compile(r"(?i)bearer\s+[a-z0-9\-._~+/]+=*"),
    re.compile(r"(?i)(cookie|set-cookie)\s*[:=]\s*\S+"),
    re.compile(r"(?i)postgres(?:ql)?(\+psycopg)?://\S+"),
    re.compile(r"(?i)mysql://\S+"),
    re.compile(r"(?i)mongodb(\+srv)?://\S+"),
    re.compile(r"(?i)redis://\S+"),
    re.compile(r"(?i)-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
    # Absolute paths (Unix / Windows drive)
    re.compile(r"(?i)(?:^|[\s\"'=])(/[\w.-]+)+/?"),
    re.compile(r"(?i)(?:^|[\s\"'=])([A-Z]:\\[\w.\\ -]+)"),
)


def safe_error_summary(
    message: Any = None,
    *,
    error_type: str | None = None,
    error_code: str | None = None,
    max_len: int = 400,
) -> str:
    """Build a truncated, redacted summary safe for DB / API / SSE / state.

    Never includes full prompts, tool args, secrets, connection strings,
    absolute paths, or tracebacks.
    """
    if isinstance(message, BaseException):
        error_type = error_type or type(message).__name__
        text = str(message)
    else:
        text = str(message or "").strip()

    if "Traceback" in text:
        text = text.split("Traceback", 1)[0].strip()
    # Drop multi-line stacks
    if "\n" in text:
        text = " ".join(line.strip() for line in text.splitlines() if line.strip())[:max_len]

    for pat in _SECRET_PATTERNS:
        text = pat.sub("[REDACTED]", text)

    # Aggressive token / credential scrubbing after regex pass.
    text = re.sub(r"(?i)\bBearer\s+\S+", "[REDACTED]", text)
    text = re.sub(r"(?i)\bsk-[A-Za-z0-9\-_]+\b", "[REDACTED]", text)
    text = re.sub(r"(?i)\btok-[A-Za-z0-9\-_]+\b", "[REDACTED]", text)
    text = re.sub(r"(?i)super-secret-\S+", "[REDACTED]", text)
    text = re.sub(
        r"(?i)(['\"])?(api_key|token|password|secret)\1?\s*[:=]\s*\S+",
        "[REDACTED]",
        text,
    )

    lowered = text.lower()
    for marker in (
        "authorization:",
        "api_key",
        "postgresql://",
        "postgresql+psycopg://",
        "private_key",
        "-----begin",
    ):
        if marker in lowered:
            text = re.sub(re.escape(marker) + r"\S*", "[REDACTED]", text, flags=re.I)
            lowered = text.lower()

    # Drop residual absolute path fragments.
    text = re.sub(r"(?i)(/[\w.-]+){2,}", "[REDACTED]", text)

    text = " ".join(text.split()).strip()
    if len(text) > max_len:
        text = text[: max_len - 1] + "…"

    parts: list[str] = []
    if error_code:
        parts.append(str(error_code)[:64])
    if error_type:
        parts.append(str(error_type)[:128])
    if text and text not in parts:
        parts.append(text)
    return ": ".join(parts) if parts else "agent error"


def safe_text(value: str | None, *, limit: int = 500) -> str | None:
    """General safe truncation for non-error summaries."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return safe_error_summary(text, max_len=limit) or None


class EventPersistError(RuntimeError):
    """Raised when a required timeline event could not be committed.

    Callers must not invoke the real tool / continue node business work after
    a failed ``*_started`` persist. After a failed ``*_completed`` persist the
    tool body must not be re-executed.
    """

    def __init__(
        self,
        message: str = "event persistence failed",
        *,
        code: str = "event_persist_failed",
    ):
        super().__init__(message)
        self.code = code
