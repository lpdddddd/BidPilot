"""Orchestrator for BidPilot auto reference dataset builder."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bidpilot_data.logging import get_logger, log_stats
from bidpilot_data.reference_dataset.export import export_reference_dataset, matching_stats
from bidpilot_data.reference_dataset.generate import generate_all_candidates, overgenerate_for_retry
from bidpilot_data.reference_dataset.llm_judge import apply_judge
from bidpilot_data.reference_dataset.schema import DEFAULT_TARGETS, GENERATOR_VERSION, ReferenceSample
from bidpilot_data.reference_dataset.select import load_corpus, select_projects, selection_fingerprint
from bidpilot_data.reference_dataset.split import assign_splits
from bidpilot_data.reference_dataset.validate import dedupe_samples, validate_sample
from bidpilot_data.settings import get_settings

log = get_logger(__name__)


def parse_build_timestamp(value: datetime | str | None) -> datetime | None:
    """Parse optional ISO-8601 build timestamp to UTC datetime."""
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        raw = str(value).strip()
        if not raw:
            return None
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def build_reference_dataset(
    *,
    seed: int = 42,
    output_dir: Path | None = None,
    dry_run: bool = False,
    use_llm: bool = False,
    max_retries: int = 2,
    targets: dict[str, int] | None = None,
    max_projects: int | None = 48,
    datasets_root: Path | None = None,
    build_timestamp: datetime | str | None = None,
) -> dict[str, Any]:
    """Build auto reference dataset under datasets/eval/reference/ (new dir only)."""
    settings = get_settings()
    root = Path(datasets_root) if datasets_root else settings.datasets_root
    out_dir = Path(output_dir) if output_dir else (root / "eval" / "reference")
    tgt = dict(DEFAULT_TARGETS)
    if targets:
        tgt.update(targets)

    created_at = parse_build_timestamp(build_timestamp)

    corpus = load_corpus(root)
    selected = select_projects(corpus, seed=seed, max_projects=max_projects)
    log.info("selected %s projects for reference build (seed=%s)", len(selected), seed)

    # Over-generate so validation/retry can still meet minima
    candidates = overgenerate_for_retry(
        corpus, selected, seed=seed, targets=tgt, multiplier=3.0, created_at=created_at
    )
    # Also keep a second wave with offset seed for retries
    extra_pool = generate_all_candidates(
        corpus,
        selected,
        seed=seed + 17,
        targets={k: v * 2 for k, v in tgt.items()},
        created_at=created_at,
    )

    accepted: list[ReferenceSample] = []
    rejected: list[ReferenceSample] = []
    attempt_stats = Counter()

    def _try_accept(sample: ReferenceSample) -> bool:
        ok, msgs, parsed = validate_sample(
            sample,
            chunk_index=corpus.chunks,
            document_index=corpus.documents,
        )
        if parsed is None:
            rejected.append(sample)
            attempt_stats["schema_fail"] += 1
            return False
        if not ok:
            # retry path handled by caller; mark rejected for now
            qc = parsed.quality_checks
            qc.messages = list(msgs)
            rejected.append(parsed.model_copy(update={"quality_checks": qc}))
            attempt_stats["validate_fail"] += 1
            return False
        judge_ok, judged, _jr = apply_judge(parsed, chunk_index=corpus.chunks, use_llm=use_llm)
        if not judge_ok:
            rejected.append(judged)
            attempt_stats["judge_fail"] += 1
            return False
        accepted.append(judged)
        attempt_stats["accepted"] += 1
        return True

    # First pass
    for sample in candidates:
        _try_accept(sample)

    # Dedupe accepted
    accepted, dupes = dedupe_samples(accepted)
    rejected.extend(dupes)
    attempt_stats["dedupe_reject"] += len(dupes)

    # Retry failed tasks up to max_retries using extra pool / regenerations
    for retry_i in range(max_retries):
        have = Counter(s.task_type for s in accepted)
        shortfall = {t: max(0, n - have.get(t, 0)) for t, n in tgt.items() if have.get(t, 0) < n}
        if not shortfall:
            break
        log.info("retry %s shortfall=%s", retry_i + 1, shortfall)
        wave = generate_all_candidates(
            corpus,
            selected,
            seed=seed + 100 * (retry_i + 1),
            targets={t: max(shortfall[t] * 3, shortfall[t] + 5) for t in shortfall},
            created_at=created_at,
        )
        wave.extend(extra_pool)
        # Avoid already accepted ids
        have_ids = {s.sample_id for s in accepted}
        new_accepted: list[ReferenceSample] = []
        for sample in wave:
            if sample.sample_id in have_ids:
                continue
            task = sample.task_type
            if have.get(task, 0) + sum(1 for s in new_accepted if s.task_type == task) >= tgt.get(task, 0):
                continue
            ok, msgs, parsed = validate_sample(sample, chunk_index=corpus.chunks, document_index=corpus.documents)
            if not ok or parsed is None:
                if parsed is not None:
                    rejected.append(parsed)
                else:
                    rejected.append(sample)
                continue
            judge_ok, judged, _ = apply_judge(parsed, chunk_index=corpus.chunks, use_llm=use_llm)
            if not judge_ok:
                rejected.append(judged)
                continue
            new_accepted.append(judged)
            have_ids.add(judged.sample_id)
        accepted.extend(new_accepted)
        accepted, more_dupes = dedupe_samples(accepted)
        rejected.extend(more_dupes)
        attempt_stats["retry_accepted"] += len(new_accepted)

    # Trim to targets (prefer higher confidence; diversify matching provenance)
    by_task: dict[str, list[ReferenceSample]] = defaultdict(list)
    for s in accepted:
        by_task[s.task_type].append(s)
    trimmed: list[ReferenceSample] = []
    for task, need in tgt.items():
        rows = sorted(by_task.get(task) or [], key=lambda s: (-s.confidence, s.sample_id))
        if task == "matching" and need > 0:
            name_only = [
                s
                for s in rows
                if (s.data_provenance.method if s.data_provenance else "") == "company_name_only"
            ]
            others = [s for s in rows if s not in name_only]
            # Keep up to 2/3 name-only (company present but not clause-aligned) for report diversity
            take_name = min(len(name_only), max(need // 2, min(need, 20)))
            picked = name_only[:take_name]
            remain = need - len(picked)
            picked.extend(others[:remain])
            if len(picked) < need:
                leftover = [s for s in rows if s not in picked]
                picked.extend(leftover[: need - len(picked)])
            trimmed.extend(picked[:need])
        else:
            trimmed.extend(rows[:need])
    accepted = trimmed

    # Assign splits
    accepted, splits_manifest = assign_splits(accepted, seed=seed, document_index=corpus.documents)

    counts = Counter(s.task_type for s in accepted)
    match_stats = matching_stats(accepted)
    targets_met = {t: counts.get(t, 0) >= n for t, n in tgt.items()}
    report_ts = created_at or datetime.now(timezone.utc)
    report: dict[str, Any] = {
        "generator_version": GENERATOR_VERSION,
        "seed": seed,
        "build_timestamp": report_ts.isoformat().replace("+00:00", "Z"),
        "use_llm": use_llm,
        "max_retries": max_retries,
        "dry_run": dry_run,
        "datasets_root": str(root),
        "output_dir": str(out_dir),
        "selected_projects": selection_fingerprint(selected),
        "selected_project_count": len(selected),
        "targets": tgt,
        "counts": dict(counts),
        "targets_met": targets_met,
        "all_targets_met": all(targets_met.values()),
        "rejected_count": len(rejected),
        "attempt_stats": dict(attempt_stats),
        "matching_with_real_bilateral_evidence": match_stats["matching_with_real_bilateral_evidence"],
        "matching_with_tender_evidence_only": match_stats["matching_with_tender_evidence_only"],
        "matching_with_company_evidence_but_not_requirement_aligned": match_stats[
            "matching_with_company_evidence_but_not_requirement_aligned"
        ],
        "matching_insufficient_evidence": match_stats["matching_insufficient_evidence"],
        "matching_missing_company_evidence": match_stats["matching_missing_company_evidence"],
        "matching_status_histogram": match_stats["matching_status_histogram"],
        "splits": {
            "counts": splits_manifest.get("counts"),
            "project_counts": splits_manifest.get("project_counts"),
            "ok": splits_manifest.get("ok"),
            "document_leakage": splits_manifest.get("document_leakage"),
        },
        "label_policy": "auto_reference|silver only; never human_gold",
    }

    export_stats = export_reference_dataset(
        accepted,
        rejected,
        output_dir=out_dir,
        report=report,
        splits_manifest={
            "seed": seed,
            "build_timestamp": report["build_timestamp"],
            "project_to_split": splits_manifest.get("project_to_split") or {},
            "counts": splits_manifest.get("counts") or {},
            "ok": splits_manifest.get("ok", False),
        },
        dry_run=dry_run,
    )
    report["export"] = export_stats
    log_stats(
        log,
        "reference_dataset",
        {
            "total": len(accepted),
            "rejected": len(rejected),
            "all_targets_met": report["all_targets_met"],
            **{f"n_{k}": counts.get(k, 0) for k in tgt},
            "matching_bilateral": match_stats["matching_with_real_bilateral_evidence"],
            "matching_tender_only": match_stats["matching_with_tender_evidence_only"],
            "matching_company_not_aligned": match_stats[
                "matching_with_company_evidence_but_not_requirement_aligned"
            ],
            "matching_insufficient": match_stats["matching_insufficient_evidence"],
            "matching_missing_company": match_stats["matching_missing_company_evidence"],
        },
    )
    return report
