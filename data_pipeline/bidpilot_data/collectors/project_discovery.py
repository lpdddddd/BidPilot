from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from bidpilot_data.collectors.attachment_downloader import AttachmentDownloader, write_manifests
from bidpilot_data.collectors.deduplicator import dedupe_discovery_rows
from bidpilot_data.collectors.download_checkpoint import DownloadCheckpoint
from bidpilot_data.collectors.metadata_extractor import extract_notice_metadata
from bidpilot_data.collectors.project_bundle_builder import (
    build_project_bundles,
    filename_suggests_tender_document,
    make_project_id,
)
from bidpilot_data.collectors.search_client import SearchClient
from bidpilot_data.collectors.source_registry import load_source_registry
from bidpilot_data.logging import get_logger, log_stats
from bidpilot_data.schemas import DocumentType
from bidpilot_data.settings import get_settings
from bidpilot_data.utils import ensure_dir, read_jsonl, write_json, write_jsonl

log = get_logger(__name__)


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    return date.fromisoformat(value[:10])


def discover_and_collect(
    *,
    province: str = "广东",
    keywords: list[str] | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    target_projects: int = 10,
    max_list_pages: int = 25,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Discover real public projects and download notice HTML + official attachments."""
    settings = get_settings()
    registry = load_source_registry()
    keywords = keywords or list(registry.discovery.get("default_keywords") or [])
    ckpt = DownloadCheckpoint(settings.datasets_root / "reports" / "checkpoints")
    search = SearchClient(registry=registry, checkpoint=ckpt)
    downloader = AttachmentDownloader(checkpoint=ckpt)

    # Probe restricted portals honestly (no bypass).
    restricted = search.probe_restricted_portals(province=province)

    hits = search.search_ccgp(
        province=province,
        keywords=keywords,
        start_date=_parse_date(start_date),
        end_date=_parse_date(end_date),
        max_pages=max_list_pages,
        require_keyword_in_title=False,
    )
    # Prefer IT-ish titles first, keep others as fallback for body filtering.
    hits = sorted(hits, key=lambda h: sum(1 for kw in keywords if kw in h.title), reverse=True)

    existing_docs = read_jsonl(settings.datasets_root / "manifests" / "documents.jsonl")
    existing_sha = {d.get("sha256") for d in existing_docs if d.get("sha256")}
    sources_out = [s for s in read_jsonl(settings.datasets_root / "manifests" / "sources.jsonl")]
    docs_out = list(existing_docs)

    notice_items: list[dict[str, Any]] = []
    stats: dict[str, Any] = {
        "list_hits": len(hits),
        "notices_fetched": 0,
        "notices_kept": 0,
        "attachments_downloaded": 0,
        "attachments_failed": 0,
        "projects_built": 0,
        "restricted_probes": restricted,
        "dry_run": dry_run,
    }

    for hit in hits:
        if stats["projects_built"] >= target_projects and stats["notices_kept"] >= target_projects:
            # Continue a bit to pair awards for existing codes when possible.
            if stats["notices_kept"] >= target_projects * 3:
                break
        try:
            status, html = search.fetch_html(hit.url, referer=hit.list_page)
        except Exception as exc:  # noqa: BLE001
            ckpt.record_discovery_failure(url=hit.url, reason=str(exc), province=province, keywords=keywords)
            continue
        if status != 200:
            ckpt.record_discovery_failure(url=hit.url, reason=f"http_{status}", province=province)
            continue
        stats["notices_fetched"] += 1
        meta = extract_notice_metadata(html, source_url=hit.url, title_hint=hit.title)
        # Keep if title or body matches keywords OR already looks Guangdong IT.
        blob = f"{meta.get('title') or ''}\n{meta.get('text_excerpt') or ''}"
        score = sum(1 for kw in keywords if kw in blob)
        if score <= 0 and meta.get("it_score", 0) <= 0:
            continue
        if province in {"广东", "广东省"} and meta.get("province") not in {"广东", None}:
            # If body clearly another province and title lacked markers, skip.
            if meta.get("province") and meta.get("province") != "广东":
                continue
        if province in {"广东", "广东省"} and meta.get("province") is None:
            # Require Guangdong marker somewhere.
            if not any(m in blob for m in ("广东", "广州", "深圳", "珠海", "佛山", "东莞", "中山", "惠州", "肇庆", "汕头")):
                continue

        project_code = meta.get("project_code") or "UNKNOWN"
        project_name = meta.get("project_name") or hit.title
        purchaser = meta.get("purchaser")
        project_id = make_project_id(project_code, purchaser, project_name)
        dtype = DocumentType(meta["document_type"]) if meta.get("document_type") in DocumentType._value2member_map_ else DocumentType.other_notice

        if dry_run:
            notice_items.append({**meta, "project_id": project_id, "document_type": dtype.value})
            stats["notices_kept"] += 1
            continue

        # Persist notice HTML
        html_result = downloader.persist_document(
            content=html.encode("utf-8", errors="ignore"),
            source_url=hit.url,
            project_code=project_code,
            project_name=project_name,
            document_type=dtype,
            original_filename=f"{project_code}_{dtype.value}.html",
            project_id=project_id,
            published_at=meta.get("published_at"),
            content_type="text/html; charset=utf-8",
            issuing_authority=purchaser,
            existing_sha=existing_sha,
        )
        if not html_result.get("duplicate"):
            sources_out.append(html_result["source"])
            docs_out.append(html_result["document"])
            existing_sha.add(html_result["meta"]["sha256"])
            notice_items.append(
                {
                    **meta,
                    **html_result["meta"],
                    "official_project_url": hit.url,
                    "document_type": dtype.value,
                }
            )
        else:
            notice_items.append({**meta, "project_id": project_id, "official_project_url": hit.url, "document_type": dtype.value})
        stats["notices_kept"] += 1

        # Download official attachments (public UUID endpoint only; no signature bypass).
        for att in meta.get("attachments") or []:
            att_name = att.get("original_filename") or "attachment.bin"
            att_type = (
                DocumentType.tender_document
                if filename_suggests_tender_document(att_name)
                else DocumentType.other
            )
            dl = downloader.download_one(
                source_url=att["source_url"],
                project_code=project_code,
                project_name=project_name,
                document_type=att_type,
                original_filename=att_name,
                project_id=project_id,
                published_at=meta.get("published_at"),
                referer=hit.url,
                issuing_authority=purchaser,
                existing_sha=existing_sha,
            )
            if dl.get("failed"):
                stats["attachments_failed"] += 1
                continue
            if dl.get("duplicate") or dl.get("skipped"):
                continue
            sources_out.append(dl["source"])
            docs_out.append(dl["document"])
            existing_sha.add(dl["meta"]["sha256"])
            notice_items.append(
                {
                    **meta,
                    **dl["meta"],
                    "official_project_url": hit.url,
                    "document_type": att_type.value,
                    "original_filename": att_name,
                }
            )
            stats["attachments_downloaded"] += 1

        # Rebuild project count from current bundles
        bundles = build_project_bundles(notice_items)
        stats["projects_built"] = len(bundles)
        if stats["projects_built"] >= target_projects:
            # Prefer stopping once we have enough projects with at least one kept notice.
            # Still allow a few more if awards are scarce.
            award_n = sum(1 for b in bundles if any(d.document_type.value in {"award_notice", "result"} for d in b.documents))
            tender_n = sum(
                1
                for b in bundles
                if any(d.document_type.value in {"tender_document", "tender"} for d in b.documents)
            )
            if tender_n >= min(target_projects, 5) or stats["notices_kept"] >= target_projects * 2:
                if stats["projects_built"] >= target_projects:
                    break

    notice_items = dedupe_discovery_rows(notice_items)
    bundles = build_project_bundles(notice_items)
    # Prefer bundles that have tender documents; mark incomplete for formal exclusion later.
    bundles = sorted(
        bundles,
        key=lambda b: (
            {"level_a": 0, "level_b": 1, "level_c": 2, "incomplete": 3}[b.bundle_level.value],
            b.project_name,
        ),
    )
    # Keep up to target_projects for formal mini-batch, but persist all discovered bundles.
    selected = bundles[: max(target_projects, len(bundles))]

    projects_path = ensure_dir(settings.datasets_root / "manifests") / "projects.jsonl"
    if not dry_run:
        write_manifests(sources_out, docs_out)
        write_jsonl(projects_path, [b.model_dump(mode="json") for b in selected])
        write_json(
            settings.datasets_root / "reports" / "discovery_batch_report.json",
            {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "province": province,
                "keywords": keywords,
                "stats": stats,
                "bundle_levels": {
                    level: sum(1 for b in selected if b.bundle_level.value == level)
                    for level in ("level_a", "level_b", "level_c", "incomplete")
                },
                "projects": [
                    {
                        "project_id": b.project_id,
                        "project_code": b.project_code,
                        "project_name": b.project_name,
                        "bundle_level": b.bundle_level.value,
                        "official_project_url": b.official_project_url,
                        "documents": len(b.documents),
                    }
                    for b in selected
                ],
            },
        )

    stats["projects_built"] = len(selected)
    stats["bundle_levels"] = {
        level: sum(1 for b in selected if b.bundle_level.value == level)
        for level in ("level_a", "level_b", "level_c", "incomplete")
    }
    stats["projects_path"] = str(projects_path)
    log_stats(log, "discover_and_collect", {k: v for k, v in stats.items() if k != "restricted_probes"})
    return stats


def rebuild_projects_from_documents() -> dict[str, Any]:
    """Rebuild projects.jsonl using HTML metadata + CCGP attachment UUID links."""
    import re

    settings = get_settings()
    docs = read_jsonl(settings.datasets_root / "manifests" / "documents.jsonl")
    by_uuid: dict[str, dict[str, Any]] = {}
    for d in docs:
        m = re.search(r"uuid=([A-Fa-f0-9]+)", str(d.get("source_url") or ""))
        if m:
            by_uuid[m.group(1).upper()] = d

    items: list[dict[str, Any]] = []
    for d in docs:
        path = settings.datasets_root / d["storage_path"]
        url = d.get("source_url") or ""
        if path.suffix.lower() in {".html", ".htm"} and path.exists():
            meta = extract_notice_metadata(path.read_text(encoding="utf-8", errors="ignore"), source_url=url)
            dtype = d.get("document_type") or meta.get("document_type")
            items.append(
                {
                    **meta,
                    "source_url": url,
                    "sha256": d.get("sha256"),
                    "local_path": d.get("storage_path"),
                    "original_filename": d.get("original_filename"),
                    "document_id": d.get("document_id"),
                    "document_type": dtype,
                    "official_project_url": url,
                }
            )
            for att in meta.get("attachments") or []:
                uid = str(att.get("attachment_id") or "").upper()
                ad = by_uuid.get(uid)
                if not ad:
                    continue
                aname = att.get("original_filename") or ad.get("original_filename")
                atype = (
                    DocumentType.tender_document.value
                    if filename_suggests_tender_document(aname or "")
                    else (ad.get("document_type") or DocumentType.other.value)
                )
                items.append(
                    {
                        **{
                            k: meta.get(k)
                            for k in (
                                "project_code",
                                "project_name",
                                "purchaser",
                                "procurement_agency",
                                "budget_cny",
                                "published_at",
                                "province",
                                "industry",
                            )
                        },
                        "source_url": ad.get("source_url"),
                        "sha256": ad.get("sha256"),
                        "local_path": ad.get("storage_path"),
                        "original_filename": aname,
                        "document_id": ad.get("document_id"),
                        "document_type": atype,
                        "official_project_url": url,
                    }
                )
        elif "download.ccgp.gov.cn" in url:
            continue
        else:
            items.append(
                {
                    "project_code": None,
                    "project_name": d.get("original_filename"),
                    "source_url": url,
                    "sha256": d.get("sha256"),
                    "local_path": d.get("storage_path"),
                    "original_filename": d.get("original_filename"),
                    "document_id": d.get("document_id"),
                    "document_type": d.get("document_type") or DocumentType.other.value,
                    "official_project_url": url,
                    "province": "广东",
                }
            )

    bundles = build_project_bundles(items)
    write_jsonl(
        ensure_dir(settings.datasets_root / "manifests") / "projects.jsonl",
        [b.model_dump(mode="json") for b in bundles],
    )
    # Sync document project_ids
    url_to_pid: dict[str, str] = {}
    for b in bundles:
        for d in b.documents:
            if d.source_url:
                url_to_pid[d.source_url] = b.project_id
            if d.document_id:
                url_to_pid[d.document_id] = b.project_id
    for d in docs:
        pid = url_to_pid.get(d.get("source_url") or "") or url_to_pid.get(d.get("document_id") or "")
        if pid:
            d["project_id"] = pid
    write_jsonl(settings.datasets_root / "manifests" / "documents.jsonl", docs)
    stats = {
        "projects": len(bundles),
        "bundle_levels": {
            level: sum(1 for b in bundles if b.bundle_level.value == level)
            for level in ("level_a", "level_b", "level_c", "incomplete")
        },
    }
    log_stats(log, "rebuild_projects_from_documents", stats)
    return stats


def collect_from_seed_manifest(manifest_path: str, *, dry_run: bool = False) -> dict[str, Any]:
    """Collect from human-provided official seed URLs (one JSON object per line)."""
    settings = get_settings()
    rows = read_jsonl(Path(manifest_path))
    ckpt = DownloadCheckpoint(settings.datasets_root / "reports" / "checkpoints")
    search = SearchClient(checkpoint=ckpt)
    downloader = AttachmentDownloader(checkpoint=ckpt)
    existing_docs = read_jsonl(settings.datasets_root / "manifests" / "documents.jsonl")
    existing_sha = {d.get("sha256") for d in existing_docs if d.get("sha256")}
    sources_out = list(read_jsonl(settings.datasets_root / "manifests" / "sources.jsonl"))
    docs_out = list(existing_docs)
    items: list[dict[str, Any]] = []
    stats = {"seeds": len(rows), "downloaded": 0, "failed": 0, "dry_run": dry_run}

    for row in rows:
        url = row.get("source_url") or row.get("url")
        if not url:
            stats["failed"] += 1
            continue
        if dry_run:
            continue
        try:
            status, html = search.fetch_html(url)
        except Exception as exc:  # noqa: BLE001
            ckpt.record_discovery_failure(url=url, reason=str(exc))
            stats["failed"] += 1
            continue
        if status != 200:
            ckpt.record_discovery_failure(url=url, reason=f"http_{status}")
            stats["failed"] += 1
            continue
        meta = extract_notice_metadata(html, source_url=url, title_hint=row.get("project_name"))
        project_code = meta.get("project_code") or row.get("project_code") or "UNKNOWN"
        project_name = meta.get("project_name") or row.get("project_name") or url
        purchaser = meta.get("purchaser")
        project_id = make_project_id(project_code, purchaser, project_name)
        dtype = DocumentType(meta["document_type"]) if meta.get("document_type") in DocumentType._value2member_map_ else DocumentType.other_notice
        result = downloader.persist_document(
            content=html.encode("utf-8", errors="ignore"),
            source_url=url,
            project_code=project_code,
            project_name=project_name,
            document_type=dtype,
            original_filename=row.get("original_filename"),
            project_id=project_id,
            published_at=meta.get("published_at"),
            content_type="text/html; charset=utf-8",
            issuing_authority=purchaser,
            existing_sha=existing_sha,
        )
        if not result.get("duplicate"):
            sources_out.append(result["source"])
            docs_out.append(result["document"])
            existing_sha.add(result["meta"]["sha256"])
            stats["downloaded"] += 1
            items.append({**meta, **result["meta"], "official_project_url": url})
        for att in meta.get("attachments") or []:
            att_name = att.get("original_filename") or "attachment.bin"
            att_type = DocumentType.tender_document if filename_suggests_tender_document(att_name) else DocumentType.other
            dl = downloader.download_one(
                source_url=att["source_url"],
                project_code=project_code,
                project_name=project_name,
                document_type=att_type,
                original_filename=att_name,
                project_id=project_id,
                published_at=meta.get("published_at"),
                referer=url,
                issuing_authority=purchaser,
                existing_sha=existing_sha,
            )
            if dl.get("failed"):
                stats["failed"] += 1
                continue
            if dl.get("duplicate") or dl.get("skipped"):
                continue
            sources_out.append(dl["source"])
            docs_out.append(dl["document"])
            existing_sha.add(dl["meta"]["sha256"])
            items.append({**meta, **dl["meta"], "official_project_url": url, "document_type": att_type.value})
            stats["downloaded"] += 1

    if not dry_run:
        write_manifests(sources_out, docs_out)
        rebuilt = rebuild_projects_from_documents()
        stats["projects_built"] = rebuilt.get("projects", 0)
        stats["bundle_levels"] = rebuilt.get("bundle_levels", {})
    else:
        bundles = build_project_bundles(items)
        stats["projects_built"] = len(bundles)
    log_stats(log, "collect_from_seed_manifest", stats)
    return stats
