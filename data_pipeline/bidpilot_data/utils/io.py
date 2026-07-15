from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, TypeVar

import orjson
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_json(path: Path, data: Any) -> None:
    ensure_dir(path.parent)
    if isinstance(data, BaseModel):
        payload = data.model_dump(mode="json")
    else:
        payload = data
    path.write_bytes(orjson.dumps(payload, option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS))


def read_json(path: Path) -> Any:
    return orjson.loads(path.read_bytes())


def append_jsonl(path: Path, record: Any) -> None:
    ensure_dir(path.parent)
    if isinstance(record, BaseModel):
        payload = record.model_dump(mode="json")
    else:
        payload = record
    with path.open("ab") as fh:
        fh.write(orjson.dumps(payload))
        fh.write(b"\n")


def write_jsonl(path: Path, records: Iterable[Any]) -> int:
    ensure_dir(path.parent)
    count = 0
    with path.open("wb") as fh:
        for record in records:
            if isinstance(record, BaseModel):
                payload = record.model_dump(mode="json")
            else:
                payload = record
            fh.write(orjson.dumps(payload))
            fh.write(b"\n")
            count += 1
    return count


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("rb") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            obj = orjson.loads(line)
            if isinstance(obj, dict):
                rows.append(obj)
    return rows


def upsert_jsonl_by_key(path: Path, records: list[BaseModel | dict[str, Any]], key: str) -> dict[str, int]:
    existing = {row.get(key): row for row in read_jsonl(path) if row.get(key)}
    created = 0
    updated = 0
    for record in records:
        payload = record.model_dump(mode="json") if isinstance(record, BaseModel) else dict(record)
        k = payload.get(key)
        if k is None:
            continue
        if k in existing:
            updated += 1
        else:
            created += 1
        existing[k] = payload
    write_jsonl(path, list(existing.values()))
    return {"created": created, "updated": updated, "total": len(existing)}
