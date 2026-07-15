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

    CCGP_CATEGORIES = ("gkzb", "jzxcs", "zbgg", "cjgg", "qtgg", "fblbgg")

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
        keywords = keywords or list(self.registry.discovery.get("default_keywords") or [])
        out: list[NoticeHit] = []
        seen: set[str] = set()

        for list_url, channel, category in self.iter_ccgp_list_pages(max_pages=max_pages):
            try:
                status, html = self.fetch_html(list_url, referer="https://www.ccgp.gov.cn/")
            except Exception as exc:  # noqa: BLE001
                log.warning("ccgp list failed url=%s err=%s", list_url, exc)
                if self.checkpoint:
                    self.checkpoint.record_discovery_failure(url=list_url, reason=str(exc), province=province)
                time.sleep(self._pause)
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

            for hit in self._parse_ccgp_list(html, list_url, channel, category):
                if hit.url in seen:
                    continue
                if province in {"广东", "广东省"}:
                    if not is_guangdong_text(hit.title):
                        continue
                elif province and province not in hit.title:
                    continue
                score = it_score(hit.title, keywords)
                if require_keyword_in_title and score <= 0:
                    continue
                # Optional date filter from URL fragment YYYYMM
                if start_date or end_date:
                    m = re.search(r"/(20\d{2})(\d{2})/", hit.url)
                    if m:
                        y, mo = int(m.group(1)), int(m.group(2))
                        if start_date and date(y, mo, 1) < date(start_date.year, start_date.month, 1):
                            continue
                        if end_date and date(y, mo, 1) > date(end_date.year, end_date.month, 1):
                            continue
                seen.add(hit.url)
                out.append(hit)
            time.sleep(self._pause)
        return out

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
