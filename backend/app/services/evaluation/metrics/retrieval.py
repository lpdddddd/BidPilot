"""Retrieval metrics — only when direct chunk/doc references exist."""

from __future__ import annotations

from typing import Any

from app.services.evaluation.case_loader import EvaluationCase
from app.services.evaluation.metrics.base import MetricObservation, na_metric, scored_metric


def _gold_ids(case: EvaluationCase) -> tuple[set[str], set[str], set[str]]:
    meta = case.citation_metadata or {}
    chunks = {str(x) for x in (meta.get("chunk_ids") or []) if x}
    docs = {str(x) for x in (meta.get("document_ids") or []) if x}
    pages = {str(x) for x in (meta.get("page_numbers") or []) if x is not None}
    ref = case.reference_output or {}
    for c in ref.get("citations") or []:
        chunks.add(str(c))
    return chunks, docs, pages


def _pred_ids(prediction: dict[str, Any]) -> tuple[list[str], list[str], list[str]]:
    chunks: list[str] = []
    docs: list[str] = []
    pages: list[str] = []
    for c in prediction.get("citations") or prediction.get("retrieved_chunk_ids") or []:
        if isinstance(c, dict):
            if c.get("chunk_id"):
                chunks.append(str(c["chunk_id"]))
            if c.get("document_id"):
                docs.append(str(c["document_id"]))
            if c.get("page") is not None or c.get("page_start") is not None:
                pages.append(str(c.get("page", c.get("page_start"))))
        else:
            chunks.append(str(c))
    for d in prediction.get("document_ids") or []:
        docs.append(str(d))
    return chunks, docs, pages


def score(case, prediction, *, weights, thresholds) -> list[MetricObservation]:
    gold_c, gold_d, gold_p = _gold_ids(case)
    has_direct = bool(gold_c or gold_d) and case.reference_kind in {
        "auto_reference",
        "rule_expected",
        "human_gold",
    }
    names = ["hit_at_k", "recall_at_k", "mrr", "document_hit", "page_hit", "chunk_hit"]
    if not has_direct:
        return [
            na_metric(
                n,
                weight=float(weights.get(n, 0.0)),
                reason="executed_without_direct_reference",
                reference_kind="executed_without_direct_reference",
            )
            for n in names
        ]
    pred_c, pred_d, pred_p = _pred_ids(prediction)
    k = int(prediction.get("top_k") or max(len(pred_c), 5) or 5)
    top = pred_c[:k]
    hit = 1.0 if gold_c and any(c in gold_c for c in top) else (1.0 if not gold_c and top else 0.0)
    recalled = len(set(top) & gold_c) / len(gold_c) if gold_c else None
    mrr = 0.0
    for i, c in enumerate(top, start=1):
        if c in gold_c:
            mrr = 1.0 / i
            break
    doc_hit = 1.0 if gold_d and any(d in gold_d for d in pred_d) else (0.0 if gold_d else None)
    page_hit = 1.0 if gold_p and any(p in gold_p for p in pred_p) else (0.0 if gold_p else None)
    chunk_hit = 1.0 if gold_c and any(c in gold_c for c in pred_c) else 0.0
    rk = case.reference_kind
    out: list[MetricObservation] = []
    out.append(
        scored_metric(
            "hit_at_k",
            value=hit,
            weight=float(weights.get("hit_at_k", 0.25)),
            threshold=thresholds.get("hit_at_k", 0.5),
            reference_kind=rk,
            evidence_summary=f"k={k}",
        )
    )
    if recalled is None:
        out.append(
            na_metric(
                "recall_at_k",
                weight=float(weights.get("recall_at_k", 0.25)),
                reason="no gold chunks",
            )
        )
    else:
        out.append(
            scored_metric(
                "recall_at_k",
                value=recalled,
                weight=float(weights.get("recall_at_k", 0.25)),
                threshold=thresholds.get("recall_at_k", 0.5),
                reference_kind=rk,
            )
        )
    out.append(
        scored_metric(
            "mrr",
            value=mrr,
            weight=float(weights.get("mrr", 0.2)),
            threshold=thresholds.get("mrr", 0.3),
            reference_kind=rk,
        )
    )
    if doc_hit is None:
        out.append(
            na_metric(
                "document_hit",
                weight=float(weights.get("document_hit", 0.1)),
                reason="no gold docs",
            )
        )
    else:
        out.append(
            scored_metric(
                "document_hit",
                value=doc_hit,
                weight=float(weights.get("document_hit", 0.1)),
                threshold=thresholds.get("document_hit", 0.5),
                reference_kind=rk,
            )
        )
    if page_hit is None:
        out.append(
            na_metric(
                "page_hit", weight=float(weights.get("page_hit", 0.1)), reason="no gold pages"
            )
        )
    else:
        out.append(
            scored_metric(
                "page_hit",
                value=page_hit,
                weight=float(weights.get("page_hit", 0.1)),
                threshold=thresholds.get("page_hit", 0.5),
                reference_kind=rk,
            )
        )
    out.append(
        scored_metric(
            "chunk_hit",
            value=chunk_hit,
            weight=float(weights.get("chunk_hit", 0.1)),
            threshold=thresholds.get("chunk_hit", 0.5),
            reference_kind=rk,
        )
    )
    return out
