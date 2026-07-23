"""Reference leakage and spy-target tests."""

from __future__ import annotations

from pathlib import Path

from app.services.evaluation.case_loader import (
    FORBIDDEN_TARGET_KEYS,
    assert_no_reference_in_target_input,
    filter_cases,
    normalize_case,
)
from app.services.evaluation.suite_loader import load_jsonl, load_reference_suite
from app.services.evaluation.targets.base import TargetResult

FIXTURE = Path(__file__).parent / "fixtures" / "evaluation" / "mini_suite.jsonl"


class SpyTarget:
    target_type = "spy"

    def __init__(self):
        self.captured: list[dict] = []

    def capability(self):
        from app.services.evaluation.targets.base import TargetCapability

        return TargetCapability(target_type=self.target_type, available=True)

    def run_case(self, case):
        payload = case.target_input()
        self.captured.append(payload)
        assert_no_reference_in_target_input(payload)
        return TargetResult(ok=True, output={"answer": "spy"}, duration_ms=1)


def test_recursive_no_leak_all_splits():
    bundle = load_reference_suite()
    for split in ("train", "validation", "test"):
        cases = filter_cases(bundle.samples, split=split, limit=5)
        assert cases
        for case in cases:
            payload = case.target_input()
            assert_no_reference_in_target_input(payload)
            assert "citation_chunk_ids" not in (payload.get("context_hints") or {})
            assert "has_evidence" not in (payload.get("context_hints") or {})
            # private reference still available to evaluator
            priv = case.private_reference()
            assert "reference_kind" in priv


def test_spy_target_never_sees_forbidden_keys():
    samples = load_jsonl(FIXTURE)
    spy = SpyTarget()
    for sample in samples:
        case = normalize_case(sample)
        spy.run_case(case)
    assert spy.captured
    for payload in spy.captured:
        blob = str(payload).lower()
        for key in FORBIDDEN_TARGET_KEYS:
            assert f"'{key}'" not in blob and f'"{key}"' not in str(payload)


def test_fake_target_does_not_use_citation_metadata():
    from app.services.evaluation.targets.fake import DeterministicFakeTarget

    sample = load_jsonl(FIXTURE)[0]
    case = normalize_case(sample)
    # Inject gold citation metadata — target must ignore it
    case.citation_metadata = {
        "chunk_ids": ["gold-chunk-should-not-appear"],
        "document_ids": ["gold-doc"],
    }
    out = DeterministicFakeTarget(seed=1).run_case(case)
    assert out.ok
    text = str(out.output)
    assert "gold-chunk-should-not-appear" not in text
    assert "gold-doc" not in text


def test_compliance_adapter_calls_engine_not_fixed_fail():
    from app.services.evaluation.targets.adapters import ComplianceServiceAdapter

    sample = next(s for s in load_jsonl(FIXTURE) if s.get("task_type") == "compliance")
    case = normalize_case(sample)
    case.citation_metadata = {"chunk_ids": ["should-not-leak"]}
    result = ComplianceServiceAdapter().run_case(case)
    assert result.ok
    assert "should-not-leak" not in str(result.output)
    assert result.output.get("verdict") in {"pass", "fail", "unknown"}
    # Must not be a hard-coded critical fail copied from gold
    assert result.output.get("finding") != "adapter offline deterministic"
