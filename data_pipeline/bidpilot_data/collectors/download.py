from __future__ import annotations

import mimetypes
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from bidpilot_data.logging import get_logger, log_stats
from bidpilot_data.schemas import DocumentRecord, DocumentType, ParseStatus, SourceRecord, SourceStatus
from bidpilot_data.settings import get_settings, load_pipeline_config
from bidpilot_data.utils import (
    CheckpointStore,
    append_jsonl,
    content_fingerprint,
    ensure_dir,
    read_jsonl,
    sha256_bytes,
    sha256_file,
    stable_uuid,
    write_jsonl,
)

log = get_logger(__name__)


def _guess_ext(url: str, content_type: str | None) -> str:
    path = Path(urlparse(url).path)
    if path.suffix:
        return path.suffix.lower()
    if content_type:
        ext = mimetypes.guess_extension(content_type.split(";")[0].strip())
        if ext:
            return ext
    return ".bin"


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8), reraise=True)
def _http_get(url: str, *, timeout: float, user_agent: str) -> httpx.Response:
    with httpx.Client(timeout=timeout, follow_redirects=True, headers={"User-Agent": user_agent}) as client:
        resp = client.get(url)
        if resp.status_code in {401, 403, 429, 451}:
            # Do not bypass anti-bot / captcha / access restrictions.
            raise PermissionError(f"access restricted status={resp.status_code} url={url}")
        resp.raise_for_status()
        return resp


def _load_file_url(url: str) -> bytes:
    path = Path(url.replace("file://", ""))
    return path.read_bytes()


def collect_from_manifest(
    manifest_path: Path,
    *,
    dry_run: bool = False,
    resume: bool = True,
) -> dict[str, Any]:
    settings = get_settings()
    cfg = load_pipeline_config()
    dl_cfg = cfg.get("download", {})
    datasets = settings.datasets_root
    raw_dir = ensure_dir(datasets / "raw" / "documents")
    sources_path = datasets / "manifests" / "sources.jsonl"
    docs_path = datasets / "manifests" / "documents.jsonl"
    pending_path = datasets / "reports" / "download_pending.jsonl"
    ckpt = CheckpointStore(datasets / "reports" / "checkpoints" / "collect.json")

    rows = read_jsonl(manifest_path)
    stats = {"seen": 0, "downloaded": 0, "skipped": 0, "failed": 0, "duplicates": 0, "dry_run": dry_run}
    existing_docs = {d.get("sha256"): d for d in read_jsonl(docs_path) if d.get("sha256")}
    source_out: list[dict[str, Any]] = []
    doc_out: list[dict[str, Any]] = list(existing_docs.values())

    rate = float(dl_cfg.get("rate_limit_per_second", 1.0) or 1.0)
    pause = 1.0 / rate if rate > 0 else 0.0

    for raw in rows:
        stats["seen"] += 1
        source = SourceRecord.model_validate(
            {
                **raw,
                "collected_at": raw.get("collected_at") or datetime.now(timezone.utc).isoformat(),
            }
        )
        key = source.source_id
        if resume and ckpt.done(key):
            stats["skipped"] += 1
            source_out.append(source.model_dump(mode="json"))
            continue

        if dry_run:
            source.status = SourceStatus.pending
            source_out.append(source.model_dump(mode="json"))
            continue

        try:
            if source.source_url.startswith("file://"):
                content = _load_file_url(source.source_url)
                content_type = mimetypes.guess_type(source.source_url)[0]
            else:
                resp = _http_get(
                    source.source_url,
                    timeout=float(dl_cfg.get("timeout_seconds", 30)),
                    user_agent=str(dl_cfg.get("user_agent")),
                )
                content = resp.content
                content_type = resp.headers.get("content-type")
            digest = sha256_bytes(content)
            if digest in existing_docs:
                stats["duplicates"] += 1
                source.status = SourceStatus.duplicate
                source.sha256 = digest
                ckpt.mark_done(key, {"duplicate": True, "sha256": digest})
                source_out.append(source.model_dump(mode="json"))
                if pause:
                    time.sleep(pause)
                continue

            ext = _guess_ext(source.source_url, content_type)
            project_id = str(stable_uuid(f"project:{source.project_code}"))
            document_id = str(stable_uuid(f"document:{digest}"))
            rel = f"{source.project_code}/{document_id}{ext}"
            dest = raw_dir / rel
            ensure_dir(dest.parent)
            dest.write_bytes(content)

            doc = DocumentRecord(
                document_id=document_id,
                project_id=project_id,
                source_id=source.source_id,
                original_filename=Path(urlparse(source.source_url).path).name or dest.name,
                mime_type=content_type,
                sha256=digest,
                file_size=len(content),
                storage_path=str(dest.relative_to(datasets)),
                parse_status=ParseStatus.pending,
                document_type=source.document_type,
                source_url=source.source_url,
            )
            existing_docs[digest] = doc.model_dump(mode="json")
            doc_out = list(existing_docs.values())
            source.status = SourceStatus.downloaded
            source.sha256 = digest
            source.local_path = doc.storage_path
            stats["downloaded"] += 1
            ckpt.mark_done(key, {"sha256": digest, "document_id": document_id})
        except Exception as exc:  # noqa: BLE001
            stats["failed"] += 1
            source.status = SourceStatus.failed
            source.error_message = str(exc)
            append_jsonl(
                pending_path,
                {
                    "source_id": source.source_id,
                    "source_url": source.source_url,
                    "project_code": source.project_code,
                    "error": str(exc),
                    "failed_at": datetime.now(timezone.utc).isoformat(),
                },
            )
            ckpt.failed(key, str(exc))
            log.warning("download failed source_id=%s error=%s", source.source_id, exc)
        source_out.append(source.model_dump(mode="json"))
        if pause:
            time.sleep(pause)

    write_jsonl(sources_path, source_out)
    write_jsonl(docs_path, doc_out)
    log_stats(log, "collect", stats)
    return stats


def download_pending(*, dry_run: bool = False, resume: bool = True) -> dict[str, Any]:
    settings = get_settings()
    pending = settings.datasets_root / "reports" / "download_pending.jsonl"
    if not pending.exists():
        return {"seen": 0, "note": "no pending downloads"}
    # Rebuild a temporary manifest from pending (keep source_url).
    rows = read_jsonl(pending)
    tmp = ensure_dir(settings.datasets_root / "manifests") / "_pending_retry.jsonl"
    # Pending rows may be incomplete; only retry those with required fields.
    retry_rows = []
    for row in rows:
        if row.get("source_url") and row.get("source_id"):
            retry_rows.append(
                {
                    "source_id": row["source_id"],
                    "source_url": row["source_url"],
                    "source_site": row.get("source_site", "unknown"),
                    "project_code": row.get("project_code", "UNKNOWN"),
                    "project_name": row.get("project_name", row.get("project_code", "UNKNOWN")),
                    "document_type": row.get("document_type", "other"),
                    "status": "pending",
                }
            )
    write_jsonl(tmp, retry_rows)
    return collect_from_manifest(tmp, dry_run=dry_run, resume=resume)


def deduplicate_raw() -> dict[str, Any]:
    settings = get_settings()
    docs_path = settings.datasets_root / "manifests" / "documents.jsonl"
    docs = read_jsonl(docs_path)
    by_hash: dict[str, dict[str, Any]] = {}
    near: list[dict[str, Any]] = []
    text_fps: dict[str, str] = {}

    for doc in docs:
        digest = doc.get("sha256")
        if not digest:
            continue
        if digest in by_hash:
            near.append({"type": "exact", "keep": by_hash[digest]["document_id"], "drop": doc["document_id"]})
            continue
        by_hash[digest] = doc
        storage = settings.datasets_root / doc["storage_path"]
        if storage.exists() and storage.suffix.lower() in {".txt", ".html", ".htm", ".md"}:
            fp = content_fingerprint(storage.read_text(encoding="utf-8", errors="ignore"))
            if fp in text_fps:
                near.append(
                    {
                        "type": "near_text",
                        "keep": text_fps[fp],
                        "drop": doc["document_id"],
                        "fingerprint": fp,
                    }
                )
            else:
                text_fps[fp] = doc["document_id"]

    write_jsonl(docs_path, list(by_hash.values()))
    report = {
        "unique_documents": len(by_hash),
        "duplicate_pairs": len(near),
        "duplicates": near[:200],
    }
    from bidpilot_data.utils.io import write_json

    write_json(settings.datasets_root / "reports" / "deduplicate_report.json", report)
    log_stats(log, "deduplicate", {"unique": len(by_hash), "duplicate_pairs": len(near)})
    return report
