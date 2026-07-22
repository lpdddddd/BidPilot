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

_POSITIVE_STATUSES = frozenset({"supported", "partially_supported"})
_NAME_ONLY_MARKERS = (
    "company_name_only_not_requirement_aligned",
    "company_name_only",
)
_BILATERAL_MARKERS = (
    "real_bilateral_evidence",
    "clause_aligned_company_evidence",
    "disclosed_match",
)


def matching_stats(samples: list[ReferenceSample]) -> dict[str, Any]:
    """Partition matching samples by evidence provenance + status histogram.

    `matching_with_real_bilateral_evidence` requires tender + company evidence with
    clause-level alignment (or silver disclosed_match with supported/partial status).
    Supplier-name-only pairs are NEVER counted as bilateral.
    """
    bilateral = 0
    tender_only = 0
    company_not_aligned = 0
    insufficient = 0
    status_hist: Counter[str] = Counter()
    for s in samples:
        if s.task_type != "matching":
            continue
        status = str((s.reference_output or {}).get("status") or "unknown")
        status_hist[status] += 1
        method = (s.data_provenance.method if s.data_provenance else "") or ""
        notes = (s.data_provenance.notes if s.data_provenance else "") or ""
        cite_notes = (s.citation_metadata.notes if s.citation_metadata else "") or ""
        company_material = str((s.input or {}).get("company_material") or "")
        blob = f"{method}|{notes}|{cite_notes}"

        is_name_only = any(m in blob for m in _NAME_ONLY_MARKERS)
        is_bilateral = (
            notes == "real_bilateral_evidence"
            or method in {"disclosed_match", "clause_aligned_company_evidence"}
            and status in _POSITIVE_STATUSES
            and not is_name_only
            and any(m in blob for m in _BILATERAL_MARKERS)
        )
        # Tighten: name-only notes always win over bilateral claim
        if is_name_only or "disclosed_supplier_bilateral" in blob:
            # Legacy path or explicit name-only — never bilateral
            if status in _POSITIVE_STATUSES and "disclosed_supplier_bilateral" in blob:
                # Old buggy rows: still classify as not-aligned, not bilateral
                company_not_aligned += 1
            else:
                company_not_aligned += 1
        elif is_bilateral and status in _POSITIVE_STATUSES:
            bilateral += 1
        elif (
            method == "insufficient_company_evidence"
            or "matching_missing_company_evidence" in notes
            or any(m in company_material for m in _MISSING_COMPANY_MARKERS)
        ):
            tender_only += 1
        else:
            # Residual insufficient / unknown without clear tender-only or name-only markers
            insufficient += 1

    # Status-based insufficient count (may overlap partitions; useful for reports)
    status_insufficient = int(status_hist.get("insufficient_evidence", 0)) + int(
        status_hist.get("unknown", 0)
    )

    return {
        "matching_with_real_bilateral_evidence": bilateral,
        "matching_with_tender_evidence_only": tender_only,
        "matching_with_company_evidence_but_not_requirement_aligned": company_not_aligned,
        # Prefer status-based total when partition residual is empty but statuses are insufficient
        "matching_insufficient_evidence": max(insufficient, status_insufficient - bilateral),
        # Back-compat alias used by older callers
        "matching_missing_company_evidence": tender_only + company_not_aligned + insufficient,
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
        "matching_with_tender_evidence_only": match["matching_with_tender_evidence_only"],
        "matching_with_company_evidence_but_not_requirement_aligned": match[
            "matching_with_company_evidence_but_not_requirement_aligned"
        ],
        "matching_insufficient_evidence": match["matching_insufficient_evidence"],
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
            f"- matching_with_tender_evidence_only: **{match['matching_with_tender_evidence_only']}**",
            (
                "- matching_with_company_evidence_but_not_requirement_aligned: "
                f"**{match['matching_with_company_evidence_but_not_requirement_aligned']}**"
            ),
            f"- matching_insufficient_evidence: **{match['matching_insufficient_evidence']}**",
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
            "- Matching bilateral evidence requires clause-level company alignment; "
            "supplier-name-only pairs are `insufficient_evidence`.",
            "",
        ]
    )
    return "\n".join(lines)
