"""Atomic SFT artifact publish with exclusive build lock."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from bidpilot_data.utils import ensure_dir, write_json, write_jsonl


class BuildLockError(RuntimeError):
    pass


@contextmanager
def exclusive_build_lock(lock_path: Path, *, timeout_sec: float = 2.0) -> Iterator[None]:
    """Non-blocking exclusive lock; second builder must fail rather than overwrite."""
    ensure_dir(lock_path.parent)
    fd = None
    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o644)
        start = time.time()
        while True:
            try:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError as exc:
                if time.time() - start >= timeout_sec:
                    raise BuildLockError(f"another SFT build holds lock: {lock_path}") from exc
                time.sleep(0.05)
        os.write(fd, f"pid={os.getpid()} ts={time.time()}\n".encode("utf-8"))
        yield
    finally:
        if fd is not None:
            try:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_UN)
            except Exception:  # noqa: BLE001
                pass
            os.close(fd)
            try:
                lock_path.unlink(missing_ok=True)
            except Exception:  # noqa: BLE001
                pass


def make_staging_dir(datasets_root: Path) -> Path:
    staging_root = ensure_dir(datasets_root / ".build_staging")
    return Path(tempfile.mkdtemp(prefix="sft_", dir=str(staging_root)))


def write_split_bundle(
    staging: Path,
    *,
    splits: dict[str, list[Any]],
    rejected: list[Any],
    source_bundles: dict[str, list[Any]],
) -> None:
    for name, items in splits.items():
        out = ensure_dir(staging / "sft" / name)
        payload = []
        for r in items:
            if hasattr(r, "messages"):
                payload.append({"messages": [m.model_dump() for m in r.messages]})
            else:
                payload.append({"messages": r.get("messages")})
        write_json(out / "sharegpt.json", payload)
        write_jsonl(out / "records.jsonl", items)
    write_jsonl(ensure_dir(staging / "rejected") / "sft.jsonl", rejected)
    src = ensure_dir(staging / "sft" / "source")
    for key, rows in source_bundles.items():
        write_jsonl(src / f"{key}.jsonl", rows)


def publish_staging_to_formal(
    *,
    staging: Path,
    datasets_root: Path,
    reports: dict[str, Any],
    manifest: Any,
    llamafactory_data_dir: Path,
) -> None:
    """Replace formal SFT artifacts only after staging is complete.

    On failure before rename, formal tree is left untouched.
    """
    # Write reports into staging first
    rep_dir = ensure_dir(staging / "reports")
    for name, payload in reports.items():
        write_json(rep_dir / name, payload)
    write_json(ensure_dir(staging / "manifests") / "sft_split_manifest.json", manifest)

    # Prepare LF copies in staging
    lf_stage = ensure_dir(staging / "llamafactory_data")
    info_path = llamafactory_data_dir / "dataset_info.json"
    info: dict[str, Any] = {}
    if info_path.exists():
        info = json.loads(info_path.read_text(encoding="utf-8"))
    sharegpt_tags = {
        "role_tag": "role",
        "content_tag": "content",
        "user_tag": "user",
        "assistant_tag": "assistant",
        "system_tag": "system",
        "tool_tag": "tool",
    }
    for split in ("train", "validation", "test"):
        src = staging / "sft" / split / "sharegpt.json"
        dest = lf_stage / f"bidpilot_sft_{split}.json"
        if src.exists():
            dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        info[f"bidpilot_sft_{split}"] = {
            "file_name": f"bidpilot_sft_{split}.json",
            "formatting": "sharegpt",
            "columns": {"messages": "messages"},
            "tags": sharegpt_tags,
        }
    info["bidpilot_sft_train_qwen3"] = info["bidpilot_sft_train"]
    write_json(lf_stage / "dataset_info.json", info)

    # Atomic-ish publish: move each subtree
    replacements = [
        (staging / "sft" / "train", datasets_root / "sft" / "train"),
        (staging / "sft" / "validation", datasets_root / "sft" / "validation"),
        (staging / "sft" / "test", datasets_root / "sft" / "test"),
        (staging / "sft" / "source", datasets_root / "sft" / "source"),
        (staging / "rejected" / "sft.jsonl", datasets_root / "rejected" / "sft.jsonl"),
        (staging / "manifests" / "sft_split_manifest.json", datasets_root / "manifests" / "sft_split_manifest.json"),
    ]
    for src, dest in replacements:
        _replace_path(src, dest)

    for name in reports:
        _replace_path(staging / "reports" / name, datasets_root / "reports" / name)

    ensure_dir(llamafactory_data_dir)
    for split in ("train", "validation", "test"):
        _replace_path(
            lf_stage / f"bidpilot_sft_{split}.json",
            llamafactory_data_dir / f"bidpilot_sft_{split}.json",
        )
    _replace_path(lf_stage / "dataset_info.json", llamafactory_data_dir / "dataset_info.json")


def _replace_path(src: Path, dest: Path) -> None:
    ensure_dir(dest.parent if dest.suffix else dest.parent)
    if dest.exists():
        if dest.is_dir():
            backup = dest.with_name(dest.name + ".bak_publish")
            if backup.exists():
                shutil.rmtree(backup)
            dest.rename(backup)
            try:
                src.rename(dest)
                shutil.rmtree(backup, ignore_errors=True)
            except Exception:
                # rollback
                if dest.exists():
                    shutil.rmtree(dest, ignore_errors=True)
                backup.rename(dest)
                raise
        else:
            tmp = dest.with_suffix(dest.suffix + ".tmp_publish")
            if tmp.exists():
                tmp.unlink()
            src.rename(tmp)
            tmp.replace(dest)
    else:
        if src.is_dir():
            src.rename(dest)
        else:
            ensure_dir(dest.parent)
            src.rename(dest)


def cleanup_staging(staging: Path) -> None:
    shutil.rmtree(staging, ignore_errors=True)
