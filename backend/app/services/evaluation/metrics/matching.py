"""Supplier matching metrics."""

from __future__ import annotations

from app.services.evaluation.metrics.base import MetricObservation, na_metric, scored_metric


def score(case, prediction, *, weights, thresholds) -> list[MetricObservation]:
    ref = case.reference_output
    names = [
        "match_decision_accuracy",
        "required_capability_coverage",
        "constraint_satisfaction",
        "evidence_support_rate",
        "unsupported_reason_rate",
    ]
    if not ref:
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
    pred_status = str(prediction.get("status") or prediction.get("match_status") or "")
    gold_status = str(ref.get("status") or "")
    acc = 1.0 if pred_status and gold_status and pred_status == gold_status else 0.0
    gold_chunks = {str(x) for x in (ref.get("evidence_chunk_ids") or [])}
    pred_chunks = {
        str(x) for x in (prediction.get("evidence_chunk_ids") or prediction.get("chunk_ids") or [])
    }
    support = len(pred_chunks & gold_chunks) / len(gold_chunks) if gold_chunks else None
    reason = prediction.get("reason") or prediction.get("unsupported_reason")
    unsupported_rate = 1.0 if reason and not pred_chunks else 0.0
    # capability / constraint proxies from reference keys when present
    caps_gold = set(ref.get("required_capabilities") or [])
    caps_pred = set(prediction.get("required_capabilities") or prediction.get("capabilities") or [])
    cap_cov = (len(caps_pred & caps_gold) / len(caps_gold)) if caps_gold else None
    constraints_ok = prediction.get("constraint_satisfied")
    if constraints_ok is None:
        constraints_ok = acc
    out = [
        scored_metric(
            "match_decision_accuracy",
            value=acc,
            weight=float(weights.get("match_decision_accuracy", 0.35)),
            threshold=thresholds.get("match_decision_accuracy", 0.5),
            reference_kind=rk,
        ),
    ]
    if cap_cov is None:
        out.append(
            na_metric(
                "required_capability_coverage",
                weight=float(weights.get("required_capability_coverage", 0.15)),
                reason="no capability reference",
            )
        )
    else:
        out.append(
            scored_metric(
                "required_capability_coverage",
                value=cap_cov,
                weight=float(weights.get("required_capability_coverage", 0.15)),
                threshold=thresholds.get("required_capability_coverage", 0.5),
                reference_kind=rk,
            )
        )
    out.append(
        scored_metric(
            "constraint_satisfaction",
            value=float(constraints_ok),
            weight=float(weights.get("constraint_satisfaction", 0.15)),
            threshold=thresholds.get("constraint_satisfaction", 0.5),
            reference_kind=rk,
        )
    )
    if support is None:
        out.append(
            na_metric(
                "evidence_support_rate",
                weight=float(weights.get("evidence_support_rate", 0.2)),
                reason="no evidence reference chunks",
            )
        )
    else:
        out.append(
            scored_metric(
                "evidence_support_rate",
                value=support,
                weight=float(weights.get("evidence_support_rate", 0.2)),
                threshold=thresholds.get("evidence_support_rate", 0.5),
                reference_kind=rk,
            )
        )
    out.append(
        scored_metric(
            "unsupported_reason_rate",
            value=unsupported_rate,
            weight=float(weights.get("unsupported_reason_rate", 0.15)),
            threshold=None,
            reference_kind=rk,
            evidence_summary="lower is better; not gated",
        )
    )
    return out
