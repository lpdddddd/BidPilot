#!/usr/bin/env python3
"""Strict validation of real BidPilot LLaMAFactory SFT exports (not sample)."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_messages(messages: list[dict[str, Any]], idx: int) -> list[str]:
    errors: list[str] = []
    if not messages:
        return [f"#{idx}: empty messages"]
    roles = [m.get("role") for m in messages]
    if roles[0] == "assistant":
        errors.append(f"#{idx}: starts with assistant")
    for i, role in enumerate(roles):
        if role == "tool":
            if i == 0 or roles[i - 1] != "assistant":
                errors.append(f"#{idx}: tool not after assistant at {i}")
            else:
                try:
                    prev = json.loads(messages[i - 1]["content"])
                except Exception:  # noqa: BLE001
                    errors.append(f"#{idx}: tool-call parent not JSON")
                    continue
                if not prev.get("tool_name") or "arguments" not in prev:
                    # accept if previous assistant is already final (should not precede tool)
                    errors.append(f"#{idx}: assistant before tool missing tool_name/arguments")
    if roles[-1] != "assistant":
        errors.append(f"#{idx}: must end with assistant")
    else:
        try:
            final = json.loads(messages[-1]["content"])
        except Exception as exc:  # noqa: BLE001
            errors.append(f"#{idx}: final assistant JSON invalid: {exc}")
            return errors
        if "tool" in roles:
            if not (final.get("answer") or final.get("clarify")):
                errors.append(f"#{idx}: agent final missing answer/clarify")
            if final.get("answer") and not final.get("clarify") and not final.get("citations"):
                errors.append(f"#{idx}: factual agent answer missing citations")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", default=str(ROOT))
    args = parser.parse_args()
    root = Path(args.repo_root)
    data_dir = root / "training" / "llamafactory" / "data"
    info = load_json(data_dir / "dataset_info.json")
    names = [
        "bidpilot_sft_train",
        "bidpilot_sft_validation",
        "bidpilot_sft_test",
        "bidpilot_sft_train_qwen3",
    ]
    errors: list[str] = []
    reports: dict[str, Any] = {}
    project_sets: dict[str, set[str]] = {}
    all_fps: set[str] = set()

    rejected_path = root / "datasets" / "rejected" / "sft.jsonl"
    rejected_fps: set[str] = set()
    if rejected_path.exists():
        for line in rejected_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            msgs = row.get("messages") or []
            rejected_fps.add(json.dumps(msgs, ensure_ascii=False, sort_keys=True))

    for name in names:
        if name not in info:
            errors.append(f"dataset_info missing {name}")
            continue
        fname = info[name]["file_name"]
        path = data_dir / fname
        if not path.exists():
            errors.append(f"missing file {path}")
            continue
        data = load_json(path)
        if not isinstance(data, list):
            errors.append(f"{name} root not list")
            continue
        # Prefer full records beside sharegpt for project ids
        split = name.replace("bidpilot_sft_", "").replace("_qwen3", "")
        rec_path = root / "datasets" / "sft" / split / "records.jsonl"
        projects: set[str] = set()
        if rec_path.exists() and "qwen3" not in name:
            for line in rec_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    projects.add(json.loads(line).get("project_id"))
            project_sets[split] = projects
        local_err: list[str] = []
        for i, row in enumerate(data):
            msgs = row.get("messages")
            if not isinstance(msgs, list):
                local_err.append(f"{name}#{i}: no messages")
                continue
            fp = json.dumps(msgs, ensure_ascii=False, sort_keys=True)
            if fp in rejected_fps:
                local_err.append(f"{name}#{i}: rejected record leaked into export")
            local_err.extend(validate_messages(msgs, i))
        reports[name] = {"records": len(data), "errors": local_err[:50], "error_count": len(local_err)}
        errors.extend([f"{name}: {e}" for e in local_err[:20]])

    # Project mutual exclusion
    if {"train", "validation", "test"} <= set(project_sets):
        if project_sets["train"] & project_sets["test"]:
            errors.append("train/test project leakage")
        if project_sets["train"] & project_sets["validation"]:
            errors.append("train/validation project leakage")
        if project_sets["validation"] & project_sets["test"]:
            errors.append("validation/test project leakage")

    # Structure counts
    stats_path = root / "datasets" / "reports" / "sft_build_stats.json"
    if stats_path.exists():
        stats = load_json(stats_path)
        split_sum = int(stats.get("train") or 0) + int(stats.get("validation") or 0) + int(stats.get("test") or 0)
        if split_sum != int(stats.get("structurally_valid_sft") or -1):
            errors.append(
                f"split sum {split_sum} != structurally_valid_sft {stats.get('structurally_valid_sft')}"
            )

    external = "not_run"
    lf_home = os.environ.get("LLAMAFACTORY_HOME")
    if lf_home and Path(lf_home).exists():
        # Dry preprocess smoke: check tool tag accepted in dataset_info tags only
        tags = (info.get("bidpilot_sft_train") or {}).get("tags") or {}
        if tags.get("tool_tag") != "tool":
            errors.append("dataset_info missing tool_tag for real train")
        external = "tags_checked_only_no_training"
    else:
        external = "not_run"

    report = {
        "ok": not errors,
        "errors": errors[:200],
        "datasets": reports,
        "external_llamafactory_validation": external,
        "followup_command": (
            "export LLAMAFACTORY_HOME=/path/to/LLaMA-Factory && "
            "python scripts/vllm_infer.py --help >/dev/null; "
            "# or: llamafactory-cli train with --preview / do_train false if available"
        ),
    }
    out = root / "datasets" / "reports" / "llamafactory_real_validation.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
