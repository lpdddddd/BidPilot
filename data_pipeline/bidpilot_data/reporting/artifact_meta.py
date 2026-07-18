"""Build metadata shared by all SFT artifact reports in one build."""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

GENERATOR_VERSION = "bidpilot-sft-build-v2"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def try_commit_sha(repo_root: Path | None = None) -> str | None:
    try:
        cwd = str(repo_root) if repo_root else None
        out = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=cwd,
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        return out or None
    except Exception:  # noqa: BLE001
        return None


def sha256_text(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()  # keep short; reports also store sha256_


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_jsonl_file(path: Path) -> str:
    h = hashlib.sha256()
    if not path.exists():
        return sha256_bytes(b"")
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_json_obj(obj: Any) -> str:
    payload = json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str)
    return sha256_bytes(payload.encode("utf-8"))


def make_dataset_build_id(*, seed: int, source_records_sha256: str, commit_sha: str | None) -> str:
    raw = f"{GENERATOR_VERSION}|{seed}|{source_records_sha256}|{commit_sha or 'unknown'}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def attach_artifact_meta(
    report: dict[str, Any],
    *,
    dataset_build_id: str,
    split_manifest_sha256: str,
    source_records_sha256: str,
    commit_sha: str | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    out = dict(report)
    out["generated_at"] = generated_at or utc_now_iso()
    out["dataset_build_id"] = dataset_build_id
    out["split_manifest_sha256"] = split_manifest_sha256
    out["source_records_sha256"] = source_records_sha256
    out["generator_version"] = GENERATOR_VERSION
    if commit_sha:
        out["commit_sha"] = commit_sha
    return out
