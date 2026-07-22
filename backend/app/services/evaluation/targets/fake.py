"""Deterministic fake target for CI — never reads reference_output."""

from __future__ import annotations

import hashlib
import time
from typing import Any

from app.services.evaluation.case_loader import EvaluationCase, assert_no_reference_in_target_input
from app.services.evaluation.targets.base import TargetCapability, TargetResult


class DeterministicFakeTarget:
    """Produce deterministic predictions from case input only."""

    target_type = "deterministic_fake"

    def __init__(self, *, seed: int = 42, fail_case_keys: set[str] | None = None):
        self.seed = seed
        self.fail_case_keys = fail_case_keys or set()

    def capability(self) -> TargetCapability:
        return TargetCapability(target_type=self.target_type, available=True, reason=None)

    def run_case(self, case: EvaluationCase) -> TargetResult:
        started = time.perf_counter()
        payload = case.target_input()
        assert_no_reference_in_target_input(payload)
        if case.case_key in self.fail_case_keys:
            return TargetResult(ok=False, error_summary="injected case failure", duration_ms=1)
        digest = hashlib.sha256(
            f"{self.seed}:{case.case_key}:{case.task_family}".encode()
        ).hexdigest()
        out = self._predict(case, digest)
        ms = max(1, int((time.perf_counter() - started) * 1000))
        return TargetResult(ok=True, output=out, duration_ms=ms)

    def _predict(self, case: EvaluationCase, digest: str) -> dict[str, Any]:
        family = case.task_family
        inp = case.input_data
        # Use only input / citation_metadata chunk ids as "retrieved" — never reference answers.
        chunk_ids = list(
            (case.citation_metadata or {}).get("chunk_ids") or inp.get("context_chunk_ids") or []
        )
        doc_ids = list((case.citation_metadata or {}).get("document_ids") or [])
        if case.document_id:
            doc_ids = doc_ids or [case.document_id]
        if family == "rag":
            return {
                "answer": f"fake-answer:{digest[:8]}",
                "answerable": True,
                "citations": [
                    {"chunk_id": c, "document_id": (doc_ids[0] if doc_ids else None)}
                    for c in chunk_ids[:3]
                ],
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
                "citations": chunk_ids[:2],
            }
        if family == "matching":
            return {
                "status": "insufficient_evidence",
                "reason": "deterministic fake lacks bilateral evidence",
                "evidence_chunk_ids": chunk_ids[:2],
            }
        if family == "compliance":
            return {
                "verdict": "fail",
                "severity": "critical",
                "rule_type": inp.get("rule_type") or "coverage",
                "finding": "deterministic fake finding",
                "rule_ids": [str(inp.get("rule_id") or "A001")],
                "citations": chunk_ids[:1],
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
        return {"answer": digest[:12], "citations": chunk_ids[:1]}
