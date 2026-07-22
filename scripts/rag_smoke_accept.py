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
INSUFFICIENT_PHRASES = ("当前资料不足以确认", "资料不足以确认")


class CaseFailure(Exception):
    """One live smoke case failed validation."""

    def __init__(self, message: str, *, events: list[str] | None = None, detail: str | None = None):
        super().__init__(message)
        self.message = message
        self.events = events or []
        self.detail = detail or message


def _citation_has_locator(citation: dict) -> bool:
    if citation.get("section"):
        return True
    if citation.get("clause_id"):
        return True
    if citation.get("page_start") is not None or citation.get("page_end") is not None:
        return True
    return False


def validate_answerable_payload(
    payload: dict,
    *,
    expected_model: str | None = None,
) -> None:
    """Strict checks for an expected answerable grounded response."""
    answer = (payload.get("answer") or "").strip()
    citations = payload.get("citations") or []
    if not answer:
        raise CaseFailure("final.answer is empty")
    if not citations:
        raise CaseFailure("final.citations is empty")

    cited = CITATION_RE.findall(answer)
    allowed = {c.get("source_id") for c in citations}
    for sid in cited:
        if sid not in allowed:
            raise CaseFailure(f"answer cites unknown {sid}", detail=f"allowed={sorted(allowed)}")

    for c in citations:
        for key in ("source_id", "chunk_id", "document_id", "file_name"):
            if not c.get(key):
                raise CaseFailure(f"citation missing {key}")
        if not _citation_has_locator(c):
            raise CaseFailure(
                "citation missing locator (need section, clause_id, or page_start/page_end)",
                detail=f"source_id={c.get('source_id')}",
            )

    gen = payload.get("generation_trace") or {}
    model = gen.get("model")
    if expected_model and model != expected_model:
        raise CaseFailure(
            "generation_trace.model mismatch",
            detail=f"got={model!r} expected={expected_model!r}",
        )
    latency = gen.get("latency_ms")
    if not isinstance(latency, (int, float)) or latency < 0:
        raise CaseFailure(
            "generation_trace.latency_ms invalid",
            detail=f"got={latency!r}",
        )


def validate_insufficient_payload(payload: dict) -> None:
    """Strict checks for an expected insufficient-evidence response."""
    if payload.get("status") not in {None, "insufficient_evidence", "answered"}:
        # Accept answered only if phrase present; status preferred.
        pass
    answer = payload.get("answer") or ""
    if not any(p in answer for p in INSUFFICIENT_PHRASES):
        raise CaseFailure(
            "insufficient case missing safety phrase",
            detail="expected 当前资料不足以确认",
        )


def validate_sse_answerable(
    events: list[dict],
    *,
    expected_model: str | None = None,
) -> dict:
    """Validate SSE event stream for an answerable question. Returns final payload."""
    names = [e.get("event") for e in events]
    if "delta" in names:
        raise CaseFailure("client received delta (unvalidated leak)", events=names)
    if "error" in names:
        err = next(e for e in events if e.get("event") == "error")
        data = err.get("data") or {}
        raise CaseFailure(
            f"SSE error: {data.get('message') or 'unknown'}",
            events=names,
            detail=str(data.get("detail") or "")[:300],
        )
    if "retrieval" not in names:
        raise CaseFailure("missing retrieval event", events=names)
    if "generation_started" not in names:
        raise CaseFailure("missing generation_started event", events=names)
    finals = [e for e in events if e.get("event") == "final"]
    if len(finals) != 1:
        raise CaseFailure(f"expected exactly one final, got {len(finals)}", events=names)
    result = (finals[0].get("data") or {}).get("result") or {}
    validate_answerable_payload(result, expected_model=expected_model)
    return result


def validate_sse_insufficient(events: list[dict]) -> dict:
    """Validate SSE for expect_insufficient_evidence=true."""
    names = [e.get("event") for e in events]
    if "error" in names:
        err = next(e for e in events if e.get("event") == "error")
        data = err.get("data") or {}
        raise CaseFailure(
            "insufficient case must not receive SSE error "
            f"(got {data.get('message')!r}; infra/LLM/citation failures are not insufficient)",
            events=names,
            detail=str(data.get("detail") or "")[:300],
        )
    finals = [e for e in events if e.get("event") == "final"]
    if len(finals) != 1:
        raise CaseFailure(f"insufficient case needs exactly one final, got {len(finals)}", events=names)
    result = (finals[0].get("data") or {}).get("result") or {}
    validate_insufficient_payload(result)
    return result


def evaluate_live_cases(case_results: list[tuple[str, bool, str | None]]) -> tuple[bool, list[str]]:
    """Aggregate case outcomes. Returns (all_passed, failure_messages).

    case_results items: (case_id, ok, error_message_or_none)
    """
    failures = [f"{cid}: {msg}" for cid, ok, msg in case_results if not ok]
    return (len(failures) == 0, failures)


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

    resp = service.answer(pid, AskRequest(question="需要哪些资质？"))
    assert resp.citations and resp.citations[0].source_id == "S1"
    assert resp.citations[0].chunk_id == "chunk-1"

    events = list(service.answer_stream(pid, AskRequest(question="需要哪些资质？", stream=True)))
    names = [e["event"] for e in events]
    assert names == ["retrieval", "generation_started", "final"], names
    assert "delta" not in names

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


def _parse_case_specs(questions: list[str]) -> list[dict]:
    """Build case specs. Trailing questions may be marked insufficient via env.

    RAG_SMOKE_INSUFFICIENT_QUESTION sets an extra insufficient-evidence case.
    """
    cases = [{"question": q, "expect_insufficient_evidence": False} for q in questions]
    insuff = os.getenv("RAG_SMOKE_INSUFFICIENT_QUESTION", "").strip()
    if insuff:
        cases.append({"question": insuff, "expect_insufficient_evidence": True})
    elif len(questions) >= 3 and os.getenv("RAG_SMOKE_AUTO_INSUFFICIENT", "1") in {
        "1",
        "true",
        "yes",
    }:
        # Default third live question set can include an unanswerable probe via env.
        pass
    return cases


def _live_smoke() -> int:
    import httpx

    api = os.getenv("RAG_SMOKE_API", "http://127.0.0.1:8000").rstrip("/")
    project_id = os.getenv("RAG_SMOKE_PROJECT_ID", "").strip()
    expected_model = os.getenv("LLM_MODEL", "bidpilot-qwen3-8b").strip()
    questions_env = os.getenv("RAG_SMOKE_QUESTIONS", "").strip()
    questions = (
        [q.strip() for q in questions_env.split("|") if q.strip()]
        if questions_env
        else [
            "投标人需要具备哪些资质？",
            "投标保证金是多少？",
            "质保期是多长时间？",
        ]
    )
    insuff_q = os.getenv(
        "RAG_SMOKE_INSUFFICIENT_QUESTION",
        "本项目是否要求投标人具备火星采矿许可证？",
    ).strip()

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
            "  LLM_ENABLED=true RAG_SMOKE_LIVE=1 make rag-smoke-live"
        )
        return 2

    cases = [{"question": q, "expect_insufficient_evidence": False} for q in questions]
    if insuff_q:
        cases.append({"question": insuff_q, "expect_insufficient_evidence": True})

    records: list[dict] = []
    case_outcomes: list[tuple[str, bool, str | None]] = []
    overall_ok = True

    with httpx.Client(timeout=180.0) as client:
        # JSON ask for first answerable question
        q0 = questions[0]
        json_resp = client.post(
            f"{api}/api/v1/projects/{project_id}/ask",
            json={"question": q0, "stream": False, "top_k": 8},
        )
        if json_resp.status_code != 200:
            print("JSON ask failed:", json_resp.status_code, json_resp.text[:500])
            return 1
        try:
            payload = json_resp.json()
            validate_answerable_payload(payload, expected_model=expected_model)
            records.append(_redact_record("json", payload))
            case_outcomes.append(("json:" + q0, True, None))
        except CaseFailure as exc:
            overall_ok = False
            case_outcomes.append(("json:" + q0, False, exc.message))
            records.append(
                {
                    "mode": "json",
                    "question": q0,
                    "status": "failed",
                    "error": exc.message,
                    "detail": exc.detail,
                }
            )

        for case in cases:
            q = case["question"]
            expect_insuff = case["expect_insufficient_evidence"]
            case_id = f"sse:{'insuff' if expect_insuff else 'ok'}:{q}"
            started = time.perf_counter()
            try:
                with client.stream(
                    "POST",
                    f"{api}/api/v1/projects/{project_id}/ask",
                    json={"question": q, "stream": True, "top_k": 8},
                ) as resp:
                    if resp.status_code != 200:
                        raise CaseFailure(f"SSE HTTP {resp.status_code}")
                    events = _parse_sse("".join(resp.iter_text()))
                if expect_insuff:
                    final = validate_sse_insufficient(events)
                else:
                    final = validate_sse_answerable(events, expected_model=expected_model)
                rec = _redact_record("sse", final)
                rec["latency_ms"] = round((time.perf_counter() - started) * 1000, 1)
                rec["events"] = [e.get("event") for e in events]
                records.append(rec)
                case_outcomes.append((case_id, True, None))
            except CaseFailure as exc:
                overall_ok = False
                case_outcomes.append((case_id, False, exc.message))
                records.append(
                    {
                        "mode": "sse",
                        "question": q,
                        "status": "failed",
                        "error": exc.message,
                        "detail": exc.detail,
                        "events": exc.events,
                        "latency_ms": round((time.perf_counter() - started) * 1000, 1),
                    }
                )

    out_dir = ROOT / "docs" / "acceptance"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = out_dir / f"rag_smoke_{stamp}.json"
    summary = {
        "created_at": stamp,
        "api": api,
        "project_id": project_id,
        "model_probe": {
            k: llm_body.get(k)
            for k in ("status", "enabled", "model", "base_url", "reachable", "latency_ms")
        },
        "expected_model": expected_model,
        "records": records,
        "case_outcomes": [
            {"id": cid, "ok": ok, "error": err} for cid, ok, err in case_outcomes
        ],
        "note": "Redacted acceptance summary. No raw document bodies included.",
    }
    out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    passed, failures = evaluate_live_cases(case_outcomes)
    if not passed or not overall_ok:
        print("rag_smoke live: FAIL")
        for f in failures:
            print(f"  - {f}")
        print(f"details -> {out_path}")
        return 1

    print(f"rag_smoke live: PASS ({len(records)} records) -> {out_path}")
    return 0


def _assert_ask_payload(payload: dict) -> None:
    """Backward-compatible helper used by older callers; prefers answerable rules."""
    validate_answerable_payload(payload)


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
