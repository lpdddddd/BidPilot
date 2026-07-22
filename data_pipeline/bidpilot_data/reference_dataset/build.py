"""Orchestrator for BidPilot auto reference dataset builder."""

from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from bidpilot_data.logging import get_logger, log_stats
from bidpilot_data.reference_dataset.export import export_reference_dataset
from bidpilot_data.reference_dataset.generate import generate_all_candidates, overgenerate_for_retry
from bidpilot_data.reference_dataset.llm_judge import apply_judge
from bidpilot_data.reference_dataset.schema import DEFAULT_TARGETS, GENERATOR_VERSION, ReferenceSample
from bidpilot_data.reference_dataset.select import load_corpus, select_projects, selection_fingerprint
from bidpilot_data.reference_dataset.split import assign_splits
from bidpilot_data.reference_dataset.validate import dedupe_samples, validate_sample
from bidpilot_data.settings import get_settings

log = get_logger(__name__)


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
) -> dict[str, Any]:
    """Build auto reference dataset under datasets/eval/reference/ (new dir only)."""
    settings = get_settings()
    root = Path(datasets_root) if datasets_root else settings.datasets_root
    out_dir = Path(output_dir) if output_dir else (root / "eval" / "reference")
    tgt = dict(DEFAULT_TARGETS)
    if targets:
        tgt.update(targets)

    corpus = load_corpus(root)
    selected = select_projects(corpus, seed=seed, max_projects=max_projects)
    log.info("selected %s projects for reference build (seed=%s)", len(selected), seed)

    # Over-generate so validation/retry can still meet minima
    candidates = overgenerate_for_retry(corpus, selected, seed=seed, targets=tgt, multiplier=3.0)
    # Also keep a second wave with offset seed for retries
    extra_pool = generate_all_candidates(corpus, selected, seed=seed + 17, targets={k: v * 2 for k, v in tgt.items()})

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

    # Trim to targets (prefer higher confidence)
    by_task: dict[str, list[ReferenceSample]] = defaultdict(list)
    for s in accepted:
        by_task[s.task_type].append(s)
    trimmed: list[ReferenceSample] = []
    for task, need in tgt.items():
        rows = sorted(by_task.get(task) or [], key=lambda s: (-s.confidence, s.sample_id))
        trimmed.extend(rows[:need])
        # Keep extras only if below need (already handled)
    accepted = trimmed

    # Assign splits
    accepted, splits_manifest = assign_splits(accepted, seed=seed, document_index=corpus.documents)

    counts = Counter(s.task_type for s in accepted)
    targets_met = {t: counts.get(t, 0) >= n for t, n in tgt.items()}
    report: dict[str, Any] = {
        "generator_version": GENERATOR_VERSION,
        "seed": seed,
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
        },
    )
    return report
