"""Full-scan cross-split leakage detection (candidate recall + precise confirm)."""

from __future__ import annotations

import hashlib
import re
from collections import Counter, defaultdict
from typing import Any

from rapidfuzz import fuzz

from bidpilot_data.settings import get_settings
from bidpilot_data.sft.dedup import hamming64, normalize_user_text, simhash64
from bidpilot_data.utils import ensure_dir, read_jsonl, write_json

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

# Strong project-specific / scoring / technical entities (not generic law boilerplate).
BUSINESS_MARKERS = (
    "评分表",
    "评分因素",
    "综合得分",
    "技术部分",
    "商务部分",
    "分值",
    "权重",
    "技术参数",
    "参数响应",
    "采购需求一览",
    "服务要求明细",
    "合同主要条款",
    "付款方式",
    "付款条件",
    "服务期限为",
    "中标金额",
    "预算金额",
    "★号条款",
    "废标条款明细",
)

STRONG_TEMPLATE_MARKERS = (
    "GDCA",
    "温馨提示",
    "中山大学智能电子采购系统",
    "联合体协议书",
    "政府采购法第二十二条",
    "电子招投标项目",
    "数字证书电子签名",
    "采购服务费",
    "履约保证金",
    "投标须知前附表",
    "政府采购投标及履约承诺函",
    "供应商基本情况表",
    "投标无效",
    "无效参评",
    "失信被执行人",
)


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def strip_template_boilerplate(text: str) -> str:
    """Remove common legal/portal boilerplate spans; residual used for business overlap."""
    t = text or ""
    for m in TEMPLATE_MARKERS:
        t = t.replace(m, " ")
    # Strip long GDCA / portal instruction blocks
    t = re.sub(r"温馨提示[\s\S]{0,800}", " ", t)
    t = re.sub(r"https?://\S+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _template_marker_hits(text: str) -> int:
    c = _compact(text)
    return sum(1 for m in TEMPLATE_MARKERS if _compact(m) in c)


def _business_entity_hits(text: str) -> int:
    c = _compact(text)
    return sum(1 for m in BUSINESS_MARKERS if _compact(m) in c)


def _strong_template(text: str) -> bool:
    c = _compact(text)
    return sum(1 for m in STRONG_TEMPLATE_MARKERS if _compact(m) in c) >= 1 and _template_marker_hits(text) >= 1


def classify_overlap(
    left: dict[str, Any],
    right: dict[str, Any],
    *,
    sim: float,
    exact: bool,
) -> tuple[str, str]:
    """Return (kind, reason)."""
    text_l = left.get("text") or ""
    text_r = right.get("text") or ""
    biz_l = _business_entity_hits(text_l)
    biz_r = _business_entity_hits(text_r)
    tmpl_l = _template_marker_hits(text_l)
    tmpl_r = _template_marker_hits(text_r)
    residual_l = strip_template_boilerplate(text_l)
    residual_r = strip_template_boilerplate(text_r)
    residual_sim = (
        fuzz.token_set_ratio(residual_l[:500], residual_r[:500]) if residual_l and residual_r else 0
    )
    residual_short = len(_compact(residual_l)) < 40 and len(_compact(residual_r)) < 40
    portal_template = _strong_template(text_l) and _strong_template(text_r)

    if exact or sim >= 99.999:
        if left.get("project_id") == right.get("project_id") or (
            left.get("document_id") and left.get("document_id") == right.get("document_id")
        ):
            return "same_project_or_document", "identical_text_same_project_or_document"
        if left.get("kind") == "sft_qa" or right.get("kind") == "sft_qa":
            return "severe_business_overlap", "identical_sft_user_assistant_across_splits"
        # Portal / agency boilerplate across distinct projects
        if (portal_template or (tmpl_l >= 2 and tmpl_r >= 2)) and biz_l + biz_r <= 1:
            return "template_overlap", "exact_boilerplate_low_business_residue"
        if biz_l >= 2 and biz_r >= 2 and not residual_short:
            return "severe_business_overlap", "exact_or_near_exact_business_content_across_projects"
        if portal_template or tmpl_l >= 1 or tmpl_r >= 1 or _strong_template(text_l) or _strong_template(text_r):
            return "template_overlap", "exact_agency_or_legal_boilerplate"
        return "exact_duplicate", "identical_normalized_text_across_splits"

    same_project = left.get("project_id") and left.get("project_id") == right.get("project_id")
    same_doc = left.get("document_id") and left.get("document_id") == right.get("document_id")
    if same_project or same_doc:
        return "same_project_or_document", "shared_project_or_document_across_splits"

    # SFT QA: only exact / near-exact identical prompts+answers are hard fails
    if left.get("kind") == "sft_qa" or right.get("kind") == "sft_qa":
        if sim >= 99.5:
            return "severe_business_overlap", "near_duplicate_sft_qa_across_splits"
        return "normal", "sft_qa_similar_but_not_identical"

    # Dominantly portal template → template_overlap even if fuzzy residual high (agency boilerplate body)
    if portal_template and biz_l + biz_r <= 1:
        return "template_overlap", "shared_strong_portal_boilerplate"

    # Business scoring / tech near-dup cannot be template-exempted
    if sim >= 95 and biz_l >= 2 and biz_r >= 2 and residual_sim >= 85 and not residual_short:
        return "severe_business_overlap", "high_residual_similarity_with_business_entities"

    if tmpl_l >= 2 and tmpl_r >= 2 and biz_l + biz_r <= 1 and (residual_sim < 70 or residual_short or portal_template):
        return "template_overlap", "shared_boilerplate_after_strip_residual_low"

    if sim >= 98 and residual_sim >= 90 and biz_l >= 2 and biz_r >= 2 and not residual_short:
        return "severe_business_overlap", "near_identical_after_template_strip"

    return "normal", "below_severe_threshold"


def _collect_split_items(
    records: list[dict[str, Any]],
    *,
    split: str,
    chunks: dict[str, dict[str, Any]],
    docs: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen_chunk: set[str] = set()
    for r in records:
        # SFT QA fingerprint item
        msgs = r.get("messages") or []
        user = next((m.get("content") for m in msgs if m.get("role") == "user"), "")
        asst = ""
        for m in reversed(msgs):
            if m.get("role") == "assistant":
                asst = m.get("content") or ""
                break
        qa_text = f"{user}\n{asst}".strip()
        if qa_text:
            items.append(
                {
                    "split": split,
                    "kind": "sft_qa",
                    "record_id": r.get("record_id"),
                    "project_id": r.get("project_id"),
                    "document_id": (r.get("source_document_ids") or [None])[0],
                    "chunk_id": (r.get("source_chunk_ids") or [None])[0],
                    "source_url": (r.get("source_urls") or [None])[0],
                    "task_type": r.get("task_type"),
                    "text": qa_text[:1200],
                }
            )
        for cid in r.get("source_chunk_ids") or []:
            if not cid or cid in seen_chunk:
                continue
            c = chunks.get(cid)
            if not c:
                continue
            seen_chunk.add(cid)
            did = c.get("document_id")
            items.append(
                {
                    "split": split,
                    "kind": "chunk",
                    "record_id": r.get("record_id"),
                    "project_id": r.get("project_id") or c.get("project_id"),
                    "document_id": did,
                    "chunk_id": cid,
                    "source_url": (docs.get(did or "") or {}).get("source_url"),
                    "task_type": r.get("task_type"),
                    "text": (c.get("text") or "")[:1200],
                }
            )
    return items


def _build_indexes(items: list[dict[str, Any]]) -> tuple[dict[str, list[int]], dict[tuple[int, int], list[int]], dict[str, list[int]]]:
    """exact_hash -> idxs; (band, band_val) -> idxs; ngram -> idxs."""
    exact: dict[str, list[int]] = defaultdict(list)
    bands: dict[tuple[int, int], list[int]] = defaultdict(list)
    ngrams: dict[str, list[int]] = defaultdict(list)
    for i, it in enumerate(items):
        norm = normalize_user_text(it.get("text") or "")
        if not norm:
            continue
        h = hashlib.sha1(norm.encode("utf-8")).hexdigest()
        it["_norm"] = norm
        it["_hash"] = h
        it["_simhash"] = simhash64(norm)
        exact[h].append(i)
        sh = it["_simhash"]
        for b in range(4):
            bands[(b, (sh >> (b * 16)) & 0xFFFF)].append(i)
        # char bigrams sample for inverted recall
        chars = re.findall(r"[\u4e00-\u9fff]{2}|[a-z0-9]{3,}", norm)
        for g in chars[:40]:
            ngrams[g].append(i)
    return exact, bands, ngrams


def analyze_cross_split_similarity(*, threshold: int = 95, max_pairs_in_report: int = 200) -> dict[str, Any]:
    """Full-scan train/validation/test leakage with candidate recall + RapidFuzz confirm."""
    settings = get_settings()
    root = settings.datasets_root
    chunks = {c["chunk_id"]: c for c in read_jsonl(root / "interim" / "chunks" / "chunks.jsonl")}
    docs = {d["document_id"]: d for d in read_jsonl(root / "manifests" / "documents.jsonl")}
    splits = {
        "train": read_jsonl(root / "sft" / "train" / "records.jsonl"),
        "validation": read_jsonl(root / "sft" / "validation" / "records.jsonl"),
        "test": read_jsonl(root / "sft" / "test" / "records.jsonl"),
    }

    split_stats: dict[str, Any] = {}
    all_items: list[dict[str, Any]] = []
    for name, rows in splits.items():
        items = _collect_split_items(rows, split=name, chunks=chunks, docs=docs)
        all_items.extend(items)
        split_stats[name] = {
            "records": len(rows),
            "projects": len({r.get("project_id") for r in rows if r.get("project_id")}),
            "documents": len({it.get("document_id") for it in items if it.get("document_id")}),
            "chunks": len({it.get("chunk_id") for it in items if it.get("kind") == "chunk" and it.get("chunk_id")}),
            "items": len(items),
        }

    exact_idx, band_idx, ngram_idx = _build_indexes(all_items)

    # Candidate pairs across splits only
    split_pairs = (("train", "validation"), ("train", "test"), ("validation", "test"))
    candidate_keys: set[tuple[int, int]] = set()

    # Exact hash collisions across splits
    for _h, idxs in exact_idx.items():
        if len(idxs) < 2:
            continue
        by_split: dict[str, list[int]] = defaultdict(list)
        for i in idxs:
            by_split[all_items[i]["split"]].append(i)
        for a, b in split_pairs:
            for i in by_split.get(a, []):
                for j in by_split.get(b, []):
                    candidate_keys.add((min(i, j), max(i, j)))

    # SimHash band collisions
    for _key, idxs in band_idx.items():
        if len(idxs) < 2:
            continue
        by_split = defaultdict(list)
        for i in idxs:
            by_split[all_items[i]["split"]].append(i)
        for a, b in split_pairs:
            la, lb = by_split.get(a, []), by_split.get(b, [])
            # Cap per-bucket fanout
            for i in la[:80]:
                for j in lb[:80]:
                    if hamming64(all_items[i].get("_simhash", 0), all_items[j].get("_simhash", 0)) <= 3:
                        candidate_keys.add((min(i, j), max(i, j)))

    # N-gram inverted recall: items sharing >= 3 rare-ish grams
    co_count: dict[tuple[int, int], int] = defaultdict(int)
    for _g, idxs in ngram_idx.items():
        if len(idxs) < 2 or len(idxs) > 200:
            continue
        by_split = defaultdict(list)
        for i in idxs:
            by_split[all_items[i]["split"]].append(i)
        for a, b in split_pairs:
            la, lb = by_split.get(a, [])[:40], by_split.get(b, [])[:40]
            for i in la:
                for j in lb:
                    key = (min(i, j), max(i, j))
                    co_count[key] += 1
                    if co_count[key] >= 3:
                        candidate_keys.add(key)

    # Precise confirm
    pairs: list[dict[str, Any]] = []
    kind_counts: Counter[str] = Counter()
    precise_compared = 0
    for i, j in sorted(candidate_keys):
        left, right = all_items[i], all_items[j]
        if left["split"] == right["split"]:
            continue
        t1 = left.get("text") or ""
        t2 = right.get("text") or ""
        if not t1 or not t2:
            continue
        precise_compared += 1
        exact = left.get("_hash") and left.get("_hash") == right.get("_hash")
        sim = 100.0 if exact else float(fuzz.token_set_ratio(t1[:600], t2[:600]))
        if not exact and sim < threshold:
            continue
        kind, reason = classify_overlap(left, right, sim=sim, exact=bool(exact))
        if kind == "normal":
            continue
        kind_counts[kind] += 1
        pairs.append(
            {
                "similarity": sim,
                "kind": kind,
                "reason": reason,
                "split_pair": f"{left['split']}/{right['split']}",
                "left": {
                    "split": left["split"],
                    "project_id": left.get("project_id"),
                    "document_id": left.get("document_id"),
                    "chunk_id": left.get("chunk_id"),
                    "record_id": left.get("record_id"),
                    "task_type": left.get("task_type"),
                    "source_url": left.get("source_url"),
                    "item_kind": left.get("kind"),
                },
                "right": {
                    "split": right["split"],
                    "project_id": right.get("project_id"),
                    "document_id": right.get("document_id"),
                    "chunk_id": right.get("chunk_id"),
                    "record_id": right.get("record_id"),
                    "task_type": right.get("task_type"),
                    "source_url": right.get("source_url"),
                    "item_kind": right.get("kind"),
                },
            }
        )

    severe = (
        kind_counts.get("severe_business_overlap", 0)
        + kind_counts.get("same_project_or_document", 0)
        + kind_counts.get("exact_duplicate", 0)
    )
    # exact_duplicate across different projects with template-only may be counted in exact_duplicate
    # Recompute fail set: same_project, same_doc, severe_business, and exact non-template
    fail_n = 0
    for p in pairs:
        if p["kind"] in {"same_project_or_document", "severe_business_overlap"}:
            fail_n += 1
        elif p["kind"] == "exact_duplicate" and "boilerplate" not in (p.get("reason") or ""):
            # template exact uses template_overlap; remaining exact_duplicate fails gate
            fail_n += 1

    # Project-level mutual exclusion (hard fail)
    proj_sets = {k: {r.get("project_id") for r in v if r.get("project_id")} for k, v in splits.items()}
    project_leaks = []
    for a, b in split_pairs:
        inter = proj_sets[a] & proj_sets[b]
        if inter:
            project_leaks.append({"splits": f"{a}/{b}", "count": len(inter), "sample": sorted(inter)[:5]})
            fail_n += len(inter)

    report = {
        "full_scan": True,
        "threshold": threshold,
        "split_stats": split_stats,
        "chunks_scanned": sum(s["chunks"] for s in split_stats.values()),
        "items_scanned": len(all_items),
        "candidate_pairs": len(candidate_keys),
        "precise_comparisons": precise_compared,
        "pair_count": len(pairs),
        "kind_counts": dict(kind_counts),
        "exact_duplicate": kind_counts.get("exact_duplicate", 0),
        "same_project_or_document": kind_counts.get("same_project_or_document", 0),
        "template_overlap": kind_counts.get("template_overlap", 0),
        "severe_business_overlap": kind_counts.get("severe_business_overlap", 0),
        "severe_train_test_near_duplicates": kind_counts.get("severe_business_overlap", 0)
        + kind_counts.get("same_project_or_document", 0),
        "project_leaks": project_leaks,
        "pairs": pairs[:max_pairs_in_report],
        "ok": fail_n == 0 and not project_leaks,
        "fail_count": fail_n,
        "note": (
            "full_scan candidate recall via exact-hash + SimHash LSH + n-gram co-occurrence; "
            "template_overlap requires low residual similarity after boilerplate strip and low business entities."
        ),
    }
    write_json(ensure_dir(root / "reports") / "cross_split_similarity_report.json", report)
    return report


def collect_leaky_project_pairs(report: dict[str, Any] | None = None) -> list[tuple[str, str]]:
    """Return project_id pairs that must stay in the same split (severe / exact leaks)."""
    if report is None:
        settings = get_settings()
        path = settings.datasets_root / "reports" / "cross_split_similarity_report.json"
        if not path.exists():
            report = analyze_cross_split_similarity()
        else:
            import json

            report = json.loads(path.read_text(encoding="utf-8"))
    pairs: list[tuple[str, str]] = []
    for p in report.get("pairs") or []:
        if p.get("kind") in {"template_overlap", "normal"}:
            continue
        a = (p.get("left") or {}).get("project_id")
        b = (p.get("right") or {}).get("project_id")
        if a and b and a != b:
            pairs.append((a, b))
    return pairs


def coalesce_projects_to_split(
    *,
    train: set[str],
    validation: set[str],
    test: set[str],
    leak_pairs: list[tuple[str, str]],
) -> tuple[set[str], set[str], set[str], int]:
    """Move projects so each leaky connected component lives in one split."""
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    all_pids = train | validation | test
    for p in all_pids:
        find(p)
    for a, b in leak_pairs:
        if a in all_pids and b in all_pids:
            union(a, b)

    comps: dict[str, list[str]] = defaultdict(list)
    for p in all_pids:
        comps[find(p)].append(p)

    moved = 0
    for members in comps.values():
        if len(members) < 2:
            continue
        # Always collapse multi-split components into train to avoid floor/oscillation
        # (train absorbs near-duplicate projects; validation/test keep unique clusters).
        scores = {
            "train": sum(1 for m in members if m in train),
            "validation": sum(1 for m in members if m in validation),
            "test": sum(1 for m in members if m in test),
        }
        occupied = [k for k, v in scores.items() if v > 0]
        if len(occupied) >= 2:
            dest = "train"
        else:
            dest = occupied[0] if occupied else "train"
        for m in members:
            cur = "train" if m in train else "validation" if m in validation else "test"
            if cur == dest:
                continue
            moved += 1
            train.discard(m)
            validation.discard(m)
            test.discard(m)
            if dest == "train":
                train.add(m)
            elif dest == "validation":
                validation.add(m)
            else:
                test.add(m)
    return train, validation, test, moved
