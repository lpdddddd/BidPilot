"""Cross-encoder reranking service (real model, loaded once per process).

Scores (query, chunk) pairs with a cross-encoder such as BAAI/bge-reranker-base.
If the model is unavailable the caller gets an explicit error; RRF scores are
never passed off as rerank scores.
"""

from __future__ import annotations

import logging
import threading

from app.core.config import get_settings

logger = logging.getLogger("bidpilot.reranker")


class RerankerUnavailableError(RuntimeError):
    """Raised when the cross-encoder model cannot be loaded or used."""


class RerankerService:
    def __init__(self, model_name: str) -> None:
        self.model_name = model_name
        self._model = None
        self._load_error: str | None = None
        self._lock = threading.Lock()

    def _ensure_model(self):
        if self._model is not None:
            return self._model
        with self._lock:
            if self._model is not None:
                return self._model
            if self._load_error is not None:
                raise RerankerUnavailableError(self._load_error)
            try:
                import torch
                from sentence_transformers import CrossEncoder

                device = "cuda" if torch.cuda.is_available() else "cpu"
                logger.info("Loading reranker model %s on %s", self.model_name, device)
                self._model = CrossEncoder(self.model_name, device=device)
            except Exception as exc:  # noqa: BLE001 - report an honest failure
                self._load_error = f"Reranker 模型 {self.model_name} 加载失败: {exc}"
                logger.exception("Could not load reranker model %s", self.model_name)
                raise RerankerUnavailableError(self._load_error) from exc
        return self._model

    def score(self, query: str, passages: list[str]) -> list[float]:
        """Real cross-encoder scores for (query, passage) pairs."""
        model = self._ensure_model()
        pairs = [(query, passage) for passage in passages]
        scores = model.predict(pairs, show_progress_bar=False)
        return [float(score) for score in scores]


_service: RerankerService | None = None
_service_lock = threading.Lock()


def get_reranker_service() -> RerankerService:
    global _service
    if _service is None:
        with _service_lock:
            if _service is None:
                _service = RerankerService(model_name=get_settings().reranker_model_name)
    return _service
