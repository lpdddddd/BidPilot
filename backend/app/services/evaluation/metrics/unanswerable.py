"""Unanswerable / abstention metrics."""

from __future__ import annotations

from app.services.evaluation.metrics.base import MetricObservation, na_metric, scored_metric


def score(case, prediction, *, weights, thresholds) -> list[MetricObservation]:
    ref = case.reference_output
    if not ref:
        names = [
            "abstention_accuracy",
            "false_answer_rate",
            "hallucination_rate",
            "safe_explanation_presence",
            "unsupported_citation_count",
        ]
        return [
            na_metric(
                n,
                weight=float(weights.get(n, 0.0)),
                reason="no_direct_reference",
                reference_kind="no_direct_reference",
            )
            for n in names
        ]
    rk = case.reference_kind
    gold_abs = bool(ref.get("abstain") or ref.get("answerable") is False)
    pred_abs = bool(
        prediction.get("abstain")
        or prediction.get("answerable") is False
        or prediction.get("status") == "abstain"
    )
    abstention_acc = 1.0 if gold_abs == pred_abs else 0.0
    answered = not pred_abs and bool(str(prediction.get("answer") or "").strip())
    false_answer = 1.0 if gold_abs and answered else 0.0
    hallu = float(prediction.get("hallucination_rate") or false_answer)
    explanation = (
        1.0
        if str(
            prediction.get("explanation")
            or prediction.get("safe_explanation")
            or prediction.get("reason")
            or ""
        ).strip()
        else 0.0
    )
    unsupported = float(prediction.get("unsupported_citation_count") or 0)
    return [
        scored_metric(
            "abstention_accuracy",
            value=abstention_acc,
            weight=float(weights.get("abstention_accuracy", 0.35)),
            threshold=thresholds.get("abstention_accuracy", 0.5),
            reference_kind=rk,
        ),
        scored_metric(
            "false_answer_rate",
            value=false_answer,
            weight=float(weights.get("false_answer_rate", 0.25)),
            threshold=thresholds.get("false_answer_rate", 0.0),
            reference_kind=rk,
        ),
        scored_metric(
            "hallucination_rate",
            value=hallu,
            weight=float(weights.get("hallucination_rate", 0.15)),
            threshold=thresholds.get("hallucination_rate", 0.0),
            reference_kind=rk,
        ),
        scored_metric(
            "safe_explanation_presence",
            value=explanation,
            weight=float(weights.get("safe_explanation_presence", 0.15)),
            threshold=thresholds.get("safe_explanation_presence", 0.5),
            reference_kind=rk,
        ),
        scored_metric(
            "unsupported_citation_count",
            value=unsupported,
            weight=float(weights.get("unsupported_citation_count", 0.1)),
            threshold=thresholds.get("unsupported_citation_count", 0.0),
            reference_kind=rk,
        ),
    ]
