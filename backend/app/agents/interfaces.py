from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID


@dataclass
class AgentRequest:
    organization_id: UUID
    project_id: UUID | None
    conversation_id: UUID | None
    user_message: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentResult:
    status: str
    answer: str | None = None
    citations: list[dict[str, Any]] = field(default_factory=list)
    state: dict[str, Any] = field(default_factory=dict)


class AgentPort(ABC):
    """Placeholder for LangGraph-based bidding analysis agents."""

    @abstractmethod
    def run(self, request: AgentRequest) -> AgentResult:
        raise NotImplementedError
