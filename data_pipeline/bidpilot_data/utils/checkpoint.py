from __future__ import annotations

from pathlib import Path
from typing import Any

from bidpilot_data.utils.io import ensure_dir, read_json, write_json


class CheckpointStore:
    """Simple JSON checkpoint for resume / idempotent pipelines."""

    def __init__(self, path: Path) -> None:
        self.path = path
        ensure_dir(path.parent)
        self._data: dict[str, Any] = {}
        if path.exists():
            loaded = read_json(path)
            if isinstance(loaded, dict):
                self._data = loaded

    def done(self, key: str) -> bool:
        return bool(self._data.get("done", {}).get(key))

    def mark_done(self, key: str, meta: dict[str, Any] | None = None) -> None:
        done = self._data.setdefault("done", {})
        done[key] = meta or {"ok": True}
        self.save()

    def failed(self, key: str, error: str) -> None:
        fails = self._data.setdefault("failed", {})
        fails[key] = {"error": error}
        self.save()

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value
        self.save()

    def save(self) -> None:
        write_json(self.path, self._data)
