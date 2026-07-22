"""Project/document-isolated train/validation/test split for reference samples."""

from __future__ import annotations

import random
from collections import defaultdict
from typing import Any

from bidpilot_data.reference_dataset.schema import ReferenceSample


def _assign_projects(
    project_ids: list[str],
    *,
    seed: int,
    train_r: float = 0.7,
    val_r: float = 0.15,
    test_r: float = 0.15,
) -> dict[str, str]:
    """Deterministic project→split assignment. Never splits a project."""
    ids = sorted({p for p in project_ids if p})
    if not ids:
        return {}
    rng = random.Random(seed)
    order = ids[:]
    rng.shuffle(order)
    n = len(order)
    if n == 1:
        return {order[0]: "train"}
    if n == 2:
        return {order[0]: "train", order[1]: "test"}
    n_test = max(1, int(round(n * test_r)))
    n_val = max(1, int(round(n * val_r)))
    if n_test + n_val >= n:
        n_test = 1
        n_val = 1 if n > 2 else 0
    test_ids = set(order[:n_test])
    val_ids = set(order[n_test : n_test + n_val])
    assignment: dict[str, str] = {}
    for pid in order:
        if pid in test_ids:
            assignment[pid] = "test"
        elif pid in val_ids:
            assignment[pid] = "validation"
        else:
            assignment[pid] = "train"
    return assignment


def assign_splits(
    samples: list[ReferenceSample],
    *,
    seed: int = 42,
    document_index: dict[str, dict[str, Any]] | None = None,
) -> tuple[list[ReferenceSample], dict[str, Any]]:
    """Assign splits by project_id isolation; also ensure no document_id crosses splits.

    If the same document appears under multiple projects (should not), documents are
    pinned to the first project's split and conflicting samples are moved with that project.
    """
    document_index = document_index or {}
    project_ids = sorted({s.project_id for s in samples if s.project_id})
    project_to_split = _assign_projects(project_ids, seed=seed)

    # Build document→project map from samples + index
    doc_to_projects: dict[str, set[str]] = defaultdict(set)
    for s in samples:
        if s.document_id:
            doc_to_projects[s.document_id].add(s.project_id)
    for did, doc in document_index.items():
        if doc.get("project_id"):
            doc_to_projects[did].add(doc["project_id"])

    # If a document is tied to multiple projects in different splits, unify to one split
    # by moving all related projects to the train split (conservative).
    for did, pids in doc_to_projects.items():
        splits = {project_to_split.get(p) for p in pids if p in project_to_split}
        splits.discard(None)
        if len(splits) > 1:
            for p in pids:
                if p in project_to_split:
                    project_to_split[p] = "train"

    # Document→split must be unique
    doc_to_split: dict[str, str] = {}
    for s in samples:
        split = project_to_split.get(s.project_id, "train")
        if s.document_id:
            prev = doc_to_split.get(s.document_id)
            if prev and prev != split:
                # Force both projects to same split
                project_to_split[s.project_id] = prev
                split = prev
            doc_to_split[s.document_id] = split

    updated: list[ReferenceSample] = []
    for s in samples:
        split = project_to_split.get(s.project_id, "train")
        if s.document_id and s.document_id in doc_to_split:
            split = doc_to_split[s.document_id]
            project_to_split[s.project_id] = split
        updated.append(s.model_copy(update={"split": split}))  # type: ignore[arg-type]

    # Final leakage check
    split_docs: dict[str, set[str]] = defaultdict(set)
    split_projects: dict[str, set[str]] = defaultdict(set)
    for s in updated:
        sp = s.split or "train"
        split_projects[sp].add(s.project_id)
        if s.document_id:
            split_docs[sp].add(s.document_id)

    leakage_docs = (split_docs["train"] & split_docs["validation"]) | (
        split_docs["train"] & split_docs["test"]
    ) | (split_docs["validation"] & split_docs["test"])
    leakage_projects = (split_projects["train"] & split_projects["validation"]) | (
        split_projects["train"] & split_projects["test"]
    ) | (split_projects["validation"] & split_projects["test"])

    manifest = {
        "seed": seed,
        "project_to_split": project_to_split,
        "counts": {
            "train": sum(1 for s in updated if s.split == "train"),
            "validation": sum(1 for s in updated if s.split == "validation"),
            "test": sum(1 for s in updated if s.split == "test"),
        },
        "project_counts": {k: len(v) for k, v in split_projects.items()},
        "document_leakage": sorted(leakage_docs),
        "project_leakage": sorted(leakage_projects),
        "ok": not leakage_docs and not leakage_projects,
    }
    return updated, manifest
