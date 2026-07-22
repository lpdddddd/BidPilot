"""Shared metric result types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class MetricObservation:
    name: str
    version: str
    value: float | None
    applicable: bool
    weight: float
    threshold: float | None
    passed: bool | None
    evidence_summary: str | None
    reference_kind: str
    error: bool = False
    extras: dict[str, Any] = field(default_factory=dict)


def na_metric(
    name: str,
    *,
    version: str = "1.0.0",
    weight: float = 0.0,
    reason: str = "not_applicable",
    reference_kind: str = "not_applicable",
) -> MetricObservation:
    return MetricObservation(
        name=name,
        version=version,
        value=None,
        applicable=False,
        weight=weight,
        threshold=None,
        passed=None,
        evidence_summary=reason,
        reference_kind=reference_kind,
    )


def error_metric(
    name: str,
    *,
    version: str = "1.0.0",
    weight: float = 0.0,
    summary: str,
) -> MetricObservation:
    return MetricObservation(
        name=name,
        version=version,
        value=None,
        applicable=False,
        weight=weight,
        threshold=None,
        passed=None,
        evidence_summary=summary,
        reference_kind="metric_error",
        error=True,
    )


def scored_metric(
    name: str,
    *,
    value: float,
    weight: float,
    threshold: float | None,
    reference_kind: str,
    version: str = "1.0.0",
    evidence_summary: str | None = None,
) -> MetricObservation:
    passed = None if threshold is None else bool(value >= threshold)
    return MetricObservation(
        name=name,
        version=version,
        value=float(value),
        applicable=True,
        weight=weight,
        threshold=threshold,
        passed=passed,
        evidence_summary=evidence_summary,
        reference_kind=reference_kind,
    )
