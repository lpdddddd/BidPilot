#!/usr/bin/env python3
"""Export BidPilot SFT data into LLaMAFactory ShareGPT messages format.

This script does NOT connect to online models and does NOT start training.
It can read JSONL annotation files and optionally a PostgreSQL dump via JSON export.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ALLOWED_ROLES = {"system", "user", "assistant", "tool"}


def load_records(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return []
    if path.suffix.lower() == ".jsonl":
        records: list[dict[str, Any]] = []
        for line_no, line in enumerate(text.splitlines(), start=1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSONL at {path}:{line_no}: {exc}") from exc
            if not isinstance(obj, dict):
                raise ValueError(f"JSONL record must be object at {path}:{line_no}")
            records.append(obj)
        return records
    data = json.loads(text)
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and "records" in data:
        return list(data["records"])
    raise ValueError("Input must be a JSON list, JSONL, or object with 'records'")


def normalize_record(raw: dict[str, Any]) -> dict[str, Any]:
    if "messages" in raw:
        messages = raw["messages"]
    elif "conversations" in raw:
        # ShareGPT alternative conversations -> messages
        messages = [
            {"role": item.get("from") or item.get("role"), "content": item.get("value") or item.get("content")}
            for item in raw["conversations"]
        ]
    else:
        raise ValueError("Record missing messages/conversations")

    if not isinstance(messages, list) or not messages:
        raise ValueError("messages must be a non-empty list")

    normalized_messages: list[dict[str, str]] = []
    for msg in messages:
        role = msg.get("role")
        content = msg.get("content")
        if role not in ALLOWED_ROLES:
            raise ValueError(f"Invalid role: {role}")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("Empty message content")
        normalized_messages.append({"role": role, "content": content})

    roles = [m["role"] for m in normalized_messages]
    if "assistant" not in roles:
        raise ValueError("assistant turn is required")
    if roles[0] == "assistant":
        raise ValueError("messages must not start with assistant")

    record = {
        "messages": normalized_messages,
        "project_id": raw.get("project_id") or raw.get("source_project_id"),
        "task_type": raw.get("task_type") or "general",
        "is_test_project": bool(raw.get("is_test_project", False)),
    }
    return record


def validate_assistant_json(record: dict[str, Any], *, require_json: bool) -> None:
    if not require_json:
        return
    for msg in reversed(record["messages"]):
        if msg["role"] == "assistant":
            json.loads(msg["content"])
            return
    raise ValueError("No assistant message for JSON validation")


def split_records(
    records: list[dict[str, Any]],
    *,
    train_ratio: float,
    val_ratio: float,
    seed: int,
) -> dict[str, list[dict[str, Any]]]:
    if train_ratio + val_ratio >= 1.0:
        raise ValueError("train_ratio + val_ratio must be < 1.0 (remainder goes to test)")

    # Project-level split: never put test projects into train.
    test_projects = {
        r["project_id"]
        for r in records
        if r.get("is_test_project") or str(r.get("project_id", "")).endswith("-test") or "test" in str(r.get("project_id", "")).lower()
    }
    by_project: dict[str | None, list[dict[str, Any]]] = {}
    for record in records:
        by_project.setdefault(record.get("project_id"), []).append(record)

    project_ids = sorted([pid for pid in by_project if pid is not None and pid not in test_projects], key=str)
    rng = random.Random(seed)
    rng.shuffle(project_ids)

    n = len(project_ids)
    n_train = int(n * train_ratio)
    n_val = int(n * val_ratio)
    train_ids = set(project_ids[:n_train])
    val_ids = set(project_ids[n_train : n_train + n_val])
    heldout_ids = set(project_ids[n_train + n_val :]) | test_projects

    splits: dict[str, list[dict[str, Any]]] = {"train": [], "validation": [], "test": []}
    for pid, items in by_project.items():
        if pid in train_ids:
            splits["train"].extend(items)
        elif pid in val_ids:
            splits["validation"].extend(items)
        elif pid in heldout_ids or pid is None:
            # Unknown project ids go to validation to avoid accidental train leakage.
            if pid in test_projects:
                splits["test"].extend(items)
            else:
                splits["validation"].extend(items)
        else:
            splits["test"].extend(items)

    # Enforce no project intersection between train and test.
    train_projects = {r.get("project_id") for r in splits["train"]}
    test_projects_out = {r.get("project_id") for r in splits["test"]}
    overlap = train_projects & test_projects_out - {None}
    if overlap:
        raise RuntimeError(f"Train/test project leakage detected: {sorted(overlap)}")

    return splits


def write_split(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Keep only messages for LLaMAFactory consumption; metadata retained in sidecar stats.
    payload = [{"messages": r["messages"]} for r in records]
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Export ShareGPT SFT dataset for LLaMAFactory")
    parser.add_argument("--input", required=True, help="JSON / JSONL source path")
    parser.add_argument("--output-dir", required=True, help="Directory for train/validation/test JSON")
    parser.add_argument("--task-type", default=None, help="Optional task_type filter")
    parser.add_argument("--train-ratio", type=float, default=0.8)
    parser.add_argument("--val-ratio", type=float, default=0.1)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--require-json-assistant", action="store_true")
    args = parser.parse_args()

    input_path = Path(args.input)
    output_dir = Path(args.output_dir)
    raw_records = load_records(input_path)
    records: list[dict[str, Any]] = []
    errors = 0
    for idx, raw in enumerate(raw_records):
        try:
            record = normalize_record(raw)
            if args.task_type and record["task_type"] != args.task_type:
                continue
            validate_assistant_json(record, require_json=args.require_json_assistant)
            records.append(record)
        except Exception as exc:  # noqa: BLE001
            errors += 1
            print(f"[skip] record#{idx}: {exc}", file=sys.stderr)

    splits = split_records(
        records,
        train_ratio=args.train_ratio,
        val_ratio=args.val_ratio,
        seed=args.seed,
    )
    for name, items in splits.items():
        write_split(output_dir / f"{name}.json", items)

    stats = {
        "input": str(input_path),
        "accepted": len(records),
        "skipped_errors": errors,
        "counts": {k: len(v) for k, v in splits.items()},
        "task_types": dict(Counter(r["task_type"] for r in records)),
        "train_projects": sorted({r.get("project_id") for r in splits["train"] if r.get("project_id")}),
        "test_projects": sorted({r.get("project_id") for r in splits["test"] if r.get("project_id")}),
    }
    (output_dir / "export_stats.json").write_text(
        json.dumps(stats, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0 if errors == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
