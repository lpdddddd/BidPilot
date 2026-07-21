#!/usr/bin/env python3
"""BidPilot grounded RAG smoke / acceptance runner.

Two modes (strictly separated):

1) Default mock mode (always runnable without GPU / vLLM):
   python scripts/rag_smoke_accept.py
   Exercises citation buffering semantics with an in-process FakeLlm.
   Does NOT claim a real vLLM E2E.

2) Live mode (requires infra + indexed project + running vLLM):
   RAG_SMOKE_LIVE=1 python scripts/rag_smoke_accept.py
   Hits real /health, /ready, /api/v1/health/llm, JSON ask, and SSE ask.
   Writes a redacted summary under docs/acceptance/ (no raw document text).

Exit codes:
  0 success
  2 live prerequisites missing (reported, not a silent pass)
  1 assertion / request failure
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

CITATION_RE = re.compile(r"\[(S\d+)\]")


def _mock_smoke() -> int:
    from app.schemas.ask import AskRequest
    from app.schemas.search import (
        RetrievalTrace,
        SearchResponse,
        SearchResultItem,
        StageLatency,
    )
    from app.services.llm_client import ChatResult
    from app.services.rag_answer_service import RagAnswerService

    class FakeRetrieval:
        def __init__(self, response: SearchResponse):
            self.response = response

        def search(self, project_id, request):
            return self.response

    class FakeLlm:
        enabled = True
        model = "bidpilot-qwen3-8b"

        def __init__(self, content: str):
            self.content = content
            self.stream_calls = 0

        def chat(self, messages, **kwargs):
            return ChatResult(
                content=self.content,
                model=self.model,
                latency_ms=1.0,
                finish_reason="stop",
                request_id="smoke",
            )

        def chat_stream(self, messages, **kwargs):
            self.stream_calls += 1
            yield from self.content

    def make_item(chunk_id: str, content: str) -> SearchResultItem:
        return SearchResultItem(
            rank=1,
            chunk_id=chunk_id,
            document_id="doc-smoke",
            file_name="smoke.txt",
            document_type="tender",
            chunk_index=0,
            section="第一章",
            clause_id="1.1",
            page_start=1,
            page_end=1,
            content=content,
            content_hash="h",
            source_sha256="s",
            chunker_version="1.1.0",
            dense_rank=1,
            dense_score=0.9,
            bm25_rank=1,
            bm25_score=1.0,
            rrf_score=0.03,
            rerank_score=0.5,
        )

    trace = RetrievalTrace(
        dense_candidate_count=1,
        bm25_candidate_count=1,
        fused_candidate_count=1,
        returned_count=1,
        embedding_model="fake",
        reranker_model="fake",
        qdrant_collection="c",
        opensearch_index="i",
        rrf_k=60,
        latency=StageLatency(
            embed_ms=0, dense_ms=0, bm25_ms=0, fusion_ms=0, rerank_ms=0, total_ms=0
        ),
    )
    item = make_item("chunk-1", "投标人须具备安全生产许可证")
    retrieval = FakeRetrieval(SearchResponse(query="q", results=[item], trace=trace))
    llm = FakeLlm("投标人须具备安全生产许可证 [S1]。")
    service = RagAnswerService(db=SimpleNamespace(), retrieval=retrieval, llm=llm)  # type: ignore[arg-type]
    pid = uuid4()

    # Non-stream
    resp = service.answer(pid, AskRequest(question="需要哪些资质？"))
    assert resp.citations and resp.citations[0].source_id == "S1"
    assert resp.citations[0].chunk_id == "chunk-1"

    # Stream: no delta leak
    events = list(service.answer_stream(pid, AskRequest(question="需要哪些资质？", stream=True)))
    names = [e["event"] for e in events]
    assert names == ["retrieval", "generation_started", "final"], names
    assert "delta" not in names

    # Validation failure path
    bad = FakeLlm("未知引用 [S99]。")
    bad_service = RagAnswerService(db=SimpleNamespace(), retrieval=retrieval, llm=bad)  # type: ignore[arg-type]
    bad_events = list(
        bad_service.answer_stream(pid, AskRequest(question="需要哪些资质？", stream=True))
    )
    assert bad_events[-1]["event"] == "error"
    assert all("answer" not in (e.get("data") or {}) for e in bad_events)
    assert "delta" not in [e["event"] for e in bad_events]

    print("rag_smoke mock: PASS (buffered SSE + citation checks)")
    print("NOTE: mock mode does not constitute real vLLM E2E.")
    return 0


def _http_get(url: str, timeout: float = 10.0):
    import httpx

    return httpx.get(url, timeout=timeout)


def _live_smoke() -> int:
    import httpx

    api = os.getenv("RAG_SMOKE_API", "http://127.0.0.1:8000").rstrip("/")
    project_id = os.getenv("RAG_SMOKE_PROJECT_ID", "").strip()
    questions_env = os.getenv("RAG_SMOKE_QUESTIONS", "").strip()
    questions = (
        [q.strip() for q in questions_env.split("|") if q.strip()]
        if questions_env
        else [
            "投标人需要具备哪些资质？",
            "投标保证金是多少？",
            "投标截止时间是什么时候？",
        ]
    )

    blockers: list[str] = []

    try:
        health = _http_get(f"{api}/health")
        if health.status_code != 200:
            blockers.append(f"/health HTTP {health.status_code}")
    except Exception as exc:  # noqa: BLE001
        blockers.append(f"/health unreachable: {exc}")

    try:
        ready = _http_get(f"{api}/ready")
        body = ready.json()
        if ready.status_code != 200:
            blockers.append(f"/ready HTTP {ready.status_code}")
        else:
            for svc in body.get("services", []):
                if svc.get("name") in {"postgres", "qdrant", "opensearch"} and svc.get(
                    "status"
                ) != "ok":
                    blockers.append(f"{svc.get('name')} not ok: {svc.get('detail')}")
    except Exception as exc:  # noqa: BLE001
        blockers.append(f"/ready unreachable: {exc}")

    llm_body: dict = {}
    try:
        llm = _http_get(f"{api}/api/v1/health/llm")
        llm_body = llm.json()
        if not llm_body.get("enabled"):
            blockers.append("LLM_ENABLED=false")
        elif not llm_body.get("reachable"):
            blockers.append(f"LLM unreachable: {llm_body.get('detail')}")
    except Exception as exc:  # noqa: BLE001
        blockers.append(f"/api/v1/health/llm unreachable: {exc}")

    if not project_id:
        # Discover a project with indexed docs if possible.
        try:
            projects = _http_get(f"{api}/api/v1/projects").json()
            for p in projects.get("items", []):
                docs = _http_get(f"{api}/api/v1/projects/{p['id']}/documents").json()
                for d in docs.get("items", []):
                    indexing = (d.get("metadata_json") or {}).get("indexing") or {}
                    if indexing.get("status") == "success":
                        project_id = p["id"]
                        break
                if project_id:
                    break
        except Exception as exc:  # noqa: BLE001
            blockers.append(f"project discovery failed: {exc}")
        if not project_id:
            blockers.append("no project with indexed documents found")

    if blockers:
        print("rag_smoke live: BLOCKED")
        for b in blockers:
            print(f"  - {b}")
        print(
            "Run infra + vLLM first, e.g.\n"
            "  make infra-up && make backend\n"
            "  ./scripts/serve_qwen3_vllm.sh\n"
            "  LLM_ENABLED=true RAG_SMOKE_LIVE=1 make rag-smoke"
        )
        return 2

    records: list[dict] = []
    with httpx.Client(timeout=180.0) as client:
        # JSON ask
        q0 = questions[0]
        json_resp = client.post(
            f"{api}/api/v1/projects/{project_id}/ask",
            json={"question": q0, "stream": False, "top_k": 8},
        )
        if json_resp.status_code != 200:
            print("JSON ask failed:", json_resp.status_code, json_resp.text[:500])
            return 1
        payload = json_resp.json()
        _assert_ask_payload(payload)
        records.append(_redact_record("json", payload))

        # SSE ask for remaining questions (and verify event shape)
        for q in questions:
            started = time.perf_counter()
            with client.stream(
                "POST",
                f"{api}/api/v1/projects/{project_id}/ask",
                json={"question": q, "stream": True, "top_k": 8},
            ) as resp:
                if resp.status_code != 200:
                    print("SSE ask failed:", resp.status_code)
                    return 1
                events = _parse_sse("".join(resp.iter_text()))
            names = [e["event"] for e in events]
            if "delta" in names:
                print("FAIL: client received delta (unvalidated leak)")
                return 1
            if names and names[0] != "retrieval":
                print("FAIL: first event must be retrieval, got", names)
                return 1
            if "error" in names and "final" not in names:
                err = next(e for e in events if e["event"] == "error")
                records.append(
                    {
                        "mode": "sse",
                        "question": q,
                        "status": "error",
                        "error": err["data"].get("message"),
                        "latency_ms": round((time.perf_counter() - started) * 1000, 1),
                    }
                )
                continue
            if "final" not in names:
                print("FAIL: missing final event", names)
                return 1
            if "generation_started" not in names and events[0]["data"].get("status") == "ok":
                # insufficient_evidence may skip generation_started
                pass
            final = next(e for e in events if e["event"] == "final")["data"]["result"]
            _assert_ask_payload(final)
            rec = _redact_record("sse", final)
            rec["latency_ms"] = round((time.perf_counter() - started) * 1000, 1)
            records.append(rec)

    out_dir = ROOT / "docs" / "acceptance"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = out_dir / f"rag_smoke_{stamp}.json"
    summary = {
        "created_at": stamp,
        "api": api,
        "project_id": project_id,
        "model_probe": llm_body,
        "records": records,
        "note": "Redacted acceptance summary. No raw document bodies included.",
    }
    out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"rag_smoke live: PASS ({len(records)} records) -> {out_path}")
    return 0


def _assert_ask_payload(payload: dict) -> None:
    answer = payload.get("answer") or ""
    citations = payload.get("citations") or []
    cited = CITATION_RE.findall(answer)
    allowed = {c["source_id"] for c in citations}
    for sid in cited:
        if sid not in allowed and payload.get("status") != "insufficient_evidence":
            # Cited ids must map to returned citations when present.
            # Insufficient answers may have zero citations.
            if citations:
                raise AssertionError(f"citation {sid} not in response citations")
    for c in citations:
        for key in ("source_id", "chunk_id", "document_id", "excerpt"):
            if not c.get(key):
                raise AssertionError(f"citation missing {key}: {c}")


def _redact_record(mode: str, payload: dict) -> dict:
    citations = []
    for c in payload.get("citations") or []:
        citations.append(
            {
                "source_id": c.get("source_id"),
                "file_name": c.get("file_name"),
                "section": c.get("section"),
                "clause_id": c.get("clause_id"),
                "page_start": c.get("page_start"),
                "page_end": c.get("page_end"),
                "chunk_id": c.get("chunk_id"),
                "document_id": c.get("document_id"),
                "excerpt_chars": len(c.get("excerpt") or ""),
            }
        )
    gen = payload.get("generation_trace") or {}
    return {
        "mode": mode,
        "question": payload.get("question"),
        "status": payload.get("status"),
        "answer_preview": (payload.get("answer") or "")[:240],
        "citation_ids": [c["source_id"] for c in citations],
        "citations": citations,
        "model": gen.get("model"),
        "latency_ms": gen.get("latency_ms"),
        "context_chunk_count": gen.get("context_chunk_count"),
    }


def _parse_sse(body: str) -> list[dict]:
    events: list[dict] = []
    event = "message"
    data_lines: list[str] = []
    for line in body.splitlines():
        if line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data_lines.append(line[5:].strip())
        elif line == "":
            if data_lines:
                events.append({"event": event, "data": json.loads("\n".join(data_lines))})
                data_lines = []
                event = "message"
    if data_lines:
        events.append({"event": event, "data": json.loads("\n".join(data_lines))})
    return events


def main() -> int:
    live = os.getenv("RAG_SMOKE_LIVE", "").strip() in {"1", "true", "yes"}
    if live:
        return _live_smoke()
    return _mock_smoke()


if __name__ == "__main__":
    raise SystemExit(main())
