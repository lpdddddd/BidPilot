"""Drafting structure and evidence metrics."""

from __future__ import annotations

import re

from app.services.evaluation.metrics.base import MetricObservation, na_metric, scored_metric

_SENSITIVE_RE = re.compile(r"(api[_-]?key|authorization|bearer\s+\S+|postgres(?:ql)?://\S+)", re.I)
_FABRICATE_RE = re.compile(r"(完全满足|保证中标|虚构业绩|伪造资质)")


def score(case, prediction, *, weights, thresholds) -> list[MetricObservation]:
    ref = case.reference_output
    rk = case.reference_kind if ref else "no_direct_reference"
    text = str(
        prediction.get("draft_text") or prediction.get("summary") or prediction.get("answer") or ""
    )
    outline = prediction.get("outline") or prediction.get("sections") or []
    if isinstance(outline, str):
        outline = [outline]
    gold_outline = (ref or {}).get("outline") or []
    if isinstance(gold_outline, str):
        gold_outline = [gold_outline]
    if gold_outline:
        covered = sum(
            1 for s in gold_outline if any(str(s) in str(x) or str(x) in str(s) for x in outline)
        )
        section_cov = covered / len(gold_outline)
    else:
        section_cov = 1.0 if outline or text else 0.0
        if not ref:
            section_cov_metric = na_metric(
                "required_section_coverage",
                weight=float(weights.get("required_section_coverage", 0.2)),
                reason="no_direct_reference",
                reference_kind="no_direct_reference",
            )
        else:
            section_cov_metric = scored_metric(
                "required_section_coverage",
                value=section_cov,
                weight=float(weights.get("required_section_coverage", 0.2)),
                threshold=thresholds.get("required_section_coverage", 0.5),
                reference_kind=rk,
            )
    if ref and gold_outline:
        section_cov_metric = scored_metric(
            "required_section_coverage",
            value=section_cov,
            weight=float(weights.get("required_section_coverage", 0.2)),
            threshold=thresholds.get("required_section_coverage", 0.5),
            reference_kind=rk,
        )
    elif not ref:
        section_cov_metric = na_metric(
            "required_section_coverage",
            weight=float(weights.get("required_section_coverage", 0.2)),
            reason="no_direct_reference",
            reference_kind="no_direct_reference",
        )
    else:
        section_cov_metric = scored_metric(
            "required_section_coverage",
            value=section_cov,
            weight=float(weights.get("required_section_coverage", 0.2)),
            threshold=thresholds.get("required_section_coverage", 0.5),
            reference_kind=rk,
        )

    gold_chunks = {
        str(e.get("chunk_id"))
        for e in (case.evidence or [])
        if isinstance(e, dict) and e.get("chunk_id")
    }
    cites = prediction.get("citations") or []
    cite_ids = [
        str(c if not isinstance(c, dict) else c.get("chunk_id") or c.get("document_id") or "")
        for c in cites
    ]
    cite_ids = [c for c in cite_ids if c]
    if cite_ids and gold_chunks:
        valid = sum(1 for c in cite_ids if c in gold_chunks) / len(cite_ids)
        coverage = len(set(cite_ids) & gold_chunks) / len(gold_chunks) if gold_chunks else 0.0
    elif cite_ids and not gold_chunks:
        valid = None
        coverage = None
    else:
        valid = 0.0 if gold_chunks else None
        coverage = 0.0 if gold_chunks else None
    fabrications = len(_FABRICATE_RE.findall(text))
    leaks = len(_SENSITIVE_RE.findall(text))
    empty = 1.0 if not text.strip() and not outline else 0.0
    supported = float(
        prediction.get("supported_claim_rate") or (valid if valid is not None else 0.0)
    )
    unsupported = float(
        prediction.get("unsupported_claim_rate") or (1.0 - supported if valid is not None else 0.0)
    )
    out = [section_cov_metric]
    if valid is None:
        out.append(
            na_metric(
                "citation_validity",
                weight=float(weights.get("citation_validity", 0.15)),
                reason="no citation gold",
            )
        )
        out.append(
            na_metric(
                "citation_coverage",
                weight=float(weights.get("citation_coverage", 0.1)),
                reason="no citation gold",
            )
        )
    else:
        out.append(
            scored_metric(
                "citation_validity",
                value=valid,
                weight=float(weights.get("citation_validity", 0.15)),
                threshold=thresholds.get("citation_validity", 0.5),
                reference_kind=rk if ref else "executed_without_direct_reference",
            )
        )
        out.append(
            scored_metric(
                "citation_coverage",
                value=coverage or 0.0,
                weight=float(weights.get("citation_coverage", 0.1)),
                threshold=thresholds.get("citation_coverage", 0.3),
                reference_kind=rk if ref else "executed_without_direct_reference",
            )
        )
    out.extend(
        [
            scored_metric(
                "supported_claim_rate",
                value=supported,
                weight=float(weights.get("supported_claim_rate", 0.15)),
                threshold=thresholds.get("supported_claim_rate", 0.5),
                reference_kind=rk if ref else "executed_without_direct_reference",
            ),
            scored_metric(
                "unsupported_claim_rate",
                value=unsupported,
                weight=float(weights.get("unsupported_claim_rate", 0.1)),
                threshold=None,
                reference_kind=rk if ref else "executed_without_direct_reference",
            ),
            scored_metric(
                "forbidden_fabrication_count",
                value=float(fabrications),
                weight=float(weights.get("forbidden_fabrication_count", 0.15)),
                threshold=0.0,
                reference_kind="rule_expected",
            ),
            scored_metric(
                "sensitive_data_leakage_count",
                value=float(leaks),
                weight=float(weights.get("sensitive_data_leakage_count", 0.1)),
                threshold=0.0,
                reference_kind="rule_expected",
            ),
            scored_metric(
                "empty_or_malformed_output",
                value=empty,
                weight=float(weights.get("empty_or_malformed_output", 0.05)),
                threshold=0.0,
                reference_kind="rule_expected",
            ),
        ]
    )
    for m in out:
        if (
            m.name
            in {
                "forbidden_fabrication_count",
                "sensitive_data_leakage_count",
                "empty_or_malformed_output",
            }
            and m.value is not None
        ):
            m.passed = m.value <= 0.0
            m.applicable = True
    return out
