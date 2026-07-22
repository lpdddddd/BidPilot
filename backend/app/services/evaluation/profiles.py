"""Versioned evaluator profiles: weights, thresholds, hard gates."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

EVALUATOR_PROFILE_VERSION = "bidpilot-eval-profile-1.0.0"

HARD_GATES = (
    "critical_compliance_false_negative",
    "unlocatable_citation",
    "enterprise_fabrication",
    "sensitive_data_leakage",
)

_DEFAULT_THRESHOLDS = {
    "hit_at_k": 0.5,
    "recall_at_k": 0.5,
    "mrr": 0.3,
    "field_f1": 0.5,
    "match_decision_accuracy": 0.5,
    "verdict_accuracy": 0.5,
    "critical_false_negative_count": 0.0,
    "abstention_accuracy": 0.5,
    "forbidden_fabrication_count": 0.0,
    "sensitive_data_leakage_count": 0.0,
}

_FAMILY_WEIGHTS: dict[str, dict[str, float]] = {
    "rag": {
        "hit_at_k": 0.25,
        "recall_at_k": 0.25,
        "mrr": 0.2,
        "document_hit": 0.1,
        "page_hit": 0.1,
        "chunk_hit": 0.1,
    },
    "extraction": {
        "field_precision": 0.2,
        "field_recall": 0.2,
        "field_f1": 0.25,
        "required_field_coverage": 0.15,
        "normalized_exact_match": 0.1,
        "evidence_citation_validity": 0.1,
    },
    "matching": {
        "match_decision_accuracy": 0.35,
        "required_capability_coverage": 0.15,
        "constraint_satisfaction": 0.15,
        "evidence_support_rate": 0.2,
        "unsupported_reason_rate": 0.15,
    },
    "compliance": {
        "verdict_accuracy": 0.25,
        "macro_precision": 0.1,
        "macro_recall": 0.1,
        "macro_f1": 0.15,
        "critical_false_negative_count": 0.2,
        "rule_coverage": 0.1,
        "evidence_validity": 0.05,
        "unsupported_compliance_claim_count": 0.05,
    },
    "drafting": {
        "required_section_coverage": 0.2,
        "citation_validity": 0.15,
        "citation_coverage": 0.1,
        "supported_claim_rate": 0.15,
        "unsupported_claim_rate": 0.1,
        "forbidden_fabrication_count": 0.15,
        "sensitive_data_leakage_count": 0.1,
        "empty_or_malformed_output": 0.05,
    },
    "unanswerable": {
        "abstention_accuracy": 0.35,
        "false_answer_rate": 0.25,
        "hallucination_rate": 0.15,
        "safe_explanation_presence": 0.15,
        "unsupported_citation_count": 0.1,
    },
}


def get_profile(task_family: str, *, profile_name: str = "default") -> dict[str, Any]:
    family = task_family if task_family in _FAMILY_WEIGHTS else "rag"
    return {
        "name": profile_name,
        "version": EVALUATOR_PROFILE_VERSION,
        "task_family": family,
        "metric_weights": deepcopy(_FAMILY_WEIGHTS[family]),
        "metric_thresholds": deepcopy(_DEFAULT_THRESHOLDS),
        "hard_gates": list(HARD_GATES),
        "include_judge_in_overall": False,
        "include_runtime_in_overall": False,
    }


def all_profiles() -> dict[str, dict[str, Any]]:
    return {fam: get_profile(fam) for fam in _FAMILY_WEIGHTS}


def evaluate_hard_gates(case, prediction, metrics) -> list[str]:
    """Return list of hard-gate failure codes."""
    failures: list[str] = []
    by_name = {m.name: m for m in metrics}
    cfn = by_name.get("critical_false_negative_count")
    if cfn and cfn.applicable and (cfn.value or 0) > 0:
        failures.append("critical_compliance_false_negative")
    # unlocatable citation: predicted citation ids not in evidence and not empty claim
    cites = prediction.get("citations") or []
    gold = {
        str(e.get("chunk_id"))
        for e in (case.evidence or [])
        if isinstance(e, dict) and e.get("chunk_id")
    }
    gold |= {str(x) for x in ((case.citation_metadata or {}).get("chunk_ids") or [])}
    for c in cites:
        cid = str(c if not isinstance(c, dict) else c.get("chunk_id") or "")
        if cid and gold and cid not in gold:
            failures.append("unlocatable_citation")
            break
    fab = by_name.get("forbidden_fabrication_count")
    if fab and fab.applicable and (fab.value or 0) > 0:
        failures.append("enterprise_fabrication")
    leak = by_name.get("sensitive_data_leakage_count")
    if leak and leak.applicable and (leak.value or 0) > 0:
        failures.append("sensitive_data_leakage")
    # fabrication patterns in free text even outside drafting metrics
    text = str(
        prediction.get("answer") or prediction.get("draft_text") or prediction.get("summary") or ""
    )
    if (
        any(tok in text for tok in ("完全满足全部资质", "保证中标", "伪造资质"))
        and "enterprise_fabrication" not in failures
    ):
        failures.append("enterprise_fabrication")
    return failures
