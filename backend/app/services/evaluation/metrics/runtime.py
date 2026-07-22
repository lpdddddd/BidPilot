"""Runtime metrics — not mixed into quality score unless profile says so."""

from __future__ import annotations

from app.services.evaluation.metrics.base import MetricObservation, scored_metric


def score(*, duration_ms, prediction, weights) -> list[MetricObservation]:
    # weight 0 by default so aggregator ignores unless profile elevates
    lat = (
        float(duration_ms) if duration_ms is not None else float(prediction.get("duration_ms") or 0)
    )
    return [
        scored_metric(
            "latency_ms",
            value=lat,
            weight=float(weights.get("latency_ms", 0.0)),
            threshold=None,
            reference_kind="rule_expected",
            evidence_summary="runtime only",
        ),
        scored_metric(
            "tool_failure_count",
            value=float(prediction.get("tool_failure_count") or 0),
            weight=float(weights.get("tool_failure_count", 0.0)),
            threshold=None,
            reference_kind="rule_expected",
        ),
        scored_metric(
            "retry_count",
            value=float(prediction.get("retry_count") or 0),
            weight=float(weights.get("retry_count", 0.0)),
            threshold=None,
            reference_kind="rule_expected",
        ),
    ]
