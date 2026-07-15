from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

from bidpilot_data.collectors.source_registry import SourceRegistry, load_source_registry


@dataclass(frozen=True)
class SourceValidation:
    ok: bool
    domain: str
    site_id: str | None
    reason: str


def extract_domain(url: str) -> str:
    return urlparse(url).netloc.lower().split(":")[0]


def validate_official_source(url: str, registry: SourceRegistry | None = None) -> SourceValidation:
    """Check that URL belongs to a whitelisted official domain."""
    reg = registry or load_source_registry()
    if not url or not url.startswith(("http://", "https://")):
        return SourceValidation(False, "", None, "source_url must be http(s)")
    domain = extract_domain(url)
    if not domain:
        return SourceValidation(False, "", None, "missing domain")
    if not reg.is_official_domain(url):
        return SourceValidation(False, domain, None, "non_official_domain")
    site = reg.match_site(url)
    return SourceValidation(True, domain, site.site_id if site else None, "ok")


def assert_official_or_raise(url: str, registry: SourceRegistry | None = None) -> SourceValidation:
    result = validate_official_source(url, registry)
    if not result.ok:
        raise ValueError(f"non-official source rejected: {url} ({result.reason})")
    return result
