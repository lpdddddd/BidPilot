"""Deterministic project-cluster weighted split aiming for 80/10/10 by record count."""

from __future__ import annotations

import random
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

from bidpilot_data.schemas import DatasetSplitManifest


@dataclass
class ProjectCluster:
    cluster_id: str
    project_ids: list[str]
    record_count: int
    task_counts: Counter = field(default_factory=Counter)
    bundle_levels: Counter = field(default_factory=Counter)
    industries: Counter = field(default_factory=Counter)
    source_domains: Counter = field(default_factory=Counter)


def build_project_clusters(
    *,
    project_ids: Iterable[str],
    cluster_of: dict[str, str],
    records: list[Any],
    projects: dict[str, dict[str, Any]] | None = None,
) -> list[ProjectCluster]:
    """Aggregate atomic clusters with record-weighted stats."""
    projects = projects or {}
    groups: dict[str, list[str]] = defaultdict(list)
    ids = sorted({p for p in project_ids if p and p != "unknown"})
    for p in ids:
        groups[cluster_of.get(p, p)].append(p)

    by_project: dict[str, list[Any]] = defaultdict(list)
    for r in records:
        pid = r.project_id if hasattr(r, "project_id") else r.get("project_id")
        if pid:
            by_project[pid].append(r)

    clusters: list[ProjectCluster] = []
    for cid, members in sorted(groups.items(), key=lambda x: x[0]):
        members = sorted(set(members))
        task_counts: Counter = Counter()
        levels: Counter = Counter()
        industries: Counter = Counter()
        domains: Counter = Counter()
        n = 0
        for pid in members:
            rows = by_project.get(pid) or []
            n += len(rows)
            for r in rows:
                task = r.task_type.value if hasattr(r, "task_type") else r.get("task_type")
                if task:
                    task_counts[task] += 1
                urls = r.source_urls if hasattr(r, "source_urls") else (r.get("source_urls") or [])
                if urls:
                    from urllib.parse import urlparse

                    host = urlparse(str(urls[0])).netloc.lower().split(":")[0] or "unknown"
                    domains[host] += 1
            proj = projects.get(pid) or {}
            levels[proj.get("bundle_level") or "unknown"] += 1
            industries[proj.get("industry") or "unknown"] += 1
        clusters.append(
            ProjectCluster(
                cluster_id=cid,
                project_ids=members,
                record_count=n,
                task_counts=task_counts,
                bundle_levels=levels,
                industries=industries,
                source_domains=domains,
            )
        )
    return clusters


def _ratio_penalty(n_train: int, n_val: int, n_test: int, *, train_r: float, val_r: float, test_r: float) -> float:
    total = max(1, n_train + n_val + n_test)
    return (
        abs(n_train / total - train_r) * 8.0
        + abs(n_val / total - val_r) * 8.0
        + abs(n_test / total - test_r) * 8.0
    )


def _task_balance_penalty(assignment: dict[str, str], clusters: dict[str, ProjectCluster]) -> float:
    """Encourage similar task mix across splits (L1 of share diffs)."""
    split_task: dict[str, Counter] = {"train": Counter(), "validation": Counter(), "test": Counter()}
    split_n: dict[str, int] = {"train": 0, "validation": 0, "test": 0}
    for cid, split in assignment.items():
        c = clusters[cid]
        split_task[split].update(c.task_counts)
        split_n[split] += c.record_count
    tasks = sorted({t for c in clusters.values() for t in c.task_counts})
    pen = 0.0
    for t in tasks:
        shares = []
        for s in ("train", "validation", "test"):
            shares.append((split_task[s][t] / split_n[s]) if split_n[s] else 0.0)
        pen += max(shares) - min(shares)
    return pen * 0.15


def assign_clusters_weighted(
    clusters: list[ProjectCluster],
    *,
    seed: int,
    train_r: float = 0.8,
    val_r: float = 0.1,
    test_r: float = 0.1,
    min_validation_projects: int = 5,
    min_test_projects: int = 10,
    heldout_project_count: int = 10,
) -> tuple[dict[str, str], dict[str, Any]]:
    """Assign each cluster to train/validation/test.

    Returns mapping cluster_id -> split and diagnostics.
    Never splits a cluster. Deterministic for fixed seed + inputs.
    """
    if not clusters:
        return {}, {"note": "empty"}

    by_id = {c.cluster_id: c for c in clusters}
    order = sorted(clusters, key=lambda c: (-c.record_count, c.cluster_id))
    rng = random.Random(seed)
    # Stable seeded shuffle among equal-size ties is already handled by sort key;
    # additionally shuffle a copy of mid-size clusters for exploration then sort back
    # into a deterministic exploration order keyed by hash(seed, cluster_id).
    explore = sorted(order, key=lambda c: (rng.random(), c.cluster_id))
    # Re-seed for reproducibility of subsequent rng uses
    rng = random.Random(seed)

    total_records = sum(c.record_count for c in clusters)
    total_projects = sum(len(c.project_ids) for c in clusters)
    target_test = int(round(total_records * test_r))
    target_val = int(round(total_records * val_r))
    min_test_proj = min(min_test_projects, max(1, total_projects // 3))
    min_val_proj = min(min_validation_projects, max(1, (total_projects - min_test_proj) // 3))
    held_floor = min(max(heldout_project_count, min_test_proj), max(1, total_projects - min_val_proj - 1))

    assignment: dict[str, str] = {}
    test_recs = 0
    test_projs = 0
    val_recs = 0
    val_projs = 0

    oversized = [
        {
            "cluster_id": c.cluster_id,
            "project_ids": c.project_ids,
            "record_count": c.record_count,
            "share": round(c.record_count / max(1, total_records), 4),
        }
        for c in clusters
        if c.record_count / max(1, total_records) > 0.15
    ]

    # Phase 1: fill test — aim for ~target_test records with >= min_test projects.
    # Prefer clusters near ideal size so project floors do not force tiny-only packing.
    ideal_test = max(1, target_test // max(1, min_test_proj))
    ranked_test = sorted(
        explore,
        key=lambda c: (abs(c.record_count - ideal_test), c.record_count, c.cluster_id),
    )
    for c in ranked_test:
        if test_projs >= held_floor and test_recs >= target_test * 0.9:
            break
        if test_recs + c.record_count > max(target_test * 1.25, target_test + 80) and test_projs >= min_test_proj:
            continue
        if c.record_count > total_records * 0.20 and len(clusters) > 3 and test_projs == 0:
            continue
        assignment[c.cluster_id] = "test"
        test_recs += c.record_count
        test_projs += len(c.project_ids)

    # Ensure min test projects
    for c in sorted(explore, key=lambda c: (c.record_count, c.cluster_id)):
        if test_projs >= min_test_proj:
            break
        if c.cluster_id in assignment:
            continue
        assignment[c.cluster_id] = "test"
        test_recs += c.record_count
        test_projs += len(c.project_ids)

    # Phase 2: fill validation similarly
    ideal_val = max(1, target_val // max(1, min_val_proj))
    remain = [c for c in explore if c.cluster_id not in assignment]
    ranked_val = sorted(
        remain,
        key=lambda c: (abs(c.record_count - ideal_val), c.record_count, c.cluster_id),
    )
    for c in ranked_val:
        if val_projs >= min_val_proj and val_recs >= target_val * 0.9:
            break
        if val_recs + c.record_count > max(target_val * 1.25, target_val + 80) and val_projs >= min_val_proj:
            continue
        assignment[c.cluster_id] = "validation"
        val_recs += c.record_count
        val_projs += len(c.project_ids)

    for c in sorted(remain, key=lambda c: (c.record_count, c.cluster_id)):
        if val_projs >= min_val_proj:
            break
        if c.cluster_id in assignment:
            continue
        assignment[c.cluster_id] = "validation"
        val_recs += c.record_count
        val_projs += len(c.project_ids)

    # Phase 3: rest → train
    for c in clusters:
        if c.cluster_id not in assignment:
            assignment[c.cluster_id] = "train"

    def counts() -> tuple[int, int, int, int, int, int]:
        nt = nv = nte = pt = pv = pte = 0
        for cid, sp in assignment.items():
            c = by_id[cid]
            if sp == "train":
                nt += c.record_count
                pt += len(c.project_ids)
            elif sp == "validation":
                nv += c.record_count
                pv += len(c.project_ids)
            else:
                nte += c.record_count
                pte += len(c.project_ids)
        return nt, nv, nte, pt, pv, pte

    def score() -> float:
        nt, nv, nte, pt, pv, pte = counts()
        pen = _ratio_penalty(nt, nv, nte, train_r=train_r, val_r=val_r, test_r=test_r)
        pen += _task_balance_penalty(assignment, by_id)
        # Hard floor soft penalties
        if pv < min_val_proj:
            pen += 5.0 * (min_val_proj - pv)
        if pte < min_test_proj:
            pen += 5.0 * (min_test_proj - pte)
        if pt < 1:
            pen += 20.0
        return pen

    # Phase 4b: ratio repair — move smallest surplus clusters toward targets while protecting floors
    def project_count(sp: str) -> int:
        return sum(len(by_id[cid].project_ids) for cid, s in assignment.items() if s == sp)

    def record_count(sp: str) -> int:
        return sum(by_id[cid].record_count for cid, s in assignment.items() if s == sp)

    for _ in range(200):
        nt, nv, nte = record_count("train"), record_count("validation"), record_count("test")
        total = max(1, nt + nv + nte)
        changed = False
        # validation too large → move smallest validation cluster to train
        if nv / total > val_r + 0.02 and project_count("validation") > min_val_proj:
            cands = sorted(
                [cid for cid, s in assignment.items() if s == "validation"],
                key=lambda cid: (by_id[cid].record_count, cid),
            )
            for cid in cands:
                if project_count("validation") - len(by_id[cid].project_ids) < min_val_proj:
                    continue
                assignment[cid] = "train"
                changed = True
                break
        # test too large → move smallest test cluster to train
        if (not changed) and nte / total > test_r + 0.02 and project_count("test") > min_test_proj:
            cands = sorted(
                [cid for cid, s in assignment.items() if s == "test"],
                key=lambda cid: (by_id[cid].record_count, cid),
            )
            for cid in cands:
                if project_count("test") - len(by_id[cid].project_ids) < min_test_proj:
                    continue
                assignment[cid] = "train"
                changed = True
                break
        # test too small → move smallest train cluster to test
        if (not changed) and nte / total < test_r - 0.02:
            cands = sorted(
                [cid for cid, s in assignment.items() if s == "train"],
                key=lambda cid: (by_id[cid].record_count, cid),
            )
            for cid in cands:
                if project_count("train") - len(by_id[cid].project_ids) < 1:
                    continue
                # avoid moving oversized cluster into tiny test
                if by_id[cid].record_count > total_records * 0.12:
                    continue
                assignment[cid] = "test"
                changed = True
                break
        # validation too small → move smallest train cluster to validation
        if (not changed) and nv / total < val_r - 0.02:
            cands = sorted(
                [cid for cid, s in assignment.items() if s == "train"],
                key=lambda cid: (by_id[cid].record_count, cid),
            )
            for cid in cands:
                if project_count("train") - len(by_id[cid].project_ids) < 1:
                    continue
                if by_id[cid].record_count > total_records * 0.12:
                    continue
                assignment[cid] = "validation"
                changed = True
                break
        if not changed:
            break

    # Phase 4: deterministic local swaps (train ↔ val / train ↔ test)
    # Consider clusters sorted by id for reproducibility.
    movable = sorted(assignment.keys())
    improved = True
    rounds = 0
    while improved and rounds < 40:
        improved = False
        rounds += 1
        base = score()
        for cid in movable:
            cur = assignment[cid]
            c = by_id[cid]
            for dest in ("train", "validation", "test"):
                if dest == cur:
                    continue
                # Protect floors when moving out of val/test
                if cur == "validation":
                    _, _, _, _, pv, pte = counts()
                    if pv - len(c.project_ids) < min_val_proj:
                        continue
                if cur == "test":
                    _, _, _, _, pv, pte = counts()
                    if pte - len(c.project_ids) < min_test_proj:
                        continue
                assignment[cid] = dest
                new = score()
                if new + 1e-12 < base:
                    base = new
                    cur = dest
                    improved = True
                else:
                    assignment[cid] = cur

    # Phase 5: final ratio repair after local search (local search can undo Phase 4b)
    for _ in range(200):
        nt, nv, nte = record_count("train"), record_count("validation"), record_count("test")
        total = max(1, nt + nv + nte)
        changed = False
        if nv / total > val_r + 0.02 and project_count("validation") > min_val_proj:
            cands = sorted(
                [cid for cid, s in assignment.items() if s == "validation"],
                key=lambda cid: (by_id[cid].record_count, cid),
            )
            for cid in cands:
                if project_count("validation") - len(by_id[cid].project_ids) < min_val_proj:
                    continue
                assignment[cid] = "train"
                changed = True
                break
        if (not changed) and nte / total > test_r + 0.02 and project_count("test") > min_test_proj:
            cands = sorted(
                [cid for cid, s in assignment.items() if s == "test"],
                key=lambda cid: (by_id[cid].record_count, cid),
            )
            for cid in cands:
                if project_count("test") - len(by_id[cid].project_ids) < min_test_proj:
                    continue
                assignment[cid] = "train"
                changed = True
                break
        if (not changed) and nte / total < test_r - 0.015:
            cands = sorted(
                [cid for cid, s in assignment.items() if s == "train"],
                key=lambda cid: (by_id[cid].record_count, cid),
            )
            for cid in cands:
                if project_count("train") - len(by_id[cid].project_ids) < 1:
                    continue
                if by_id[cid].record_count > total_records * 0.12:
                    continue
                # do not overshoot test badly
                if (nte + by_id[cid].record_count) / total > test_r + 0.05:
                    continue
                assignment[cid] = "test"
                changed = True
                break
        if (not changed) and nv / total < val_r - 0.015:
            cands = sorted(
                [cid for cid, s in assignment.items() if s == "train"],
                key=lambda cid: (by_id[cid].record_count, cid),
            )
            for cid in cands:
                if project_count("train") - len(by_id[cid].project_ids) < 1:
                    continue
                if by_id[cid].record_count > total_records * 0.12:
                    continue
                if (nv + by_id[cid].record_count) / total > val_r + 0.05:
                    continue
                assignment[cid] = "validation"
                changed = True
                break
        if not changed:
            break

    # Phase 5b: replace oversized validation/test members with multiple smaller train clusters
    for _ in range(50):
        nt, nv, nte = record_count("train"), record_count("validation"), record_count("test")
        total = max(1, nt + nv + nte)
        changed = False
        if nv / total > val_r + 0.03:
            val_cids = sorted(
                [cid for cid, s in assignment.items() if s == "validation"],
                key=lambda cid: (-by_id[cid].record_count, cid),
            )
            train_cids = sorted(
                [cid for cid, s in assignment.items() if s == "train"],
                key=lambda cid: (by_id[cid].record_count, cid),
            )
            if val_cids and train_cids:
                big = val_cids[0]
                # find a set of small train clusters whose size << big and keep project floor
                picked: list[str] = []
                acc = 0
                for cid in train_cids:
                    if by_id[cid].record_count >= by_id[big].record_count:
                        continue
                    picked.append(cid)
                    acc += by_id[cid].record_count
                    new_val_recs = nv - by_id[big].record_count + acc
                    new_val_proj = project_count("validation") - len(by_id[big].project_ids) + sum(
                        len(by_id[x].project_ids) for x in picked
                    )
                    if new_val_proj >= min_val_proj and new_val_recs <= nv - max(20, by_id[big].record_count // 5):
                        # commit swap
                        assignment[big] = "train"
                        for x in picked:
                            assignment[x] = "validation"
                        changed = True
                        break
                    if acc > by_id[big].record_count:
                        break
        if (not changed) and nte / total < test_r - 0.02:
            # already handled in phase 5
            pass
        if not changed:
            break

    nt, nv, nte, pt, pv, pte = counts()
    total = max(1, nt + nv + nte)
    diagnostics = {
        "total_records": total_records,
        "total_projects": total_projects,
        "cluster_count": len(clusters),
        "targets": {"train": train_r, "validation": val_r, "test": test_r},
        "achieved_ratios": {
            "train": round(nt / total, 4),
            "validation": round(nv / total, 4),
            "test": round(nte / total, 4),
        },
        "achieved_counts": {"train": nt, "validation": nv, "test": nte},
        "achieved_projects": {"train": pt, "validation": pv, "test": pte},
        "absolute_errors_pp": {
            "train": round(abs(nt / total - train_r) * 100, 2),
            "validation": round(abs(nv / total - val_r) * 100, 2),
            "test": round(abs(nte / total - test_r) * 100, 2),
        },
        "min_validation_projects": min_val_proj,
        "min_test_projects": min_test_proj,
        "heldout_project_floor": held_floor,
        "oversized_clusters": oversized,
        "ratio_within_5pp": (
            abs(nt / total - train_r) <= 0.05
            and abs(nv / total - val_r) <= 0.05
            and abs(nte / total - test_r) <= 0.05
        ),
        "objective_score": round(score(), 6),
        "local_search_rounds": rounds,
        "note": (
            "Record-weighted cluster assignment with project floors; "
            "oversized leak-safe clusters may prevent exact 80/10/10."
            if oversized or not (
                abs(nt / total - train_r) <= 0.05
                and abs(nv / total - val_r) <= 0.05
                and abs(nte / total - test_r) <= 0.05
            )
            else "Record-weighted cluster assignment with project floors."
        ),
    }
    return assignment, diagnostics


def expand_assignment_to_projects(
    clusters: list[ProjectCluster],
    assignment: dict[str, str],
) -> tuple[set[str], set[str], set[str]]:
    train: set[str] = set()
    val: set[str] = set()
    test: set[str] = set()
    by_id = {c.cluster_id: c for c in clusters}
    for cid, sp in assignment.items():
        members = by_id[cid].project_ids
        if sp == "train":
            train.update(members)
        elif sp == "validation":
            val.update(members)
        else:
            test.update(members)
    return train, val, test


def make_manifest(
    *,
    seed: int,
    train: set[str],
    validation: set[str],
    test: set[str],
    heldout: set[str] | None = None,
    extra_counts: dict[str, Any] | None = None,
) -> DatasetSplitManifest:
    held = sorted(heldout if heldout is not None else test)
    counts = {
        "train_projects": len(train),
        "validation_projects": len(validation),
        "test_projects": len(test),
        "total_projects": len(train | validation | test),
    }
    if extra_counts:
        counts.update(extra_counts)
    return DatasetSplitManifest(
        seed=seed,
        created_at=datetime.now(timezone.utc),
        train_project_ids=sorted(train),
        validation_project_ids=sorted(validation),
        test_project_ids=sorted(test),
        heldout_project_ids=held,
        counts=counts,
    )


def merge_cluster_roots(cluster_of: dict[str, str], pairs: list[tuple[str, str]]) -> dict[str, str]:
    """Union-find merge of project pairs into cluster_of mapping."""
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            # Prefer lexicographically smaller root for determinism
            if ra < rb:
                parent[rb] = ra
            else:
                parent[ra] = rb

    for p, root in cluster_of.items():
        find(p)
        find(root)
        union(p, root)
    for a, b in pairs:
        if a and b:
            union(a, b)
    return {p: find(p) for p in parent}
