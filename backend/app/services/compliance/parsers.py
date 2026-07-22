"""Shared structured-field parsers for compliance rules (amounts, dates, units)."""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

_AMOUNT_RE = re.compile(
    r"(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>万元|万|元|￥|人民币)?"
)
_YEARS_RE = re.compile(
    r"(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>年|个月|月)?"
)
_QTY_RE = re.compile(
    r"(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>个|项|套|台|人|名)?"
)
_DATE_RE = re.compile(
    r"(?P<y>20\d{2}|19\d{2})[年\-/\.](?P<m>\d{1,2})[月\-/\.](?P<d>\d{1,2})"
)
_ISO_DATE_RE = re.compile(r"^(?P<y>\d{4})-(?P<m>\d{2})-(?P<d>\d{2})")


def dig_keys(obj: Any, keys: tuple[str, ...]) -> dict[str, Any]:
    found: dict[str, Any] = {}
    if not isinstance(obj, dict):
        return found
    lower_map = {str(k).lower(): k for k in obj}
    for key in keys:
        raw = lower_map.get(key.lower())
        if raw is not None and obj.get(raw) not in (None, "", [], {}):
            found[key] = obj.get(raw)
    return found


def parse_amount_to_yuan(value: Any) -> float | None:
    """Normalize amount to 元. Unparseable → None (never guess)."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    # Explicit unit in structured dict form
    if isinstance(value, dict):
        num = value.get("value") or value.get("amount") or value.get("num")
        unit = str(value.get("unit") or "元")
        try:
            n = float(num)
        except (TypeError, ValueError):
            return None
        if "万" in unit:
            return n * 10000.0
        return n
    m = _AMOUNT_RE.search(text)
    if not m:
        try:
            return float(text)
        except ValueError:
            return None
    n = float(m.group("num"))
    unit = m.group("unit") or "元"
    if "万" in unit:
        return n * 10000.0
    return n


def parse_years_to_years(value: Any) -> float | None:
    """Normalize duration to years. 月 → /12. Unparseable → None."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict):
        num = value.get("value") or value.get("years") or value.get("num")
        unit = str(value.get("unit") or "年")
        try:
            n = float(num)
        except (TypeError, ValueError):
            return None
        if "月" in unit:
            return n / 12.0
        return n
    text = str(value).strip()
    m = _YEARS_RE.search(text)
    if not m:
        try:
            return float(text)
        except ValueError:
            return None
    n = float(m.group("num"))
    unit = m.group("unit") or "年"
    if unit == "月" or unit == "个月":
        return n / 12.0
    return n


def parse_quantity(value: Any) -> float | None:
    """Normalize quantity (个/项/套 are equivalent counts). Unparseable → None."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict):
        num = value.get("value") or value.get("quantity") or value.get("num")
        try:
            return float(num)
        except (TypeError, ValueError):
            return None
    text = str(value).strip()
    m = _QTY_RE.search(text)
    if not m:
        try:
            return float(text)
        except ValueError:
            return None
    return float(m.group("num"))


def parse_level(value: Any) -> str | None:
    if value is None or value == "":
        return None
    text = str(value).strip()
    return text or None


def parse_date(value: Any) -> date | None:
    """Parse date-like values. Unparseable → None (never guess)."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    m = _ISO_DATE_RE.match(text[:10])
    if m:
        try:
            return date(int(m.group("y")), int(m.group("m")), int(m.group("d")))
        except ValueError:
            return None
    m2 = _DATE_RE.search(text)
    if m2:
        try:
            return date(int(m2.group("y")), int(m2.group("m")), int(m2.group("d")))
        except ValueError:
            return None
    # try fromisoformat loosely
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def extract_dates_from_text(text: str) -> list[date]:
    found: list[date] = []
    if not text:
        return found
    for m in _DATE_RE.finditer(text):
        try:
            found.append(date(int(m.group("y")), int(m.group("m")), int(m.group("d"))))
        except ValueError:
            continue
    return found
