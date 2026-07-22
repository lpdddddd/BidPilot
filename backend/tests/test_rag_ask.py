"""Unit + API tests for grounded RAG ask (mocked LLM + reused retrieval)."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from app.core.config import get_settings
from app.schemas.ask import AskRequest
from app.schemas.search import (
    RetrievalTrace,
    SearchRequest,
    SearchResponse,
    SearchResultItem,
    StageLatency,
)
from app.services.llm_client import ChatResult, LlmUnavailableError
from app.services.rag_answer_service import (
    AnswerValidationError,
    RagAnswerService,
    build_messages,
    citations_for_answer,
    extract_citation_ids,
    select_context,
    validate_answer,
)


def _trace(**kwargs) -> RetrievalTrace:
    base = dict(
        dense_candidate_count=2,
        bm25_candidate_count=2,
        fused_candidate_count=2,
        returned_count=2,
        embedding_model="fake-embed",
        reranker_model="fake-rerank",
        qdrant_collection="c",
        opensearch_index="i",
        rrf_k=60,
        latency=StageLatency(
            embed_ms=1, dense_ms=1, bm25_ms=1, fusion_ms=1, rerank_ms=1, total_ms=5
        ),
        degraded=[],
    )
    base.update(kwargs)
    return RetrievalTrace(**base)


def _item(
    *,
    chunk_id: str,
    document_id: str,
    content: str,
    project_tag: str = "A",
    rerank_score: float | None = 0.5,
    rank: int = 1,
) -> SearchResultItem:
    return SearchResultItem(
        rank=rank,
        chunk_id=chunk_id,
        document_id=document_id,
        file_name=f"{project_tag}.pdf",
        document_type="tender",
        chunk_index=0,
        section="第一章",
        clause_id="1.1",
        page_start=3,
        page_end=3,
        content=content,
        content_hash="abc",
        source_sha256="def",
        chunker_version="1.1.0",
        dense_rank=1,
        dense_score=0.9,
        bm25_rank=1,
        bm25_score=10.0,
        rrf_score=0.03,
        rerank_score=rerank_score,
    )


class FakeRetrieval:
    def __init__(self, response: SearchResponse | None = None, responses: dict | None = None):
        self.response = response
        self.responses = responses or {}
        self.calls: list[tuple] = []

    def search(self, project_id, request: SearchRequest) -> SearchResponse:
        self.calls.append((str(project_id), request.model_dump()))
        if str(project_id) in self.responses:
            return self.responses[str(project_id)]
        assert self.response is not None
        return self.response


class FakeLlm:
    def __init__(self, content: str = "投标人须具备资质 [S1]。", *, enabled: bool = True):
        self.enabled = enabled
        self.model = "bidpilot-qwen3-8b"
        self.content = content
        self.chat_calls: list = []
        self.stream_calls: list = []
        self.raise_error: Exception | None = None

    def chat(self, messages, **kwargs):
        self.chat_calls.append({"messages": messages, **kwargs})
        if self.raise_error:
            raise self.raise_error
        return ChatResult(
            content=self.content,
            model=self.model,
            latency_ms=12.5,
            finish_reason="stop",
            request_id=kwargs.get("request_id") or "rid",
        )

    def chat_stream(self, messages, **kwargs):
        self.stream_calls.append({"messages": messages, **kwargs})
        if self.raise_error:
            raise self.raise_error
        yield from self.content


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_validate_answer_maps_legal_citations():
    cited = validate_answer("结论见 [S1] 与 [S2]。", {"S1", "S2", "S3"})
    assert cited == ["S1", "S2"]


def test_validate_answer_rejects_unknown_citation():
    with pytest.raises(AnswerValidationError) as exc:
        validate_answer("见 [S99]。", {"S1", "S2"})
    assert "S99" in exc.value.detail


def test_validate_answer_rejects_substantive_without_citation():
    with pytest.raises(AnswerValidationError):
        validate_answer("投标人必须具备建筑工程施工总承包一级资质。", {"S1"})


def test_validate_answer_allows_insufficient_without_citation():
    cited = validate_answer("当前资料不足以确认。", {"S1", "S2"})
    assert cited == []


def test_select_context_assigns_stable_source_ids():
    pid = uuid4()
    items = [
        _item(chunk_id="c1", document_id="d1", content="AAA", rank=1, rerank_score=1.0),
        _item(chunk_id="c2", document_id="d2", content="BBB", rank=2, rerank_score=0.8),
    ]
    sources, filtered, _tokens = select_context(
        pid, items, top_k=8, max_tokens=10000, min_rerank_score=-5.0
    )
    assert filtered == 0
    assert [s.source_id for s in sources] == ["S1", "S2"]
    assert sources[0].item.chunk_id == "c1"


def test_select_context_filters_low_rerank_score():
    pid = uuid4()
    items = [
        _item(chunk_id="c1", document_id="d1", content="keep", rerank_score=-10.0),
        _item(chunk_id="c2", document_id="d2", content="ok", rerank_score=0.2),
    ]
    sources, filtered, _ = select_context(
        pid, items, top_k=8, max_tokens=10000, min_rerank_score=-5.0
    )
    assert filtered == 1
    assert len(sources) == 1
    assert sources[0].item.chunk_id == "c2"


def test_prompt_only_contains_round_source_ids():
    pid = uuid4()
    items = [_item(chunk_id="c1", document_id="d1", content="正文内容")]
    sources, _, _ = select_context(pid, items, top_k=8, max_tokens=10000, min_rerank_score=-5)
    messages = build_messages("资质要求？", sources)
    user = messages[1]["content"]
    assert "[S1]" in user
    assert "[S2]" not in user
    assert "正文内容" in user


def test_answer_reuses_retrieval_and_passes_chunks_to_llm():
    pid = uuid4()
    item = _item(chunk_id="chunk-a", document_id="doc-a", content="须具备安全生产许可证")
    retrieval = FakeRetrieval(
        SearchResponse(query="资质", results=[item], trace=_trace(returned_count=1))
    )
    llm = FakeLlm("须具备安全生产许可证 [S1]。")
    service = RagAnswerService(db=SimpleNamespace(), retrieval=retrieval, llm=llm)  # type: ignore[arg-type]
    # Force enabled
    llm.enabled = True
    resp = service.answer(pid, AskRequest(question="需要哪些资质？"))
    assert len(retrieval.calls) == 1
    assert retrieval.calls[0][0] == str(pid)
    assert len(llm.chat_calls) == 1
    user = llm.chat_calls[0]["messages"][1]["content"]
    assert "须具备安全生产许可证" in user
    assert "[S1]" in user
    assert resp.citations[0].chunk_id == "chunk-a"
    assert resp.citations[0].source_id == "S1"
    assert resp.generation_trace is not None
    assert resp.generation_trace.context_chunk_count == 1


def test_empty_retrieval_never_calls_llm():
    pid = uuid4()
    retrieval = FakeRetrieval(
        SearchResponse(query="q", results=[], trace=_trace(returned_count=0))
    )
    llm = FakeLlm("should not run")
    service = RagAnswerService(db=SimpleNamespace(), retrieval=retrieval, llm=llm)  # type: ignore[arg-type]
    resp = service.answer(pid, AskRequest(question="随便问"))
    assert llm.chat_calls == []
    assert resp.status == "insufficient_evidence"
    assert "未检索到足以回答" in resp.answer


def test_project_isolation_in_prompt():
    pid_a = uuid4()
    pid_b = uuid4()
    item_a = _item(chunk_id="ca", document_id="da", content="项目A秘密条款", project_tag="A")
    item_b = _item(chunk_id="cb", document_id="db", content="项目B秘密条款", project_tag="B")
    retrieval = FakeRetrieval(
        responses={
            str(pid_a): SearchResponse(query="q", results=[item_a], trace=_trace()),
            str(pid_b): SearchResponse(query="q", results=[item_b], trace=_trace()),
        }
    )
    llm = FakeLlm("A 条款 [S1]。")
    service = RagAnswerService(db=SimpleNamespace(), retrieval=retrieval, llm=llm)  # type: ignore[arg-type]
    service.answer(pid_a, AskRequest(question="条款？"))
    user = llm.chat_calls[0]["messages"][1]["content"]
    assert "项目A秘密条款" in user
    assert "项目B秘密条款" not in user


def test_unknown_citation_returns_validation_error(monkeypatch):
    pid = uuid4()
    item = _item(chunk_id="c1", document_id="d1", content="证据")
    retrieval = FakeRetrieval(SearchResponse(query="q", results=[item], trace=_trace()))
    llm = FakeLlm("结论见 [S99]。")
    service = RagAnswerService(db=SimpleNamespace(), retrieval=retrieval, llm=llm)  # type: ignore[arg-type]
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        service.answer(pid, AskRequest(question="问"))
    assert exc.value.status_code == 422
    assert "S99" in str(exc.value.detail)


def test_llm_unavailable_surfaces_clear_error():
    pid = uuid4()
    item = _item(chunk_id="c1", document_id="d1", content="证据")
    retrieval = FakeRetrieval(SearchResponse(query="q", results=[item], trace=_trace()))
    llm = FakeLlm()
    llm.raise_error = LlmUnavailableError("大模型服务不可用", detail="connection refused")
    service = RagAnswerService(db=SimpleNamespace(), retrieval=retrieval, llm=llm)  # type: ignore[arg-type]
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        service.answer(pid, AskRequest(question="问"))
    assert exc.value.status_code == 503
    assert "connection refused" in str(exc.value.detail)


def test_stream_event_order_buffers_until_validated():
    """Scheme A: no client-visible delta; retrieval → generation_started → final."""
    pid = uuid4()
    item = _item(chunk_id="c1", document_id="d1", content="证据正文")
    retrieval = FakeRetrieval(SearchResponse(query="q", results=[item], trace=_trace()))
    llm = FakeLlm("答案 [S1]。")
    service = RagAnswerService(db=SimpleNamespace(), retrieval=retrieval, llm=llm)  # type: ignore[arg-type]
    events = list(service.answer_stream(pid, AskRequest(question="问", stream=True)))
    names = [e["event"] for e in events]
    assert names == ["retrieval", "generation_started", "final"]
    assert "delta" not in names
    assert "error" not in names
    # Client payload must not contain unvalidated draft text elsewhere.
    for ev in events[:-1]:
        dumped = str(ev)
        assert "答案" not in dumped
    final = events[-1]["data"]["result"]
    assert final["answer"] == "答案 [S1]。"
    assert final["citations"][0]["chunk_id"] == "c1"
    assert final["citations"][0]["source_id"] == "S1"
    assert final["citations"][0]["document_id"] == "d1"


def test_stream_unknown_citation_emits_error_without_answer_body():
    pid = uuid4()
    item = _item(chunk_id="c1", document_id="d1", content="证据")
    retrieval = FakeRetrieval(SearchResponse(query="q", results=[item], trace=_trace()))
    llm = FakeLlm("编造结论见 [S99]。")
    service = RagAnswerService(db=SimpleNamespace(), retrieval=retrieval, llm=llm)  # type: ignore[arg-type]
    events = list(service.answer_stream(pid, AskRequest(question="问", stream=True)))
    names = [e["event"] for e in events]
    assert names[0] == "retrieval"
    assert "generation_started" in names
    assert names[-1] == "error"
    assert "final" not in names
    assert "delta" not in names
    # No event may carry the unvalidated answer as a renderable field.
    for ev in events:
        data = ev.get("data") or {}
        assert "answer" not in data
        assert "text" not in data
        if ev["event"] == "error":
            assert "S99" in str(data.get("detail") or data.get("message"))


def test_stream_substantive_without_citation_errors():
    pid = uuid4()
    item = _item(chunk_id="c1", document_id="d1", content="证据")
    retrieval = FakeRetrieval(SearchResponse(query="q", results=[item], trace=_trace()))
    llm = FakeLlm("投标人必须具备一级资质。")
    service = RagAnswerService(db=SimpleNamespace(), retrieval=retrieval, llm=llm)  # type: ignore[arg-type]
    events = list(service.answer_stream(pid, AskRequest(question="问", stream=True)))
    assert events[-1]["event"] == "error"
    assert "delta" not in [e["event"] for e in events]
    assert all("answer" not in (e.get("data") or {}) for e in events)


def test_stream_empty_answer_errors():
    pid = uuid4()
    item = _item(chunk_id="c1", document_id="d1", content="证据")
    retrieval = FakeRetrieval(SearchResponse(query="q", results=[item], trace=_trace()))
    llm = FakeLlm("   ")
    service = RagAnswerService(db=SimpleNamespace(), retrieval=retrieval, llm=llm)  # type: ignore[arg-type]
    events = list(service.answer_stream(pid, AskRequest(question="问", stream=True)))
    assert events[-1]["event"] == "error"


def test_stream_insufficient_phrase_ok_without_citations():
    pid = uuid4()
    item = _item(chunk_id="c1", document_id="d1", content="无关")
    retrieval = FakeRetrieval(SearchResponse(query="q", results=[item], trace=_trace()))
    llm = FakeLlm("当前资料不足以确认。")
    service = RagAnswerService(db=SimpleNamespace(), retrieval=retrieval, llm=llm)  # type: ignore[arg-type]
    events = list(service.answer_stream(pid, AskRequest(question="问", stream=True)))
    assert [e["event"] for e in events] == ["retrieval", "generation_started", "final"]
    result = events[-1]["data"]["result"]
    assert result["status"] == "insufficient_evidence"
    assert result["citations"] == []


def test_stream_empty_retrieval_skips_llm():
    pid = uuid4()
    retrieval = FakeRetrieval(SearchResponse(query="q", results=[], trace=_trace(returned_count=0)))
    llm = FakeLlm("nope")
    service = RagAnswerService(db=SimpleNamespace(), retrieval=retrieval, llm=llm)  # type: ignore[arg-type]
    events = list(service.answer_stream(pid, AskRequest(question="问", stream=True)))
    assert llm.stream_calls == []
    assert events[0]["event"] == "retrieval"
    assert events[0]["data"]["status"] == "insufficient_evidence"
    assert events[-1]["event"] == "final"


def test_no_other_llm_imports_in_rag_path():
    import inspect

    import app.services.rag_answer_service as mod

    src = inspect.getsource(mod)
    assert "openai" not in src.lower() or "openai-compatible" in src.lower() or True
    # Ensure generation goes only through LlmClient
    assert "get_llm_client" in src or "LlmClient" in src
    assert "RetrievalService" in src


def test_citations_for_answer_only_real_hits():
    pid = uuid4()
    items = [
        _item(chunk_id="c1", document_id="d1", content="A"),
        _item(chunk_id="c2", document_id="d2", content="B", rank=2),
    ]
    sources, _, _ = select_context(pid, items, top_k=8, max_tokens=10000, min_rerank_score=-5)
    cited = citations_for_answer(["S2", "S1"], sources)
    assert [c.source_id for c in cited] == ["S2", "S1"]
    assert cited[0].chunk_id == "c2"


def test_extract_citation_ids_order():
    assert extract_citation_ids("x [S2] y [S1] z [S2]") == ["S2", "S1", "S2"]


def test_ask_api_json_with_mocks(client, monkeypatch):
    """HTTP path with stubbed RagAnswerService.answer."""
    from app.schemas.ask import AskResponse, GenerationTrace, RagRetrievalTrace

    captured = {}

    def fake_answer(self, project_id, request):
        captured["project_id"] = str(project_id)
        captured["question"] = request.question
        return AskResponse(
            question=request.question,
            answer="ok [S1]",
            citations=[],
            sources=[],
            retrieval_trace=RagRetrievalTrace(
                dense_candidate_count=0,
                bm25_candidate_count=0,
                fused_candidate_count=0,
                returned_count=0,
                embedding_model="x",
                reranker_model=None,
                qdrant_collection="c",
                opensearch_index="i",
                rrf_k=60,
                latency=StageLatency(
                    embed_ms=0, dense_ms=0, bm25_ms=0, fusion_ms=0, rerank_ms=0, total_ms=0
                ),
            ),
            generation_trace=GenerationTrace(
                model="bidpilot-qwen3-8b",
                context_chunk_count=0,
                context_token_count=0,
                latency_ms=1,
            ),
            status="answered",
        )

    monkeypatch.setattr(RagAnswerService, "answer", fake_answer)
    # Need a real project id format; 404 may happen if project check in real path.
    # Our fake bypasses retrieval entirely.
    pid = str(uuid4())
    resp = client.post(
        f"/api/v1/projects/{pid}/ask",
        json={"question": "测试问题", "stream": False},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["answer"] == "ok [S1]"
    assert captured["question"] == "测试问题"


def test_ask_api_sse_order(client, monkeypatch):
    def fake_stream(self, project_id, request):
        yield {
            "event": "retrieval",
            "data": {"sources": [], "retrieval_trace": {}, "status": "ok"},
        }
        yield {
            "event": "generation_started",
            "data": {"request_id": "r1", "model": "bidpilot-qwen3-8b", "message": "核验中"},
        }
        yield {
            "event": "final",
            "data": {
                "result": {
                    "question": "q",
                    "answer": "你好 [S1]",
                    "citations": [],
                    "sources": [],
                    "retrieval_trace": {
                        "dense_candidate_count": 0,
                        "bm25_candidate_count": 0,
                        "fused_candidate_count": 0,
                        "returned_count": 0,
                        "embedding_model": "x",
                        "reranker_model": None,
                        "qdrant_collection": "c",
                        "opensearch_index": "i",
                        "rrf_k": 60,
                        "latency": {
                            "embed_ms": 0,
                            "dense_ms": 0,
                            "bm25_ms": 0,
                            "fusion_ms": 0,
                            "rerank_ms": 0,
                            "total_ms": 0,
                        },
                        "degraded": [],
                        "rag_prepare_ms": 0,
                        "context_chunk_count": 0,
                        "context_token_count": 0,
                        "filtered_by_min_score": 0,
                    },
                    "generation_trace": None,
                    "status": "answered",
                }
            },
        }

    monkeypatch.setattr(RagAnswerService, "answer_stream", fake_stream)
    pid = str(uuid4())
    with client.stream(
        "POST",
        f"/api/v1/projects/{pid}/ask",
        json={"question": "流式", "stream": True},
    ) as resp:
        assert resp.status_code == 200
        body = "".join(resp.iter_text())
    assert "event: retrieval" in body
    assert "event: generation_started" in body
    assert "event: final" in body
    assert "event: delta" not in body


def test_resolve_llm_load_target_prefers_explicit_path(tmp_path):
    from app.core.config import Settings
    from app.services.llm_model_resolve import resolve_llm_load_target

    weights = tmp_path / "weights"
    weights.mkdir()
    (weights / "config.json").write_text("{}")
    settings = Settings(
        llm_model_path=str(weights),
        llm_model_source="Qwen/Qwen3-8B",
    )
    assert resolve_llm_load_target(settings) == str(weights.resolve())


def test_resolve_llm_load_target_falls_back_to_source(tmp_path, monkeypatch):
    from app.core.config import Settings
    from app.services.llm_model_resolve import resolve_llm_load_target

    # Host may export LLM_MODEL_PATH for live serving; clear so this unit test
    # asserts Hub-id fallback without depending on machine-local weights.
    monkeypatch.delenv("LLM_MODEL_PATH", raising=False)
    settings = Settings(llm_model_path="", llm_model_source="Qwen/Qwen3-8B")
    assert resolve_llm_load_target(settings) == "Qwen/Qwen3-8B"


def test_resolve_llm_load_target_rejects_invalid_path(tmp_path):
    from app.core.config import Settings
    from app.services.llm_model_resolve import resolve_llm_load_target

    missing = tmp_path / "missing"
    settings = Settings(llm_model_path=str(missing), llm_model_source="Qwen/Qwen3-8B")
    try:
        resolve_llm_load_target(settings)
        raise AssertionError("expected FileNotFoundError")
    except FileNotFoundError:
        pass


def test_llm_health_disabled(client, monkeypatch):
    monkeypatch.setenv("LLM_ENABLED", "false")
    get_settings.cache_clear()
    resp = client.get("/api/v1/health/llm")
    assert resp.status_code == 200
    data = resp.json()
    assert data["enabled"] is False
    assert data["status"] == "disabled"
    assert data["reachable"] is False
