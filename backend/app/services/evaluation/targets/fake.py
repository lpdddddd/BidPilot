"""Deterministic fake target for CI — only via test override; never reads private reference."""

from __future__ import annotations

import hashlib
import time
from typing import Any

from app.services.evaluation.targets.base import TargetCapability, TargetResult
from app.services.evaluation.types import (
    TargetCaseInput,
    TargetExecutionContext,
    assert_no_private_reference,
)


class DeterministicFakeTarget:
    """Produce deterministic predictions from TargetCaseInput only."""

    target_type = "deterministic_fake"

    def __init__(self, *, seed: int = 42, fail_case_keys: set[str] | None = None):
        self.seed = seed
        self.fail_case_keys = fail_case_keys or set()

    def capability(self) -> TargetCapability:
        return TargetCapability(target_type=self.target_type, available=True)

    def run_case(
        self, target_input: TargetCaseInput, context: TargetExecutionContext
    ) -> TargetResult:
        started = time.perf_counter()
        assert_no_private_reference(target_input, context)
        if target_input.case_key in self.fail_case_keys:
            return TargetResult(ok=False, error_summary="injected case failure", duration_ms=1)
        digest = hashlib.sha256(
            f"{context.seed}:{target_input.case_key}:{target_input.task_family}".encode()
        ).hexdigest()
        out = self._predict(target_input, digest)
        ms = max(1, int((time.perf_counter() - started) * 1000))
        citations = list(out.get("citations") or [])
        retrieved = [str(x) for x in (out.get("retrieved_chunk_ids") or [])]
        return TargetResult(
            ok=True,
            output=out,
            citations=citations if all(isinstance(c, dict) for c in citations) else [],
            retrieved_chunk_ids=retrieved,
            duration_ms=ms,
        )

    def _predict(self, target_input: TargetCaseInput, digest: str) -> dict[str, Any]:
        family = target_input.task_family
        inp = target_input.task_input
        chunk_ids = [str(x) for x in (inp.get("context_chunk_ids") or [])]
        doc_ids = [str(x) for x in (inp.get("document_ids") or [])]
        if family == "rag":
            return {
                "answer": f"fake-answer:{digest[:8]}",
                "answerable": True,
                "citations": [{"chunk_id": c} for c in chunk_ids[:3]],
                "retrieved_chunk_ids": chunk_ids[:5],
                "document_ids": doc_ids[:3],
                "top_k": 5,
            }
        if family == "extraction":
            title = str(inp.get("text") or inp.get("clause") or inp.get("title") or "requirement")[
                :80
            ]
            return {
                "extracted": {
                    "title": title,
                    "category": inp.get("category") or "qualification",
                    "mandatory": True,
                    "risk_level": "high",
                    "normalized_requirement": title,
                },
                "citations": [{"chunk_id": c} for c in chunk_ids[:2]],
            }
        if family == "matching":
            return {
                "status": "insufficient_evidence",
                "reason": "deterministic fake lacks bilateral supplier evidence",
                "evidence_chunk_ids": chunk_ids[:2],
                "match_decision": "insufficient_evidence",
            }
        if family == "compliance":
            return {
                "verdict": "unknown",
                "severity": "info",
                "rule_type": inp.get("rule_type") or "coverage",
                "finding": "deterministic fake offline finding",
                "rule_ids": [str(inp.get("rule_id") or "A001")],
                "citations": [{"chunk_id": c} for c in chunk_ids[:1]],
            }
        if family == "drafting":
            return {
                "outline": ["概述", "资质响应", "风险说明"],
                "summary": "deterministic draft summary",
                "draft_text": "本响应基于已知材料整理，不做满足性承诺。",
                "citations": [{"chunk_id": c} for c in chunk_ids[:2]],
                "supported_claim_rate": 0.8,
                "unsupported_claim_rate": 0.2,
            }
        if family == "unanswerable":
            return {
                "abstain": True,
                "answerable": False,
                "answer": "",
                "status": "abstain",
                "safe_explanation": "证据不足，拒绝作答",
                "unsupported_citation_count": 0,
            }
        return {"answer": digest[:12], "citations": [{"chunk_id": c} for c in chunk_ids[:1]]}
