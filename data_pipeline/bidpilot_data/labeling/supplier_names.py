from __future__ import annotations

import re
from typing import Any

from bs4 import BeautifulSoup

ORG_SUFFIXES = (
    "股份有限公司",
    "集团有限公司",
    "有限责任公司",
    "有限公司",
    "研究院",
    "设计院",
    "大学",
    "学院",
    "医院",
    "中心",
    "事务所",
    "合作社",
    "分公司",
    "总公司",
    "科技公司",
    "公司",
)

STOP_NAMES = {
    "一次",
    "单、",
    "的评",
    "名称",
    "名单",
    "评审",
    "单价",
    "地址",
    "金额",
    "代理费",
    "服务要求",
    "供应商",
    "中标供应商",
    "成交供应商",
    "中标人",
    "投标人",
    "采购人",
    "暂无",
    "无",
    "/",
    "-",
    "——",
}

STOP_CONTAINS = (
    "一次采购",
    "服务要求",
    "代理服务费",
    "评审委员会",
    "评标委员会",
    "中标金额",
    "成交金额",
    "采购预算",
    "项目名称",
    "项目编号",
    "交易服务费",
    "需注册",
    "登录",
    "或者采购人",
    "线上／线下",
    "公共资源交易",
    "资格性审查",
    "技术得分",
    "货物名称",
    "服务名称",
    "工程名称",
)


def normalize_supplier_name(name: str) -> str:
    t = re.sub(r"\s+", "", name or "")
    t = t.strip(" 。；;，,：:、|/\\")
    return t


def is_valid_supplier_name(name: str) -> tuple[bool, str]:
    n = normalize_supplier_name(name)
    if not n:
        return False, "empty"
    if n in STOP_NAMES:
        return False, "stop_name"
    if any(s in n for s in STOP_CONTAINS):
        return False, "stop_contains"
    if any(k in n for k in ("支付", "注册", "登录", "服务费", "或者采购", "需", "点击")):
        return False, "narrative"
    if "联合体" in n and "公司" not in n:
        return False, "invalid_joint_venture"
    if len(n) < 4 or len(n) > 50:
        return False, "length"
    if re.search(r"\d{5,}", n) and not any(sfx in n for sfx in ORG_SUFFIXES):
        return False, "looks_like_code"
    if not re.search(r"[\u4e00-\u9fff]", n):
        return False, "no_cjk"
    if not any(n.endswith(sfx) or sfx in n for sfx in ORG_SUFFIXES):
        # Allow hospital/university phrasing without exact suffix end
        if not any(k in n for k in ("公司", "大学", "学院", "医院", "中心", "研究所", "研究院", "设计院")):
            return False, "no_org_suffix"
    # Reject truncated scraps
    if n.endswith(("的", "、", "，", ",", "：", ":")):
        return False, "trailing_punct"
    if len(n) <= 3:
        return False, "too_short"
    return True, "ok"


def _from_html_tables(html: str) -> list[str]:
    names: list[str] = []
    if "<table" not in html.lower() and "供应商" not in html:
        return names
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:  # noqa: BLE001
        return names
    label_keys = ("中标供应商", "成交供应商", "供应商名称", "中标人", "供应商")
    for tr in soup.find_all("tr"):
        cells = [re.sub(r"\s+", "", c.get_text(" ", strip=True)) for c in tr.find_all(["td", "th"])]
        if len(cells) < 2:
            continue
        for i, cell in enumerate(cells[:-1]):
            if any(k in cell for k in label_keys) and "地址" not in cell and "金额" not in cell:
                cand = cells[i + 1]
                if cand and cand not in names:
                    names.append(cand)
    return names


def _from_strict_regex(text: str) -> list[str]:
    names: list[str] = []
    # Capture until common field delimiters; do not cross into next labeled field
    patterns = [
        r"中标供应商(?:名称)?[：:\s]*([^\n；;。]{2,60}?(?:有限公司|股份有限公司|集团有限公司|研究院|设计院|大学|学院|医院|中心|事务所|合作社))",
        r"成交供应商(?:名称)?[：:\s]*([^\n；;。]{2,60}?(?:有限公司|股份有限公司|集团有限公司|研究院|设计院|大学|学院|医院|中心|事务所|合作社))",
        r"供应商名称[：:\s]*([^\n；;。]{2,60}?(?:有限公司|股份有限公司|集团有限公司|研究院|设计院|大学|学院|医院|中心|事务所|合作社))",
        r"中标人[：:\s]*([^\n；;。]{2,60}?(?:有限公司|股份有限公司|集团有限公司|研究院|设计院|大学|学院|医院|中心|事务所|合作社))",
    ]
    for pat in patterns:
        for m in re.finditer(pat, text):
            names.append(m.group(1))
    # Loose labeled captures (validated later → rejected_supplier_candidate when dirty)
    loose = [
        r"供应商名称[：:\s]*([^\n；;。，,]{2,40})",
        r"中标供应商[：:\s]*([^\n；;。，,]{2,40})",
        r"成交供应商[：:\s]*([^\n；;。，,]{2,40})",
    ]
    for pat in loose:
        for m in re.finditer(pat, text):
            cand = m.group(1).strip()
            # Cut at next labeled field
            cand = re.split(r"(?:地址|金额|代理费|中标金额|成交金额|预算)", cand)[0].strip()
            if cand:
                names.append(cand)
    return names


def extract_award_suppliers(text: str, *, html: str | None = None) -> dict[str, Any]:
    """Extract and validate supplier names from award/result text or HTML."""
    raw: list[str] = []
    if html:
        raw.extend(_from_html_tables(html))
    raw.extend(_from_html_tables(text))
    raw.extend(_from_strict_regex(text))

    accepted: list[str] = []
    rejected: list[dict[str, str]] = []
    seen_norm: set[str] = set()
    dup_removed = 0
    for cand in raw:
        ok, reason = is_valid_supplier_name(cand)
        norm = normalize_supplier_name(cand)
        if not ok:
            rejected.append({"name": cand, "reason": reason})
            continue
        if norm in seen_norm:
            dup_removed += 1
            continue
        seen_norm.add(norm)
        accepted.append(norm)
    return {
        "raw_candidates": raw,
        "accepted_suppliers": accepted,
        "rejected_candidates": rejected,
        "rejection_reasons": {r["reason"]: sum(1 for x in rejected if x["reason"] == r["reason"]) for r in rejected},
        "duplicate_suppliers_removed": dup_removed,
    }
