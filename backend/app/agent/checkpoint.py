"""Custom DB checkpoint store + MemorySaver helpers for the agent loop."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from langgraph.checkpoint.memory import MemorySaver
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.agent import AgentCheckpoint


def new_memory_saver() -> MemorySaver:
    """In-process checkpointer for unit tests / single-process runs."""
    return MemorySaver()


class DbCheckpointStore:
    """Persist agent state blobs keyed by thread_id (== run_id)."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def save(
        self,
        *,
        agent_run_id: UUID,
        thread_id: str,
        node_name: str | None,
        state: dict[str, Any],
        metadata: dict[str, Any] | None = None,
        checkpoint_id: str | None = None,
    ) -> AgentCheckpoint:
        cp_id = checkpoint_id or f"{node_name or 'node'}-{uuid4().hex[:12]}"
        # Serialize via JSON round-trip for safety (UUID → str, etc.).
        blob = json.loads(json.dumps(state, default=str))
        row = AgentCheckpoint(
            agent_run_id=agent_run_id,
            thread_id=thread_id,
            checkpoint_id=cp_id,
            node_name=node_name,
            checkpoint_json=blob,
            metadata_json=metadata or {"saved_at": datetime.now(UTC).isoformat()},
        )
        self.db.add(row)
        self.db.flush()
        return row

    def latest(self, thread_id: str) -> AgentCheckpoint | None:
        return self.db.scalar(
            select(AgentCheckpoint)
            .where(AgentCheckpoint.thread_id == thread_id)
            .order_by(AgentCheckpoint.created_at.desc())
            .limit(1)
        )

    def load_state(self, thread_id: str) -> dict[str, Any] | None:
        row = self.latest(thread_id)
        if row is None:
            return None
        return dict(row.checkpoint_json or {})

    def list_for_run(self, agent_run_id: UUID) -> list[AgentCheckpoint]:
        return list(
            self.db.scalars(
                select(AgentCheckpoint)
                .where(AgentCheckpoint.agent_run_id == agent_run_id)
                .order_by(AgentCheckpoint.created_at.asc())
            ).all()
        )
