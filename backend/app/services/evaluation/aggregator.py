"""Score aggregation with applicable-only weight renormalization."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from app.services.evaluation.metrics.base import MetricObservation


def aggregate_case_score(metrics: list[MetricObservation]) -> float | None:
    applicable = [
        m for m in metrics if m.applicable and not m.error and m.value is not None and m.weight > 0
    ]
    if not applicable:
        return None
    total_w = sum(m.weight for m in applicable)
    if total_w <= 0:
        return None
    # Renormalize weights
    total = 0.0
    for m in applicable:
        assert m.value is not None
        total += float(m.value) * (m.weight / total_w)
    return total


def aggregate_run(
    case_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Aggregate case rows (score/passed/status/family/reference/hard gates)."""
    scored = [
        c
        for c in case_rows
        if c.get("score") is not None and c.get("status") in {"passed", "failed"}
    ]
    overall = None
    if scored:
        overall = sum(float(c["score"]) for c in scored) / len(scored)
    total = len(case_rows)
    passed = sum(1 for c in case_rows if c.get("passed") is True)
    failed = sum(1 for c in case_rows if c.get("status") == "failed" or c.get("passed") is False)
    errors = sum(1 for c in case_rows if c.get("status") == "error")
    family_scores: dict[str, list[float]] = defaultdict(list)
    for c in scored:
        family_scores[str(c.get("task_family"))].append(float(c["score"]))
    task_family_scores = {k: sum(v) / len(v) for k, v in sorted(family_scores.items())}
    direct = sum(
        1
        for c in case_rows
        if c.get("reference_kind") in {"auto_reference", "rule_expected", "human_gold"}
    )
    return {
        "overall_score": overall,
        "pass_rate": (passed / total) if total else 0.0,
        "error_rate": (errors / total) if total else 0.0,
        "reference_coverage": (direct / total) if total else 0.0,
        "task_family_scores": task_family_scores,
        "passed_cases": passed,
        "failed_cases": failed,
        "error_cases": errors,
        "total_cases": total,
        "scored_cases": len(scored),
        "hard_gate_failure_count": sum(len(c.get("hard_gate_failures") or []) for c in case_rows),
    }
