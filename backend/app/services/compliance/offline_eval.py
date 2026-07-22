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

DEFAULT_REFERENCE = (
    Path(__file__).resolve().parents[4]
    / "datasets"
    / "eval"
    / "reference"
    / "compliance_reference.jsonl"
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
    "or otherwise (pass). Engine version: "
    + ENGINE_VERSION
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


def run_offline_eval(
    reference_path: Path | None = None,
    output_path: Path | None = None,
    *,
    apply_label_corrections: bool = True,
) -> dict[str, Any]:
    ref_path = reference_path or DEFAULT_REFERENCE
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
    false_positives = 0  # engine fail, reference pass
    false_negatives = 0  # engine pass, reference fail

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
                    None
                    if evaluated["verdict_match"]
                    else evaluated.get("mismatch_reason")
                )

        results.append(evaluated)
        if evaluated.get("verdict_match") is not None:
            compared += 1
            if evaluated["verdict_match"]:
                matched += 1
            else:
                if evaluated.get("engine_verdict") == "fail" and evaluated.get(
                    "reference_verdict"
                ) == "pass":
                    false_positives += 1
                if evaluated.get("engine_verdict") == "pass" and evaluated.get(
                    "reference_verdict"
                ) == "fail":
                    false_negatives += 1

        for rid in evaluated.get("focus_rule_ids") or []:
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

    consistency = (matched / compared) if compared else None
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
        "label_consistency_rate": consistency,
        "overall_consistency": consistency,
        "per_rule_consistency": per_rule_consistency,
        "false_positives": false_positives,
        "false_negatives": false_negatives,
        "rule_trigger_counts": dict(sorted(rule_trigger_counts.items())),
        "rule_id_distribution": dict(sorted(rule_id_dist.items())),
        "severity_distribution": dict(sorted(severity_counts.items())),
        "category_distribution": dict(sorted(category_counts.items())),
        "note": OFFLINE_NOTE,
        "results": results,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
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
