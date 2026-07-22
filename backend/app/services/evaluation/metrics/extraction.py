"""Requirement extraction field metrics with centralized normalization."""

from __future__ import annotations

from typing import Any

from app.services.evaluation.metrics.base import MetricObservation, na_metric, scored_metric


def normalize_value(value: Any) -> Any:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, list):
        return [normalize_value(v) for v in value]
    text = str(value).strip().lower()
    text = text.replace("，", ",").replace("：", ":")
    if text in {"true", "yes", "是", "mandatory", "必选"}:
        return True
    if text in {"false", "no", "否", "optional", "非必选"}:
        return False
    # amount-like
    for suffix, mult in (("万元", 10000.0), ("元", 1.0)):
        if text.endswith(suffix):
            try:
                return float(text[: -len(suffix)].replace(",", "")) * mult
            except ValueError:
                break
    # date yyyy-mm-dd / yyyy/mm/dd
    for sep in ("-", "/", "."):
        parts = text.split(sep)
        if len(parts) == 3 and all(p.isdigit() for p in parts):
            y, m, d = (int(parts[0]), int(parts[1]), int(parts[2]))
            return f"{y:04d}-{m:02d}-{d:02d}"
    return text


def _fields(ref: dict, pred: dict):
    keys = sorted(set(ref) | set(pred))
    tp = fp = fn = 0
    matched = 0
    considered = 0
    for k in keys:
        if k in {"raw", "text"}:
            continue
        rv, pv = normalize_value(ref.get(k)), normalize_value(pred.get(k))
        if rv is None and pv is None:
            continue
        considered += 1
        if rv is not None and pv is not None and rv == pv:
            tp += 1
            matched += 1
        elif pv is not None and rv is None:
            fp += 1
        elif rv is not None and pv is None:
            fn += 1
        elif rv != pv:
            fp += 1
            fn += 1
    return tp, fp, fn, matched, considered


def score(case, prediction, *, weights, thresholds) -> list[MetricObservation]:
    ref = case.reference_output
    if not ref:
        names = [
            "field_precision",
            "field_recall",
            "field_f1",
            "required_field_coverage",
            "normalized_exact_match",
            "evidence_citation_validity",
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
    pred = prediction.get("extracted") or prediction.get("fields") or prediction
    if not isinstance(pred, dict):
        pred = {}
    tp, fp, fn, matched, considered = _fields(ref, pred)
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    required = [k for k in ("title", "category", "mandatory", "risk_level") if k in ref]
    covered = sum(
        1 for k in required if normalize_value(pred.get(k)) == normalize_value(ref.get(k))
    )
    coverage = covered / len(required) if required else 0.0
    em = 1.0 if considered and matched == considered else 0.0
    # citation validity: predicted citation ids that appear in case evidence
    gold_chunks = {
        str(e.get("chunk_id"))
        for e in (case.evidence or [])
        if isinstance(e, dict) and e.get("chunk_id")
    }
    pred_cites = prediction.get("citations") or []
    if isinstance(pred_cites, list) and pred_cites and gold_chunks:
        ok = sum(
            1
            for c in pred_cites
            if str(c if not isinstance(c, dict) else c.get("chunk_id")) in gold_chunks
        )
        cite_val = ok / len(pred_cites)
    else:
        cite_val = None
    rk = case.reference_kind
    out = [
        scored_metric(
            "field_precision",
            value=precision,
            weight=float(weights.get("field_precision", 0.2)),
            threshold=thresholds.get("field_precision", 0.5),
            reference_kind=rk,
        ),
        scored_metric(
            "field_recall",
            value=recall,
            weight=float(weights.get("field_recall", 0.2)),
            threshold=thresholds.get("field_recall", 0.5),
            reference_kind=rk,
        ),
        scored_metric(
            "field_f1",
            value=f1,
            weight=float(weights.get("field_f1", 0.25)),
            threshold=thresholds.get("field_f1", 0.5),
            reference_kind=rk,
        ),
        scored_metric(
            "required_field_coverage",
            value=coverage,
            weight=float(weights.get("required_field_coverage", 0.15)),
            threshold=thresholds.get("required_field_coverage", 0.5),
            reference_kind=rk,
        ),
        scored_metric(
            "normalized_exact_match",
            value=em,
            weight=float(weights.get("normalized_exact_match", 0.1)),
            threshold=thresholds.get("normalized_exact_match", 0.5),
            reference_kind=rk,
        ),
    ]
    if cite_val is None:
        out.append(
            na_metric(
                "evidence_citation_validity",
                weight=float(weights.get("evidence_citation_validity", 0.1)),
                reason="no citations to validate",
            )
        )
    else:
        out.append(
            scored_metric(
                "evidence_citation_validity",
                value=cite_val,
                weight=float(weights.get("evidence_citation_validity", 0.1)),
                threshold=thresholds.get("evidence_citation_validity", 0.5),
                reference_kind=rk,
            )
        )
    return out
