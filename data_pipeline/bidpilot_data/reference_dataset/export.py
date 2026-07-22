"""Export reference dataset artifacts under datasets/eval/reference/."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from bidpilot_data.reference_dataset.schema import GENERATOR_VERSION, ReferenceSample
from bidpilot_data.utils import ensure_dir, write_json, write_jsonl

TASK_FILES = {
    "rag": "rag_reference.jsonl",
    "extraction": "extraction_reference.jsonl",
    "matching": "matching_reference.jsonl",
    "compliance": "compliance_reference.jsonl",
    "drafting": "drafting_reference.jsonl",
    "unanswerable": "unanswerable_reference.jsonl",
}

_MISSING_COMPANY_MARKERS = (
    "缺少企业侧证据",
    "当前材料未找到充分证据",
)


def matching_stats(samples: list[ReferenceSample]) -> dict[str, Any]:
    """Count bilateral vs missing-company matching samples and status histogram."""
    bilateral = 0
    missing = 0
    status_hist: Counter[str] = Counter()
    for s in samples:
        if s.task_type != "matching":
            continue
        status = str((s.reference_output or {}).get("status") or "unknown")
        status_hist[status] += 1
        method = (s.data_provenance.method if s.data_provenance else "") or ""
        notes = (s.data_provenance.notes if s.data_provenance else "") or ""
        company_material = str((s.input or {}).get("company_material") or "")
        if (
            method in {"disclosed_match", "disclosed_supplier_bilateral"}
            or "real_bilateral_evidence" in notes
            or (len(s.evidence) >= 2 and not any(m in company_material for m in _MISSING_COMPANY_MARKERS))
        ):
            bilateral += 1
        elif (
            method == "insufficient_company_evidence"
            or "matching_missing_company_evidence" in notes
            or any(m in company_material for m in _MISSING_COMPANY_MARKERS)
            or status in {"insufficient_evidence", "unknown"}
        ):
            missing += 1
        else:
            # Fallback: single tender-side evidence without company material markers
            missing += 1
    return {
        "matching_with_real_bilateral_evidence": bilateral,
        "matching_missing_company_evidence": missing,
        "matching_status_histogram": dict(status_hist),
    }


def export_reference_dataset(
    samples: list[ReferenceSample],
    rejected: list[dict[str, Any]] | list[ReferenceSample],
    *,
    output_dir: Path,
    report: dict[str, Any],
    splits_manifest: dict[str, Any] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    out = ensure_dir(Path(output_dir))
    by_task: dict[str, list[ReferenceSample]] = {k: [] for k in TASK_FILES}
    for s in samples:
        by_task.setdefault(s.task_type, []).append(s)

    paths: dict[str, str] = {}
    counts = {k: len(v) for k, v in by_task.items()}
    match = matching_stats(samples)
    # Ensure report carries matching stats even if caller omitted them
    report = {
        **report,
        "matching_with_real_bilateral_evidence": match["matching_with_real_bilateral_evidence"],
        "matching_missing_company_evidence": match["matching_missing_company_evidence"],
        "matching_status_histogram": match["matching_status_histogram"],
    }

    if not dry_run:
        write_jsonl(out / "reference_dataset.jsonl", samples)
        paths["reference_dataset.jsonl"] = str(out / "reference_dataset.jsonl")
        for task, filename in TASK_FILES.items():
            write_jsonl(out / filename, by_task.get(task) or [])
            paths[filename] = str(out / filename)

        rejected_rows: list[Any] = []
        for r in rejected:
            if isinstance(r, ReferenceSample):
                rejected_rows.append(
                    {
                        **r.to_jsonl_dict(),
                        "reject_reasons": list(r.quality_checks.messages),
                    }
                )
            else:
                rejected_rows.append(r)
        write_jsonl(out / "rejected_samples.jsonl", rejected_rows)
        paths["rejected_samples.jsonl"] = str(out / "rejected_samples.jsonl")

        write_json(out / "reference_dataset_report.json", report)
        paths["reference_dataset_report.json"] = str(out / "reference_dataset_report.json")

        if splits_manifest is not None:
            write_json(out / "splits.json", splits_manifest)
            paths["splits.json"] = str(out / "splits.json")

        summary_md = render_summary_md(samples, rejected_rows, report)
        (out / "reference_dataset_summary.md").write_text(summary_md, encoding="utf-8")
        paths["reference_dataset_summary.md"] = str(out / "reference_dataset_summary.md")

    return {
        "output_dir": str(out),
        "dry_run": dry_run,
        "counts": counts,
        "total": len(samples),
        "rejected": len(rejected),
        "paths": paths,
        **match,
    }


def render_summary_md(
    samples: list[ReferenceSample],
    rejected: list[Any],
    report: dict[str, Any],
) -> str:
    by_task = Counter(s.task_type for s in samples)
    by_split = Counter(s.split or "unset" for s in samples)
    by_label = Counter(s.label_source for s in samples)
    match = matching_stats(samples)
    lines = [
        "# BidPilot Auto Reference Dataset Summary",
        "",
        f"- Generator: `{GENERATOR_VERSION}`",
        f"- Label source: auto_reference / silver only (never human_gold)",
        f"- Total accepted samples: **{len(samples)}**",
        f"- Rejected samples: **{len(rejected)}**",
        f"- Seed: `{report.get('seed')}`",
        f"- build_timestamp: `{report.get('build_timestamp')}`",
        f"- use_llm: `{report.get('use_llm')}`",
        "",
        "## Counts by task",
        "",
    ]
    for task in ("rag", "extraction", "matching", "compliance", "drafting", "unanswerable"):
        lines.append(f"- `{task}`: {by_task.get(task, 0)}")
    lines.extend(
        [
            "",
            "## Matching evidence",
            "",
            f"- matching_with_real_bilateral_evidence: **{match['matching_with_real_bilateral_evidence']}**",
            f"- matching_missing_company_evidence: **{match['matching_missing_company_evidence']}**",
            "",
            "### Matching status histogram",
            "",
        ]
    )
    hist = match["matching_status_histogram"] or {}
    if hist:
        for status, n in sorted(hist.items()):
            lines.append(f"- `{status}`: {n}")
    else:
        lines.append("- *(none)*")
    lines.extend(["", "## Splits", ""])
    for sp in ("train", "validation", "test", "unset"):
        if by_split.get(sp):
            lines.append(f"- `{sp}`: {by_split[sp]}")
    lines.extend(["", "## Label sources", ""])
    for k, v in sorted(by_label.items()):
        lines.append(f"- `{k}`: {v}")
    targets = report.get("targets") or {}
    met = report.get("targets_met") or {}
    lines.extend(["", "## Target checklist", ""])
    for task, need in targets.items():
        ok = met.get(task, False)
        lines.append(f"- `{task}`: {by_task.get(task, 0)} / {need} {'✓' if ok else '✗'}")
    lines.extend(
        [
            "",
            "## Notes",
            "",
            "- This is an **auto reference** set for course demos and automatic evaluation.",
            "- It is **not** expert human gold.",
            "- All citation quotes are validated against real chunk text (whitespace-normalized).",
            "- Matching uses real disclosed company evidence only; otherwise status is `insufficient_evidence`.",
            "",
        ]
    )
    return "\n".join(lines)
