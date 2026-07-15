from __future__ import annotations

import re
from typing import Any

INDUSTRY_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("cybersecurity", ("网络安全", "信息安全", "等保", "安全防护", "漏洞", "防火墙", "攻防")),
    ("cloud_service", ("云计算", "云服务", "云平台", "公有云", "私有云", "SaaS", "IaaS", "PaaS")),
    ("data_governance", ("数据治理", "数据中台", "大数据", "数据仓库", "数据湖", "主数据")),
    ("information_system_maintenance", ("运维", "系统维护", "信息系统运行维护", "机房维护", "维保")),
    ("software_development", ("软件开发", "系统开发", "定制开发", "信息化建设", "平台开发", "应用系统")),
    ("system_integration", ("系统集成", "集成服务", "弱电", "智能化集成")),
    ("hardware_equipment", ("服务器", "存储设备", "网络设备", "硬件", "交换机", "计算机设备", "终端")),
    ("consulting_service", ("咨询", "监理", "规划设计", "实施方案编制", "评估服务")),
]


def classify_industry(project: dict[str, Any], texts: list[str] | None = None) -> dict[str, Any]:
    """Rule-based industry classification; silver metadata only."""
    official = project.get("industry") or project.get("purchase_category") or project.get("品目")
    if isinstance(official, str) and official.strip() and official.strip().lower() not in {"unknown", "其他", "其它"}:
        mapped = _map_official(official)
        if mapped != "unknown":
            return {
                "industry": mapped,
                "industry_source": "official_field",
                "industry_confidence": 0.85,
            }

    blob = " ".join(
        [
            str(project.get("project_name") or ""),
            str(project.get("title") or ""),
            str(project.get("description") or ""),
            *(texts or []),
        ]
    )
    scores: dict[str, int] = {}
    for industry, kws in INDUSTRY_RULES:
        scores[industry] = sum(1 for kw in kws if kw in blob)
    # Non-IT signal
    non_it_hits = sum(1 for kw in ("物业", "食堂", "绿化", "安保巡逻", "医疗器械", "药品", "办公家具") if kw in blob)
    best = max(scores, key=scores.get) if scores else "unknown"
    best_score = scores.get(best, 0)
    if best_score == 0 and non_it_hits >= 1:
        return {"industry": "non_it", "industry_source": "rules", "industry_confidence": 0.55}
    if best_score == 0:
        return {"industry": "unknown", "industry_source": "rules", "industry_confidence": 0.0}
    conf = min(0.9, 0.4 + 0.15 * best_score)
    return {"industry": best, "industry_source": "rules", "industry_confidence": round(conf, 3)}


def _map_official(text: str) -> str:
    t = text.lower()
    mapping = [
        ("cybersecurity", ("安全", "网络安全")),
        ("cloud_service", ("云",)),
        ("data_governance", ("数据",)),
        ("information_system_maintenance", ("运维", "维护")),
        ("software_development", ("软件", "信息化")),
        ("system_integration", ("集成",)),
        ("hardware_equipment", ("硬件", "设备", "服务器")),
        ("consulting_service", ("咨询", "监理")),
    ]
    for industry, keys in mapping:
        if any(k in t for k in keys):
            return industry
    return "unknown"


def enrich_projects_industry(projects: list[dict[str, Any]], chunks_by_project: dict[str, list[str]] | None = None) -> list[dict[str, Any]]:
    out = []
    for p in projects:
        texts = (chunks_by_project or {}).get(p.get("project_id") or "", [])[:3]
        meta = classify_industry(p, texts)
        row = dict(p)
        row.update(meta)
        out.append(row)
    return out
