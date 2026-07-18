"""Artifact consistency validator — single source of truth = final split records."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from bidpilot_data.reporting.artifact_meta import sha256_json_obj, sha256_jsonl_file
from bidpilot_data.settings import get_settings
from bidpilot_data.utils import read_json, read_jsonl, write_json


def _load(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return read_json(path) if path.suffix == ".json" else {}


def compute_truth_from_records(datasets_root: Path) -> dict[str, Any]:
    splits: dict[str, list[dict[str, Any]]] = {}
    for name in ("train", "validation", "test"):
        splits[name] = read_jsonl(datasets_root / "sft" / name / "records.jsonl")
    rejected = read_jsonl(datasets_root / "rejected" / "sft.jsonl")
    rejected_ids = {r.get("record_id") for r in rejected if r.get("record_id")}

    truth: dict[str, Any] = {
        "records": {k: len(v) for k, v in splits.items()},
        "projects": {k: sorted({r.get("project_id") for r in v if r.get("project_id")}) for k, v in splits.items()},
        "project_counts": {k: len({r.get("project_id") for r in v if r.get("project_id")}) for k, v in splits.items()},
        "by_split_and_task": {
            k: dict(Counter(r.get("task_type") for r in v if r.get("task_type"))) for k, v in splits.items()
        },
        "quality_level": {
            k: dict(Counter(r.get("quality_level") for r in v if r.get("quality_level"))) for k, v in splits.items()
        },
        "total": sum(len(v) for v in splits.values()),
        "rejected_ids_in_splits": [],
    }
    for name, rows in splits.items():
        for r in rows:
            rid = r.get("record_id")
            if rid and rid in rejected_ids:
                truth["rejected_ids_in_splits"].append({"split": name, "record_id": rid})
    return truth


def validate_artifact_consistency(*, write_report: bool = True) -> dict[str, Any]:
    settings = get_settings()
    root = settings.datasets_root
    reports = root / "reports"
    errors: list[str] = []
    warnings: list[str] = []

    truth = compute_truth_from_records(root)
    manifest = _load(root / "manifests" / "sft_split_manifest.json")
    sft_stats = _load(reports / "sft_build_stats.json")
    split_dist = _load(reports / "split_distribution.json")
    task_dist = _load(reports / "task_distribution.json")
    ds_stats = _load(reports / "dataset_statistics.json")
    xsim = _load(reports / "cross_split_similarity_report.json")
    readiness = _load(reports / "training_readiness_report.json")

    # Record counts
    for split in ("train", "validation", "test"):
        t_n = truth["records"][split]
        if int(sft_stats.get(split) or -1) != t_n:
            errors.append(f"sft_build_stats.{split}={sft_stats.get(split)} != records={t_n}")
        sd = (split_dist.get(split) or {}).get("record_count")
        if sd is not None and int(sd) != t_n:
            errors.append(f"split_distribution.{split}.record_count={sd} != records={t_n}")
        xs = ((xsim.get("split_stats") or {}).get(split) or {}).get("records")
        if xs is not None and int(xs) != t_n:
            errors.append(f"cross_split.split_stats.{split}.records={xs} != records={t_n}")
        cm = (truth["project_counts"][split])
        if int(sft_stats.get(f"{split}_projects") or -1) not in (-1, cm) and f"{split}_projects" in sft_stats:
            if int(sft_stats.get(f"{split}_projects")) != cm:
                errors.append(f"sft_build_stats.{split}_projects={sft_stats.get(f'{split}_projects')} != {cm}")
        sd_pc = (split_dist.get(split) or {}).get("project_count")
        if sd_pc is not None and int(sd_pc) != cm:
            errors.append(f"split_distribution.{split}.project_count={sd_pc} != {cm}")

    total = truth["total"]
    if int(sft_stats.get("structurally_valid_sft") or -1) not in (-1, total) and "structurally_valid_sft" in sft_stats:
        if int(sft_stats["structurally_valid_sft"]) != total:
            errors.append(
                f"sft_build_stats.structurally_valid_sft={sft_stats['structurally_valid_sft']} != sum splits={total}"
            )
    if sft_stats:
        summed = int(sft_stats.get("train") or 0) + int(sft_stats.get("validation") or 0) + int(sft_stats.get("test") or 0)
        if summed != total:
            errors.append(f"sft_build_stats split sum {summed} != records total {total}")

    # Manifest project sets
    if manifest:
        for split, key in (
            ("train", "train_project_ids"),
            ("validation", "validation_project_ids"),
            ("test", "test_project_ids"),
        ):
            mset = set(manifest.get(key) or [])
            tset = set(truth["projects"][split])
            if mset != tset:
                errors.append(
                    f"manifest {key} mismatch: only_in_manifest={sorted(mset - tset)[:5]} "
                    f"only_in_records={sorted(tset - mset)[:5]}"
                )

    # Task distribution by split
    by_split_task = task_dist.get("by_split_and_task") or {}
    for split, expected in truth["by_split_and_task"].items():
        got = by_split_task.get(split) or {}
        if dict(got) != dict(expected):
            errors.append(f"task_distribution.by_split_and_task.{split} mismatch got={got} expected={expected}")
        sd_task = (split_dist.get(split) or {}).get("task_type") or {}
        if dict(sd_task) != dict(expected):
            errors.append(f"split_distribution.{split}.task_type mismatch")

    # Quality levels
    for split, expected in truth["quality_level"].items():
        got = (split_dist.get(split) or {}).get("quality_level") or {}
        if got and dict(got) != dict(expected):
            errors.append(f"split_distribution.{split}.quality_level mismatch")

    # Rejected in splits
    if truth["rejected_ids_in_splits"]:
        errors.append(f"rejected records present in splits: {truth['rejected_ids_in_splits'][:10]}")

    # LLaMAFactory export counts
    lf_dir = settings.repo_root / "training" / "llamafactory" / "data"
    for split in ("train", "validation", "test"):
        path = lf_dir / f"bidpilot_sft_{split}.json"
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if len(data) != truth["records"][split]:
                    errors.append(
                        f"LLaMAFactory bidpilot_sft_{split}.json len={len(data)} != records={truth['records'][split]}"
                    )
            except Exception as exc:  # noqa: BLE001
                errors.append(f"LLaMAFactory {split} unreadable: {exc}")

    # dataset_statistics only enforced when stamped with same build_id (report may lag build-sft)
    if ds_stats and sft_stats.get("dataset_build_id") and ds_stats.get("dataset_build_id") == sft_stats.get("dataset_build_id"):
        ds_sft = ds_stats.get("sft") or {}
        for split in ("train", "validation", "test"):
            if split in ds_sft and int(ds_sft[split]) != truth["records"][split]:
                errors.append(f"dataset_statistics.sft.{split}={ds_sft[split]} != records={truth['records'][split]}")
    elif ds_stats and sft_stats.get("dataset_build_id") and ds_stats.get("dataset_build_id") not in {
        None,
        sft_stats.get("dataset_build_id"),
    }:
        errors.append("dataset_statistics.dataset_build_id mismatch vs sft_build_stats")
    elif ds_stats and sft_stats.get("dataset_build_id") and not ds_stats.get("dataset_build_id"):
        warnings.append("dataset_statistics missing dataset_build_id; run make dataset-report")

    # build_id / manifest hash consistency across reports
    build_ids: dict[str, str] = {}
    manifest_hashes: dict[str, str] = {}
    for label, obj in (
        ("sft_build_stats", sft_stats),
        ("split_distribution", split_dist),
        ("task_distribution", task_dist),
        ("cross_split_similarity_report", xsim),
        ("training_readiness_report", readiness),
        ("dataset_statistics", ds_stats),
    ):
        if not obj:
            continue
        if "dataset_build_id" in obj:
            build_ids[label] = obj["dataset_build_id"]
        if "split_manifest_sha256" in obj:
            manifest_hashes[label] = obj["split_manifest_sha256"]

    if build_ids:
        # dataset_statistics may lag; exclude it from hard mismatch until stamped
        core_ids = {k: v for k, v in build_ids.items() if k != "dataset_statistics"}
        if core_ids and len(set(core_ids.values())) > 1:
            errors.append(f"dataset_build_id mismatch across reports: {core_ids}")
        if "dataset_statistics" in build_ids and core_ids:
            core_val = next(iter(core_ids.values()))
            if build_ids["dataset_statistics"] != core_val:
                errors.append("dataset_statistics.dataset_build_id mismatch vs core SFT reports")
    else:
        warnings.append("dataset_build_id missing from reports")

    if manifest and manifest_hashes:
        expected_mh = sha256_json_obj(manifest)
        for label, h in manifest_hashes.items():
            if h != expected_mh:
                errors.append(f"{label}.split_manifest_sha256 mismatch vs current manifest")

    # source records hash if present
    source_hashes = {
        label: obj.get("source_records_sha256")
        for label, obj in (
            ("sft_build_stats", sft_stats),
            ("split_distribution", split_dist),
            ("task_distribution", task_dist),
        )
        if obj and obj.get("source_records_sha256")
    }
    if source_hashes and len(set(source_hashes.values())) > 1:
        errors.append(f"source_records_sha256 mismatch: {source_hashes}")

    # cross_split full_scan required for gate
    if xsim and not xsim.get("full_scan", False):
        errors.append("cross_split full_scan=false (skipped candidates or incomplete scan)")
    if xsim and xsim.get("skipped_candidates_count", 0) > 0 and xsim.get("full_scan"):
        errors.append("cross_split claims full_scan but skipped_candidates_count>0")

    # Project mutex
    t, v, te = set(truth["projects"]["train"]), set(truth["projects"]["validation"]), set(truth["projects"]["test"])
    if t & v:
        errors.append(f"train/validation project overlap: {sorted(t & v)[:5]}")
    if t & te:
        errors.append(f"train/test project overlap: {sorted(t & te)[:5]}")
    if v & te:
        errors.append(f"validation/test project overlap: {sorted(v & te)[:5]}")

    report = {
        "ok": not errors,
        "error_count": len(errors),
        "warning_count": len(warnings),
        "errors": errors[:200],
        "warnings": warnings[:100],
        "truth": {
            "records": truth["records"],
            "project_counts": truth["project_counts"],
            "total": truth["total"],
            "by_split_and_task": truth["by_split_and_task"],
        },
        "build_ids": build_ids,
        "source_records_sha256_live": {
            "train": sha256_jsonl_file(root / "sft" / "train" / "records.jsonl"),
            "validation": sha256_jsonl_file(root / "sft" / "validation" / "records.jsonl"),
            "test": sha256_jsonl_file(root / "sft" / "test" / "records.jsonl"),
        },
    }
    if write_report:
        write_json(reports / "artifact_consistency_report.json", report)
    return report
