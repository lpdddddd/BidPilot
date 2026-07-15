from __future__ import annotations

import re
from datetime import datetime
from html import unescape
from typing import Any
from urllib.parse import urlparse

from bidpilot_data.schemas.enums import DocumentType

# Prefer unambiguous Guangdong markers. Avoid bare "中山" (matches 中山医院 outside GD).
GD_MARKERS = (
    "广东",
    "广州",
    "深圳市",
    "深圳校区",
    "珠海",
    "佛山",
    "东莞",
    "中山市",
    "中山大学",
    "惠州",
    "汕头",
    "湛江",
    "江门",
    "肇庆",
    "梅州",
    "茂名",
    "清远",
    "揭阳",
    "潮州",
    "河源",
    "阳江",
    "云浮",
    "韶关",
)

NON_GD_BLOCKLIST = (
    "山东",
    "上海",
    "江苏",
    "浙江",
    "北京",
    "天津",
    "重庆",
    "河南",
    "河北",
    "湖南",
    "湖北",
    "四川",
    "福建",
    "安徽",
    "江西",
    "辽宁",
    "吉林",
    "黑龙江",
    "陕西",
    "山西",
    "云南",
    "贵州",
    "广西",
    "海南",
    "内蒙古",
    "宁夏",
    "新疆",
    "西藏",
    "青海",
    "甘肃",
)

IT_KEYWORDS = (
    "信息化",
    "软件",
    "运维",
    "数据治理",
    "网络安全",
    "信息系统",
    "系统集成",
    "数字化",
    "云服务",
    "电子政务",
    "大数据",
    "信息技术",
    "等保",
    "信息安全",
    "数据中心",
    "机房",
    "智慧",
    "平台建设",
    "系统升级",
    "办公系统",
    "政务",
)


def html_to_text(html: str) -> str:
    text = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", html)
    text = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", text)
    text = re.sub(r"(?is)<br\s*/?>", "\n", text)
    text = re.sub(r"(?is)</p>", "\n", text)
    text = re.sub(r"(?is)<[^>]+>", "\n", text)
    text = unescape(text)
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r"\n+", "\n", text)
    return text.strip()


def _field(patterns: list[str], text: str) -> str | None:
    for pat in patterns:
        m = re.search(pat, text, flags=re.I | re.M)
        if m:
            val = m.group(1).strip()
            val = re.sub(r"\s+", " ", val)
            if val:
                return val
    return None


def parse_budget_cny(text: str) -> float | None:
    patterns = [
        r"预算金额[：:\s]*￥?\s*([0-9]+(?:\.[0-9]+)?)\s*万元",
        r"预算金额[：:\s]*￥?\s*([0-9]+(?:\.[0-9]+)?)\s*元",
        r"预算[：:\s]*￥?\s*([0-9]+(?:\.[0-9]+)?)\s*万元",
    ]
    for pat in patterns:
        m = re.search(pat, text)
        if not m:
            continue
        value = float(m.group(1))
        if "万元" in pat:
            return value * 10000.0
        return value
    return None


def parse_published_at(text: str) -> str | None:
    m = re.search(r"(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日(?:\s*(\d{1,2}):(\d{2}))?", text)
    if not m:
        m = re.search(r"(20\d{2})-(\d{1,2})-(\d{1,2})(?:\s+(\d{1,2}):(\d{2}))?", text)
    if not m:
        return None
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    hh = int(m.group(4)) if m.lastindex and m.lastindex >= 4 and m.group(4) else 0
    mm = int(m.group(5)) if m.lastindex and m.lastindex >= 5 and m.group(5) else 0
    try:
        return datetime(y, mo, d, hh, mm).isoformat()
    except ValueError:
        return None


def infer_document_type(title: str, url: str = "") -> DocumentType:
    blob = f"{title} {url}"
    if any(k in blob for k in ("中标公告", "成交公告", "/zbgg/", "/cjgg/")):
        return DocumentType.award_notice
    if any(k in blob for k in ("合同公告", "采购合同", "/htgg/")):
        return DocumentType.contract_notice
    if any(k in blob for k in ("更正公告", "变更公告", "/gzgg/")):
        return DocumentType.amendment
    if any(k in blob for k in ("澄清", "答疑")):
        return DocumentType.clarification
    if any(k in blob for k in ("废标", "流标", "/fblbgg/")):
        return DocumentType.other_notice
    if any(k in blob for k in ("采购意向", "意向公告")):
        return DocumentType.intention_notice
    if any(k in blob for k in ("竞争性磋商", "公开招标", "招标公告", "/gkzb/", "/jzxcs/")):
        return DocumentType.tender_notice
    if "招标文件" in blob or "采购文件" in blob:
        return DocumentType.tender_document
    return DocumentType.other


def infer_industry(text: str, industry_map: dict[str, list[str]] | None = None) -> str | None:
    blob = text
    mapping = industry_map or {
        "信息化软件": ["信息化", "软件", "信息系统", "系统集成", "应用系统"],
        "系统运维": ["运维", "运维服务", "系统运维", "维保"],
        "网络安全": ["网络安全", "信息安全", "等保", "安全服务"],
        "数据治理": ["数据治理", "大数据", "数据共享", "数据中心"],
        "云服务": ["云服务", "云计算", "云平台"],
        "咨询服务": ["咨询服务", "管理咨询", "监理"],
    }
    best = None
    best_score = 0
    for industry, kws in mapping.items():
        score = sum(1 for kw in kws if kw in blob)
        if score > best_score:
            best_score = score
            best = industry
    return best if best_score > 0 else None


def is_guangdong_text(text: str) -> bool:
    if not any(m in text for m in GD_MARKERS):
        return False
    # If another province is named and Guangdong is not explicit, reject.
    if "广东" in text or "广州" in text or "深圳" in text:
        return True
    for other in NON_GD_BLOCKLIST:
        if other in text:
            return False
    return True


def it_score(text: str, keywords: list[str] | None = None) -> int:
    kws = keywords or list(IT_KEYWORDS)
    return sum(1 for kw in kws if kw in text)


def extract_ccgp_attachments(html: str) -> list[dict[str, str]]:
    """Extract publicly downloadable CCGP attachment UUIDs (bizDownload anchors)."""
    out: list[dict[str, str]] = []
    for m in re.finditer(
        r"<a[^>]*class=['\"]bizDownload['\"][^>]*id=['\"]([A-F0-9]+)['\"][^>]*>(.*?)</a>",
        html,
        flags=re.I | re.S,
    ):
        uuid = m.group(1)
        name = unescape(re.sub(r"<[^>]+>", "", m.group(2))).strip() or f"{uuid}.bin"
        out.append(
            {
                "attachment_id": uuid,
                "original_filename": name,
                "source_url": f"https://download.ccgp.gov.cn/oss/download?uuid={uuid}",
            }
        )
    # Also catch id-before-class order
    for m in re.finditer(
        r"<a[^>]*id=['\"]([A-F0-9]+)['\"][^>]*class=['\"]bizDownload['\"][^>]*>(.*?)</a>",
        html,
        flags=re.I | re.S,
    ):
        uuid = m.group(1)
        if any(a["attachment_id"] == uuid for a in out):
            continue
        name = unescape(re.sub(r"<[^>]+>", "", m.group(2))).strip() or f"{uuid}.bin"
        out.append(
            {
                "attachment_id": uuid,
                "original_filename": name,
                "source_url": f"https://download.ccgp.gov.cn/oss/download?uuid={uuid}",
            }
        )
    return out


def extract_notice_metadata(html: str, *, source_url: str, title_hint: str | None = None) -> dict[str, Any]:
    text = html_to_text(html)
    title = title_hint
    if not title:
        m = re.search(r"<title[^>]*>(.*?)</title>", html, flags=re.I | re.S)
        title = unescape(re.sub(r"<[^>]+>", "", m.group(1))).strip() if m else None
    title = (title or "").strip() or None

    project_code = _field(
        [
            r"项目编号[：:\s]*([^\n<；;]{3,80})",
            r"采购项目编号[：:\s]*([^\n<；;]{3,80})",
            r"代理机构编号[：:\s]*([^\n<；;]{3,80})",
        ],
        text,
    )
    if project_code:
        # Prefer primary code before parenthetical agency alias.
        project_code = project_code.split("（代理")[0].split("(代理")[0].strip()
        project_code = project_code.rstrip("。.;；")
    project_name = _field(
        [
            r"采购项目名称\s*\n?\s*([^\n]+)",
            r"项目名称[：:\s]*([^\n]+)",
        ],
        text,
    ) or title
    purchaser = _field(
        [
            r"采购单位\s*\n?\s*([^\n]+)",
            r"采购人[：:\s]*([^\n]+)",
        ],
        text,
    )
    agency = _field(
        [
            r"代理机构名称\s*\n?\s*([^\n]+)",
            r"采购代理机构[：:\s]*([^\n]+)",
        ],
        text,
    )
    published_at = parse_published_at(text)
    budget = parse_budget_cny(text)
    dtype = infer_document_type(title or "", source_url)
    industry = infer_industry(f"{title or ''}\n{text[:4000]}")
    province = "广东" if is_guangdong_text(f"{title or ''}\n{text[:2000]}") else None

    return {
        "project_code": project_code,
        "project_name": project_name,
        "purchaser": purchaser,
        "procurement_agency": agency,
        "budget_cny": budget,
        "published_at": published_at,
        "document_type": dtype.value if isinstance(dtype, DocumentType) else str(dtype),
        "industry": industry,
        "province": province,
        "source_url": source_url,
        "source_domain": urlparse(source_url).netloc.lower().split(":")[0],
        "title": title,
        "attachments": extract_ccgp_attachments(html),
        "text_excerpt": text[:4000],
        "it_score": it_score(f"{title or ''}\n{text[:5000]}"),
    }
