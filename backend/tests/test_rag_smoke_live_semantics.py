"""Unit tests for live RAG smoke failure / success semantics.

These tests exercise validation helpers in scripts/rag_smoke_accept.py only.
They do NOT claim a real vLLM E2E.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "rag_smoke_accept.py"


def _load_smoke():
    spec = importlib.util.spec_from_file_location("rag_smoke_accept", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["rag_smoke_accept"] = mod
    spec.loader.exec_module(mod)
    return mod


smoke = _load_smoke()


def _ok_citation(**overrides):
    base = {
        "source_id": "S1",
        "chunk_id": "chunk-1",
        "document_id": "doc-1",
        "file_name": "tender.txt",
        "section": "第一章",
        "clause_id": None,
        "page_start": None,
        "page_end": None,
        "excerpt": "证据片段",
    }
    base.update(overrides)
    return base


def _ok_final(**overrides):
    base = {
        "question": "资质？",
        "status": "answered",
        "answer": "须具备贰级资质 [S1]。",
        "citations": [_ok_citation()],
        "generation_trace": {
            "model": "bidpilot-qwen3-8b",
            "latency_ms": 12.5,
            "context_chunk_count": 1,
        },
    }
    base.update(overrides)
    return base


def _sse(events: list[tuple[str, dict]]) -> list[dict]:
    return [{"event": name, "data": data} for name, data in events]


def test_sse_error_fails():
    events = _sse(
        [
            ("retrieval", {"status": "ok"}),
            ("generation_started", {}),
            ("error", {"message": "引用校验失败", "detail": "unknown [S99]"}),
        ]
    )
    with pytest.raises(smoke.CaseFailure) as exc:
        smoke.validate_sse_answerable(events, expected_model="bidpilot-qwen3-8b")
    assert "SSE error" in exc.value.message


def test_missing_final_fails():
    events = _sse(
        [
            ("retrieval", {"status": "ok"}),
            ("generation_started", {}),
        ]
    )
    with pytest.raises(smoke.CaseFailure) as exc:
        smoke.validate_sse_answerable(events, expected_model="bidpilot-qwen3-8b")
    assert "exactly one final" in exc.value.message


def test_empty_answer_fails():
    payload = _ok_final(answer="")
    with pytest.raises(smoke.CaseFailure) as exc:
        smoke.validate_answerable_payload(payload, expected_model="bidpilot-qwen3-8b")
    assert "answer is empty" in exc.value.message


def test_unknown_citation_fails():
    payload = _ok_final(answer="结论 [S99]。")
    with pytest.raises(smoke.CaseFailure) as exc:
        smoke.validate_answerable_payload(payload, expected_model="bidpilot-qwen3-8b")
    assert "unknown S99" in exc.value.message


def test_legal_final_succeeds():
    events = _sse(
        [
            ("retrieval", {"status": "ok"}),
            ("generation_started", {"model": "bidpilot-qwen3-8b"}),
            ("final", {"result": _ok_final()}),
        ]
    )
    result = smoke.validate_sse_answerable(events, expected_model="bidpilot-qwen3-8b")
    assert result["citations"][0]["source_id"] == "S1"


def test_insufficient_safety_answer_succeeds():
    events = _sse(
        [
            ("retrieval", {"status": "insufficient_evidence"}),
            (
                "final",
                {
                    "result": {
                        "status": "insufficient_evidence",
                        "answer": "当前资料不足以确认。",
                        "citations": [],
                    }
                },
            ),
        ]
    )
    result = smoke.validate_sse_insufficient(events)
    assert "不足以确认" in result["answer"]


def test_insufficient_with_error_fails():
    events = _sse(
        [
            ("retrieval", {"status": "ok"}),
            ("error", {"message": "大模型暂时不可用", "detail": "timeout"}),
        ]
    )
    with pytest.raises(smoke.CaseFailure) as exc:
        smoke.validate_sse_insufficient(events)
    assert "must not receive SSE error" in exc.value.message


def test_aggregate_one_failure_blocks_pass():
    outcomes = [
        ("a", True, None),
        ("b", False, "SSE error"),
        ("c", True, None),
    ]
    ok, failures = smoke.evaluate_live_cases(outcomes)
    assert ok is False
    assert len(failures) == 1
    assert "b:" in failures[0]


def test_citation_requires_locator():
    payload = _ok_final(
        citations=[_ok_citation(section=None, clause_id=None, page_start=None, page_end=None)]
    )
    with pytest.raises(smoke.CaseFailure) as exc:
        smoke.validate_answerable_payload(payload, expected_model="bidpilot-qwen3-8b")
    assert "locator" in exc.value.message


def test_model_mismatch_fails():
    payload = _ok_final(generation_trace={"model": "other-model", "latency_ms": 1.0})
    with pytest.raises(smoke.CaseFailure) as exc:
        smoke.validate_answerable_payload(payload, expected_model="bidpilot-qwen3-8b")
    assert "model mismatch" in exc.value.message
