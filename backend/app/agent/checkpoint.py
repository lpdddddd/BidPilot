"""Custom DB checkpoint store + MemorySaver helpers for the agent loop."""

from __future__ import annotations

import base64
import json
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from langgraph.checkpoint.memory import MemorySaver
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.agent import AgentCheckpoint


def new_memory_saver() -> MemorySaver:
    """In-process checkpointer for unit tests / single-process runs."""
    return MemorySaver()


def _json_safe(obj: Any) -> Any:
    """Convert MemorySaver internals into JSON-serializable form when possible."""
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, bytes):
        return {"__bytes_b64__": base64.b64encode(obj).decode("ascii")}
    if isinstance(obj, tuple):
        return {"__tuple__": [_json_safe(x) for x in obj]}
    if isinstance(obj, set):
        return {"__set__": [_json_safe(x) for x in obj]}
    if isinstance(obj, list):
        return [_json_safe(x) for x in obj]
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            key = k if isinstance(k, str) else json.dumps(_json_safe(k), ensure_ascii=False)
            out[key] = _json_safe(v)
        return out
    if isinstance(obj, defaultdict):
        return _json_safe(dict(obj))
    # Fallback: skip non-serializable leaf
    raise TypeError(f"not json-safe: {type(obj)!r}")


def _from_json_safe(obj: Any) -> Any:
    if isinstance(obj, dict):
        if set(obj.keys()) == {"__bytes_b64__"}:
            return base64.b64decode(obj["__bytes_b64__"])
        if set(obj.keys()) == {"__tuple__"}:
            return tuple(_from_json_safe(x) for x in obj["__tuple__"])
        if set(obj.keys()) == {"__set__"}:
            return {_from_json_safe(x) for x in obj["__set__"]}
        out: dict[Any, Any] = {}
        for k, v in obj.items():
            try:
                key: Any = json.loads(k)
                key = _from_json_safe(key)
            except (json.JSONDecodeError, TypeError):
                key = k
            out[key] = _from_json_safe(v)
        return out
    if isinstance(obj, list):
        return [_from_json_safe(x) for x in obj]
    return obj


# Soft cap for full MemorySaver JSONB dumps. Larger graphs fall back to a
# compact observational summary; ``completed_nodes`` remains the durable resume path.
_LG_MEMORY_FULL_MAX_BYTES = 512_000


def _compact_memory_summary(memory: MemorySaver) -> dict[str, Any]:
    storage_keys = list(getattr(memory, "storage", {}) or {})
    writes_keys = list(getattr(memory, "writes", {}) or {})
    blob_count = len(getattr(memory, "blobs", {}) or {})
    sample: dict[str, Any] = {}
    for tid in storage_keys[:3]:
        ns_map = memory.storage.get(tid) or {}
        sample[str(tid)] = {
            str(ns): list(cps.keys())[:5]
            for ns, cps in list(ns_map.items())[:3]
            if isinstance(cps, dict)
        }
    return {
        "version": 1,
        "mode": "compact",
        "thread_ids": [str(k) for k in storage_keys[:20]],
        "writes_keys": [str(k) for k in writes_keys[:20]],
        "blob_count": blob_count,
        "storage_sample": sample,
    }


def serialize_memory_saver(memory: MemorySaver) -> dict[str, Any] | None:
    """Serialize MemorySaver for checkpoint persistence.

    Prefer a full dump (``mode=full``) when JSON-safe size is under
    ``_LG_MEMORY_FULL_MAX_BYTES`` so resume can ``stream(None)`` from the
    checkpointer. Oversized / non-serializable blobs fall back to a compact
    observational summary — durable resume then relies on ``completed_nodes``.
    """
    try:
        full = {
            "version": 2,
            "mode": "full",
            "storage": _json_safe(getattr(memory, "storage", {}) or {}),
            "writes": _json_safe(dict(getattr(memory, "writes", {}) or {})),
            "blobs": _json_safe(dict(getattr(memory, "blobs", {}) or {})),
        }
        encoded = json.dumps(full, ensure_ascii=False, default=str)
        if len(encoded.encode("utf-8")) <= _LG_MEMORY_FULL_MAX_BYTES:
            return full
    except Exception:  # noqa: BLE001
        pass
    try:
        return _compact_memory_summary(memory)
    except Exception:  # noqa: BLE001
        return None


def lg_memory_is_full(payload: dict[str, Any] | None) -> bool:
    """True when payload carries restorable MemorySaver channel bytes."""
    if not payload or not isinstance(payload, dict):
        return False
    if payload.get("mode") == "compact":
        return False
    if payload.get("version") == 1 and "storage_sample" in payload:
        return False
    storage = payload.get("storage")
    return isinstance(storage, dict) and bool(storage)


def restore_memory_saver(payload: dict[str, Any] | None) -> MemorySaver:
    """Restore a MemorySaver when a full dump is present; else empty saver.

    Compact summaries cannot rebuild channel bytes — callers should rely on
    ``completed_nodes`` skip while streaming from START.
    """
    memory = MemorySaver()
    if not payload or not isinstance(payload, dict):
        return memory
    if not lg_memory_is_full(payload):
        return memory
    try:
        storage = _from_json_safe(payload.get("storage") or {})
        writes = _from_json_safe(payload.get("writes") or {})
        blobs = _from_json_safe(payload.get("blobs") or {})
        if isinstance(storage, dict):
            for tid, ns_map in storage.items():
                if not isinstance(ns_map, dict):
                    continue
                for ns, cps in ns_map.items():
                    memory.storage[tid][ns].update(cps if isinstance(cps, dict) else {})
        if isinstance(writes, dict):
            memory.writes.update(writes)
        if isinstance(blobs, dict):
            memory.blobs.update(blobs)
    except Exception:  # noqa: BLE001
        return MemorySaver()
    return memory


class DbCheckpointStore:
    """Persist agent state blobs keyed by thread_id (== run_id)."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def _next_seq(self, thread_id: str) -> int:
        # created_at ties within a transaction (PG now() = tx start); use a
        # monotonic seq so latest() is deterministic.
        count = self.db.scalar(
            select(func.count())
            .select_from(AgentCheckpoint)
            .where(AgentCheckpoint.thread_id == thread_id)
        )
        return int(count or 0) + 1

    def save(
        self,
        *,
        agent_run_id: UUID,
        thread_id: str,
        node_name: str | None,
        state: dict[str, Any],
        metadata: dict[str, Any] | None = None,
        checkpoint_id: str | None = None,
        next_node: str | None = None,
        lg_memory: dict[str, Any] | None = None,
    ) -> AgentCheckpoint:
        seq = self._next_seq(thread_id)
        cp_id = checkpoint_id or f"{node_name or 'node'}-{seq:05d}-{uuid4().hex[:8]}"
        # Serialize via JSON round-trip for safety (UUID → str, etc.).
        # Strip ephemeral lg_memory from state if present.
        state_clean = {k: v for k, v in state.items() if k != "lg_memory"}
        blob = json.loads(json.dumps(state_clean, default=str))
        meta: dict[str, Any] = {
            "saved_at": datetime.now(UTC).isoformat(),
            "updated_at": blob.get("updated_at"),
            "current_node": blob.get("current_node") or node_name,
            "next_node": next_node,
            "completed_nodes": list(blob.get("completed_nodes") or []),
            "retry_counts": dict(blob.get("retry_counts") or {}),
            "compliance_run_id": blob.get("compliance_run_id"),
            "draft_ids": list(blob.get("draft_ids") or []),
            "checkpoint_id": cp_id,
            "checkpoint_seq": seq,
        }
        if metadata:
            meta.update(metadata)
            meta["checkpoint_seq"] = seq
        if lg_memory is not None:
            meta["lg_memory"] = lg_memory
            blob["lg_memory"] = lg_memory
        row = AgentCheckpoint(
            agent_run_id=agent_run_id,
            thread_id=thread_id,
            checkpoint_id=cp_id,
            node_name=node_name,
            checkpoint_json=blob,
            metadata_json=meta,
        )
        self.db.add(row)
        self.db.flush()
        return row

    def latest(self, thread_id: str) -> AgentCheckpoint | None:
        rows = list(
            self.db.scalars(
                select(AgentCheckpoint).where(AgentCheckpoint.thread_id == thread_id)
            ).all()
        )
        if not rows:
            return None

        def _seq(row: AgentCheckpoint) -> int:
            meta = row.metadata_json or {}
            try:
                return int(meta.get("checkpoint_seq") or 0)
            except (TypeError, ValueError):
                return 0

        # created_at is identical within a transaction; break ties with seq.
        return max(rows, key=lambda r: (_seq(r), r.created_at or datetime.min.replace(tzinfo=UTC)))

    def load_state(self, thread_id: str) -> dict[str, Any] | None:
        row = self.latest(thread_id)
        if row is None:
            return None
        blob = dict(row.checkpoint_json or {})
        blob.pop("lg_memory", None)
        return blob

    def load_lg_memory(self, thread_id: str) -> dict[str, Any] | None:
        row = self.latest(thread_id)
        if row is None:
            return None
        meta = row.metadata_json or {}
        if isinstance(meta.get("lg_memory"), dict):
            return meta["lg_memory"]
        blob = row.checkpoint_json or {}
        if isinstance(blob.get("lg_memory"), dict):
            return blob["lg_memory"]
        return None

    def list_for_run(self, agent_run_id: UUID) -> list[AgentCheckpoint]:
        rows = list(
            self.db.scalars(
                select(AgentCheckpoint).where(AgentCheckpoint.agent_run_id == agent_run_id)
            ).all()
        )

        def _seq(row: AgentCheckpoint) -> int:
            meta = row.metadata_json or {}
            try:
                return int(meta.get("checkpoint_seq") or 0)
            except (TypeError, ValueError):
                return 0

        return sorted(
            rows, key=lambda r: (_seq(r), r.created_at or datetime.min.replace(tzinfo=UTC))
        )
