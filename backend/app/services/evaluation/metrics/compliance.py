"""Compliance metrics including critical false negatives."""

from __future__ import annotations

from app.services.evaluation.metrics.base import MetricObservation, na_metric, scored_metric


def score(case, prediction, *, weights, thresholds) -> list[MetricObservation]:
    ref = case.reference_output
    names = [
        "verdict_accuracy",
        "macro_precision",
        "macro_recall",
        "macro_f1",
        "critical_false_negative_count",
        "rule_coverage",
        "evidence_validity",
        "unsupported_compliance_claim_count",
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
    gold_v = str(ref.get("verdict") or ref.get("status") or "").lower()
    pred_v = str(prediction.get("verdict") or prediction.get("status") or "").lower()
    verdict_acc = 1.0 if gold_v and pred_v and gold_v == pred_v else 0.0
    gold_sev = str(ref.get("severity") or "").lower()
    pred_sev = str(prediction.get("severity") or "").lower()
    # macro P/R/F1 over {verdict, severity, rule_type} labels when present
    labels = []
    for key in ("verdict", "severity", "rule_type"):
        g, p = ref.get(key), prediction.get(key)
        if g is not None or p is not None:
            labels.append(
                (
                    str(g).lower() if g is not None else None,
                    str(p).lower() if p is not None else None,
                )
            )
    tp = sum(1 for g, p in labels if g is not None and g == p)
    fp = sum(1 for g, p in labels if p is not None and g != p)
    fn = sum(1 for g, p in labels if g is not None and g != p)
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    # critical FN: gold critical/fail but prediction pass/ok
    critical_fn = 0.0
    if (gold_sev in {"critical", "error"} or gold_v in {"fail", "failed", "non_compliant"}) and (
        pred_v in {"pass", "passed", "ok", "compliant"} or pred_sev in {"info", "ok"}
    ):
        critical_fn = 1.0
    rule_ids_gold = set(ref.get("rule_ids") or ([ref["rule_type"]] if ref.get("rule_type") else []))
    rule_ids_pred = set(
        prediction.get("rule_ids")
        or ([prediction["rule_type"]] if prediction.get("rule_type") else [])
    )
    rule_cov = (len(rule_ids_pred & rule_ids_gold) / len(rule_ids_gold)) if rule_ids_gold else None
    unsupported = float(prediction.get("unsupported_claim_count") or 0)
    evid = prediction.get("evidence_valid")
    if evid is None:
        evid_score = 1.0 if prediction.get("citations") or prediction.get("finding") else None
    else:
        evid_score = 1.0 if evid else 0.0
    out = [
        scored_metric(
            "verdict_accuracy",
            value=verdict_acc,
            weight=float(weights.get("verdict_accuracy", 0.25)),
            threshold=thresholds.get("verdict_accuracy", 0.5),
            reference_kind=rk,
        ),
        scored_metric(
            "macro_precision",
            value=precision,
            weight=float(weights.get("macro_precision", 0.1)),
            threshold=thresholds.get("macro_precision", 0.5),
            reference_kind=rk,
        ),
        scored_metric(
            "macro_recall",
            value=recall,
            weight=float(weights.get("macro_recall", 0.1)),
            threshold=thresholds.get("macro_recall", 0.5),
            reference_kind=rk,
        ),
        scored_metric(
            "macro_f1",
            value=f1,
            weight=float(weights.get("macro_f1", 0.15)),
            threshold=thresholds.get("macro_f1", 0.5),
            reference_kind=rk,
        ),
        scored_metric(
            "critical_false_negative_count",
            value=critical_fn,
            weight=float(weights.get("critical_false_negative_count", 0.2)),
            threshold=thresholds.get("critical_false_negative_count", 0.0),
            reference_kind=rk,
            evidence_summary="0 means no critical FN; threshold 0 => pass only if 0",
        ),
    ]
    # critical_fn passes only when count <= threshold (default 0)
    out[-1].passed = critical_fn <= float(thresholds.get("critical_false_negative_count", 0.0))
    if rule_cov is None:
        out.append(
            na_metric(
                "rule_coverage",
                weight=float(weights.get("rule_coverage", 0.1)),
                reason="no rule ids",
            )
        )
    else:
        out.append(
            scored_metric(
                "rule_coverage",
                value=rule_cov,
                weight=float(weights.get("rule_coverage", 0.1)),
                threshold=thresholds.get("rule_coverage", 0.5),
                reference_kind=rk,
            )
        )
    if evid_score is None:
        out.append(
            na_metric(
                "evidence_validity",
                weight=float(weights.get("evidence_validity", 0.05)),
                reason="no evidence signal",
            )
        )
    else:
        out.append(
            scored_metric(
                "evidence_validity",
                value=evid_score,
                weight=float(weights.get("evidence_validity", 0.05)),
                threshold=thresholds.get("evidence_validity", 0.5),
                reference_kind=rk,
            )
        )
    out.append(
        scored_metric(
            "unsupported_compliance_claim_count",
            value=unsupported,
            weight=float(weights.get("unsupported_compliance_claim_count", 0.05)),
            threshold=thresholds.get("unsupported_compliance_claim_count", 0.0),
            reference_kind=rk,
        )
    )
    out[-1].passed = unsupported <= float(thresholds.get("unsupported_compliance_claim_count", 0.0))
    return out
