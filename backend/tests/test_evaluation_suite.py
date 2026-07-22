"""Suite loader and reference stats tests."""

from __future__ import annotations

from pathlib import Path

from app.services.evaluation.case_loader import (
    assert_no_reference_in_target_input,
    filter_cases,
    normalize_case,
)
from app.services.evaluation.suite_loader import compute_dataset_hash, load_reference_suite

FIXTURE = Path(__file__).parent / "fixtures" / "evaluation" / "mini_suite.jsonl"


def test_load_real_reference_suite_stats():
    bundle = load_reference_suite()
    assert bundle.stats["total_cases"] == 140
    assert bundle.stats["task_family_counts"]["rag"] == 30
    assert bundle.stats["task_family_counts"]["extraction"] == 30
    assert bundle.stats["task_family_counts"]["matching"] == 30
    assert bundle.stats["task_family_counts"]["compliance"] == 20
    assert bundle.stats["task_family_counts"]["drafting"] == 20
    assert bundle.stats["task_family_counts"]["unanswerable"] == 10
    assert bundle.stats["split_counts"]["train"] == 73
    assert bundle.stats["split_counts"]["validation"] == 40
    assert bundle.stats["split_counts"]["test"] == 27
    assert bundle.stats["reference_kind_counts"].get("human_gold", 0) == 0
    assert bundle.stats["reference_kind_counts"]["auto_reference"] == 140
    assert bundle.stats["direct_reference_coverage"] == 1.0
    assert bundle.dataset_hash
    # deterministic hash
    assert (
        compute_dataset_hash(bundle.samples, report=bundle.report, splits=bundle.splits)
        == bundle.dataset_hash
    )


def test_split_project_isolation_in_splits_file():
    bundle = load_reference_suite()
    splits = bundle.splits.get("splits") or bundle.splits
    # project-level split lists should be disjoint when present
    train = set(splits.get("train_projects") or splits.get("train") or [])
    val = set(splits.get("validation_projects") or splits.get("validation") or [])
    test = set(splits.get("test_projects") or splits.get("test") or [])
    if train and val and test and all(isinstance(x, str) and len(x) > 8 for x in list(train)[:3]):
        assert not (train & val)
        assert not (train & test)
        assert not (val & test)


def test_target_input_excludes_reference():
    from app.services.evaluation.suite_loader import load_jsonl

    sample = load_jsonl(FIXTURE)[0]
    case = normalize_case(sample)
    payload = case.target_input()
    assert_no_reference_in_target_input(payload)
    assert "reference_output" not in payload
    # test split summary must not include full reference output
    test_case = normalize_case(load_jsonl(FIXTURE)[2])
    summary = test_case.reference_summary(include_output=True)
    assert "reference_output" not in summary


def test_filter_limit_stable():
    from app.services.evaluation.suite_loader import load_jsonl

    cases = filter_cases(load_jsonl(FIXTURE), limit=2)
    assert len(cases) == 2
    assert cases[0].case_key <= cases[1].case_key
