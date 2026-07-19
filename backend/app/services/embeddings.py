"""Dense embedding service backed by sentence-transformers.

The model is loaded once per process (singleton) and shared across requests.
Embeddings are L2-normalized so cosine similarity can be computed with plain
dot products in Qdrant. Vectors live only in Qdrant, never in PostgreSQL.
"""

from __future__ import annotations

import logging
import threading

from app.core.config import get_settings

logger = logging.getLogger("bidpilot.embeddings")


class EmbeddingUnavailableError(RuntimeError):
    """Raised when the embedding model cannot be loaded or used."""


class EmbeddingService:
    def __init__(self, model_name: str, batch_size: int, query_prefix: str) -> None:
        self.model_name = model_name
        self.batch_size = batch_size
        self.query_prefix = query_prefix
        self._model = None
        self._dimension: int | None = None
        self._load_error: str | None = None
        self._lock = threading.Lock()

    def _ensure_model(self):
        if self._model is not None:
            return self._model
        with self._lock:
            if self._model is not None:
                return self._model
            if self._load_error is not None:
                raise EmbeddingUnavailableError(self._load_error)
            try:
                import torch
                from sentence_transformers import SentenceTransformer

                device = "cuda" if torch.cuda.is_available() else "cpu"
                logger.info("Loading embedding model %s on %s", self.model_name, device)
                self._model = SentenceTransformer(self.model_name, device=device)
                # sentence-transformers >= 5.6 renamed the dimension getter.
                getter = getattr(self._model, "get_embedding_dimension", None)
                if getter is None:
                    getter = self._model.get_sentence_embedding_dimension
                self._dimension = int(getter())
            except Exception as exc:  # noqa: BLE001 - report an honest failure
                self._load_error = f"Embedding 模型 {self.model_name} 加载失败: {exc}"
                logger.exception("Could not load embedding model %s", self.model_name)
                raise EmbeddingUnavailableError(self._load_error) from exc
        return self._model

    @property
    def dimension(self) -> int:
        self._ensure_model()
        assert self._dimension is not None
        return self._dimension

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        model = self._ensure_model()
        vectors = model.encode(
            texts,
            batch_size=self.batch_size,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return [vector.tolist() for vector in vectors]

    def embed_query(self, query: str) -> list[float]:
        model = self._ensure_model()
        # bge models expect an instruction prefix on short retrieval queries;
        # documents are encoded without it (same model, documented asymmetry).
        text = f"{self.query_prefix}{query}" if self.query_prefix else query
        vector = model.encode([text], normalize_embeddings=True, show_progress_bar=False)[0]
        return [float(x) for x in vector.tolist()]


_service: EmbeddingService | None = None
_service_lock = threading.Lock()


def get_embedding_service() -> EmbeddingService:
    global _service
    if _service is None:
        with _service_lock:
            if _service is None:
                settings = get_settings()
                _service = EmbeddingService(
                    model_name=settings.embedding_model_name,
                    batch_size=settings.embedding_batch_size,
                    query_prefix=settings.embedding_query_prefix,
                )
    return _service
