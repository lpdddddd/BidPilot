from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from bidpilot_data.collectors.attachment_downloader import AttachmentDownloader, write_manifests
from bidpilot_data.collectors.deduplicator import dedupe_discovery_rows
from bidpilot_data.collectors.download_checkpoint import DownloadCheckpoint
from bidpilot_data.collectors.metadata_extractor import extract_notice_metadata
from bidpilot_data.collectors.project_bundle_builder import (
    attachment_type_for_notice,
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


def _bundle_counts(notice_items: list[dict[str, Any]]) -> dict[str, int]:
    bundles = build_project_bundles(notice_items)
    tender_n = sum(
        1
        for b in bundles
        if any(d.document_type.value in {"tender_document", "tender"} for d in b.documents)
    )
    award_n = sum(
        1
        for b in bundles
        if any(d.document_type.value in {"award_notice", "result"} for d in b.documents)
    )
    return {
        "projects_built": len(bundles),
        "tender_file_projects": tender_n,
        "award_projects": award_n,
    }


def discover_and_collect(
    *,
    province: str = "广东",
    keywords: list[str] | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    target_projects: int = 10,
    max_list_pages: int = 25,
    dry_run: bool = False,
    require_keyword_in_title: bool = True,
    categories: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Discover real public projects and download notice HTML + official attachments.

    Streams CCGP list pages and fetches matching notice details immediately so
    expansion can early-stop without waiting for a full list crawl.
    """
    settings = get_settings()
    registry = load_source_registry()
    keywords = keywords or list(registry.discovery.get("default_keywords") or [])
    # Broaden title IT cues so GD completed projects are not missed.
    title_keywords = list(
        dict.fromkeys(
            keywords
            + [
                "信息化",
                "软件",
                "运维",
                "数据",
                "网络",
                "信息系统",
                "机房",
                "数据中心",
                "数字化",
                "平台",
                "系统集成",
                "系统",
                "云",
                "智",
                "信息",
            ]
        )
    )
    ckpt = DownloadCheckpoint(settings.datasets_root / "reports" / "checkpoints")
    search = SearchClient(registry=registry, checkpoint=ckpt)
    downloader = AttachmentDownloader(checkpoint=ckpt)

    restricted = search.probe_restricted_portals(province=province)

    existing_docs = read_jsonl(settings.datasets_root / "manifests" / "documents.jsonl")
    existing_sha = {d.get("sha256") for d in existing_docs if d.get("sha256")}
    existing_urls = {d.get("source_url") for d in existing_docs if d.get("source_url")}
    sources_out = [s for s in read_jsonl(settings.datasets_root / "manifests" / "sources.jsonl")]
    docs_out = list(existing_docs)

    # Seed notice_items from existing HTML docs so early-stop counts are global.
    notice_items: list[dict[str, Any]] = []
    for d in existing_docs:
        if d.get("project_code") == "PORTAL_SNAPSHOT":
            continue
        notice_items.append(
            {
                "project_code": d.get("project_code"),
                "project_name": d.get("project_name") or d.get("original_filename"),
                "purchaser": d.get("issuing_authority"),
                "source_url": d.get("source_url"),
                "sha256": d.get("sha256"),
                "local_path": d.get("storage_path"),
                "original_filename": d.get("original_filename"),
                "document_id": d.get("document_id"),
                "document_type": d.get("document_type"),
                "published_at": d.get("published_at"),
                "official_project_url": d.get("source_url"),
            }
        )

    stats: dict[str, Any] = {
        "list_hits": 0,
        "notices_fetched": 0,
        "notices_kept": 0,
        "attachments_downloaded": 0,
        "attachments_failed": 0,
        "projects_built": 0,
        "restricted_probes": restricted,
        "dry_run": dry_run,
        "require_keyword_in_title": require_keyword_in_title,
        "categories": list(categories) if categories else None,
    }

    # Prefer completed / file-rich categories first.
    use_categories = categories or ("zbgg", "cjgg", "gkzb", "jzxcs", "qtgg", "fblbgg")
    start = _parse_date(start_date)
    end = _parse_date(end_date)
    baseline_projects = _bundle_counts(notice_items)["projects_built"]

    for hit in search.iter_ccgp_hits(
        province=province,
        keywords=title_keywords,
        start_date=start,
        end_date=end,
        max_pages=max_list_pages,
        require_keyword_in_title=require_keyword_in_title,
        categories=use_categories,
    ):
        stats["list_hits"] += 1
        counts = _bundle_counts(notice_items)
        stats.update(counts)
        new_projects = counts["projects_built"] - baseline_projects
        if (
            counts["projects_built"] >= target_projects
            and counts["tender_file_projects"] >= min(50, max(10, target_projects // 4))
            and counts["award_projects"] >= min(40, max(8, target_projects // 5))
        ):
            log.info("early-stop targets met | %s", counts)
            break
        # Soft stop only when this batch itself kept enough notices OR
        # global quality targets are already met.
        if (
            counts["tender_file_projects"] >= 50
            and counts["award_projects"] >= 25
            and sum(
                1
                for b in build_project_bundles(notice_items)
                if b.bundle_level.value in {"level_a", "level_b"}
            )
            >= 25
        ):
            log.info("quality early-stop | %s", counts)
            break
        if stats["notices_kept"] >= max(40, target_projects // 3) and new_projects >= 15:
            log.info("batch early-stop | new_projects=%s notices_kept=%s %s", new_projects, stats["notices_kept"], counts)
            break
        if new_projects >= max(30, target_projects // 4) and counts["award_projects"] >= 30:
            log.info("award-batch early-stop | new_projects=%s %s", new_projects, counts)
            break

        if hit.url in existing_urls:
            continue
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
        blob = f"{meta.get('title') or ''}\n{meta.get('text_excerpt') or ''}"
        score = sum(1 for kw in keywords if kw in blob) + int(meta.get("it_score") or 0)
        if score <= 0:
            continue
        if province in {"广东", "广东省"} and meta.get("province") not in {"广东", None}:
            if meta.get("province") and meta.get("province") != "广东":
                continue
        if province in {"广东", "广东省"} and meta.get("province") is None:
            if not any(
                m in blob
                for m in ("广东", "广州", "深圳", "珠海", "佛山", "东莞", "中山", "惠州", "肇庆", "汕头")
            ):
                continue

        project_code = meta.get("project_code") or "UNKNOWN"
        project_name = meta.get("project_name") or hit.title
        purchaser = meta.get("purchaser")
        project_id = make_project_id(project_code, purchaser, project_name)
        dtype = (
            DocumentType(meta["document_type"])
            if meta.get("document_type") in DocumentType._value2member_map_
            else DocumentType.other_notice
        )

        if dry_run:
            notice_items.append({**meta, "project_id": project_id, "document_type": dtype.value})
            stats["notices_kept"] += 1
            continue

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
            existing_urls.add(hit.url)
            notice_items.append(
                {
                    **meta,
                    **html_result["meta"],
                    "official_project_url": hit.url,
                    "document_type": dtype.value,
                }
            )
            stats["notices_kept"] += 1
            ckpt.mark_done(f"notice:{hit.url}", {"sha256": html_result["meta"]["sha256"]})
        else:
            notice_items.append(
                {
                    **meta,
                    "project_id": project_id,
                    "official_project_url": hit.url,
                    "document_type": dtype.value,
                }
            )
            stats["notices_kept"] += 1
            if html_result.get("sha256"):
                ckpt.mark_done(f"notice:{hit.url}", {"sha256": html_result["sha256"]})

        for att in meta.get("attachments") or []:
            att_name = att.get("original_filename") or "attachment.bin"
            att_type = attachment_type_for_notice(dtype, att_name)
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

        if stats["notices_kept"] % 10 == 0 and not dry_run:
            write_manifests(sources_out, docs_out)
            log.info("checkpoint manifests | kept=%s attachments=%s", stats["notices_kept"], stats["attachments_downloaded"])

    notice_items = dedupe_discovery_rows(notice_items)
    projects_path = ensure_dir(settings.datasets_root / "manifests") / "projects.jsonl"
    if not dry_run:
        write_manifests(sources_out, docs_out)
        rebuilt = rebuild_projects_from_documents()
        stats["projects_built"] = rebuilt.get("projects", 0)
        stats["bundle_levels"] = rebuilt.get("bundle_levels", {})
        write_json(
            settings.datasets_root / "reports" / "discovery_batch_report.json",
            {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "province": province,
                "keywords": keywords,
                "stats": {k: v for k, v in stats.items() if k != "restricted_probes"},
                "bundle_levels": stats["bundle_levels"],
            },
        )
    else:
        bundles = build_project_bundles(notice_items)
        stats["projects_built"] = len(bundles)
        stats["bundle_levels"] = {
            level: sum(1 for b in bundles if b.bundle_level.value == level)
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
        if d.get("project_code") == "PORTAL_SNAPSHOT" or str(d.get("project_name") or "").startswith(
            "official_portal_snapshot"
        ):
            continue
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
                try:
                    notice_dtype = DocumentType(str(dtype))
                except ValueError:
                    notice_dtype = DocumentType.other_notice
                atype = attachment_type_for_notice(notice_dtype, aname or "").value
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
        elif "download.ccgp.gov.cn" in url or path.suffix.lower() in {".pdf", ".doc", ".docx", ".zip", ".rar"}:
            # Keep attachment rows even when no parent HTML UUID link (folder = project_code).
            code = d.get("project_code")
            if not code or code in {"UNKNOWN", "PORTAL_SNAPSHOT", None}:
                # Infer from storage folder
                parts = Path(d.get("storage_path") or "").parts
                if len(parts) >= 3 and parts[0] == "raw" and parts[1] == "documents":
                    code = parts[2]
            if not code or code in {"UNKNOWN", "PORTAL_SNAPSHOT"}:
                continue
            aname = d.get("original_filename") or path.name
            try:
                notice_hint = DocumentType(str(d.get("document_type") or "other"))
            except ValueError:
                notice_hint = DocumentType.other
            atype = (
                attachment_type_for_notice(DocumentType.tender_notice, aname).value
                if notice_hint.value in {"other", "tender_document", "tender"}
                else notice_hint.value
            )
            if d.get("document_type") == "tender_document":
                atype = "tender_document"
            items.append(
                {
                    "project_code": code,
                    "project_name": d.get("project_name") or aname,
                    "purchaser": d.get("issuing_authority"),
                    "source_url": url,
                    "sha256": d.get("sha256"),
                    "local_path": d.get("storage_path"),
                    "original_filename": aname,
                    "document_id": d.get("document_id"),
                    "document_type": atype,
                    "official_project_url": url,
                    "province": d.get("province") or "广东",
                }
            )
        else:
            items.append(
                {
                    "project_code": d.get("project_code"),
                    "project_name": d.get("project_name") or d.get("original_filename"),
                    "purchaser": d.get("issuing_authority"),
                    "source_url": url,
                    "sha256": d.get("sha256"),
                    "local_path": d.get("storage_path"),
                    "original_filename": d.get("original_filename"),
                    "document_id": d.get("document_id"),
                    "document_type": d.get("document_type") or DocumentType.other.value,
                    "official_project_url": url,
                    "province": d.get("province") or "广东",
                }
            )

    bundles = [
        b
        for b in build_project_bundles(items)
        if b.project_code != "PORTAL_SNAPSHOT"
        and not str(b.project_name).startswith("official_portal_snapshot")
    ]
    write_jsonl(
        ensure_dir(settings.datasets_root / "manifests") / "projects.jsonl",
        [b.model_dump(mode="json") for b in bundles],
    )
    # Sync document project_ids and reclassified document_types from bundles
    url_to_meta: dict[str, tuple[str, str, str | None, str | None]] = {}
    for b in bundles:
        for d in b.documents:
            if d.source_url:
                url_to_meta[d.source_url] = (
                    b.project_id,
                    d.document_type.value,
                    b.project_code,
                    b.project_name,
                )
            if d.document_id:
                url_to_meta[d.document_id] = (
                    b.project_id,
                    d.document_type.value,
                    b.project_code,
                    b.project_name,
                )
    for d in docs:
        key = d.get("source_url") or d.get("document_id") or ""
        meta = url_to_meta.get(key)
        if not meta:
            continue
        pid, dtype, code, name = meta
        d["project_id"] = pid
        # Prefer bundle-classified tender_document over generic other.
        if dtype == "tender_document" or d.get("document_type") in {None, "other"}:
            d["document_type"] = dtype
        d["project_code"] = code
        d["project_name"] = name
        d["issuing_authority"] = next((b.purchaser for b in bundles if b.project_id == pid), d.get("issuing_authority"))
    write_jsonl(settings.datasets_root / "manifests" / "documents.jsonl", docs)
    stats = {
        "projects": len(bundles),
        "bundle_levels": {
            level: sum(1 for b in bundles if b.bundle_level.value == level)
            for level in ("level_a", "level_b", "level_c", "incomplete")
        },
        "document_types": {},
    }
    from collections import Counter

    stats["document_types"] = dict(
        Counter(d.document_type.value for b in bundles for d in b.documents)
    )
    write_json(ensure_dir(settings.datasets_root / "reports") / "project_rebuild_stats.json", stats)
    log_stats(log, "rebuild_projects_from_documents", stats)
    return stats


def collect_from_seed_manifest(manifest_path: str, *, dry_run: bool = False) -> dict[str, Any]:
    """Collect notices listed in an official seed manifest (url / source_url rows)."""
    import json
    from pathlib import Path

    settings = get_settings()
    registry = load_source_registry()
    ckpt = DownloadCheckpoint(settings.datasets_root / "reports" / "checkpoints")
    search = SearchClient(registry=registry, checkpoint=ckpt)
    downloader = AttachmentDownloader(checkpoint=ckpt)

    rows = []
    for line in Path(manifest_path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        rows.append(json.loads(line))

    existing_docs = read_jsonl(settings.datasets_root / "manifests" / "documents.jsonl")
    existing_sha = {d.get("sha256") for d in existing_docs if d.get("sha256")}
    sources_out = list(read_jsonl(settings.datasets_root / "manifests" / "sources.jsonl"))
    docs_out = list(existing_docs)
    kept = 0
    failed = 0
    for row in rows:
        url = row.get("source_url") or row.get("url")
        if not url:
            continue
        try:
            status, html = search.fetch_html(url, referer=row.get("list_page") or "https://www.ccgp.gov.cn/")
        except Exception as exc:  # noqa: BLE001
            ckpt.record_discovery_failure(url=url, reason=str(exc))
            failed += 1
            continue
        if status != 200:
            failed += 1
            continue
        meta = extract_notice_metadata(html, source_url=url, title_hint=row.get("title"))
        project_code = meta.get("project_code") or row.get("project_code") or "UNKNOWN"
        project_name = meta.get("project_name") or row.get("title") or url
        purchaser = meta.get("purchaser")
        project_id = make_project_id(project_code, purchaser, project_name)
        dtype = (
            DocumentType(meta["document_type"])
            if meta.get("document_type") in DocumentType._value2member_map_
            else DocumentType.other_notice
        )
        if dry_run:
            kept += 1
            continue
        html_result = downloader.persist_document(
            content=html.encode("utf-8", errors="ignore"),
            source_url=url,
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
            kept += 1
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
                referer=url,
                issuing_authority=purchaser,
                existing_sha=existing_sha,
            )
            if dl.get("failed") or dl.get("duplicate") or dl.get("skipped"):
                continue
            sources_out.append(dl["source"])
            docs_out.append(dl["document"])
            existing_sha.add(dl["meta"]["sha256"])
    if not dry_run:
        write_manifests(sources_out, docs_out)
        rebuilt = rebuild_projects_from_documents()
    else:
        rebuilt = {}
    stats = {"kept": kept, "failed": failed, "rebuilt": rebuilt, "dry_run": dry_run}
    log_stats(log, "collect_from_seed_manifest", stats)
    return stats
