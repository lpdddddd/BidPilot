"""Metric unit tests."""

from __future__ import annotations

from app.services.evaluation.aggregator import aggregate_case_score
from app.services.evaluation.case_loader import normalize_case
from app.services.evaluation.metrics import evaluate_case_metrics
from app.services.evaluation.metrics.base import MetricObservation, na_metric, scored_metric
from app.services.evaluation.metrics.extraction import normalize_value
from app.services.evaluation.profiles import evaluate_hard_gates, get_profile


def _case(task, ref, inp=None, evidence=None, meta=None, split="validation"):
    return normalize_case(
        {
            "sample_id": f"{task}-x",
            "task_type": task,
            "split": split,
            "label_source": "auto_reference",
            "input": inp or {},
            "reference_output": ref,
            "evidence": evidence or [],
            "citation_metadata": meta or {},
        }
    )


def test_normalization_rules():
    assert normalize_value("是") is True
    assert normalize_value("否") is False
    assert normalize_value("2024/1/2") == "2024-01-02"
    assert normalize_value("1万元") == 10000.0
    assert normalize_value(None) is None


def test_no_reference_metrics_not_applicable():
    case = _case("rag", None)
    profile = get_profile("rag")
    metrics = evaluate_case_metrics(case, {"citations": []}, profile=profile)
    assert any(m.reference_kind == "no_direct_reference" or not m.applicable for m in metrics)
    assert aggregate_case_score([m for m in metrics if not m.applicable]) is None


def test_weight_renormalization_ignores_na():
    metrics = [
        scored_metric("a", value=1.0, weight=1.0, threshold=0.5, reference_kind="auto_reference"),
        na_metric("b", weight=1.0),
        scored_metric("c", value=0.0, weight=1.0, threshold=0.5, reference_kind="auto_reference"),
    ]
    score = aggregate_case_score(metrics)
    assert score == 0.5


def test_retrieval_hit_and_hard_gate_unlocatable():
    case = _case(
        "rag",
        {"answer": "x", "citations": ["c1"]},
        inp={"question": "q"},
        evidence=[{"chunk_id": "c1"}],
        meta={"chunk_ids": ["c1"], "document_ids": ["d1"]},
    )
    profile = get_profile("rag")
    good = evaluate_case_metrics(
        case,
        {"citations": [{"chunk_id": "c1"}], "retrieved_chunk_ids": ["c1"], "top_k": 5},
        profile=profile,
    )
    assert any(m.name == "hit_at_k" and m.value == 1.0 for m in good)
    bad_pred = {"citations": [{"chunk_id": "missing"}], "answer": "完全满足全部资质"}
    gates = evaluate_hard_gates(case, bad_pred, good)
    assert "unlocatable_citation" in gates
    assert "enterprise_fabrication" in gates


def test_extraction_f1():
    case = _case(
        "extraction",
        {"title": "一级资质", "category": "qualification", "mandatory": True, "risk_level": "high"},
        inp={"text": "一级资质"},
    )
    profile = get_profile("extraction")
    metrics = evaluate_case_metrics(
        case,
        {
            "extracted": {
                "title": "一级资质",
                "category": "qualification",
                "mandatory": True,
                "risk_level": "high",
            }
        },
        profile=profile,
    )
    f1 = next(m for m in metrics if m.name == "field_f1")
    assert f1.value == 1.0


def test_compliance_critical_fn():
    case = _case("compliance", {"verdict": "fail", "severity": "critical", "rule_type": "coverage"})
    profile = get_profile("compliance")
    metrics = evaluate_case_metrics(case, {"verdict": "pass", "severity": "info"}, profile=profile)
    cfn = next(m for m in metrics if m.name == "critical_false_negative_count")
    assert cfn.value == 1.0
    assert cfn.passed is False


def test_metric_error_distinct_from_na():
    err = MetricObservation(
        name="judge_score",
        version="1.0.0",
        value=None,
        applicable=False,
        weight=0.0,
        threshold=None,
        passed=None,
        evidence_summary="malformed",
        reference_kind="metric_error",
        error=True,
    )
    assert err.error
    assert err.reference_kind != "not_applicable"
