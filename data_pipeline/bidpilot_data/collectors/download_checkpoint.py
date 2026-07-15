from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bidpilot_data.utils import CheckpointStore, append_jsonl, ensure_dir, read_jsonl


class DownloadCheckpoint:
    """Breakpoint resume, failure retries, and status recording for collectors."""

    def __init__(self, root: Path) -> None:
        self.root = ensure_dir(root)
        self.store = CheckpointStore(self.root / "collect_official.json")
        self.failures_path = self.root.parent / "discovery_failures.jsonl"
        self.pending_path = self.root.parent / "download_pending.jsonl"

    def done(self, key: str) -> bool:
        return self.store.done(key)

    def mark_done(self, key: str, meta: dict[str, Any] | None = None) -> None:
        self.store.mark_done(key, meta or {})

    def mark_failed(self, key: str, error: str) -> None:
        self.store.failed(key, error)

    def record_discovery_failure(
        self,
        *,
        url: str,
        reason: str,
        province: str | None = None,
        keywords: list[str] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        row = {
            "url": url,
            "reason": reason,
            "province": province,
            "keywords": keywords or [],
            "failed_at": datetime.now(timezone.utc).isoformat(),
            **(extra or {}),
        }
        append_jsonl(self.failures_path, row)

    def record_download_pending(
        self,
        *,
        source_url: str,
        reason: str,
        project_code: str | None = None,
        project_name: str | None = None,
        document_type: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        row = {
            "source_url": source_url,
            "reason": reason,
            "project_code": project_code,
            "project_name": project_name,
            "document_type": document_type,
            "status": "pending_manual",
            "failed_at": datetime.now(timezone.utc).isoformat(),
            **(extra or {}),
        }
        append_jsonl(self.pending_path, row)

    def list_pending(self) -> list[dict[str, Any]]:
        return read_jsonl(self.pending_path)

    def list_failures(self) -> list[dict[str, Any]]:
        return read_jsonl(self.failures_path)
