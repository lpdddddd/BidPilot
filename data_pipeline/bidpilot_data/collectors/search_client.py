from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import date
from html import unescape
from typing import Any, Iterable
from urllib.parse import urljoin

import httpx

from bidpilot_data.collectors.download_checkpoint import DownloadCheckpoint
from bidpilot_data.collectors.metadata_extractor import is_guangdong_text, it_score
from bidpilot_data.collectors.official_source_validator import validate_official_source
from bidpilot_data.collectors.source_registry import SourceRegistry, load_source_registry
from bidpilot_data.logging import get_logger

log = get_logger(__name__)


@dataclass
class NoticeHit:
    title: str
    url: str
    list_page: str
    category: str
    channel: str  # zygg|dfgg
    published_hint: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "url": self.url,
            "list_page": self.list_page,
            "category": self.category,
            "channel": self.channel,
            "published_hint": self.published_hint,
        }


class SearchClient:
    """Search / list official notices. Never bypass captcha, login, or signed APIs."""

    # Prefer completed-project notices first (award/result) to raise level_a/b yield.
    CCGP_CATEGORIES = ("zbgg", "cjgg", "gkzb", "jzxcs", "qtgg", "fblbgg")

    def __init__(
        self,
        registry: SourceRegistry | None = None,
        checkpoint: DownloadCheckpoint | None = None,
    ) -> None:
        self.registry = registry or load_source_registry()
        self.checkpoint = checkpoint
        self._pause = 1.0 / max(self.registry.rate_limit_per_second, 0.1)

    def _client(self) -> httpx.Client:
        return httpx.Client(
            timeout=self.registry.timeout_seconds,
            follow_redirects=True,
            headers={
                "User-Agent": self.registry.user_agent,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            },
        )

    def fetch_html(self, url: str, *, referer: str | None = None) -> tuple[int, str]:
        check = validate_official_source(url, self.registry)
        if not check.ok:
            raise PermissionError(f"non-official URL blocked: {url}")
        headers = {}
        if referer:
            headers["Referer"] = referer
        with self._client() as client:
            resp = client.get(url, headers=headers)
            if resp.status_code in {401, 403, 429, 451}:
                if self.checkpoint:
                    self.checkpoint.record_discovery_failure(
                        url=url,
                        reason=f"access_restricted_{resp.status_code}",
                    )
                raise PermissionError(f"access restricted status={resp.status_code} url={url}")
            return resp.status_code, resp.text

    def _parse_ccgp_list(self, html: str, list_url: str, channel: str, category: str) -> list[NoticeHit]:
        hits: list[NoticeHit] = []
        for m in re.finditer(r'<a[^>]+href=["\']([^"\']+\.htm)["\'][^>]*>(.*?)</a>', html, flags=re.I | re.S):
            href, raw = m.group(1), m.group(2)
            title = unescape(re.sub(r"<[^>]+>", "", raw))
            title = re.sub(r"\s+", " ", title).strip()
            if len(title) < 8:
                continue
            if "/cggg/" not in href and not href.startswith("./") and not href.startswith("../"):
                # keep relative notice links
                if not re.search(r"t\d{8}_\d+\.htm", href):
                    continue
            url = urljoin(list_url, href)
            if not validate_official_source(url, self.registry).ok:
                continue
            hits.append(
                NoticeHit(
                    title=title,
                    url=url,
                    list_page=list_url,
                    category=category,
                    channel=channel,
                )
            )
        return hits

    def iter_ccgp_list_pages(
        self,
        *,
        channels: Iterable[str] = ("zygg", "dfgg"),
        categories: Iterable[str] | None = None,
        max_pages: int = 20,
    ) -> Iterable[tuple[str, str, str]]:
        """Yield list URLs. Pagination gaps are handled by the consumer (skip 404s)."""
        cats = tuple(categories or self.CCGP_CATEGORIES)
        for channel in channels:
            for category in cats:
                yield f"https://www.ccgp.gov.cn/cggg/{channel}/{category}/", channel, category
                for i in range(2, max_pages + 1):
                    yield (
                        f"https://www.ccgp.gov.cn/cggg/{channel}/{category}/index_{i}.htm",
                        channel,
                        category,
                    )

    def _hit_passes_filters(
        self,
        hit: NoticeHit,
        *,
        province: str,
        keywords: list[str],
        start_date: date | None,
        end_date: date | None,
        require_keyword_in_title: bool,
    ) -> bool:
        if province in {"广东", "广东省"}:
            if not is_guangdong_text(hit.title):
                return False
        elif province and province not in hit.title:
            return False
        score = it_score(hit.title, keywords)
        if require_keyword_in_title and score <= 0:
            return False
        if start_date or end_date:
            m = re.search(r"/(20\d{2})(\d{2})/", hit.url)
            if m:
                y, mo = int(m.group(1)), int(m.group(2))
                if start_date and date(y, mo, 1) < date(start_date.year, start_date.month, 1):
                    return False
                if end_date and date(y, mo, 1) > date(end_date.year, end_date.month, 1):
                    return False
        return True

    def iter_ccgp_hits(
        self,
        *,
        province: str = "广东",
        keywords: list[str] | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        max_pages: int = 20,
        require_keyword_in_title: bool = True,
        categories: Iterable[str] | None = None,
        channels: Iterable[str] = ("zygg", "dfgg"),
    ) -> Iterable[NoticeHit]:
        """Yield matching list hits page-by-page (search API is rate-limited; no bypass)."""
        keywords = keywords or list(self.registry.discovery.get("default_keywords") or [])
        seen: set[str] = set()
        consecutive_404 = 0
        skip_cat: tuple[str, str] | None = None
        prev_cat: tuple[str, str] | None = None
        for list_url, channel, category in self.iter_ccgp_list_pages(
            channels=channels, categories=categories, max_pages=max_pages
        ):
            cat_key = (channel, category)
            if prev_cat != cat_key:
                consecutive_404 = 0
                skip_cat = None
                prev_cat = cat_key
            if skip_cat == cat_key:
                continue
            try:
                status, html = self.fetch_html(list_url, referer="https://www.ccgp.gov.cn/")
            except Exception as exc:  # noqa: BLE001
                log.warning("ccgp list failed url=%s err=%s", list_url, exc)
                if self.checkpoint:
                    self.checkpoint.record_discovery_failure(url=list_url, reason=str(exc), province=province)
                time.sleep(self._pause)
                continue
            if status == 404:
                consecutive_404 += 1
                time.sleep(self._pause)
                if consecutive_404 >= 2:
                    log.info("skip remaining pages after 404s channel=%s category=%s", channel, category)
                    skip_cat = cat_key
                continue
            if status != 200 or len(html) < 500:
                if self.checkpoint:
                    self.checkpoint.record_discovery_failure(
                        url=list_url,
                        reason=f"http_{status}_or_empty",
                        province=province,
                    )
                time.sleep(self._pause)
                continue
            consecutive_404 = 0
            page_hits = 0
            for hit in self._parse_ccgp_list(html, list_url, channel, category):
                if hit.url in seen:
                    continue
                if not self._hit_passes_filters(
                    hit,
                    province=province,
                    keywords=keywords,
                    start_date=start_date,
                    end_date=end_date,
                    require_keyword_in_title=require_keyword_in_title,
                ):
                    continue
                seen.add(hit.url)
                page_hits += 1
                yield hit
            if page_hits:
                log.info("ccgp list hits page=%s category=%s n=%s total=%s", list_url, category, page_hits, len(seen))
            time.sleep(self._pause)

    def search_ccgp(
        self,
        *,
        province: str = "广东",
        keywords: list[str] | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        max_pages: int = 20,
        require_keyword_in_title: bool = False,
    ) -> list[NoticeHit]:
        """List-page discovery. CCGP search endpoint is often rate-limited; we do not bypass it."""
        return list(
            self.iter_ccgp_hits(
                province=province,
                keywords=keywords,
                start_date=start_date,
                end_date=end_date,
                max_pages=max_pages,
                require_keyword_in_title=require_keyword_in_title,
            )
        )

    def bxsearch_ccgp(
        self,
        *,
        keyword: str = "",
        project_id: str = "",
        province: str = "广东",
        start_date: date | None = None,
        end_date: date | None = None,
        page_index: int = 1,
        bid_type: int = 0,
    ) -> list[NoticeHit]:
        """Public CCGP bxsearch. Respects rate limits / 频繁访问 pages; never bypasses captcha."""
        from urllib.parse import urlencode

        start = start_date or date(2023, 1, 1)
        end = end_date or date(2026, 12, 31)
        zone = ""
        zone_id = ""
        if province in {"广东", "广东省"}:
            zone, zone_id = "广东", "44"
        params = {
            "searchtype": "1",
            "page_index": str(page_index),
            "bidSort": "0",
            "buyerName": "",
            "projectId": project_id or "",
            "pinMu": "0",
            "bidType": str(bid_type),
            "dbselect": "bidx",
            "kw": keyword or "",
            "start_time": f"{start.year:04d}:{start.month:02d}:{start.day:02d}",
            "end_time": f"{end.year:04d}:{end.month:02d}:{end.day:02d}",
            "timeType": "6",
            "displayZone": zone,
            "zoneId": zone_id,
            "pppStatus": "0",
            "agentName": "",
        }
        url = "https://search.ccgp.gov.cn/bxsearch?" + urlencode(params)
        try:
            status, html = self.fetch_html(url, referer="https://www.ccgp.gov.cn/")
        except Exception as exc:  # noqa: BLE001
            log.warning("bxsearch failed err=%s", exc)
            if self.checkpoint:
                self.checkpoint.record_discovery_failure(url=url, reason=str(exc), province=province)
            return []
        time.sleep(max(self._pause, 2.0))
        if status != 200 or "频繁访问" in html:
            if self.checkpoint:
                self.checkpoint.record_discovery_failure(
                    url=url,
                    reason="rate_limited_or_http",
                    province=province,
                )
            return []
        # Result URLs are provided in obfuscated JS variable ohtmlurls.
        m = re.search(r"ohtmlurls\s*=\s*['\"]([^'\"]+)['\"]", html)
        if not m:
            return []
        urls = [u.strip() for u in m.group(1).split(",") if ".htm" in u]
        # Titles from the result list block (order matches ohtmlurls).
        idx = html.find("vT-srch-result-list-bid")
        chunk = html[idx : idx + 50000] if idx >= 0 else html
        lis = re.findall(r"<li>(.*?)</li>", chunk, flags=re.I | re.S)
        titles: list[str] = []
        for li in lis:
            title = unescape(re.sub(r"<[^>]+>", "", li))
            title = re.sub(r"\s+", " ", title).strip()
            # Keep a short notice title (cut project overview fluff).
            for sep in (" 项目概况", " 一、", " 二、", " 采购需求", " 公告概要"):
                if sep in title:
                    title = title.split(sep, 1)[0].strip()
            titles.append(title)
        hits: list[NoticeHit] = []
        for i, u in enumerate(urls):
            if not u.startswith("http"):
                u = "http:" + u if u.startswith("//") else urljoin("http://www.ccgp.gov.cn/", u)
            # Normalize http->https for whitelist consistency when possible
            if u.startswith("http://www.ccgp.gov.cn/"):
                u = "https://" + u[len("http://") :]
            if not validate_official_source(u, self.registry).ok:
                continue
            title = titles[i] if i < len(titles) else u
            if title in {"站内资讯", "政策法规", "首页"} or len(title) < 8:
                continue
            cat = "unknown"
            for c in self.CCGP_CATEGORIES:
                if f"/{c}/" in u:
                    cat = c
                    break
            ch = "dfgg" if "/dfgg/" in u else ("zygg" if "/zygg/" in u else "unknown")
            hits.append(
                NoticeHit(
                    title=title,
                    url=u,
                    list_page=url,
                    category=cat,
                    channel=ch,
                )
            )
        return hits

    def probe_restricted_portals(self, province: str = "广东") -> list[dict[str, Any]]:
        """Record portals that are currently access-restricted for manual follow-up."""
        probes = [
            "https://search.ccgp.gov.cn/bxsearch",
            "https://gdgpo.czt.gd.gov.cn/",
            "https://deal.ggzy.gov.cn/",
            "https://ygp.gdzwfw.gov.cn/ggzy-portal/center/#/jygg",
        ]
        rows: list[dict[str, Any]] = []
        for url in probes:
            try:
                status, _ = self.fetch_html(url if not url.endswith("#/jygg") else "https://ygp.gdzwfw.gov.cn/")
                rows.append({"url": url, "status": status, "ok": status == 200})
            except Exception as exc:  # noqa: BLE001
                rows.append({"url": url, "status": None, "ok": False, "error": str(exc)})
                if self.checkpoint:
                    self.checkpoint.record_discovery_failure(url=url, reason=str(exc), province=province)
            time.sleep(self._pause)
        return rows
