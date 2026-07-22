"""CLI-callable offline eval over compliance_reference.jsonl."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from app.services.compliance.adapter_reference import (
    adapt_compliance_reference_sample,
    evaluate_adapted_sample,
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
    "This report evaluates lightweight REF_* checks on compliance_reference.jsonl. "
    "Full DB-backed engine rules (A001–E006, compliance-rules-1.1.0) apply to live "
    "projects via the compliance API/service — not to these JSONL samples alone."
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
) -> dict[str, Any]:
    ref_path = reference_path or DEFAULT_REFERENCE
    out_path = output_path or DEFAULT_OUTPUT
    samples = load_jsonl(ref_path)
    results = []
    matched = 0
    compared = 0
    rule_trigger_counts: Counter[str] = Counter()
    severity_counts: Counter[str] = Counter()
    category_counts: Counter[str] = Counter()

    for sample in samples:
        adapted = adapt_compliance_reference_sample(sample)
        evaluated = evaluate_adapted_sample(adapted)
        results.append(evaluated)
        if evaluated.get("verdict_match") is not None:
            compared += 1
            if evaluated["verdict_match"]:
                matched += 1
        for finding in evaluated.get("findings") or []:
            # Count triggers for non-pass findings (and always count rule_id hits)
            rule_id = str(finding.get("rule_id") or "unknown")
            rule_trigger_counts[rule_id] += 1
            severity_counts[str(finding.get("severity") or "info")] += 1
            category_counts[str(finding.get("category") or "engine")] += 1

    report = {
        "engine_version": ENGINE_VERSION,
        "reference_path": str(ref_path),
        "sample_count": len(samples),
        "total_sample_count": len(samples),
        "compared_count": compared,
        "verdict_match_count": matched,
        "verdict_match_rate": (matched / compared) if compared else None,
        "label_consistency_rate": (matched / compared) if compared else None,
        "rule_trigger_counts": dict(sorted(rule_trigger_counts.items())),
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
    parser = argparse.ArgumentParser(description="Compliance rule offline eval")
    parser.add_argument("--reference", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)
    report = run_offline_eval(args.reference, args.output)
    print(
        json.dumps(
            {
                "sample_count": report["sample_count"],
                "verdict_match_rate": report["verdict_match_rate"],
                "label_consistency_rate": report["label_consistency_rate"],
                "rule_trigger_counts": report["rule_trigger_counts"],
                "severity_distribution": report["severity_distribution"],
                "category_distribution": report["category_distribution"],
                "output_path": report["output_path"],
                "engine_version": report["engine_version"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
