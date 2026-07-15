#!/usr/bin/env python3
"""Validate ShareGPT-style SFT datasets for BidPilot / LLaMAFactory."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ALLOWED_ROLES = {"system", "user", "assistant", "tool"}
VALID_ORDER_PREFIXES = (
    ("system", "user", "assistant"),
    ("user", "assistant"),
)


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def message_fingerprint(messages: list[dict[str, str]]) -> str:
    payload = json.dumps(messages, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def validate_messages(messages: Any, *, idx: int, require_json: bool) -> list[str]:
    errors: list[str] = []
    if not isinstance(messages, list) or not messages:
        return [f"record#{idx}: messages must be non-empty list"]

    roles: list[str] = []
    for turn_i, msg in enumerate(messages):
        if not isinstance(msg, dict):
            errors.append(f"record#{idx}: turn#{turn_i} is not an object")
            continue
        role = msg.get("role")
        content = msg.get("content")
        if role not in ALLOWED_ROLES:
            errors.append(f"record#{idx}: invalid role '{role}'")
        if not isinstance(content, str) or not content.strip():
            errors.append(f"record#{idx}: empty content at turn#{turn_i}")
        roles.append(str(role))

    if "assistant" not in roles:
        errors.append(f"record#{idx}: missing assistant output")

    # Accept multi-turn dialogues ending with assistant; verify first three roles when short.
    prefix = tuple(roles[:3])
    if len(roles) >= 3 and prefix not in VALID_ORDER_PREFIXES and roles[0] not in {"system", "user"}:
        errors.append(f"record#{idx}: unexpected message order {roles}")
    if roles and roles[0] == "assistant":
        errors.append(f"record#{idx}: messages must not start with assistant")

    if require_json:
        for msg in reversed(messages):
            if msg.get("role") == "assistant":
                try:
                    json.loads(msg["content"])
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"record#{idx}: assistant JSON parse failed: {exc}")
                break

    return errors


def validate_dataset(
    records: list[dict[str, Any]],
    *,
    require_json: bool,
    train_projects: set[str] | None = None,
    test_projects: set[str] | None = None,
) -> dict[str, Any]:
    errors: list[str] = []
    fingerprints: list[str] = []
    project_ids: list[str] = []

    for idx, record in enumerate(records):
        msgs = record.get("messages")
        errors.extend(validate_messages(msgs, idx=idx, require_json=require_json))
        if isinstance(msgs, list):
            fingerprints.append(message_fingerprint(msgs))
        pid = record.get("project_id")
        if isinstance(pid, str) and pid:
            project_ids.append(pid)

    dup_counts = Counter(fingerprints)
    duplicates = [fp for fp, cnt in dup_counts.items() if cnt > 1]

    leakage: list[str] = []
    if train_projects is not None and test_projects is not None:
        leakage = sorted(train_projects & test_projects)

    return {
        "records": len(records),
        "errors": errors,
        "duplicate_count": len(duplicates),
        "duplicates": duplicates[:20],
        "project_count": len(set(project_ids)),
        "project_leakage": leakage,
        "ok": not errors and not duplicates and not leakage,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate BidPilot ShareGPT SFT dataset")
    parser.add_argument("--dataset-file", required=True)
    parser.add_argument("--dataset-info", required=True)
    parser.add_argument("--dataset-name", required=True)
    parser.add_argument("--require-json-assistant", action="store_true", default=True)
    parser.add_argument("--no-require-json-assistant", action="store_true")
    parser.add_argument("--train-file", default=None)
    parser.add_argument("--test-file", default=None)
    args = parser.parse_args()

    require_json = not args.no_require_json_assistant
    dataset_file = Path(args.dataset_file)
    dataset_info = load_json(Path(args.dataset_info))
    if args.dataset_name not in dataset_info:
        print(
            json.dumps(
                {
                    "ok": False,
                    "errors": [f"dataset '{args.dataset_name}' not found in dataset_info.json"],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1

    entry = dataset_info[args.dataset_name]
    expected_file = entry.get("file_name")
    if expected_file and Path(expected_file).name != dataset_file.name:
        print(
            json.dumps(
                {
                    "ok": False,
                    "errors": [
                        f"dataset file name mismatch: dataset_info expects '{expected_file}', "
                        f"got '{dataset_file.name}'"
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1

    data = load_json(dataset_file)
    if not isinstance(data, list):
        print(json.dumps({"ok": False, "errors": ["dataset root must be a list"]}, indent=2))
        return 1

    train_projects = test_projects = None
    if args.train_file and args.test_file:
        train = load_json(Path(args.train_file))
        test = load_json(Path(args.test_file))
        train_projects = {r.get("project_id") for r in train if r.get("project_id")}
        test_projects = {r.get("project_id") for r in test if r.get("project_id")}

    report = validate_dataset(
        data,
        require_json=require_json,
        train_projects=train_projects,
        test_projects=test_projects,
    )
    report["dataset_name"] = args.dataset_name
    report["formatting"] = entry.get("formatting")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
