"""Deterministic evaluation metrics."""

from __future__ import annotations

from typing import Any

from app.services.evaluation.metrics import compliance as compliance_m
from app.services.evaluation.metrics import drafting as drafting_m
from app.services.evaluation.metrics import extraction as extraction_m
from app.services.evaluation.metrics import matching as matching_m
from app.services.evaluation.metrics import retrieval as retrieval_m
from app.services.evaluation.metrics import runtime as runtime_m
from app.services.evaluation.metrics import unanswerable as unanswerable_m
from app.services.evaluation.metrics.base import MetricObservation


def evaluate_case_metrics(
    case: Any,
    prediction: dict[str, Any],
    *,
    profile: dict[str, Any],
    duration_ms: int | None = None,
) -> list[MetricObservation]:
    """Score a prediction against evaluator-facing reference (EvaluatorCaseView).

    ``case`` must expose task_family / reference_* / evidence / citation_metadata.
    Prefer ``EvaluatorCaseView`` built from ``PrivateReferenceBundle`` after target
    returns — do not pass target-facing objects here.
    """
    family = case.task_family
    weights = dict(profile.get("metric_weights") or {})
    thresholds = dict(profile.get("metric_thresholds") or {})
    obs: list[MetricObservation] = []
    if family in {"rag"}:
        obs.extend(retrieval_m.score(case, prediction, weights=weights, thresholds=thresholds))
    elif family == "extraction":
        obs.extend(extraction_m.score(case, prediction, weights=weights, thresholds=thresholds))
    elif family == "matching":
        obs.extend(matching_m.score(case, prediction, weights=weights, thresholds=thresholds))
    elif family == "compliance":
        obs.extend(compliance_m.score(case, prediction, weights=weights, thresholds=thresholds))
    elif family == "drafting":
        obs.extend(drafting_m.score(case, prediction, weights=weights, thresholds=thresholds))
    elif family == "unanswerable":
        obs.extend(unanswerable_m.score(case, prediction, weights=weights, thresholds=thresholds))
    else:
        obs.extend(retrieval_m.score(case, prediction, weights=weights, thresholds=thresholds))
    obs.extend(runtime_m.score(duration_ms=duration_ms, prediction=prediction, weights=weights))
    return obs


__all__ = ["MetricObservation", "evaluate_case_metrics"]
