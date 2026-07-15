from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bidpilot_data.collectors.attachment_downloader import AttachmentDownloader, write_manifests
from bidpilot_data.collectors.download_checkpoint import DownloadCheckpoint
from bidpilot_data.collectors.metadata_extractor import extract_notice_metadata, is_guangdong_text
from bidpilot_data.collectors.project_bundle_builder import (
    attachment_type_for_notice,
    filename_suggests_tender_document,
    make_project_id,
)
from bidpilot_data.collectors.search_client import SearchClient
from bidpilot_data.collectors.source_registry import load_source_registry
from bidpilot_data.logging import get_logger, log_stats
from bidpilot_data.schemas import DocumentType
from bidpilot_data.settings import get_settings
from bidpilot_data.utils import ensure_dir, read_jsonl, write_json

log = get_logger(__name__)


def _normalize_code(code: str) -> str:
    return re.sub(r"\s+", "", (code or "").replace("（", "(").replace("）", ")"))


def _title_matches_project(title: str, code: str, name: str) -> bool:
    title_n = re.sub(r"\s+", "", title or "")
    code_n = _normalize_code(code)
    if code_n and code_n not in {"UNKNOWN", ""}:
        if code_n in title_n:
            return True
        # Codes often appear without separators / with bracket variants in titles.
        code_compact = re.sub(r"[^\w\u4e00-\u9fff]", "", code_n)
        if len(code_compact) >= 8 and code_compact in re.sub(r"[^\w\u4e00-\u9fff]", "", title_n):
            return True
    name = (name or "").strip()
    name_n = re.sub(r"\s+", "", name)
    # Strip common procurement suffixes for better title matching.
    for suffix in ("招标公告", "中标公告", "成交公告", "更正公告", "合同公告", "采购公告", "竞争性磋商公告", "竞争性谈判公告"):
        name_n = name_n.replace(suffix, "")
    if len(name_n) >= 8:
        key = name_n[:16]
        if key and key in title_n:
            return True
        if len(name_n) >= 14:
            mid = name_n[3:17]
            if mid and mid in title_n:
                return True
        # Prefix of purchaser-facing project name without org boilerplate.
        short = re.sub(r"^(关于|公示|公告)", "", name_n)[:12]
        if len(short) >= 8 and short in title_n:
            return True
    return False


def backfill_projects_by_code(*, dry_run: bool = False, max_list_pages: int = 30) -> dict[str, Any]:
    """Backfill notices/attachments for known projects using list-title prefiltering."""
    from bidpilot_data.collectors.project_discovery import rebuild_projects_from_documents

    settings = get_settings()
    registry = load_source_registry()
    ckpt = DownloadCheckpoint(settings.datasets_root / "reports" / "checkpoints")
    search = SearchClient(registry=registry, checkpoint=ckpt)
    downloader = AttachmentDownloader(checkpoint=ckpt)

    projects = read_jsonl(settings.datasets_root / "manifests" / "projects.jsonl")
    by_code: dict[str, dict[str, Any]] = {}
    for p in projects:
        code = _normalize_code(p.get("project_code") or "")
        if code and code != "UNKNOWN":
            by_code[code] = p

    existing_docs = read_jsonl(settings.datasets_root / "manifests" / "documents.jsonl")
    existing_sha = {d.get("sha256") for d in existing_docs if d.get("sha256")}
    existing_urls = {d.get("source_url") for d in existing_docs if d.get("source_url")}
    sources_out = list(read_jsonl(settings.datasets_root / "manifests" / "sources.jsonl"))
    docs_out = list(existing_docs)

    categories = ("zbgg", "cjgg", "gkzb", "jzxcs", "qtgg")
    scanned_pages = 0
    title_hits = 0
    notices_added = 0
    attachments = 0

    matched_hits: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for list_url, channel, category in search.iter_ccgp_list_pages(categories=categories, max_pages=max_list_pages):
        scanned_pages += 1
        try:
            status, html = search.fetch_html(list_url, referer="https://www.ccgp.gov.cn/")
        except Exception as exc:  # noqa: BLE001
            ckpt.record_discovery_failure(url=list_url, reason=str(exc), province="广东")
            time.sleep(search._pause)
            continue
        if status != 200:
            time.sleep(search._pause)
            continue
        for hit in search._parse_ccgp_list(html, list_url, channel, category):
            for code, proj in by_code.items():
                if _title_matches_project(hit.title, code, proj.get("project_name") or ""):
                    matched_hits.append((hit.to_dict(), proj))
                    title_hits += 1
                    break
        time.sleep(search._pause)

    # Dedup by URL
    seen: set[str] = set()
    uniq: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for hit, proj in matched_hits:
        u = hit.get("url")
        if not u or u in seen or u in existing_urls:
            continue
        seen.add(u)
        uniq.append((hit, proj))

    for hit, matched_project in uniq:
        url = hit["url"]
        try:
            status, html = search.fetch_html(url, referer=hit.get("list_page") or "https://www.ccgp.gov.cn/")
        except Exception as exc:  # noqa: BLE001
            ckpt.record_discovery_failure(url=url, reason=str(exc), province="广东")
            continue
        if status != 200:
            continue
        meta = extract_notice_metadata(html, source_url=url, title_hint=hit.get("title"))
        project_code = matched_project.get("project_code") or meta.get("project_code") or "UNKNOWN"
        project_name = matched_project.get("project_name") or meta.get("project_name") or hit.get("title")
        purchaser = matched_project.get("purchaser") or meta.get("purchaser")
        project_id = matched_project.get("project_id") or make_project_id(project_code, purchaser, project_name)
        dtype = (
            DocumentType(meta["document_type"])
            if meta.get("document_type") in DocumentType._value2member_map_
            else DocumentType.other_notice
        )
        if dry_run:
            notices_added += 1
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
            existing_urls.add(url)
            notices_added += 1
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
                referer=url,
                issuing_authority=purchaser,
                existing_sha=existing_sha,
            )
            if dl.get("failed") or dl.get("duplicate") or dl.get("skipped"):
                continue
            sources_out.append(dl["source"])
            docs_out.append(dl["document"])
            existing_sha.add(dl["meta"]["sha256"])
            attachments += 1

    rebuilt = {}
    if not dry_run:
        write_manifests(sources_out, docs_out)
        rebuilt = rebuild_projects_from_documents()

    stats = {
        "known_codes": len(by_code),
        "scanned_pages": scanned_pages,
        "title_hits": title_hits,
        "unique_matched_urls": len(uniq),
        "notices_added": notices_added,
        "attachments_added": attachments,
        "projects_after": rebuilt.get("projects"),
        "bundle_levels": rebuilt.get("bundle_levels"),
        "dry_run": dry_run,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    write_json(ensure_dir(settings.datasets_root / "reports") / "backfill_report.json", stats)
    log_stats(log, "backfill_projects_by_code", stats)
    return stats


def pair_via_bxsearch(*, max_projects: int = 40, dry_run: bool = False) -> dict[str, Any]:
    """Use official CCGP bxsearch by project_code/name to find complementary notices."""
    from datetime import date

    from bidpilot_data.collectors.project_discovery import rebuild_projects_from_documents

    settings = get_settings()
    registry = load_source_registry()
    ckpt = DownloadCheckpoint(settings.datasets_root / "reports" / "checkpoints")
    search = SearchClient(registry=registry, checkpoint=ckpt)
    downloader = AttachmentDownloader(checkpoint=ckpt)

    projects = read_jsonl(settings.datasets_root / "manifests" / "projects.jsonl")
    targets = []
    for p in projects:
        types = {d.get("document_type") for d in (p.get("documents") or [])}
        has_award = bool(types & {"award_notice", "result"})
        has_tender_file = bool(types & {"tender_document", "tender"})
        if has_award ^ has_tender_file or (has_tender_file and not has_award) or (has_award and not has_tender_file):
            if p.get("bundle_level") in {"level_c", "incomplete", "level_b"}:
                targets.append(p)
    targets = targets[:max_projects]

    existing_docs = read_jsonl(settings.datasets_root / "manifests" / "documents.jsonl")
    existing_sha = {d.get("sha256") for d in existing_docs if d.get("sha256")}
    existing_urls = {d.get("source_url") for d in existing_docs if d.get("source_url")}
    sources_out = list(read_jsonl(settings.datasets_root / "manifests" / "sources.jsonl"))
    docs_out = list(existing_docs)

    queries = 0
    hits_total = 0
    notices_added = 0
    atts_added = 0
    blocked = 0

    for proj in targets:
        code = (proj.get("project_code") or "").strip()
        name = (proj.get("project_name") or "").strip()
        types = {d.get("document_type") for d in (proj.get("documents") or [])}
        want_award = not bool(types & {"award_notice", "result"})
        want_tender = not bool(types & {"tender_document", "tender"})

        # Keyword plan: code in kw field (projectId rarely returns rows), then name slices.
        query_kws: list[str] = []
        if code and code != "UNKNOWN":
            query_kws.append(code)
        name_n = re.sub(r"\s+", "", name)
        for suffix in ("采购项目", "项目", "招标公告", "中标公告", "结果公告", "竞争性磋商公告"):
            name_n = name_n.replace(suffix, "")
        if len(name_n) >= 10:
            query_kws.append(name_n[:16])
        if len(name_n) >= 18:
            query_kws.append(name_n[4:20])
        # Dedup preserve order
        seen_q: set[str] = set()
        query_kws = [k for k in query_kws if not (k in seen_q or seen_q.add(k))]  # type: ignore[func-returns-value]

        for kw in query_kws[:3]:
            queries += 1
            hits = search.bxsearch_ccgp(
                keyword=kw,
                project_id="",
                province="广东",
                start_date=date(2023, 1, 1),
                end_date=date(2026, 12, 31),
                bid_type=0,
            )
            hits_total += len(hits)
            for hit in hits:
                url = hit.url
                if not url or url in existing_urls:
                    continue
                # Must match this project by code or discriminative name.
                if not _title_matches_project(hit.title, code, name):
                    # Also accept code appearing in search title even when project name differs slightly.
                    title_n = re.sub(r"\s+", "", hit.title or "")
                    code_n = re.sub(r"\s+", "", code or "")
                    if not (code_n and code_n not in {"UNKNOWN", ""} and code_n in title_n):
                        continue
                if want_award and hit.category not in {"zbgg", "cjgg", "unknown", "qtgg"}:
                    if not (want_tender and hit.category in {"gkzb", "jzxcs"}):
                        continue
                if want_tender and not want_award and hit.category not in {"gkzb", "jzxcs", "unknown", "qtgg"}:
                    continue
                if dry_run:
                    notices_added += 1
                    continue
                try:
                    status, html = search.fetch_html(url, referer="https://search.ccgp.gov.cn/")
                except Exception:  # noqa: BLE001
                    continue
                if status != 200:
                    continue
                meta = extract_notice_metadata(html, source_url=url, title_hint=hit.title)
                # Bind to existing project
                project_code = proj.get("project_code") or meta.get("project_code") or "UNKNOWN"
                project_name = proj.get("project_name") or meta.get("project_name") or hit.title
                purchaser = proj.get("purchaser") or meta.get("purchaser")
                project_id_bind = proj.get("project_id") or make_project_id(project_code, purchaser, project_name)
                dtype = (
                    DocumentType(meta["document_type"])
                    if meta.get("document_type") in DocumentType._value2member_map_
                    else DocumentType.other_notice
                )
                html_result = downloader.persist_document(
                    content=html.encode("utf-8", errors="ignore"),
                    source_url=url,
                    project_code=project_code,
                    project_name=project_name,
                    document_type=dtype,
                    original_filename=f"{project_code}_{dtype.value}.html",
                    project_id=project_id_bind,
                    published_at=meta.get("published_at"),
                    content_type="text/html; charset=utf-8",
                    issuing_authority=purchaser,
                    existing_sha=existing_sha,
                )
                if not html_result.get("duplicate"):
                    sources_out.append(html_result["source"])
                    docs_out.append(html_result["document"])
                    existing_sha.add(html_result["meta"]["sha256"])
                    existing_urls.add(url)
                    notices_added += 1
                    ckpt.mark_done(f"notice:{url}", {"sha256": html_result["meta"]["sha256"]})
                for att in meta.get("attachments") or []:
                    att_name = att.get("original_filename") or "attachment.bin"
                    att_type = attachment_type_for_notice(dtype, att_name)
                    dl = downloader.download_one(
                        source_url=att["source_url"],
                        project_code=project_code,
                        project_name=project_name,
                        document_type=att_type,
                        original_filename=att_name,
                        project_id=project_id_bind,
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
                    atts_added += 1
            time.sleep(3.0)

    rebuilt = {}
    if not dry_run:
        write_manifests(sources_out, docs_out)
        rebuilt = rebuild_projects_from_documents()
    stats = {
        "targets": len(targets),
        "queries": queries,
        "hits_total": hits_total,
        "notices_added": notices_added,
        "attachments_added": atts_added,
        "rebuilt": rebuilt,
        "dry_run": dry_run,
    }
    write_json(ensure_dir(settings.datasets_root / "reports") / "pair_bxsearch_report.json", stats)
    log_stats(log, "pair_via_bxsearch", stats)
    return stats


def download_missing_notice_attachments(*, dry_run: bool = False) -> dict[str, Any]:
    """Scan all local notice HTML and download any missing official attachments."""
    from bidpilot_data.collectors.project_discovery import rebuild_projects_from_documents

    settings = get_settings()
    ckpt = DownloadCheckpoint(settings.datasets_root / "reports" / "checkpoints")
    downloader = AttachmentDownloader(checkpoint=ckpt)
    docs = read_jsonl(settings.datasets_root / "manifests" / "documents.jsonl")
    sources = read_jsonl(settings.datasets_root / "manifests" / "sources.jsonl")
    existing_sha = {d.get("sha256") for d in docs if d.get("sha256")}
    existing_urls = {d.get("source_url") for d in docs if d.get("source_url")}
    scanned = 0
    downloaded = 0
    failed = 0
    for d in docs:
        path = settings.datasets_root / (d.get("storage_path") or "")
        if path.suffix.lower() not in {".html", ".htm"} or not path.exists():
            continue
        if d.get("project_code") == "PORTAL_SNAPSHOT":
            continue
        scanned += 1
        url = d.get("source_url") or "https://www.ccgp.gov.cn/"
        meta = extract_notice_metadata(path.read_text(encoding="utf-8", errors="ignore"), source_url=url)
        try:
            notice_dtype = DocumentType(str(d.get("document_type") or meta.get("document_type") or "other_notice"))
        except ValueError:
            notice_dtype = DocumentType.other_notice
        project_code = meta.get("project_code") or d.get("project_code") or Path(path).parent.name
        project_name = meta.get("project_name") or d.get("project_name") or path.name
        purchaser = meta.get("purchaser") or d.get("issuing_authority")
        project_id = d.get("project_id") or make_project_id(project_code, purchaser, project_name)
        for att in meta.get("attachments") or []:
            att_url = att.get("source_url")
            if not att_url or att_url in existing_urls:
                continue
            if dry_run:
                downloaded += 1
                continue
            att_name = att.get("original_filename") or "attachment.bin"
            att_type = attachment_type_for_notice(notice_dtype, att_name)
            dl = downloader.download_one(
                source_url=att_url,
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
                failed += 1
                continue
            if dl.get("duplicate") or dl.get("skipped"):
                continue
            sources.append(dl["source"])
            docs.append(dl["document"])
            existing_sha.add(dl["meta"]["sha256"])
            existing_urls.add(att_url)
            downloaded += 1
    rebuilt = {}
    if not dry_run and downloaded:
        write_manifests(sources, docs)
        rebuilt = rebuild_projects_from_documents()
    stats = {
        "html_scanned": scanned,
        "attachments_downloaded": downloaded,
        "attachments_failed": failed,
        "rebuilt": rebuilt,
        "dry_run": dry_run,
    }
    write_json(ensure_dir(settings.datasets_root / "reports") / "missing_attachments_report.json", stats)
    log_stats(log, "download_missing_notice_attachments", stats)
    return stats


def rematerialize_orphaned_raw_documents(*, max_list_pages: int = 18, dry_run: bool = False) -> dict[str, Any]:
    """Re-link raw files missing from manifests via checkpoint SHA map + list title rematch."""
    import hashlib
    import json

    from bidpilot_data.collectors.project_discovery import rebuild_projects_from_documents
    from bidpilot_data.utils import stable_uuid

    settings = get_settings()
    registry = load_source_registry()
    ckpt = DownloadCheckpoint(settings.datasets_root / "reports" / "checkpoints")
    search = SearchClient(registry=registry, checkpoint=ckpt)

    ck_path = settings.datasets_root / "reports" / "checkpoints" / "collect_official.json"
    sha_to_url: dict[str, str] = {}
    if ck_path.exists():
        done = (json.loads(ck_path.read_text(encoding="utf-8")).get("done") or {})
        for key, meta in done.items():
            if not isinstance(meta, dict) or not meta.get("sha256"):
                continue
            if key.startswith("dl:"):
                sha_to_url[meta["sha256"]] = key[3:]
            elif key.startswith("notice:"):
                sha_to_url[meta["sha256"]] = key[len("notice:") :]

    docs = read_jsonl(settings.datasets_root / "manifests" / "documents.jsonl")
    sources = read_jsonl(settings.datasets_root / "manifests" / "sources.jsonl")
    known_paths = {d.get("storage_path") for d in docs}
    known_sha = {d.get("sha256") for d in docs if d.get("sha256")}

    orphans: list[tuple[Path, str, str]] = []
    raw_root = settings.datasets_root / "raw" / "documents"
    for f in raw_root.rglob("*"):
        if not f.is_file():
            continue
        rel = str(f.relative_to(settings.datasets_root))
        if rel in known_paths:
            continue
        digest = hashlib.sha256(f.read_bytes()).hexdigest()
        if digest in known_sha:
            continue
        orphans.append((f, rel, digest))

    title_index: dict[str, str] = {}
    for list_url, channel, category in search.iter_ccgp_list_pages(max_pages=max_list_pages):
        try:
            status, html = search.fetch_html(list_url, referer="https://www.ccgp.gov.cn/")
        except Exception:  # noqa: BLE001
            time.sleep(search._pause)
            continue
        if status != 200:
            time.sleep(search._pause)
            continue
        for hit in search._parse_ccgp_list(html, list_url, channel, category):
            key = re.sub(r"\s+", "", hit.title or "")
            if key:
                title_index[key] = hit.url
        time.sleep(search._pause)

    def _resolve_title_url(title: str | None) -> str | None:
        if not title:
            return None
        tkey = re.sub(r"\s+", "", title)
        if tkey in title_index:
            return title_index[tkey]
        for k, u in title_index.items():
            if len(tkey) >= 16 and (tkey[:20] in k or k[:20] in tkey):
                return u
        return None

    def _register(
        *,
        path: Path,
        rel: str,
        digest: str,
        url: str,
        project_code: str,
        project_name: str,
        dtype: DocumentType,
        mime: str | None,
    ) -> None:
        nonlocal sources, docs
        project_id = make_project_id(project_code, None, project_name)
        document_id = str(stable_uuid(f"document:{digest}"))
        source_id = str(stable_uuid(f"source:{digest}:{url}"))
        domain = re.sub(r"^https?://", "", url).split("/")[0].split(":")[0]
        sources.append(
            {
                "source_id": source_id,
                "source_url": url,
                "source_site": domain,
                "project_code": project_code,
                "project_name": project_name,
                "document_type": dtype.value,
                "published_at": None,
                "province": None,
                "industry": None,
                "license_or_terms": "official public notice; follow robots and site terms",
                "collected_at": datetime.now(timezone.utc).isoformat(),
                "status": "downloaded",
                "local_path": rel,
                "sha256": digest,
                "error_message": None,
            }
        )
        docs.append(
            {
                "document_id": document_id,
                "project_id": project_id,
                "source_id": source_id,
                "original_filename": path.name,
                "mime_type": mime,
                "sha256": digest,
                "file_size": path.stat().st_size,
                "storage_path": rel,
                "page_count": None,
                "parse_method": None,
                "parse_status": "pending",
                "document_type": dtype.value,
                "source_url": url,
            }
        )
        known_sha.add(digest)
        known_paths.add(rel)

    restored = 0
    rematched = 0
    unresolved = 0
    html_by_code: dict[str, list[dict[str, Any]]] = {}

    for path, rel, digest in orphans:
        if path.suffix.lower() not in {".html", ".htm"}:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        url = sha_to_url.get(digest)
        meta = extract_notice_metadata(text, source_url=url or "https://www.ccgp.gov.cn/")
        title = meta.get("title") or path.name
        code = meta.get("project_code") or path.parent.name
        if not url:
            url = _resolve_title_url(title)
            if url:
                rematched += 1
        if not url:
            unresolved += 1
            continue
        dtype = (
            DocumentType(meta["document_type"])
            if meta.get("document_type") in DocumentType._value2member_map_
            else DocumentType.other_notice
        )
        if dry_run:
            restored += 1
            continue
        _register(
            path=path,
            rel=rel,
            digest=digest,
            url=url,
            project_code=code,
            project_name=title,
            dtype=dtype,
            mime="text/html; charset=utf-8",
        )
        ckpt.mark_done(f"notice:{url}", {"sha256": digest})
        html_by_code.setdefault(path.parent.name, []).append({"source_url": url, "storage_path": rel, "meta": meta})
        restored += 1

    pdf_restored = 0
    for path, rel, digest in orphans:
        if path.suffix.lower() != ".pdf":
            continue
        if digest in known_sha:
            continue
        url = sha_to_url.get(digest)
        code = path.parent.name
        dtype = attachment_type_for_notice(DocumentType.tender_notice, path.name)
        if not url:
            for hd in html_by_code.get(code, []):
                meta = hd.get("meta") or extract_notice_metadata(
                    (settings.datasets_root / hd["storage_path"]).read_text(encoding="utf-8", errors="ignore"),
                    source_url=hd.get("source_url") or "https://www.ccgp.gov.cn/",
                )
                atts = meta.get("attachments") or []
                for att in atts:
                    an = att.get("original_filename") or ""
                    if path.name == an or path.stem in an or an in path.name or len(atts) == 1:
                        url = att.get("source_url")
                        dtype = attachment_type_for_notice(
                            DocumentType(meta.get("document_type"))
                            if meta.get("document_type") in DocumentType._value2member_map_
                            else DocumentType.tender_notice,
                            an or path.name,
                        )
                        break
                if url:
                    break
        if not url:
            unresolved += 1
            continue
        if dry_run:
            restored += 1
            pdf_restored += 1
            continue
        _register(
            path=path,
            rel=rel,
            digest=digest,
            url=url,
            project_code=code,
            project_name=path.name,
            dtype=dtype,
            mime="application/pdf",
        )
        ckpt.mark_done(f"dl:{url}", {"sha256": digest})
        restored += 1
        pdf_restored += 1

    # Other binary orphans (docx/zip) with checkpoint URL
    other_restored = 0
    for path, rel, digest in orphans:
        if path.suffix.lower() in {".html", ".htm", ".pdf"}:
            continue
        if digest in known_sha:
            continue
        url = sha_to_url.get(digest)
        if not url:
            unresolved += 1
            continue
        if dry_run:
            restored += 1
            other_restored += 1
            continue
        _register(
            path=path,
            rel=rel,
            digest=digest,
            url=url,
            project_code=path.parent.name,
            project_name=path.name,
            dtype=attachment_type_for_notice(DocumentType.tender_notice, path.name),
            mime=None,
        )
        restored += 1
        other_restored += 1

    rebuilt = {}
    if not dry_run and restored:
        write_manifests(sources, docs)
        rebuilt = rebuild_projects_from_documents()
    elif not dry_run:
        rebuilt = rebuild_projects_from_documents()

    stats = {
        "orphans_found": len(orphans),
        "restored": restored,
        "pdf_restored": pdf_restored,
        "other_restored": other_restored,
        "title_rematched": rematched,
        "unresolved_estimate": unresolved,
        "title_index_size": len(title_index),
        "rebuilt": rebuilt,
        "dry_run": dry_run,
    }
    write_json(ensure_dir(settings.datasets_root / "reports") / "rematerialize_orphans_report.json", stats)
    log_stats(log, "rematerialize_orphaned_raw_documents", stats)
    return stats


def collect_homepage_domain_snapshots(*, dry_run: bool = False) -> dict[str, Any]:
    """Persist reachable official portal homepage HTML as provenance snapshots (not train labels)."""
    settings = get_settings()
    registry = load_source_registry()
    ckpt = DownloadCheckpoint(settings.datasets_root / "reports" / "checkpoints")
    search = SearchClient(registry=registry, checkpoint=ckpt)
    downloader = AttachmentDownloader(checkpoint=ckpt)
    urls = [
        "https://www.ccgp.gov.cn/",
        "https://www.ggzy.gov.cn/",
        "https://www.zycg.gov.cn/",
        "https://www.gzggzy.cn/",
        "https://ygp.gdzwfw.gov.cn/",
        "http://www.ccgp-beijing.gov.cn/",
        "http://www.ccgp-jiangsu.gov.cn/",
        "http://www.ccgp-zhejiang.gov.cn/",
    ]
    existing_docs = read_jsonl(settings.datasets_root / "manifests" / "documents.jsonl")
    existing_sha = {d.get("sha256") for d in existing_docs if d.get("sha256")}
    sources_out = list(read_jsonl(settings.datasets_root / "manifests" / "sources.jsonl"))
    docs_out = list(existing_docs)
    saved = []
    for url in urls:
        try:
            status, html = search.fetch_html(url)
        except Exception as exc:  # noqa: BLE001
            ckpt.record_discovery_failure(url=url, reason=str(exc))
            continue
        if status != 200 or dry_run:
            saved.append({"url": url, "status": status, "saved": False})
            continue
        # Do not put portal homepages into project training bundles; keep as source provenance only.
        result = downloader.persist_document(
            content=html.encode("utf-8", errors="ignore"),
            source_url=url,
            project_code="PORTAL_SNAPSHOT",
            project_name=f"official_portal_snapshot:{url}",
            document_type=DocumentType.other,
            original_filename="portal_home.html",
            project_id=make_project_id("PORTAL_SNAPSHOT", None, url),
            content_type="text/html; charset=utf-8",
            existing_sha=existing_sha,
        )
        if not result.get("duplicate"):
            sources_out.append(result["source"])
            docs_out.append(result["document"])
            existing_sha.add(result["meta"]["sha256"])
            saved.append({"url": url, "status": status, "saved": True})
        else:
            saved.append({"url": url, "status": status, "saved": False, "duplicate": True})
    if not dry_run:
        write_manifests(sources_out, docs_out)
    stats = {"portals": saved, "domains": sorted({re.sub(r'^https?://', '', u).split('/')[0] for u in urls})}
    write_json(ensure_dir(settings.datasets_root / "reports") / "portal_domain_snapshots.json", stats)
    return stats


def pair_incomplete_projects(*, max_list_pages: int = 45, dry_run: bool = False) -> dict[str, Any]:
    """For award-only / tender-file-only projects, rematch the missing notice types by title/code."""
    from bidpilot_data.collectors.project_discovery import rebuild_projects_from_documents

    settings = get_settings()
    registry = load_source_registry()
    ckpt = DownloadCheckpoint(settings.datasets_root / "reports" / "checkpoints")
    search = SearchClient(registry=registry, checkpoint=ckpt)
    downloader = AttachmentDownloader(checkpoint=ckpt)

    projects = read_jsonl(settings.datasets_root / "manifests" / "projects.jsonl")
    targets: list[dict[str, Any]] = []
    for p in projects:
        types = {d.get("document_type") for d in (p.get("documents") or [])}
        has_award = bool(types & {"award_notice", "result"})
        has_tender_file = bool(types & {"tender_document", "tender"})
        has_contract = bool(types & {"contract_notice", "contract", "evaluation_result"})
        if has_tender_file and has_award and has_contract:
            continue
        if has_award or has_tender_file:
            targets.append(p)

    existing_docs = read_jsonl(settings.datasets_root / "manifests" / "documents.jsonl")
    existing_sha = {d.get("sha256") for d in existing_docs if d.get("sha256")}
    existing_urls = {d.get("source_url") for d in existing_docs if d.get("source_url")}
    sources_out = list(read_jsonl(settings.datasets_root / "manifests" / "sources.jsonl"))
    docs_out = list(existing_docs)

    # Prefer complementary categories.
    categories = ("gkzb", "jzxcs", "zbgg", "cjgg", "qtgg")
    matched = 0
    added_notices = 0
    added_atts = 0
    hits: list[tuple[dict[str, Any], dict[str, Any]]] = []

    for list_url, channel, category in search.iter_ccgp_list_pages(categories=categories, max_pages=max_list_pages):
        try:
            status, html = search.fetch_html(list_url, referer="https://www.ccgp.gov.cn/")
        except Exception:  # noqa: BLE001
            time.sleep(search._pause)
            continue
        if status != 200:
            time.sleep(search._pause)
            continue
        for hit in search._parse_ccgp_list(html, list_url, channel, category):
            for proj in targets:
                if _title_matches_project(hit.title, proj.get("project_code") or "", proj.get("project_name") or ""):
                    hits.append((hit.to_dict(), proj))
                    matched += 1
                    break
        time.sleep(search._pause)

    seen: set[str] = set()
    for hit, proj in hits:
        url = hit.get("url")
        if not url or url in seen or url in existing_urls:
            continue
        seen.add(url)
        try:
            status, html = search.fetch_html(url, referer=hit.get("list_page") or "https://www.ccgp.gov.cn/")
        except Exception:  # noqa: BLE001
            continue
        if status != 200:
            continue
        meta = extract_notice_metadata(html, source_url=url, title_hint=hit.get("title"))
        project_code = proj.get("project_code") or meta.get("project_code") or "UNKNOWN"
        project_name = proj.get("project_name") or meta.get("project_name") or hit.get("title")
        purchaser = proj.get("purchaser") or meta.get("purchaser")
        project_id = proj.get("project_id") or make_project_id(project_code, purchaser, project_name)
        dtype = (
            DocumentType(meta["document_type"])
            if meta.get("document_type") in DocumentType._value2member_map_
            else DocumentType.other_notice
        )
        if dry_run:
            added_notices += 1
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
            existing_urls.add(url)
            added_notices += 1
            ckpt.mark_done(f"notice:{url}", {"sha256": html_result["meta"]["sha256"]})
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
                referer=url,
                issuing_authority=purchaser,
                existing_sha=existing_sha,
            )
            if dl.get("failed") or dl.get("duplicate") or dl.get("skipped"):
                continue
            sources_out.append(dl["source"])
            docs_out.append(dl["document"])
            existing_sha.add(dl["meta"]["sha256"])
            added_atts += 1

    rebuilt = {}
    if not dry_run:
        write_manifests(sources_out, docs_out)
        rebuilt = rebuild_projects_from_documents()
    stats = {
        "targets": len(targets),
        "title_hits": matched,
        "unique_urls": len(seen),
        "notices_added": added_notices,
        "attachments_added": added_atts,
        "rebuilt": rebuilt,
        "dry_run": dry_run,
    }
    write_json(ensure_dir(settings.datasets_root / "reports") / "pair_incomplete_report.json", stats)
    log_stats(log, "pair_incomplete_projects", stats)
    return stats


def harvest_completed_awards(
    *,
    target_new_awards: int = 40,
    max_list_pages: int = 60,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Harvest GD IT award + tender notices from reachable list pages, then pair files.

    Note: CCGP category indexes only expose ~recent months via pagination; the
    search endpoint is rate-limited. We therefore harvest completed zbgg/cjgg and
    file-rich gkzb/jzxcs from available pages (2023-2026 window) without relying
    on deep historical pagination.
    """
    from bidpilot_data.collectors.project_discovery import discover_and_collect, rebuild_projects_from_documents

    keywords = [
        "信息化",
        "软件",
        "运维",
        "数据",
        "网络安全",
        "信息系统",
        "机房",
        "数字化",
        "平台",
        "系统",
    ]
    # Awards first for completed bundles
    award_stats = discover_and_collect(
        province="广东",
        keywords=keywords,
        start_date="2023-01-01",
        end_date="2026-12-31",
        target_projects=180,
        max_list_pages=max_list_pages,
        dry_run=dry_run,
        require_keyword_in_title=True,
        categories=("zbgg", "cjgg"),
    )
    if dry_run:
        return award_stats
    # Tender notices/files for pairing
    tender_stats = discover_and_collect(
        province="广东",
        keywords=keywords,
        start_date="2023-01-01",
        end_date="2026-12-31",
        target_projects=200,
        max_list_pages=max_list_pages,
        dry_run=dry_run,
        require_keyword_in_title=True,
        categories=("gkzb", "jzxcs"),
    )
    pair = pair_incomplete_projects(max_list_pages=max(40, max_list_pages // 2))
    bf = backfill_projects_by_code(max_list_pages=max(30, max_list_pages // 2))
    rebuilt = rebuild_projects_from_documents()
    stats = {
        "awards": award_stats,
        "tenders": tender_stats,
        "pair": pair,
        "backfill": bf,
        "rebuilt": rebuilt,
    }
    write_json(ensure_dir(get_settings().datasets_root / "reports") / "harvest_completed_report.json", stats)
    return stats


def discover_completed_it_projects(
    *,
    target_projects: int = 100,
    max_list_pages: int = 35,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Discover GD IT projects then backfill by project_code title matches."""
    from bidpilot_data.collectors.project_discovery import discover_and_collect, rebuild_projects_from_documents

    keywords = [
        "信息化",
        "软件",
        "运维",
        "数据",
        "网络安全",
        "信息系统",
        "机房",
        "数据中心",
        "数字化",
        "平台",
        "系统集成",
    ]
    stats = discover_and_collect(
        province="广东",
        keywords=keywords,
        start_date="2023-01-01",
        end_date="2026-12-31",
        target_projects=target_projects,
        max_list_pages=max_list_pages,
        dry_run=dry_run,
    )
    if not dry_run:
        domains = collect_homepage_domain_snapshots()
        bf = backfill_projects_by_code(max_list_pages=max(12, max_list_pages // 2))
        rebuilt = rebuild_projects_from_documents()
        stats["domain_snapshots"] = domains
        stats["backfill"] = bf
        stats["rebuilt"] = rebuilt
    return stats
