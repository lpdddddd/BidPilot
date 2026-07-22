"""Optional second-pass consistency judge (LLM or deterministic mock)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from bidpilot_data.labeling.evidence import quote_supported_by_chunk
from bidpilot_data.logging import get_logger
from bidpilot_data.reference_dataset.schema import ReferenceSample
from bidpilot_data.reference_dataset.validate import quote_contiguous_in_text

log = get_logger(__name__)


class JudgeResult(BaseModel):
    ok: bool = True
    score: float = Field(default=1.0, ge=0.0, le=1.0)
    reasons: list[str] = Field(default_factory=list)
    mode: str = "mock"


def _mock_judge_sample(
    sample: ReferenceSample,
    *,
    chunk_index: dict[str, dict[str, Any]],
) -> JudgeResult:
    reasons: list[str] = []
    score = 1.0

    # Grounding: each non-empty quote should appear in some cited/evidence chunk
    quotes: list[str] = []
    chunk_ids: list[str] = list(sample.citation_metadata.chunk_ids)
    for ev in sample.evidence:
        if ev.quote:
            quotes.append(ev.quote)
        if ev.chunk_id:
            chunk_ids.append(ev.chunk_id)
    quotes.extend(sample.citation_metadata.quotes)

    if sample.task_type == "unanswerable":
        # Empty or irrelevant evidence is fine; reject if answer looks definitive without abstain.
        ans = str((sample.reference_output or {}).get("answer") or "")
        if ans and "无法" not in ans and "不足" not in ans and "未" not in ans[:40]:
            # Soft: still ok if status says insufficient
            if not (sample.reference_output or {}).get("abstain", True):
                reasons.append("unanswerable answer lacks abstain language")
                score -= 0.4
        return JudgeResult(ok=score >= 0.6, score=max(0.0, score), reasons=reasons, mode="mock")

    if quotes and chunk_ids and chunk_index:
        grounded = 0
        for q in quotes:
            ok_q = False
            for cid in chunk_ids:
                ch = chunk_index.get(cid)
                if not ch:
                    continue
                text = ch.get("text") or ""
                if quote_contiguous_in_text(q, text) or quote_supported_by_chunk(q, text, min_ratio=0.9):
                    ok_q = True
                    break
            if ok_q:
                grounded += 1
            else:
                reasons.append("quote_in_content failed")
        if quotes:
            score = grounded / len(quotes)
    elif sample.task_type in {"rag", "extraction", "compliance", "drafting"} and not quotes:
        reasons.append("missing quotes for answerable task")
        score = 0.3

    # Matching status sanity
    if sample.task_type == "matching":
        status = str((sample.reference_output or {}).get("status") or "")
        allowed = {
            "supported",
            "partially_supported",
            "insufficient_evidence",
            "conflicting",
            "not_applicable",
        }
        if status not in allowed:
            reasons.append(f"invalid match status={status}")
            score = 0.0

    return JudgeResult(ok=score >= 0.6 and not any("invalid" in r for r in reasons), score=score, reasons=reasons, mode="mock")


def _llm_judge_sample(sample: ReferenceSample, *, chunk_index: dict[str, dict[str, Any]]) -> JudgeResult:
    try:
        from bidpilot_data.labeling.llm_client import OpenAICompatibleClient

        client = OpenAICompatibleClient()
        if not client.available:
            return _mock_judge_sample(sample, chunk_index=chunk_index)

        class _JudgeSchema(BaseModel):
            ok: bool
            score: float = 0.0
            reasons: list[str] = Field(default_factory=list)

        system = (
            "You are a consistency judge for procurement reference samples. "
            "Check that quotes appear in evidence, answers are grounded, and unanswerable "
            "samples abstain. Return JSON {ok, score, reasons}."
        )
        user = (
            f"task_type={sample.task_type}\n"
            f"input={sample.input}\n"
            f"reference_output={sample.reference_output}\n"
            f"evidence={[e.model_dump() for e in sample.evidence]}\n"
            f"citations={sample.citation_metadata.model_dump()}\n"
        )
        parsed, meta = client.chat_json(system=system, user=user, schema_model=_JudgeSchema, temperature=0.0)
        return JudgeResult(
            ok=parsed.ok and parsed.score >= 0.5,
            score=float(parsed.score),
            reasons=list(parsed.reasons),
            mode=f"llm:{meta.get('model_name')}",
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("llm judge unavailable, falling back to mock: %s", exc)
        return _mock_judge_sample(sample, chunk_index=chunk_index)


def judge_sample(
    sample: ReferenceSample,
    *,
    chunk_index: dict[str, dict[str, Any]],
    use_llm: bool = False,
) -> JudgeResult:
    if use_llm:
        return _llm_judge_sample(sample, chunk_index=chunk_index)
    return _mock_judge_sample(sample, chunk_index=chunk_index)


def apply_judge(
    sample: ReferenceSample,
    *,
    chunk_index: dict[str, dict[str, Any]],
    use_llm: bool = False,
) -> tuple[bool, ReferenceSample, JudgeResult]:
    result = judge_sample(sample, chunk_index=chunk_index, use_llm=use_llm)
    qc = sample.quality_checks.model_copy(deep=True)
    qc.judge_ok = result.ok
    if result.reasons:
        qc.messages = list(qc.messages) + [f"judge:{r}" for r in result.reasons]
    conf = min(sample.confidence, result.score) if result.ok else min(sample.confidence, result.score * 0.5)
    updated = sample.model_copy(update={"quality_checks": qc, "confidence": conf})
    return result.ok, updated, result
