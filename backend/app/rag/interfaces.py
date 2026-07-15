from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any
from uuid import UUID


@dataclass
class SearchHit:
    chunk_id: UUID | None
    document_id: UUID | None
    score: float
    content: str
    metadata: dict[str, Any]


class DenseSearchPort(ABC):
    """Dense vector search against Qdrant (to be implemented)."""

    @abstractmethod
    def search(self, query: str, *, project_id: UUID, top_k: int = 8) -> list[SearchHit]:
        raise NotImplementedError


class BM25SearchPort(ABC):
    """BM25 search reserved for OpenSearch (not started in this scaffold)."""

    @abstractmethod
    def search(self, query: str, *, project_id: UUID, top_k: int = 8) -> list[SearchHit]:
        raise NotImplementedError


class HybridSearchPort(ABC):
    """Hybrid fusion of dense + BM25 results (future)."""

    @abstractmethod
    def search(self, query: str, *, project_id: UUID, top_k: int = 8) -> list[SearchHit]:
        raise NotImplementedError
