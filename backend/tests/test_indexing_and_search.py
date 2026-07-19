from __future__ import annotations

import hashlib
import io
import math
from typing import Any

import pytest
from app.services import chunk_tasks, document_tasks, index_tasks, retrieval
from app.services import document as document_service
from app.services.embeddings import EmbeddingUnavailableError
from app.services.reranker import RerankerUnavailableError
from app.services.retrieval import rrf_fuse
from sqlalchemy.orm import sessionmaker

from tests.test_document_upload import FakeStorage

STRUCTURED_TXT = """第一章 招标公告

一、项目概况
本项目为智慧园区综合管理平台采购项目，预算金额为人民币叁佰万元整。

二、投标人资格要求
（一）具有独立承担民事责任的能力，持有有效的营业执照。
（二）具有良好的商业信誉和健全的财务会计制度。

第二章 投标人须知

第一条 投标文件的组成
投标文件由商务文件、技术文件和资格证明文件三部分组成。

第二条 投标报价
投标报价应包含完成本项目全部工作内容所需的一切费用。
"""


# --------------------------------------------------------------------- fakes


class FakeEmbeddingService:
    """Deterministic tiny embeddings; same scheme for docs and queries."""

    model_name = "fake-embedder"
    query_prefix = ""

    @property
    def dimension(self) -> int:
        return 8

    def _vector(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        raw = [b / 255.0 + 0.01 for b in digest[:8]]
        norm = math.sqrt(sum(x * x for x in raw))
        return [x / norm for x in raw]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]

    def embed_query(self, query: str) -> list[float]:
        return self._vector(query)


class BrokenEmbeddingService(FakeEmbeddingService):
    def embed_query(self, query: str) -> list[float]:
        raise EmbeddingUnavailableError("Embedding 模型不可用（测试）")

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        raise EmbeddingUnavailableError("Embedding 模型不可用（测试）")


class FakeRerankerService:
    """Deterministic score derived from character overlap with the query."""

    model_name = "fake-reranker"

    def __init__(self) -> None:
        self.calls: list[tuple[str, list[str]]] = []

    def score(self, query: str, passages: list[str]) -> list[float]:
        self.calls.append((query, list(passages)))
        query_chars = set(query)
        return [len(query_chars & set(passage)) / (len(query_chars) or 1) for passage in passages]


class BrokenRerankerService:
    def score(self, query: str, passages: list[str]) -> list[float]:
        raise RerankerUnavailableError("Reranker 模型不可用（测试）")


class _FakePoint:
    def __init__(self, score: float, payload: dict[str, Any]) -> None:
        self.score = score
        self.payload = payload


class _FakeQueryResponse:
    def __init__(self, points: list[_FakePoint]) -> None:
        self.points = points


class FakeQdrant:
    def __init__(self) -> None:
        self.collections: set[str] = set()
        self.points: dict[str, tuple[list[float], dict[str, Any]]] = {}
        self.deleted_document_ids: list[str] = []
        self.fail_query = False

    def collection_exists(self, name: str) -> bool:
        return name in self.collections

    def create_collection(self, collection_name: str, vectors_config: Any) -> None:
        self.collections.add(collection_name)

    def create_payload_index(self, **kwargs: Any) -> None:
        pass

    @staticmethod
    def _match_value(condition: Any) -> tuple[str, Any]:
        key = condition.key
        match = condition.match
        value = getattr(match, "value", None)
        if value is None:
            value = getattr(match, "any", None)
        return key, value

    def delete(self, collection_name: str, points_selector: Any, wait: bool = True) -> None:
        conditions = points_selector.filter.must
        for condition in conditions:
            key, value = self._match_value(condition)
            if key == "document_id":
                self.deleted_document_ids.append(value)
                self.points = {
                    pid: (vec, payload)
                    for pid, (vec, payload) in self.points.items()
                    if payload.get("document_id") != value
                }

    def upsert(self, collection_name: str, points: list[Any], wait: bool = True) -> None:
        for point in points:
            self.points[str(point.id)] = (point.vector, point.payload)

    def query_points(
        self,
        collection_name: str,
        query: list[float],
        query_filter: Any,
        limit: int,
        with_payload: bool,
    ) -> _FakeQueryResponse:
        if self.fail_query:
            raise ConnectionError("qdrant down (测试)")
        filters: dict[str, Any] = {}
        for condition in query_filter.must:
            key, value = self._match_value(condition)
            filters[key] = value
        hits = []
        for vector, payload in self.points.values():
            skip = False
            for key, value in filters.items():
                target = payload.get(key)
                if isinstance(value, list):
                    if target not in value:
                        skip = True
                elif target != value:
                    skip = True
            if skip:
                continue
            score = sum(a * b for a, b in zip(vector, query, strict=True))
            hits.append(_FakePoint(score=score, payload=payload))
        hits.sort(key=lambda h: -h.score)
        return _FakeQueryResponse(points=hits[:limit])


class _FakeIndices:
    def __init__(self, parent: FakeOpenSearch) -> None:
        self.parent = parent

    def exists(self, index: str) -> bool:
        return index in self.parent.indexes

    def create(self, index: str, body: dict[str, Any]) -> None:
        self.parent.indexes[index] = body


class FakeOpenSearch:
    def __init__(self) -> None:
        self.indexes: dict[str, dict[str, Any]] = {}
        self.docs: dict[str, dict[str, Any]] = {}
        self.deleted_document_ids: list[str] = []
        self.fail_search = False
        self.indices = _FakeIndices(self)

    def delete_by_query(self, index: str, body: dict[str, Any], **kwargs: Any) -> None:
        document_id = body["query"]["term"]["document_id"]
        self.deleted_document_ids.append(document_id)
        self.docs = {
            doc_id: source
            for doc_id, source in self.docs.items()
            if source.get("document_id") != document_id
        }

    def bulk(self, body: list[dict[str, Any]], refresh: bool = False) -> dict[str, Any]:
        for action, source in zip(body[::2], body[1::2], strict=True):
            doc_id = action["index"]["_id"]
            self.docs[doc_id] = source
        return {"errors": False, "items": []}

    def search(self, index: str, body: dict[str, Any]) -> dict[str, Any]:
        if self.fail_search:
            raise ConnectionError("opensearch down (测试)")
        query = body["query"]["bool"]["must"][0]["multi_match"]["query"]
        filters = body["query"]["bool"]["filter"]
        hits = []
        for doc_id, source in self.docs.items():
            skip = False
            for f in filters:
                if "term" in f:
                    ((key, value),) = f["term"].items()
                    if source.get(key) != value:
                        skip = True
                elif "terms" in f:
                    ((key, values),) = f["terms"].items()
                    if source.get(key) not in values:
                        skip = True
            if skip:
                continue
            content = str(source.get("content") or "")
            score = sum(1.0 for ch in set(query) if ch in content)
            if score > 0:
                hits.append({"_id": doc_id, "_score": score, "_source": source})
        hits.sort(key=lambda h: -h["_score"])
        size = body.get("size", 10)
        return {"hits": {"hits": hits[:size]}}


# ------------------------------------------------------------------ fixtures


@pytest.fixture()
def storage(monkeypatch) -> FakeStorage:
    fake = FakeStorage()
    monkeypatch.setattr(document_service, "get_document_storage", lambda: fake)
    monkeypatch.setattr(document_tasks, "get_document_storage", lambda: fake)
    monkeypatch.setattr(chunk_tasks, "get_document_storage", lambda: fake)
    return fake


@pytest.fixture()
def task_session_factory(monkeypatch, engine):
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    monkeypatch.setattr(document_tasks, "SESSION_FACTORY", factory)
    monkeypatch.setattr(chunk_tasks, "SESSION_FACTORY", factory)
    monkeypatch.setattr(index_tasks, "SESSION_FACTORY", factory)
    return factory


@pytest.fixture()
def vector_stack(monkeypatch):
    """Fake Qdrant/OpenSearch/embedder/reranker wired into all modules."""
    qdrant = FakeQdrant()
    opensearch = FakeOpenSearch()
    embedder = FakeEmbeddingService()
    reranker = FakeRerankerService()
    monkeypatch.setattr(index_tasks, "get_qdrant_client", lambda: qdrant)
    monkeypatch.setattr(index_tasks, "get_opensearch_client", lambda: opensearch)
    monkeypatch.setattr(index_tasks, "get_embedding_service", lambda: embedder)
    monkeypatch.setattr(retrieval, "get_qdrant_client", lambda: qdrant)
    monkeypatch.setattr(retrieval, "get_opensearch_client", lambda: opensearch)
    monkeypatch.setattr(retrieval, "get_embedding_service", lambda: embedder)
    monkeypatch.setattr(retrieval, "get_reranker_service", lambda: reranker)
    return {
        "qdrant": qdrant,
        "opensearch": opensearch,
        "embedder": embedder,
        "reranker": reranker,
    }


@pytest.fixture()
def project_id(client) -> str:
    response = client.post(
        "/api/v1/projects",
        json={"project_code": "IDX-001", "project_name": "索引测试项目"},
    )
    assert response.status_code == 201
    return response.json()["id"]


def _upload_and_chunk(client, project_id: str, name: str = "tender.txt") -> str:
    response = client.post(
        f"/api/v1/projects/{project_id}/documents/upload",
        files={"file": (name, io.BytesIO(STRUCTURED_TXT.encode("utf-8")), "text/plain")},
    )
    assert response.status_code == 201
    doc_id = response.json()["id"]
    detail = client.get(f"/api/v1/projects/{project_id}/documents/{doc_id}").json()
    assert detail["metadata_json"]["chunking"]["status"] == "success"
    return doc_id


def _index_document(client, project_id: str, doc_id: str) -> None:
    response = client.post(f"/api/v1/projects/{project_id}/documents/{doc_id}/index")
    assert response.status_code == 200


# --------------------------------------------------------------------- tests


def test_index_builds_qdrant_and_opensearch_entries(
    client, storage, task_session_factory, vector_stack, project_id
):
    doc_id = _upload_and_chunk(client, project_id)
    _index_document(client, project_id, doc_id)

    qdrant = vector_stack["qdrant"]
    opensearch = vector_stack["opensearch"]
    assert qdrant.points, "expected points in Qdrant"
    for _vector, payload in qdrant.points.values():
        assert payload["project_id"] == project_id
        assert payload["document_id"] == doc_id
        assert payload["chunk_id"]
        assert payload["content"]
        assert payload["content_hash"]
        assert payload["source_sha256"]
        assert payload["chunker_version"]
        assert payload["embedding_model"] == "fake-embedder"
        assert payload["indexed_at"]

    # OpenSearch uses the stable chunk_id as _id and stores no vectors.
    listing = client.get(
        f"/api/v1/projects/{project_id}/documents/{doc_id}/chunks", params={"limit": 200}
    ).json()
    chunk_ids = {item["id"] for item in listing["items"]}
    assert set(opensearch.docs.keys()) == chunk_ids
    for source in opensearch.docs.values():
        assert "vector" not in source and "embedding" not in source

    summary = client.get(f"/api/v1/projects/{project_id}/documents/{doc_id}/index-summary").json()
    assert summary["status"] == "success"
    assert summary["indexed_chunk_count"] == listing["total"]
    assert summary["embedding_model"] == "fake-embedder"
    assert summary["embedding_dimension"] == 8


def test_reindex_cleans_old_entries(
    client, storage, task_session_factory, vector_stack, project_id
):
    doc_id = _upload_and_chunk(client, project_id)
    _index_document(client, project_id, doc_id)
    qdrant = vector_stack["qdrant"]
    opensearch = vector_stack["opensearch"]
    first_points = set(qdrant.points.keys())
    first_docs = set(opensearch.docs.keys())

    _index_document(client, project_id, doc_id)
    # Old entries were deleted (delete called with this document) and counts
    # did not accumulate.
    assert doc_id in qdrant.deleted_document_ids
    assert doc_id in opensearch.deleted_document_ids
    assert set(qdrant.points.keys()) == first_points
    assert set(opensearch.docs.keys()) == first_docs


def test_search_isolates_projects(client, storage, task_session_factory, vector_stack, project_id):
    doc_a = _upload_and_chunk(client, project_id, name="a.txt")
    _index_document(client, project_id, doc_a)

    response_b = client.post(
        "/api/v1/projects",
        json={"project_code": "IDX-002", "project_name": "另一个项目"},
    )
    project_b = response_b.json()["id"]
    doc_b = _upload_and_chunk(client, project_b, name="b.txt")
    _index_document(client, project_b, doc_b)

    result = client.post(
        f"/api/v1/projects/{project_id}/search",
        json={"query": "投标人资格要求"},
    )
    assert result.status_code == 200
    body = result.json()
    assert body["results"], "expected hits in project A"
    for item in body["results"]:
        assert item["document_id"] == doc_a, "project B chunks must never appear"


def test_search_returns_real_scores_and_trace(
    client, storage, task_session_factory, vector_stack, project_id
):
    doc_id = _upload_and_chunk(client, project_id)
    _index_document(client, project_id, doc_id)

    result = client.post(
        f"/api/v1/projects/{project_id}/search",
        json={"query": "投标人需要具备哪些资质？", "top_k": 5},
    )
    assert result.status_code == 200
    body = result.json()
    assert body["results"]
    ranks = [item["rank"] for item in body["results"]]
    assert ranks == list(range(1, len(ranks) + 1))
    rerank_scores = [item["rerank_score"] for item in body["results"]]
    assert rerank_scores == sorted(rerank_scores, reverse=True)
    for item in body["results"]:
        assert item["content"]
        assert item["rrf_score"] > 0
        assert item["dense_rank"] is not None or item["bm25_rank"] is not None

    trace = body["trace"]
    assert trace["embedding_model"] == "fake-embedder"
    assert trace["reranker_model"]
    assert trace["fused_candidate_count"] >= trace["returned_count"]
    assert trace["latency"]["total_ms"] >= 0
    assert trace["degraded"] == []

    # The reranker actually received (query, passage) pairs.
    reranker = vector_stack["reranker"]
    assert reranker.calls
    query, passages = reranker.calls[-1]
    assert query == "投标人需要具备哪些资质？"
    assert all(passages)


def test_rrf_fusion_combines_dense_and_bm25():
    dense = [("c1", 0.9, {"content": "a"}), ("c2", 0.8, {"content": "b"})]
    bm25 = [("c2", 12.0, {"content": "b"}), ("c3", 8.0, {"content": "c"})]
    fused = rrf_fuse(dense, bm25, rrf_k=60)

    by_id = {c.chunk_id: c for c in fused}
    # Both-channel hit outranks single-channel hits.
    assert fused[0].chunk_id == "c2"
    assert by_id["c2"].rrf_score == pytest.approx(1 / 61 + 1 / 62)
    assert by_id["c1"].rrf_score == pytest.approx(1 / 61)
    assert by_id["c3"].rrf_score == pytest.approx(1 / 62)
    assert by_id["c1"].dense_rank == 1 and by_id["c1"].bm25_rank is None
    assert by_id["c3"].bm25_rank == 2 and by_id["c3"].dense_rank is None


def test_index_unchunked_document_returns_409(
    client, storage, task_session_factory, vector_stack, project_id
):
    response = client.post(
        f"/api/v1/projects/{project_id}/documents",
        json={"file_name": "manual.txt"},
    )
    doc_id = response.json()["id"]
    result = client.post(f"/api/v1/projects/{project_id}/documents/{doc_id}/index")
    assert result.status_code == 409
    assert "解析" in result.json()["detail"]


def test_search_fails_clearly_when_qdrant_down(
    client, storage, task_session_factory, vector_stack, project_id
):
    doc_id = _upload_and_chunk(client, project_id)
    _index_document(client, project_id, doc_id)

    vector_stack["qdrant"].fail_query = True
    result = client.post(f"/api/v1/projects/{project_id}/search", json={"query": "资质"})
    assert result.status_code == 503
    assert "Qdrant" in result.json()["detail"]


def test_search_fails_clearly_when_opensearch_down(
    client, storage, task_session_factory, vector_stack, project_id
):
    doc_id = _upload_and_chunk(client, project_id)
    _index_document(client, project_id, doc_id)

    vector_stack["opensearch"].fail_search = True
    result = client.post(f"/api/v1/projects/{project_id}/search", json={"query": "资质"})
    assert result.status_code == 503
    assert "OpenSearch" in result.json()["detail"]


def test_search_fails_clearly_when_embedding_unavailable(
    client, storage, task_session_factory, vector_stack, project_id, monkeypatch
):
    monkeypatch.setattr(retrieval, "get_embedding_service", lambda: BrokenEmbeddingService())
    result = client.post(f"/api/v1/projects/{project_id}/search", json={"query": "资质"})
    assert result.status_code == 503
    assert "Embedding" in result.json()["detail"]


def test_search_degrades_explicitly_without_reranker(
    client, storage, task_session_factory, vector_stack, project_id, monkeypatch
):
    doc_id = _upload_and_chunk(client, project_id)
    _index_document(client, project_id, doc_id)

    monkeypatch.setattr(retrieval, "get_reranker_service", lambda: BrokenRerankerService())
    result = client.post(f"/api/v1/projects/{project_id}/search", json={"query": "投标人资格"})
    assert result.status_code == 200
    body = result.json()
    assert "reranker_unavailable" in body["trace"]["degraded"]
    assert body["trace"]["reranker_model"] is None
    # RRF scores are never passed off as rerank scores.
    assert all(item["rerank_score"] is None for item in body["results"])


def test_indexing_failure_is_recorded_honestly(
    client, storage, task_session_factory, vector_stack, project_id, monkeypatch
):
    doc_id = _upload_and_chunk(client, project_id)
    monkeypatch.setattr(index_tasks, "get_embedding_service", lambda: BrokenEmbeddingService())
    _index_document(client, project_id, doc_id)

    summary = client.get(f"/api/v1/projects/{project_id}/documents/{doc_id}/index-summary").json()
    assert summary["status"] == "failed"
    assert "Embedding" in summary["error"]
    assert vector_stack["qdrant"].points == {}


def test_project_reindex_schedules_only_ready_documents(
    client, storage, task_session_factory, vector_stack, project_id
):
    doc_id = _upload_and_chunk(client, project_id)
    # Metadata-only document is not parse/chunk ready.
    client.post(f"/api/v1/projects/{project_id}/documents", json={"file_name": "manual.txt"})

    result = client.post(f"/api/v1/projects/{project_id}/reindex")
    assert result.status_code == 200
    body = result.json()
    assert body["scheduled_document_count"] == 1
    assert body["document_ids"] == [doc_id]
    # The background task actually ran and indexed the document.
    summary = client.get(f"/api/v1/projects/{project_id}/documents/{doc_id}/index-summary").json()
    assert summary["status"] == "success"


def test_retrieval_stack_never_touches_llm():
    """The retrieval/indexing modules must not reference any LLM client."""
    import inspect

    from app.services import embeddings, reranker

    for module in (retrieval, index_tasks, embeddings, reranker):
        source = inspect.getsource(module)
        assert "openai" not in source.lower()
        assert "chat.completions" not in source
        assert "ChatCompletion" not in source


def test_chunk_rebuild_triggers_index_rebuild(
    client, storage, task_session_factory, project_id, _no_auto_indexing
):
    doc_id = _upload_and_chunk(client, project_id)
    # Upload already triggered chunking once -> one auto-index trigger.
    triggered = [str(x) for x in _no_auto_indexing]
    assert doc_id in triggered
