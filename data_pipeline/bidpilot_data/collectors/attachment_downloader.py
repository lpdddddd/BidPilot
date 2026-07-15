from __future__ import annotations

import mimetypes
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from bidpilot_data.collectors.download_checkpoint import DownloadCheckpoint
from bidpilot_data.collectors.official_source_validator import validate_official_source
from bidpilot_data.collectors.source_registry import load_source_registry
from bidpilot_data.logging import get_logger
from bidpilot_data.schemas import DocumentRecord, DocumentType, ParseStatus, SourceRecord, SourceStatus
from bidpilot_data.settings import get_settings
from bidpilot_data.utils import ensure_dir, read_jsonl, sha256_bytes, stable_uuid, write_jsonl

log = get_logger(__name__)


def _guess_ext(url: str, content_type: str | None, filename: str | None = None) -> str:
    if filename and Path(filename).suffix:
        return Path(filename).suffix.lower()
    path = Path(urlparse(url).path)
    if path.suffix and path.suffix.lower() not in {".htm", ".html"}:
        return path.suffix.lower()
    if content_type:
        # CCGP sometimes returns PDF with text/html content-type.
        if "pdf" in content_type.lower():
            return ".pdf"
        ext = mimetypes.guess_extension(content_type.split(";")[0].strip())
        if ext:
            return ext
    if url.rstrip("/").endswith(".htm") or url.rstrip("/").endswith(".html"):
        return ".html"
    return ".bin"


class AttachmentDownloader:
    def __init__(self, checkpoint: DownloadCheckpoint | None = None) -> None:
        self.registry = load_source_registry()
        self.checkpoint = checkpoint
        self.pause = 1.0 / max(self.registry.rate_limit_per_second, 0.1)

    def download_bytes(self, url: str, *, referer: str | None = None) -> tuple[bytes, str | None]:
        check = validate_official_source(url, self.registry)
        if not check.ok:
            raise PermissionError(f"non-official URL blocked: {url}")
        headers = {
            "User-Agent": self.registry.user_agent,
            "Accept": "*/*",
        }
        if referer:
            headers["Referer"] = referer
        with httpx.Client(timeout=self.registry.timeout_seconds, follow_redirects=True, headers=headers) as client:
            resp = client.get(url)
            if resp.status_code in {401, 403, 429, 451}:
                raise PermissionError(f"access restricted status={resp.status_code} url={url}")
            resp.raise_for_status()
            content = resp.content
            # Detect captcha / login HTML landing pages for downloads that should be binary.
            ctype = resp.headers.get("content-type")
            head = content[:200].lstrip().lower()
            if head.startswith(b"<!doctype") or head.startswith(b"<html"):
                text = content[:2000].decode("utf-8", "ignore")
                if any(k in text for k in ("验证码", "频繁访问", "请登录", "captcha")):
                    raise PermissionError(f"download redirected to access wall url={url}")
            return content, ctype

    def persist_document(
        self,
        *,
        content: bytes,
        source_url: str,
        project_code: str,
        project_name: str,
        document_type: DocumentType | str,
        original_filename: str | None = None,
        project_id: str | None = None,
        published_at: str | None = None,
        content_type: str | None = None,
        issuing_authority: str | None = None,
        existing_sha: set[str] | None = None,
    ) -> dict[str, Any]:
        settings = get_settings()
        datasets = settings.datasets_root
        raw_dir = ensure_dir(datasets / "raw" / "documents")
        digest = sha256_bytes(content)
        if existing_sha is not None and digest in existing_sha:
            return {"duplicate": True, "sha256": digest}

        dtype = DocumentType(document_type) if not isinstance(document_type, DocumentType) else document_type
        project_id = project_id or str(stable_uuid(f"project:{project_code}|{issuing_authority or ''}|{project_name}"))
        document_id = str(stable_uuid(f"document:{digest}"))
        source_id = str(stable_uuid(f"source:{digest}:{source_url}"))
        domain = urlparse(source_url).netloc.lower().split(":")[0]
        ext = _guess_ext(source_url, content_type, original_filename)
        # Magic-number override for PDF served with wrong content-type
        if content[:4] == b"%PDF":
            ext = ".pdf"
            content_type = content_type or "application/pdf"
        fname = original_filename or Path(urlparse(source_url).path).name or f"{document_id}{ext}"
        if not Path(fname).suffix:
            fname = f"{fname}{ext}"
        rel = f"{project_code}/{document_id}{ext}"
        dest = raw_dir / rel
        ensure_dir(dest.parent)
        dest.write_bytes(content)

        source = SourceRecord(
            source_id=source_id,
            source_url=source_url,
            source_site=domain,
            project_code=project_code,
            project_name=project_name,
            document_type=dtype if dtype in DocumentType else DocumentType.other,
            published_at=published_at,
            province=None,
            industry=None,
            license_or_terms="official public notice; follow robots and site terms",
            collected_at=datetime.now(timezone.utc),
            status=SourceStatus.downloaded,
            local_path=str(dest.relative_to(datasets)),
            sha256=digest,
        )
        # SourceRecord.document_type enum may reject new values depending on pydantic - we set carefully
        try:
            source.document_type = dtype
        except Exception:
            source.document_type = DocumentType.other

        doc = DocumentRecord(
            document_id=document_id,
            project_id=project_id,
            source_id=source_id,
            original_filename=fname,
            mime_type=content_type,
            sha256=digest,
            file_size=len(content),
            storage_path=str(dest.relative_to(datasets)),
            parse_status=ParseStatus.pending,
            document_type=dtype,
            source_url=source_url,
        )
        meta = {
            "source_url": source_url,
            "source_domain": domain,
            "collected_at": datetime.now(timezone.utc).isoformat(),
            "published_at": published_at,
            "sha256": digest,
            "original_filename": fname,
            "project_code": project_code,
            "project_name": project_name,
            "issuing_authority": issuing_authority,
            "document_type": dtype.value,
            "local_path": doc.storage_path,
            "document_id": document_id,
            "project_id": project_id,
            "source_id": source_id,
        }
        return {
            "duplicate": False,
            "source": source.model_dump(mode="json"),
            "document": doc.model_dump(mode="json"),
            "meta": meta,
        }

    def download_one(
        self,
        *,
        source_url: str,
        project_code: str,
        project_name: str,
        document_type: DocumentType | str,
        original_filename: str | None = None,
        project_id: str | None = None,
        published_at: str | None = None,
        referer: str | None = None,
        issuing_authority: str | None = None,
        existing_sha: set[str] | None = None,
    ) -> dict[str, Any]:
        key = f"dl:{source_url}"
        if self.checkpoint and self.checkpoint.done(key):
            return {"skipped": True, "reason": "checkpoint_done", "source_url": source_url}
        try:
            content, ctype = self.download_bytes(source_url, referer=referer)
            result = self.persist_document(
                content=content,
                source_url=source_url,
                project_code=project_code,
                project_name=project_name,
                document_type=document_type,
                original_filename=original_filename,
                project_id=project_id,
                published_at=published_at,
                content_type=ctype,
                issuing_authority=issuing_authority,
                existing_sha=existing_sha,
            )
            if self.checkpoint:
                self.checkpoint.mark_done(key, {"sha256": result.get("sha256") or result.get("meta", {}).get("sha256")})
            time.sleep(self.pause)
            return result
        except Exception as exc:  # noqa: BLE001
            log.warning("download failed url=%s err=%s", source_url, exc)
            if self.checkpoint:
                self.checkpoint.mark_failed(key, str(exc))
                self.checkpoint.record_download_pending(
                    source_url=source_url,
                    reason=str(exc),
                    project_code=project_code,
                    project_name=project_name,
                    document_type=str(document_type),
                    extra={"original_filename": original_filename},
                )
            time.sleep(self.pause)
            return {"failed": True, "error": str(exc), "source_url": source_url}


def write_manifests(sources: list[dict[str, Any]], documents: list[dict[str, Any]]) -> None:
    """Write sources/documents manifests, merging by sha256 so concurrent/checkpoint writes cannot drop rows."""
    settings = get_settings()
    man = ensure_dir(settings.datasets_root / "manifests")

    def _merge(path: Path, rows: list[dict[str, Any]], key_fields: tuple[str, ...]) -> list[dict[str, Any]]:
        existing = read_jsonl(path) if path.exists() else []
        by_key: dict[str, dict[str, Any]] = {}
        order: list[str] = []

        def _key(row: dict[str, Any]) -> str:
            for f in key_fields:
                v = row.get(f)
                if v:
                    return f"{f}:{v}"
            return f"row:{id(row)}"

        for row in existing + rows:
            k = _key(row)
            if k not in by_key:
                order.append(k)
            by_key[k] = row
        return [by_key[k] for k in order]

    sources_m = _merge(man / "sources.jsonl", sources, ("sha256", "source_url", "source_id"))
    docs_m = _merge(man / "documents.jsonl", documents, ("sha256", "document_id", "source_url"))
    write_jsonl(man / "sources.jsonl", sources_m)
    write_jsonl(man / "documents.jsonl", docs_m)
