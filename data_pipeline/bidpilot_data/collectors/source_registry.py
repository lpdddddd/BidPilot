from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

from bidpilot_data.settings import get_settings


@dataclass(frozen=True)
class OfficialSite:
    site_id: str
    name: str
    base_url: str
    domains: tuple[str, ...]
    source_type: str
    priority: int = 100
    province_focus: str | None = None
    notes: str = ""
    allowed: bool = True


@dataclass
class SourceRegistry:
    sites: list[OfficialSite] = field(default_factory=list)
    allowed_domain_suffixes: list[str] = field(default_factory=list)
    rate_limit_per_second: float = 0.8
    user_agent: str = "BidPilotDataBot/0.1"
    timeout_seconds: float = 30.0
    max_retries: int = 3
    discovery: dict[str, Any] = field(default_factory=dict)

    def site_by_id(self, site_id: str) -> OfficialSite | None:
        for s in self.sites:
            if s.site_id == site_id:
                return s
        return None

    def all_domains(self) -> set[str]:
        out: set[str] = set()
        for s in self.sites:
            out.update(d.lower() for d in s.domains)
        return out

    def is_official_domain(self, url_or_domain: str) -> bool:
        host = url_or_domain.lower()
        if "://" in host:
            host = urlparse(host).netloc.lower()
        host = host.split(":")[0].lstrip(".")
        if not host:
            return False
        if host in self.all_domains():
            return True
        for d in self.all_domains():
            if host == d or host.endswith("." + d):
                return True
        for suffix in self.allowed_domain_suffixes:
            suf = suffix.lower().lstrip(".")
            if host == suf or host.endswith("." + suf):
                # Still require government-style domains; reject commercial aggregators ending wrongly.
                if suf == "gov.cn" and host.endswith(".gov.cn"):
                    return True
                if host.endswith(suf):
                    return True
        return False

    def match_site(self, url: str) -> OfficialSite | None:
        host = urlparse(url).netloc.lower().split(":")[0]
        best: OfficialSite | None = None
        for s in self.sites:
            for d in s.domains:
                d = d.lower()
                if host == d or host.endswith("." + d):
                    if best is None or s.priority < best.priority:
                        best = s
        return best


def _config_path() -> Path:
    settings = get_settings()
    primary = settings.repo_root / "data_pipeline" / "configs" / "source_sites.yaml"
    if primary.exists():
        return primary
    example = settings.repo_root / "data_pipeline" / "configs" / "source_sites.example.yaml"
    return example


@lru_cache(maxsize=1)
def load_source_registry() -> SourceRegistry:
    path = _config_path()
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    sites: list[OfficialSite] = []
    for row in raw.get("sites") or []:
        if not row.get("allowed", True):
            continue
        sites.append(
            OfficialSite(
                site_id=str(row["site_id"]),
                name=str(row.get("name") or row["site_id"]),
                base_url=str(row.get("base_url") or ""),
                domains=tuple(str(d) for d in (row.get("domains") or [])),
                source_type=str(row.get("source_type") or "official"),
                priority=int(row.get("priority") or 100),
                province_focus=row.get("province_focus"),
                notes=str(row.get("notes") or ""),
                allowed=True,
            )
        )
    return SourceRegistry(
        sites=sorted(sites, key=lambda s: s.priority),
        allowed_domain_suffixes=[str(x) for x in (raw.get("allowed_domain_suffixes") or ["gov.cn"])],
        rate_limit_per_second=float(raw.get("rate_limit_per_second") or 0.8),
        user_agent=str(raw.get("user_agent") or "BidPilotDataBot/0.1"),
        timeout_seconds=float(raw.get("timeout_seconds") or 30),
        max_retries=int(raw.get("max_retries") or 3),
        discovery=dict(raw.get("discovery") or {}),
    )


def reload_source_registry() -> SourceRegistry:
    load_source_registry.cache_clear()
    return load_source_registry()
