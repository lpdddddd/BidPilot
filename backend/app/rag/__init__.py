"""RAG module stubs.

Dense retrieval will target Qdrant. BM25 / sparse retrieval is reserved for OpenSearch
and is intentionally not wired in this scaffold.
"""

from app.rag.interfaces import BM25SearchPort, DenseSearchPort, HybridSearchPort

__all__ = ["BM25SearchPort", "DenseSearchPort", "HybridSearchPort"]
