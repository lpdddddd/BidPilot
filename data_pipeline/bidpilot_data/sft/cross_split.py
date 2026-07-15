from __future__ import annotations

from typing import Any

from rapidfuzz import fuzz

from bidpilot_data.settings import get_settings
from bidpilot_data.utils import ensure_dir, read_jsonl, write_json

# Legal / portal / agency boilerplate that appears across distinct projects.
TEMPLATE_MARKERS = (
    "根据《中华人民共和国政府采购法》",
    "法律、行政法规",
    "投标人不得存在下列情形",
    "提供虚假材料谋取中标",
    "与采购人、采购代理机构恶意串通",
    "资格审查表",
    "必须是具有独立承担民事责任能力",
    "节能产品、环境标志产品",
    "产品适用政府采购政策情况表",
    "GDCA",
    "数字证书电子签名",
    "中山大学智能电子采购系统",
    "温馨提示",
    "电子招投标项目",
    "依法设立的电子认证服务机构",
    "投标保证金",
    "无效投标",
    "政府采购政策",
    "品目清单和认证证书",
    "投标联合体",
    "联合体协议书",
    "政府采购法第二十二条",
    "评标委员会按评分表规定的评分因素",
    "中标候选人",
    "参评无效",
    "技术条款响应一览表",
    "实质性响应指标",
    "第三方测试机构出具的证明材料",
    "行业行政主管部门颁发的荣誉证书",
)


def _is_template_text(text: str) -> bool:
    import re

    t = re.sub(r"\s+", "", text or "")
    if not t:
        return False
    hits = sum(1 for m in TEMPLATE_MARKERS if re.sub(r"\s+", "", m) in t)
    if hits >= 2:
        return True
    # Single strong boilerplate section
    strong = (
        "资格审查表",
        "GDCA",
        "节能产品、环境标志产品",
        "根据《中华人民共和国政府采购法》",
        "中山大学智能电子采购系统",
        "第三方测试机构出具的证明材料",
        "评标委员会按评分表规定的评分因素",
        "联合体协议书",
    )
    if any(re.sub(r"\s+", "", m) in t for m in strong) and len(t) < 1500:
        return True
    return False


def analyze_cross_split_similarity(*, threshold: int = 98) -> dict[str, Any]:
    settings = get_settings()
    root = settings.datasets_root
    chunks = {c["chunk_id"]: c for c in read_jsonl(root / "interim" / "chunks" / "chunks.jsonl")}
    docs = {d["document_id"]: d for d in read_jsonl(root / "manifests" / "documents.jsonl")}
    train = read_jsonl(root / "sft" / "train" / "records.jsonl")
    test = read_jsonl(root / "sft" / "test" / "records.jsonl")

    def sample_chunks(rows: list[dict[str, Any]], limit: int = 400) -> list[dict[str, Any]]:
        out = []
        seen_cids: set[str] = set()
        for r in rows:
            for cid in r.get("source_chunk_ids") or []:
                if cid in seen_cids:
                    continue
                c = chunks.get(cid)
                if not c:
                    continue
                seen_cids.add(cid)
                out.append(
                    {
                        "record_id": r.get("record_id"),
                        "project_id": r.get("project_id"),
                        "document_id": c.get("document_id"),
                        "chunk_id": c.get("chunk_id"),
                        "source_url": (docs.get(c.get("document_id") or "") or {}).get("source_url"),
                        "text": (c.get("text") or "")[:800],
                        "task_type": r.get("task_type"),
                    }
                )
                if len(out) >= limit:
                    return out
        return out

    train_c = sample_chunks(train)
    test_c = sample_chunks(test)
    pairs: list[dict[str, Any]] = []
    severe = 0
    template_overlap = 0
    same_proj = 0
    pair_keys: set[tuple[str, str]] = set()
    for te in test_c:
        for tr in train_c:
            if not te["text"] or not tr["text"]:
                continue
            key = (tr["chunk_id"], te["chunk_id"])
            if key in pair_keys:
                continue
            sim = fuzz.token_set_ratio(te["text"][:500], tr["text"][:500])
            if sim < threshold:
                continue
            pair_keys.add(key)
            is_template = _is_template_text(te["text"]) and _is_template_text(tr["text"])
            same_project = te["project_id"] == tr["project_id"]
            same_doc = te["document_id"] == tr["document_id"] and te["document_id"]
            # Core business clause heuristics for severe (not boilerplate)
            core_markers = ("评分表", "分值", "技术参数", "采购需求", "服务要求明细", "合同主要条款")
            looks_core = any(m in te["text"] and m in tr["text"] for m in core_markers) and not is_template
            if same_project or same_doc:
                kind = "same_project_or_document"
                same_proj += 1
            elif is_template or (sim >= 98 and not looks_core and _is_template_text(te["text"])):
                kind = "template_overlap"
                template_overlap += 1
            else:
                kind = "severe"
                severe += 1
            pairs.append(
                {
                    "similarity": sim,
                    "kind": kind,
                    "train": {
                        "project_id": tr["project_id"],
                        "document_id": tr["document_id"],
                        "chunk_id": tr["chunk_id"],
                        "source_url": tr["source_url"],
                        "record_id": tr["record_id"],
                    },
                    "test": {
                        "project_id": te["project_id"],
                        "document_id": te["document_id"],
                        "chunk_id": te["chunk_id"],
                        "source_url": te["source_url"],
                        "record_id": te["record_id"],
                    },
                }
            )

    report = {
        "threshold": threshold,
        "pairs": pairs[:100],
        "pair_count": len(pairs),
        "severe_train_test_near_duplicates": severe,
        "template_overlap": template_overlap,
        "same_project_or_document": same_proj,
        "ok": severe == 0,
        "note": (
            "template_overlap = shared legal/portal boilerplate across distinct projects; "
            "severe = near-identical core business clauses (scoring/spec/contract) across train/test."
        ),
    }
    write_json(ensure_dir(root / "reports") / "cross_split_similarity_report.json", report)
    return report
