"""CLI-callable offline eval over compliance_reference.jsonl using formal A–E engine."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from app.services.compliance.adapter_reference import (
    adapt_compliance_reference_sample,
    evaluate_adapted_sample,
    maybe_correct_reference_verdict,
)
from app.services.compliance.config import ENGINE_VERSION
from app.services.compliance.registry import get_default_registry

DEFAULT_REFERENCE = (
    Path(__file__).resolve().parents[4]
    / "datasets"
    / "eval"
    / "reference"
    / "compliance_reference.jsonl"
)
DEFAULT_FIXTURE_REFERENCE = (
    Path(__file__).resolve().parents[4]
    / "datasets"
    / "eval"
    / "reference"
    / "fixtures"
    / "compliance_reference.min.jsonl"
)
DEFAULT_OUTPUT = (
    Path(__file__).resolve().parents[4]
    / "datasets"
    / "reports"
    / "compliance_rule_offline_eval.json"
)

OFFLINE_NOTE = (
    "Offline eval runs the formal A–E ComplianceEngine on adapted "
    "compliance_reference.jsonl samples (ComplianceContext via SimpleNamespace). "
    "No REF_* keyword engine. Verdict agreement maps reference pass/fail/"
    "attention_required to whether focus rules for rule_type produced fail "
    "findings with severity≥error (fail), warning-fail/unknown (attention_required), "
    "or otherwise (pass). Coverage honesty: rules_executed = union of engine "
    "rule_ids across samples; focus_rules_evaluated = rules with a direct "
    "reference label for agreement; agreement denominator uses only focus "
    "samples. coverage_status distinguishes directly_evaluated / "
    "partially_evaluated / executed_without_direct_reference / not_executed. "
    "Do not claim 100% for rules without focus samples. Engine version: " + ENGINE_VERSION
)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def resolve_reference_path(reference_path: Path | None = None) -> Path:
    """Prefer explicit path; else full reference if present; else versioned fixture."""
    if reference_path is not None:
        return reference_path
    if DEFAULT_REFERENCE.exists():
        return DEFAULT_REFERENCE
    return DEFAULT_FIXTURE_REFERENCE


def _classify_reference_label(label: str | None) -> str | None:
    """Map reference verdict to positive / negative / insufficient_evidence."""
    if label is None:
        return None
    value = str(label).strip().lower()
    if value in {"fail", "failed", "positive", "triggered"}:
        return "positive"
    if value in {"pass", "passed", "negative", "not_triggered"}:
        return "negative"
    if value in {
        "insufficient_evidence",
        "attention_required",
        "unknown",
        "inconclusive",
    }:
        return "insufficient_evidence"
    return "insufficient_evidence"


def build_coverage_matrix(
    *,
    registry_rules: list[Any],
    rules_executed: set[str],
    focus_rules_evaluated: set[str],
    per_rule_stats: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Build per-rule coverage matrix with full sample statistics.

    Field meanings:
    - executed_sample_count: samples where the formal engine ran this rule
    - focus_sample_count: samples with a directly comparable reference label
    - positive_count / negative_count / insufficient_evidence_count: reference
      label breakdown among focus samples
    - agreement_count / disagreement_count / agreement: only over focus samples
    - coverage_status:
        directly_evaluated | partially_evaluated |
        executed_without_direct_reference | not_executed
    """
    matrix: dict[str, dict[str, Any]] = {}
    for rule in registry_rules:
        rid = rule.rule_id
        category = rule.category.value if hasattr(rule.category, "value") else str(rule.category)
        stats = per_rule_stats.get(rid) or {}
        executed_n = int(stats.get("executed_sample_count") or 0)
        focus_n = int(stats.get("focus_sample_count") or 0)
        agree_n = int(stats.get("agreement_count") or 0)
        disagree_n = int(stats.get("disagreement_count") or 0)
        positive_n = int(stats.get("positive_count") or 0)
        negative_n = int(stats.get("negative_count") or 0)
        insuff_n = int(stats.get("insufficient_evidence_count") or 0)
        executed = rid in rules_executed or executed_n > 0
        has_focus = rid in focus_rules_evaluated or focus_n > 0

        if has_focus and focus_n > 0 and executed:
            # All focus samples agreed and at least one focus sample → direct;
            # mixed agreement or partial focus vs executed → partially_evaluated.
            if disagree_n == 0 and agree_n == focus_n:
                coverage_status = "directly_evaluated"
            else:
                coverage_status = "partially_evaluated"
        elif executed and not has_focus:
            coverage_status = "executed_without_direct_reference"
        elif has_focus and not executed:
            coverage_status = "partially_evaluated"
        else:
            coverage_status = "not_executed"

        agreement: float | None
        if focus_n == 0:
            agreement = None
        else:
            denom = agree_n + disagree_n
            agreement = (agree_n / denom) if denom else None

        matrix[rid] = {
            "rule_id": rid,
            "category": category,
            "description": getattr(rule, "description", "") or "",
            "executed_sample_count": executed_n,
            "focus_sample_count": focus_n,
            "positive_count": positive_n,
            "negative_count": negative_n,
            "insufficient_evidence_count": insuff_n,
            "agreement_count": agree_n,
            "disagreement_count": disagree_n,
            "agreement": agreement,
            "coverage_status": coverage_status,
            # Backward-compatible aliases used by older report consumers.
            "coverage": coverage_status,
            "executed": executed,
            "agree": agree_n,
            "disagree": disagree_n,
            "rate": agreement,
        }
        if coverage_status == "executed_without_direct_reference":
            matrix[rid]["note"] = "executed_without_direct_reference"
        elif coverage_status == "not_executed":
            matrix[rid]["note"] = "not_executed"
    return matrix


def run_offline_eval(
    reference_path: Path | None = None,
    output_path: Path | None = None,
    *,
    apply_label_corrections: bool = True,
) -> dict[str, Any]:
    ref_path = resolve_reference_path(reference_path)
    out_path = output_path or DEFAULT_OUTPUT
    samples = load_jsonl(ref_path)
    results: list[dict[str, Any]] = []
    matched = 0
    compared = 0
    succeeded = 0
    failed = 0
    rule_trigger_counts: Counter[str] = Counter()
    severity_counts: Counter[str] = Counter()
    category_counts: Counter[str] = Counter()
    rule_id_dist: Counter[str] = Counter()
    per_rule_agree: dict[str, Counter[str]] = defaultdict(Counter)
    per_rule_stats: dict[str, dict[str, int]] = defaultdict(
        lambda: {
            "executed_sample_count": 0,
            "focus_sample_count": 0,
            "positive_count": 0,
            "negative_count": 0,
            "insufficient_evidence_count": 0,
            "agreement_count": 0,
            "disagreement_count": 0,
        }
    )
    false_positives = 0  # engine fail, reference pass
    false_negatives = 0  # engine pass, reference fail
    rules_executed: set[str] = set()
    focus_rules_evaluated: set[str] = set()

    registry = get_default_registry()
    all_rule_ids = registry.all_rule_ids()
    registry_rules = registry.list_rules()

    for sample in samples:
        try:
            adapted = adapt_compliance_reference_sample(sample)
            evaluated = evaluate_adapted_sample(adapted, sample=sample)
            succeeded += 1
        except Exception as exc:  # noqa: BLE001
            failed += 1
            evaluated = {
                "sample_id": sample.get("sample_id"),
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
                "verdict_match": None,
                "agreement": None,
                "findings": [],
                "rule_ids_executed": [],
                "rules_executed": [],
                "focus_rule_ids": [],
                "focus_rules_evaluated": [],
            }
            results.append(evaluated)
            continue

        if apply_label_corrections:
            ref_v = evaluated.get("reference_verdict")
            eng_v = evaluated.get("engine_verdict")
            corrected = maybe_correct_reference_verdict(
                str(evaluated.get("rule_type") or ""),
                str(ref_v) if ref_v is not None else None,
                str(eng_v or ""),
            )
            if corrected is not None and corrected != ref_v:
                evaluated["reference_verdict_original"] = ref_v
                evaluated["reference_verdict"] = corrected
                evaluated["reference_label"] = corrected
                evaluated["label_corrected"] = True
                evaluated["verdict_match"] = corrected == eng_v
                evaluated["agreement"] = evaluated["verdict_match"]
                evaluated["mismatch_reason"] = (
                    None if evaluated["verdict_match"] else evaluated.get("mismatch_reason")
                )

        executed = list(evaluated.get("rule_ids_executed") or [])
        focus = list(evaluated.get("focus_rule_ids") or [])
        evaluated["rules_executed"] = executed
        evaluated["focus_rules_evaluated"] = focus
        rules_executed.update(executed)
        focus_rules_evaluated.update(focus)

        ref_label = evaluated.get("reference_verdict")
        label_class = _classify_reference_label(str(ref_label) if ref_label is not None else None)
        for rid in executed:
            per_rule_stats[rid]["executed_sample_count"] += 1
        for rid in focus:
            per_rule_stats[rid]["focus_sample_count"] += 1
            if label_class == "positive":
                per_rule_stats[rid]["positive_count"] += 1
            elif label_class == "negative":
                per_rule_stats[rid]["negative_count"] += 1
            elif label_class == "insufficient_evidence":
                per_rule_stats[rid]["insufficient_evidence_count"] += 1
            if evaluated.get("verdict_match") is True:
                per_rule_stats[rid]["agreement_count"] += 1
            elif evaluated.get("verdict_match") is False:
                per_rule_stats[rid]["disagreement_count"] += 1

        results.append(evaluated)
        if evaluated.get("verdict_match") is not None:
            compared += 1
            if evaluated["verdict_match"]:
                matched += 1
            else:
                if (
                    evaluated.get("engine_verdict") == "fail"
                    and evaluated.get("reference_verdict") == "pass"
                ):
                    false_positives += 1
                if (
                    evaluated.get("engine_verdict") == "pass"
                    and evaluated.get("reference_verdict") == "fail"
                ):
                    false_negatives += 1

        for rid in focus:
            key = "agree" if evaluated.get("verdict_match") else "disagree"
            if evaluated.get("verdict_match") is None:
                key = "uncompared"
            per_rule_agree[rid][key] += 1

        for finding in evaluated.get("findings") or []:
            rule_id = str(finding.get("rule_id") or "unknown")
            rule_trigger_counts[rule_id] += 1
            rule_id_dist[rule_id] += 1
            severity_counts[str(finding.get("severity") or "info")] += 1
            category_counts[str(finding.get("category") or "engine")] += 1

    per_rule_consistency = {
        rid: {
            "agree": c.get("agree", 0),
            "disagree": c.get("disagree", 0),
            "rate": (
                c.get("agree", 0) / (c.get("agree", 0) + c.get("disagree", 0))
                if (c.get("agree", 0) + c.get("disagree", 0))
                else None
            ),
        }
        for rid, c in sorted(per_rule_agree.items())
    }

    without_focus = sorted(set(all_rule_ids) - focus_rules_evaluated)
    coverage_matrix = build_coverage_matrix(
        registry_rules=registry_rules,
        rules_executed=rules_executed,
        focus_rules_evaluated=focus_rules_evaluated,
        per_rule_stats=dict(per_rule_stats),
    )

    consistency = (matched / compared) if compared else None
    # Focus-only rate — do not imply 100% for rules without focus samples.
    focus_rates = [v["rate"] for v in per_rule_consistency.values() if v.get("rate") is not None]
    focus_label_consistency = sum(focus_rates) / len(focus_rates) if focus_rates else None

    directly_evaluated_ids = sorted(
        rid
        for rid, row in coverage_matrix.items()
        if row.get("coverage_status") == "directly_evaluated"
    )
    partially_evaluated_ids = sorted(
        rid
        for rid, row in coverage_matrix.items()
        if row.get("coverage_status") == "partially_evaluated"
    )

    report = {
        "engine_version": ENGINE_VERSION,
        "reference_path": str(ref_path),
        "sample_count": len(samples),
        "total": len(samples),
        "total_sample_count": len(samples),
        "succeeded": succeeded,
        "failed": failed,
        "compared_count": compared,
        "verdict_match_count": matched,
        "verdict_match_rate": consistency,
        "label_consistency_rate": focus_label_consistency,
        "overall_consistency": consistency,
        "focus_rules_label_consistency_rate": focus_label_consistency,
        "per_rule_consistency": per_rule_consistency,
        "false_positives": false_positives,
        "false_negatives": false_negatives,
        "rule_trigger_counts": dict(sorted(rule_trigger_counts.items())),
        "rule_id_distribution": dict(sorted(rule_id_dist.items())),
        "severity_distribution": dict(sorted(severity_counts.items())),
        "category_distribution": dict(sorted(category_counts.items())),
        "rules_executed": sorted(rules_executed),
        "rules_executed_count": len(rules_executed),
        "focus_rules_evaluated": sorted(focus_rules_evaluated),
        "focus_rules_evaluated_count": len(focus_rules_evaluated),
        "directly_evaluated_rule_ids": directly_evaluated_ids,
        "partially_evaluated_rule_ids": partially_evaluated_ids,
        "rules_without_direct_reference_coverage": without_focus,
        "coverage_matrix": coverage_matrix,
        "summary_headline": {
            "rules_executed": len(rules_executed),
            "rules_with_direct_reference": len(focus_rules_evaluated),
            "directly_evaluated_rule_ids": directly_evaluated_ids,
            "focus_agreement_scope": (
                "100% agreement (if any) applies only to focus samples / "
                f"focus rules {sorted(focus_rules_evaluated)}; "
                "not to all executed rules."
            ),
        },
        "note": OFFLINE_NOTE,
        "results": results,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    report["output_path"] = str(out_path)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compliance rule offline eval (formal A–E)")
    parser.add_argument("--reference", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument(
        "--no-label-corrections",
        action="store_true",
        help="Do not apply conservative auto_reference label corrections",
    )
    args = parser.parse_args(argv)
    report = run_offline_eval(
        args.reference,
        args.output,
        apply_label_corrections=not args.no_label_corrections,
    )
    print(
        json.dumps(
            {
                "sample_count": report["sample_count"],
                "succeeded": report["succeeded"],
                "failed": report["failed"],
                "verdict_match_rate": report["verdict_match_rate"],
                "label_consistency_rate": report["label_consistency_rate"],
                "focus_rules_evaluated": report["focus_rules_evaluated"],
                "rules_without_direct_reference_coverage": report[
                    "rules_without_direct_reference_coverage"
                ],
                "false_positives": report["false_positives"],
                "false_negatives": report["false_negatives"],
                "rule_trigger_counts": report["rule_trigger_counts"],
                "severity_distribution": report["severity_distribution"],
                "category_distribution": report["category_distribution"],
                "output_path": report["output_path"],
                "engine_version": report["engine_version"],
            },
            ensure_ascii=False,
        )
    )
    return 0 if report.get("failed", 0) == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
