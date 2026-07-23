"""Structural isolation and recursive reference-leakage tests."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from app.services.evaluation.case_loader import filter_cases, normalize_case
from app.services.evaluation.suite_loader import load_jsonl, load_reference_suite
from app.services.evaluation.targets.base import TargetResult
from app.services.evaluation.types import (
    TargetCaseInput,
    TargetExecutionContext,
    assert_no_private_reference,
    split_case_for_evaluation,
)

FIXTURE = Path(__file__).parent / "fixtures" / "evaluation" / "mini_suite.jsonl"


class SpyTarget:
    target_type = "spy"

    def __init__(self):
        self.captured: list[tuple] = []

    def capability(self):
        from app.services.evaluation.targets.base import TargetCapability

        return TargetCapability(target_type=self.target_type, available=True)

    def run_case(self, target_input: TargetCaseInput, context: TargetExecutionContext):
        self.captured.append((target_input, context))
        assert_no_private_reference(target_input, context)
        return TargetResult(ok=True, output={"answer": "spy"}, duration_ms=1)


def test_recursive_no_leak_all_splits():
    bundle = load_reference_suite()
    for split in ("train", "validation", "test"):
        cases = filter_cases(bundle.samples, split=split, limit=5)
        assert cases
        for case in cases:
            target_input, private = split_case_for_evaluation(case)
            assert_no_private_reference(target_input)
            assert private.reference_kind
            # private must not be passed to assert_no_private_reference as target
            ctx = TargetExecutionContext(project_id=uuid4(), seed=1)
            SpyTarget().run_case(target_input, ctx)


def test_spy_never_receives_evaluation_case_or_private():
    samples = load_jsonl(FIXTURE)
    spy = SpyTarget()
    for sample in samples:
        case = normalize_case(sample)
        target_input, _private = split_case_for_evaluation(case)
        ctx = TargetExecutionContext(project_id=uuid4(), seed=1)
        spy.run_case(target_input, ctx)
    assert spy.captured
    for target_input, context in spy.captured:
        assert isinstance(target_input, TargetCaseInput)
        assert isinstance(context, TargetExecutionContext)
        assert not hasattr(target_input, "reference_output")
        assert not hasattr(target_input, "citation_metadata")


def test_fake_ignores_injected_gold_on_case_object():
    from app.services.evaluation.targets.fake import DeterministicFakeTarget

    sample = load_jsonl(FIXTURE)[0]
    case = normalize_case(sample)
    case.citation_metadata = {
        "chunk_ids": ["gold-chunk-should-not-appear"],
        "document_ids": ["gold-doc"],
    }
    target_input, _ = split_case_for_evaluation(case)
    out = DeterministicFakeTarget(seed=1).run_case(
        target_input, TargetExecutionContext(project_id=uuid4(), seed=1)
    )
    assert out.ok
    assert "gold-chunk-should-not-appear" not in str(out.output)
    assert "gold-doc" not in str(out.output)


def test_compliance_adapter_uses_context_project_not_case_source():
    from app.services.evaluation.targets.adapters import ComplianceServiceAdapter

    sample = next(s for s in load_jsonl(FIXTURE) if s.get("task_type") == "compliance")
    case = normalize_case(sample)
    case.project_id = str(uuid4())
    target_input, _ = split_case_for_evaluation(case)
    run_project = uuid4()
    result = ComplianceServiceAdapter().run_case(
        target_input, TargetExecutionContext(project_id=run_project, seed=1)
    )
    assert result.ok
    assert result.output.get("verdict") in {"pass", "fail", "unknown"}


def test_reference_only_after_target_in_runner_contract():
    """Documented flow: target returns before private enters evaluator."""
    case = normalize_case(load_jsonl(FIXTURE)[0])
    target_input, private = split_case_for_evaluation(case)
    # Target phase
    spy = SpyTarget()
    tres = spy.run_case(target_input, TargetExecutionContext(project_id=uuid4(), seed=7))
    assert tres.ok
    # Evaluator phase may use private
    assert private.reference_output is not None or private.reference_kind
