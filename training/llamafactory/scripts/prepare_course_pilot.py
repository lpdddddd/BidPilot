#!/usr/bin/env python3
"""Build course-pilot SFT set: QC → review queue → fixed splits for LoRA.

Honest scope:
- Human Gold remains 0 in the formal gate sense.
- This script applies automatic structural QC + balanced sampling and writes a
  *course_pilot* track for coursework demos (not formal human_gold).
- Review queue JSONL/CSV is exported for optional human spot-check.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import re
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _messages_ok(messages: list[dict[str, Any]]) -> tuple[bool, str]:
    if not messages or len(messages) < 2:
        return False, "too_few_messages"
    roles = [m.get("role") for m in messages]
    if roles[-1] != "assistant":
        return False, "must_end_assistant"
    if roles[0] == "assistant":
        return False, "starts_assistant"
    for m in messages:
        content = m.get("content")
        if not isinstance(content, str) or not content.strip():
            return False, "empty_content"
        if len(content) > 12000:
            return False, "content_too_long"
    # Prefer JSON assistant for structured tasks
    try:
        json.loads(messages[-1]["content"])
    except Exception:
        # allow non-JSON for citation_qa prose if present
        if any(r == "tool" for r in roles):
            return False, "tool_path_needs_json_assistant"
    # Block obvious secrets
    blob = " ".join(str(m.get("content") or "") for m in messages).lower()
    if re.search(r"(api[_-]?key\s*[:=]|bearer\s+[a-z0-9]|postgresql://|sk-[a-z0-9]{10})", blob):
        return False, "secret_pattern"
    return True, "ok"


def _fingerprint(messages: list[dict[str, Any]]) -> str:
    payload = json.dumps(messages, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()


def collect_records(root: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for split in ("train", "validation", "test"):
        path = root / "datasets" / "sft" / split / "records.jsonl"
        for row in _load_jsonl(path):
            row = dict(row)
            row.setdefault("split", split)
            out.append(row)
    return out


def quality_filter(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        messages = row.get("messages") or []
        ok, reason = _messages_ok(messages)
        fp = _fingerprint(messages)
        if not ok:
            rejected.append({**row, "reject_reason": reason})
            continue
        if fp in seen:
            rejected.append({**row, "reject_reason": "duplicate_messages"})
            continue
        seen.add(fp)
        if not row.get("task_type") or not row.get("project_id"):
            rejected.append({**row, "reject_reason": "missing_task_or_project"})
            continue
        accepted.append(row)
    return accepted, rejected


def balanced_sample(
    rows: list[dict[str, Any]],
    *,
    seed: int,
    max_per_task: int,
    max_total: int,
) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    by_task: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_task[str(row.get("task_type"))].append(row)
    picked: list[dict[str, Any]] = []
    for task, items in sorted(by_task.items()):
        rng.shuffle(items)
        picked.extend(items[:max_per_task])
    rng.shuffle(picked)
    return picked[:max_total]


def to_sharegpt(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for row in rows:
        out.append(
            {
                "messages": row["messages"],
                "record_id": row.get("record_id"),
                "task_type": row.get("task_type"),
                "project_id": row.get("project_id"),
                "quality_level": "course_pilot",
                "review_status": "course_pilot_approved",
                "split": row.get("split"),
            }
        )
    return out


def write_review_queue(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "record_id",
                "task_type",
                "project_id",
                "split",
                "quality_level",
                "review_status",
                "approve",
                "notes",
            ],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "record_id": row.get("record_id"),
                    "task_type": row.get("task_type"),
                    "project_id": row.get("project_id"),
                    "split": row.get("split"),
                    "quality_level": row.get("quality_level"),
                    "review_status": row.get("review_status"),
                    "approve": "yes",
                    "notes": "course_pilot_auto_qc",
                }
            )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--max-per-task", type=int, default=200)
    parser.add_argument("--max-total", type=int, default=1200)
    parser.add_argument("--smoke-total", type=int, default=64)
    args = parser.parse_args()
    root = args.repo_root.resolve()

    all_rows = collect_records(root)
    accepted, rejected = quality_filter(all_rows)
    # Preserve original split assignment from manifest (do not reshuffle projects).
    by_split: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in accepted:
        by_split[str(row.get("split") or "train")].append(row)

    train = balanced_sample(
        by_split.get("train", []),
        seed=args.seed,
        max_per_task=args.max_per_task,
        max_total=args.max_total,
    )
    validation = balanced_sample(
        by_split.get("validation", []),
        seed=args.seed + 1,
        max_per_task=max(20, args.max_per_task // 4),
        max_total=max(100, args.max_total // 6),
    )
    test = balanced_sample(
        by_split.get("test", []),
        seed=args.seed + 2,
        max_per_task=max(20, args.max_per_task // 4),
        max_total=max(80, args.max_total // 8),
    )

    data_dir = root / "training" / "llamafactory" / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    for name, rows in (
        ("bidpilot_course_pilot_train.json", train),
        ("bidpilot_course_pilot_validation.json", validation),
        ("bidpilot_course_pilot_test.json", test),
    ):
        (data_dir / name).write_text(
            json.dumps(to_sharegpt(rows), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    smoke = balanced_sample(train, seed=args.seed + 9, max_per_task=20, max_total=args.smoke_total)
    (data_dir / "bidpilot_course_pilot_smoke.json").write_text(
        json.dumps(to_sharegpt(smoke), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # Update dataset_info.json entries
    info_path = data_dir / "dataset_info.json"
    info = json.loads(info_path.read_text(encoding="utf-8")) if info_path.exists() else {}
    sharegpt_tags = {
        "role_tag": "role",
        "content_tag": "content",
        "user_tag": "user",
        "assistant_tag": "assistant",
        "system_tag": "system",
        "tool_tag": "tool",
    }
    for key, file_name in (
        ("bidpilot_course_pilot_train", "bidpilot_course_pilot_train.json"),
        ("bidpilot_course_pilot_validation", "bidpilot_course_pilot_validation.json"),
        ("bidpilot_course_pilot_test", "bidpilot_course_pilot_test.json"),
        ("bidpilot_course_pilot_smoke", "bidpilot_course_pilot_smoke.json"),
    ):
        info[key] = {
            "file_name": file_name,
            "formatting": "sharegpt",
            "columns": {"messages": "messages"},
            "tags": sharegpt_tags,
        }
    info_path.write_text(json.dumps(info, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    review_dir = root / "datasets" / "review" / "course_pilot"
    review_dir.mkdir(parents=True, exist_ok=True)
    write_review_queue(review_dir / "review_queue.csv", train + validation + test)
    (review_dir / "rejected_qc.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rejected[:5000]) + ("\n" if rejected else ""),
        encoding="utf-8",
    )

    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "input_total": len(all_rows),
        "qc_accepted": len(accepted),
        "qc_rejected": len(rejected),
        "reject_reasons": dict(Counter(r.get("reject_reason") for r in rejected)),
        "course_pilot": {
            "train": len(train),
            "validation": len(validation),
            "test": len(test),
            "smoke": len(smoke),
            "by_task_train": dict(Counter(r.get("task_type") for r in train)),
        },
        "label_policy": (
            "course_pilot_approved via automatic QC + balanced sampling; "
            "NOT human_gold. Formal LoRA gate still requires reviewed gold."
        ),
    }
    report_path = root / "datasets" / "reports" / "course_pilot_sft_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
