"""Structural isolation and recursive reference-leakage tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch
from uuid import uuid4

from app.models import BidProject, Organization
from app.models.enums import EvaluationRunStatus
from app.services.evaluation.case_loader import filter_cases, normalize_case
from app.services.evaluation.metrics import evaluate_case_metrics
from app.services.evaluation.profiles import get_profile
from app.services.evaluation.service import EvaluationService
from app.services.evaluation.suite_loader import load_jsonl, load_reference_suite
from app.services.evaluation.targets.base import TargetResult
from app.services.evaluation.types import (
    EvaluatorCaseView,
    PrivateReferenceBundle,
    TargetCaseInput,
    TargetExecutionContext,
    assert_no_private_reference,
    build_evaluator_view,
    split_case_for_evaluation,
)
from sqlalchemy.orm import Session

FIXTURE = Path(__file__).parent / "fixtures" / "evaluation" / "mini_suite.jsonl"


class SpyTarget:
    target_type = "spy"

    def __init__(self) -> None:
        self.captured: list[tuple] = []

    def capability(self):
        from app.services.evaluation.targets.base import TargetCapability

        return TargetCapability(target_type=self.target_type, available=True)

    def run_case(self, target_input: TargetCaseInput, context: TargetExecutionContext):
        self.captured.append((target_input, context))
        assert_no_private_reference(target_input, context)
        assert "context_chunk_ids" not in target_input.task_input
        return TargetResult(ok=True, output={"answer": "spy", "citations": []}, duration_ms=1)


def test_recursive_no_leak_all_splits():
    bundle = load_reference_suite()
    for split in ("train", "validation", "test"):
        cases = filter_cases(bundle.samples, split=split, limit=5)
        assert cases
        for case in cases:
            target_input, private = split_case_for_evaluation(case)
            assert_no_private_reference(target_input)
            assert "context_chunk_ids" not in target_input.task_input
            assert private.reference_kind
            if case.input_data.get("context_chunk_ids"):
                assert private.context_chunk_ids == [
                    str(x) for x in case.input_data["context_chunk_ids"]
                ]
            assert private.source_document_id == case.document_id
            SpyTarget().run_case(target_input, TargetExecutionContext(project_id=uuid4(), seed=1))


def test_spy_never_receives_evaluation_case_or_private():
    spy = SpyTarget()
    for sample in load_jsonl(FIXTURE):
        case = normalize_case(sample)
        target_input, private = split_case_for_evaluation(case)
        spy.run_case(target_input, TargetExecutionContext(project_id=uuid4(), seed=1))
        assert isinstance(private, PrivateReferenceBundle)
    assert spy.captured
    for target_input, context in spy.captured:
        assert isinstance(target_input, TargetCaseInput)
        assert isinstance(context, TargetExecutionContext)
        assert "context_chunk_ids" not in target_input.task_input
        assert "reference_output" not in target_input.task_input


def test_whitelist_drops_context_chunk_ids_and_source_document():
    case = normalize_case(load_jsonl(FIXTURE)[0])
    assert case.input_data.get("context_chunk_ids") == ["chunk-a"]
    target_input, private = split_case_for_evaluation(case)
    assert "context_chunk_ids" not in target_input.task_input
    assert private.context_chunk_ids == ["chunk-a"]
    assert private.source_document_id == case.document_id
    assert private.citation_metadata is not None


def test_fake_does_not_echo_gold_chunk_ids():
    from app.services.evaluation.targets.fake import DeterministicFakeTarget

    case = normalize_case(load_jsonl(FIXTURE)[0])
    target_input, private = split_case_for_evaluation(case)
    assert private.context_chunk_ids == ["chunk-a"]
    out = DeterministicFakeTarget(seed=1).run_case(
        target_input, TargetExecutionContext(project_id=uuid4(), seed=1)
    )
    assert out.ok
    assert "chunk-a" not in str(out.output)
    assert "chunk-a" not in str(out.citations)
    assert "chunk-a" not in str(out.retrieved_chunk_ids)


def test_evaluator_uses_private_reference_bundle():
    case = normalize_case(load_jsonl(FIXTURE)[0])
    _ti, private = split_case_for_evaluation(case)
    view = build_evaluator_view(
        case_key=case.case_key,
        task_family=case.task_family,
        split=case.split,
        content_hash=case.content_hash,
        private=private,
    )
    assert isinstance(view, EvaluatorCaseView)
    assert view.reference_output == private.reference_output
    metrics = evaluate_case_metrics(
        view, {"answer": "x", "citations": [], "top_k": 5}, profile=get_profile("rag")
    )
    assert metrics
    try:
        assert_no_private_reference(private)
        raise AssertionError("PrivateReferenceBundle must be rejected as target input")
    except ValueError:
        pass


def test_runner_path_isolation_snapshots_and_export(db: Session):
    org = Organization(name=f"LeakOrg-{uuid4().hex[:6]}")
    db.add(org)
    db.flush()
    project = BidProject(organization_id=org.id, project_code="LK", project_name="Leak")
    db.add(project)
    db.commit()

    spy = SpyTarget()
    evaluator_seen: list[Any] = []
    real_evaluate = evaluate_case_metrics

    def _fake_get_target(target_type, *, config=None, db=None):
        return spy

    def _spy_evaluate(case, prediction, *, profile, duration_ms=None):
        evaluator_seen.append(case)
        assert isinstance(case, EvaluatorCaseView)
        assert isinstance(case.private, PrivateReferenceBundle)
        return real_evaluate(case, prediction, profile=profile, duration_ms=duration_ms)

    with (
        patch("app.services.evaluation.runner.get_target", _fake_get_target),
        patch("app.services.evaluation.runner.evaluate_case_metrics", _spy_evaluate),
    ):
        run, _ = EvaluationService(db).create_run(
            project.id,
            {
                "target_type": "deterministic_fake",
                "fixture_path": str(FIXTURE),
                "seed": 3,
                "case_limit": 3,
            },
            execute=True,
        )

    assert run.status in {
        EvaluationRunStatus.completed,
        EvaluationRunStatus.partial,
        EvaluationRunStatus.failed,
    }
    assert spy.captured
    assert evaluator_seen
    for target_input, _ctx in spy.captured:
        assert_no_private_reference(target_input)
        assert "context_chunk_ids" not in target_input.task_input

    for row in run.case_results:
        snap_in = row.input_snapshot or {}
        assert "context_chunk_ids" not in str(snap_in)
        assert "reference_output" not in str(snap_in)
        assert "reference_output" not in (row.reference_summary or {})
        assert "chunk-a" not in str(snap_in)

    body, _media = EvaluationService(db).export(project.id, run.id, fmt="json")
    assert "chunk-a" not in body.lower()
    assert "reference_output" not in body.lower()
    assert "/root/autodl-tmp" not in body.lower()
