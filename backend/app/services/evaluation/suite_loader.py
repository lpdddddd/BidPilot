"""Load the Step 1 auto-reference evaluation dataset and compute content hashes."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# repo_root/datasets/eval/reference — never mutate files under this path.
DEFAULT_REFERENCE_DIR = Path(__file__).resolve().parents[4] / "datasets" / "eval" / "reference"

# Fields excluded from dataset hash so timestamps / ephemeral report noise
# do not change the content digest used for suite identity.
_HASH_EXCLUDE_SAMPLE_KEYS = frozenset({"created_at"})
_HASH_EXCLUDE_REPORT_KEYS = frozenset(
    {"build_timestamp", "datasets_root", "output_dir", "selected_projects"}
)


@dataclass(frozen=True)
class ReferenceSuiteBundle:
    """Loaded reference suite: samples + report + splits + content hash."""

    samples: list[dict[str, Any]]
    report: dict[str, Any]
    splits: dict[str, Any]
    dataset_hash: str
    reference_dir: Path
    stats: dict[str, Any] = field(default_factory=dict)


def default_reference_dir() -> Path:
    return DEFAULT_REFERENCE_DIR


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def _canonical_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sample_for_hash(sample: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in sample.items() if k not in _HASH_EXCLUDE_SAMPLE_KEYS}


def _report_for_hash(report: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in report.items() if k not in _HASH_EXCLUDE_REPORT_KEYS}


def compute_dataset_hash(
    samples: list[dict[str, Any]],
    report: dict[str, Any] | None = None,
    splits: dict[str, Any] | None = None,
) -> str:
    """SHA-256 over canonical JSON of samples (+ optional report/splits sans timestamps)."""
    payload: dict[str, Any] = {
        "samples": [_sample_for_hash(s) for s in samples],
    }
    if report is not None:
        payload["report"] = _report_for_hash(report)
    if splits is not None:
        # splits.json has build_timestamp — exclude it
        payload["splits"] = {k: v for k, v in splits.items() if k not in {"build_timestamp"}}
    digest = hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()
    return digest


def map_label_source_to_reference_kind(label_source: str | None) -> str:
    """Map dataset label_source to EvaluationReferenceKind value.

    Never promotes auto_reference to human_gold.
    """
    value = (label_source or "").strip().lower()
    if value == "human_gold":
        return "human_gold"
    if value in {"rule_expected", "rule"}:
        return "rule_expected"
    if value in {"auto_reference", "silver", "auto"}:
        return "auto_reference"
    if not value:
        return "no_direct_reference"
    # Unknown non-gold labels stay auto_reference semantics for course demos.
    if value == "human_gold":  # pragma: no cover — unreachable guard
        return "human_gold"
    return "auto_reference"


def compute_suite_stats(samples: list[dict[str, Any]]) -> dict[str, Any]:
    task_counts: Counter[str] = Counter()
    split_counts: Counter[str] = Counter()
    reference_kind_counts: Counter[str] = Counter()
    label_source_counts: Counter[str] = Counter()
    direct_reference = 0
    for sample in samples:
        task = str(sample.get("task_type") or sample.get("task_family") or "unknown")
        task_counts[task] += 1
        split_counts[str(sample.get("split") or "unknown")] += 1
        label = str(sample.get("label_source") or "")
        label_source_counts[label or "missing"] += 1
        kind = map_label_source_to_reference_kind(label)
        # Never report auto_reference as human_gold in stats.
        if kind == "human_gold" and label != "human_gold":
            kind = "auto_reference"
        reference_kind_counts[kind] += 1
        if sample.get("reference_output") is not None and kind in {
            "auto_reference",
            "rule_expected",
            "human_gold",
        }:
            direct_reference += 1
    total = len(samples)
    return {
        "total_cases": total,
        "task_family_counts": dict(sorted(task_counts.items())),
        "split_counts": dict(sorted(split_counts.items())),
        "reference_kind_counts": dict(sorted(reference_kind_counts.items())),
        "label_source_counts": dict(sorted(label_source_counts.items())),
        "direct_reference_count": direct_reference,
        "direct_reference_coverage": (direct_reference / total) if total else 0.0,
        "label_policy": "auto_reference|silver only; never human_gold unless audited",
    }


def load_reference_suite(
    reference_dir: Path | None = None,
    *,
    dataset_filename: str = "reference_dataset.jsonl",
) -> ReferenceSuiteBundle:
    """Load combined reference JSONL + report + splits and compute dataset hash."""
    root = Path(reference_dir) if reference_dir is not None else default_reference_dir()
    dataset_path = root / dataset_filename
    report_path = root / "reference_dataset_report.json"
    splits_path = root / "splits.json"
    if not dataset_path.is_file():
        raise FileNotFoundError(f"reference dataset not found: {dataset_path}")
    samples = load_jsonl(dataset_path)
    report: dict[str, Any] = {}
    if report_path.is_file():
        report = json.loads(report_path.read_text(encoding="utf-8"))
    splits: dict[str, Any] = {}
    if splits_path.is_file():
        splits = json.loads(splits_path.read_text(encoding="utf-8"))
    dataset_hash = compute_dataset_hash(samples, report=report or None, splits=splits or None)
    stats = compute_suite_stats(samples)
    # Cross-check report counts when present (informational only).
    if report.get("counts"):
        stats["report_counts"] = report["counts"]
    if report.get("splits"):
        stats["report_splits"] = report["splits"]
    return ReferenceSuiteBundle(
        samples=samples,
        report=report,
        splits=splits,
        dataset_hash=dataset_hash,
        reference_dir=root,
        stats=stats,
    )


def build_manifest_snapshot(bundle: ReferenceSuiteBundle) -> dict[str, Any]:
    """Safe suite manifest for DB snapshot (no absolute paths / secrets)."""
    return {
        "generator_version": bundle.report.get("generator_version"),
        "label_policy": bundle.report.get("label_policy")
        or "auto_reference|silver only; never human_gold",
        "seed": bundle.report.get("seed") or bundle.splits.get("seed"),
        "counts": bundle.stats.get("task_family_counts"),
        "split_counts": bundle.stats.get("split_counts"),
        "reference_kind_counts": bundle.stats.get("reference_kind_counts"),
        "direct_reference_coverage": bundle.stats.get("direct_reference_coverage"),
        "dataset_hash": bundle.dataset_hash,
        "sample_count": bundle.stats.get("total_cases"),
        "all_targets_met": bundle.report.get("all_targets_met"),
    }
