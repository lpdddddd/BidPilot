"""Full-scan cross-split leakage detection (candidate recall + precise confirm).

full_scan=true means every candidate that must be checked was processed — not merely
that every record was indexed. Silent bucket truncation is forbidden.
"""

from __future__ import annotations

import hashlib
import re
import time
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

# Direct pair budget per secondary bucket; above this we sub-bucket further or skip.
MAX_DIRECT_BUCKET = 300
MAX_SECONDARY_BUCKET = 250
MAX_NGRAM_DF = 100
PAIRWISE_BUDGET = 5_000_000


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def strip_template_boilerplate(text: str) -> str:
    t = text or ""
    for m in TEMPLATE_MARKERS:
        t = t.replace(m, " ")
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

    if left.get("kind") == "sft_qa" or right.get("kind") == "sft_qa":
        if sim >= 99.5:
            return "severe_business_overlap", "near_duplicate_sft_qa_across_splits"
        return "normal", "sft_qa_similar_but_not_identical"

    if portal_template and biz_l + biz_r <= 1:
        return "template_overlap", "shared_strong_portal_boilerplate"

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


def _length_bin(n: int) -> int:
    if n < 80:
        return 0
    if n < 200:
        return 1
    if n < 500:
        return 2
    if n < 900:
        return 3
    return 4


def _build_indexes(
    items: list[dict[str, Any]],
) -> tuple[dict[str, list[int]], dict[tuple[int, int], list[int]], dict[str, list[int]], dict[str, Any]]:
    exact: dict[str, list[int]] = defaultdict(list)
    bands: dict[tuple[int, int], list[int]] = defaultdict(list)
    gram_df: Counter[str] = Counter()
    item_grams: list[list[str]] = []

    for i, it in enumerate(items):
        raw = it.get("text") or ""
        residual = strip_template_boilerplate(raw)
        norm = normalize_user_text(residual if len(_compact(residual)) >= 40 else raw)
        if not norm:
            item_grams.append([])
            continue
        h = hashlib.sha1(norm.encode("utf-8")).hexdigest()
        it["_norm"] = norm
        it["_hash"] = h
        it["_simhash"] = simhash64(norm)
        it["_len_bin"] = _length_bin(len(norm))
        it["_task"] = it.get("task_type") or "unknown"
        exact[h].append(i)
        sh = it["_simhash"]
        for b in range(4):
            bands[(b, (sh >> (b * 16)) & 0xFFFF)].append(i)
        # Index ALL character grams for this item (DF counted in pass 2)
        chars = re.findall(r"[\u4e00-\u9fff]{2}|[a-z0-9]{3,}", norm)
        # Unique preserve order
        seen: set[str] = set()
        grams: list[str] = []
        for g in chars:
            if g in seen:
                continue
            seen.add(g)
            grams.append(g)
        item_grams.append(grams)
        for g in grams:
            gram_df[g] += 1

    ngrams: dict[str, list[int]] = defaultdict(list)
    for i, grams in enumerate(item_grams):
        # Keep informative grams only; never index ultra-common boilerplate grams as candidates
        useful = [g for g in grams if 2 <= gram_df[g] <= MAX_NGRAM_DF]
        if not useful and grams:
            useful = sorted(grams, key=lambda g: (gram_df[g], g))[:40]
        for g in useful:
            ngrams[g].append(i)

    meta = {
        "grams_total_types": len(gram_df),
        "grams_indexed_types": len(ngrams),
    }
    return exact, bands, ngrams, meta


def _cross_split_pairs(
    idxs: list[int],
    all_items: list[dict[str, Any]],
    split_pairs: tuple[tuple[str, str], ...],
) -> list[tuple[int, int]]:
    by_split: dict[str, list[int]] = defaultdict(list)
    for i in idxs:
        by_split[all_items[i]["split"]].append(i)
    out: list[tuple[int, int]] = []
    for a, b in split_pairs:
        la, lb = by_split.get(a, []), by_split.get(b, [])
        for i in la:
            for j in lb:
                out.append((min(i, j), max(i, j)))
    return out


def _secondary_subbuckets(idxs: list[int], all_items: list[dict[str, Any]]) -> dict[tuple[Any, ...], list[int]]:
    """Sub-bucket high-frequency SimHash/n-gram buckets by length + task + extra hash bits."""
    subs: dict[tuple[Any, ...], list[int]] = defaultdict(list)
    for i in idxs:
        it = all_items[i]
        sh = int(it.get("_simhash") or 0)
        # Extra bands from middle bits
        extra = ((sh >> 8) & 0xFF, (sh >> 24) & 0xFF)
        key = (it.get("_len_bin"), it.get("_task"), extra[0], extra[1])
        subs[key].append(i)
    return subs


def analyze_cross_split_similarity(
    *,
    threshold: int = 95,
    max_pairs_in_report: int = 200,
    splits_override: dict[str, list[dict[str, Any]]] | None = None,
    write_report: bool = True,
) -> dict[str, Any]:
    """Full-scan train/validation/test leakage with candidate recall + RapidFuzz confirm."""
    t0 = time.time()
    settings = get_settings()
    root = settings.datasets_root
    chunks = {c["chunk_id"]: c for c in read_jsonl(root / "interim" / "chunks" / "chunks.jsonl")}
    docs = {d["document_id"]: d for d in read_jsonl(root / "manifests" / "documents.jsonl")}
    if splits_override is not None:
        splits = splits_override
    else:
        splits = {
            "train": read_jsonl(root / "sft" / "train" / "records.jsonl"),
            "validation": read_jsonl(root / "sft" / "validation" / "records.jsonl"),
            "test": read_jsonl(root / "sft" / "test" / "records.jsonl"),
        }

    split_stats: dict[str, Any] = {}
    all_items: list[dict[str, Any]] = []
    records_indexed = 0
    for name, rows in splits.items():
        records_indexed += len(rows)
        items = _collect_split_items(rows, split=name, chunks=chunks, docs=docs)
        all_items.extend(items)
        split_stats[name] = {
            "records": len(rows),
            "projects": len({r.get("project_id") for r in rows if r.get("project_id")}),
            "documents": len({it.get("document_id") for it in items if it.get("document_id")}),
            "chunks": len({it.get("chunk_id") for it in items if it.get("kind") == "chunk" and it.get("chunk_id")}),
            "items": len(items),
        }

    exact_idx, band_idx, ngram_idx, gram_meta = _build_indexes(all_items)
    chunks_indexed = sum(1 for it in all_items if it.get("kind") == "chunk")

    split_pairs = (("train", "validation"), ("train", "test"), ("validation", "test"))
    candidate_keys: set[tuple[int, int]] = set()
    exact_candidates = 0
    simhash_candidates = 0
    ngram_candidates = 0
    skipped_candidates: list[dict[str, Any]] = []
    high_frequency_buckets = 0
    max_bucket_size = 0
    pairwise_spent = 0

    # Exact hash — FULL, never truncated
    for _h, idxs in exact_idx.items():
        if len(idxs) < 2:
            continue
        max_bucket_size = max(max_bucket_size, len(idxs))
        pairs = _cross_split_pairs(idxs, all_items, split_pairs)
        exact_candidates += len(pairs)
        for p in pairs:
            candidate_keys.add(p)

    # SimHash bands — no silent [:80]; secondary bucket oversized groups
    for _key, idxs in band_idx.items():
        if len(idxs) < 2:
            continue
        max_bucket_size = max(max_bucket_size, len(idxs))
        if len(idxs) > MAX_DIRECT_BUCKET:
            high_frequency_buckets += 1
            subs = _secondary_subbuckets(idxs, all_items)
            for sk, sub in subs.items():
                if len(sub) < 2:
                    continue
                if len(sub) > MAX_SECONDARY_BUCKET:
                    # tertiary: residual-text hash prefix
                    tertiary: dict[str, list[int]] = defaultdict(list)
                    for i in sub:
                        norm = all_items[i].get("_norm") or ""
                        tertiary[hashlib.sha1(norm.encode("utf-8")).hexdigest()[:6]].append(i)
                    for tidxs in tertiary.values():
                        if len(tidxs) < 2:
                            continue
                        if len(tidxs) > MAX_SECONDARY_BUCKET:
                            est = 0
                            by_split: dict[str, int] = defaultdict(int)
                            for i in tidxs:
                                by_split[all_items[i]["split"]] += 1
                            for a, b in split_pairs:
                                est += by_split.get(a, 0) * by_split.get(b, 0)
                            skipped_candidates.append(
                                {
                                    "source": "simhash_tertiary",
                                    "bucket_size": len(tidxs),
                                    "estimated_pairs": est,
                                    "reason": "bucket_exceeds_secondary_limit_after_rehash",
                                    "subkey": sk,
                                }
                            )
                            continue
                        pairs = _cross_split_pairs(tidxs, all_items, split_pairs)
                        # Filter by hamming
                        kept = []
                        for i, j in pairs:
                            if hamming64(all_items[i].get("_simhash", 0), all_items[j].get("_simhash", 0)) <= 3:
                                kept.append((i, j))
                        pairwise_spent += len(pairs)
                        simhash_candidates += len(kept)
                        candidate_keys.update(kept)
                    continue
                pairs = _cross_split_pairs(sub, all_items, split_pairs)
                pairwise_spent += len(pairs)
                if pairwise_spent > PAIRWISE_BUDGET:
                    skipped_candidates.append(
                        {
                            "source": "simhash_secondary",
                            "bucket_size": len(sub),
                            "estimated_pairs": len(pairs),
                            "reason": "pairwise_budget_exhausted",
                        }
                    )
                    continue
                kept = [
                    (i, j)
                    for i, j in pairs
                    if hamming64(all_items[i].get("_simhash", 0), all_items[j].get("_simhash", 0)) <= 3
                ]
                simhash_candidates += len(kept)
                candidate_keys.update(kept)
            continue

        pairs = _cross_split_pairs(idxs, all_items, split_pairs)
        pairwise_spent += len(pairs)
        kept = [
            (i, j)
            for i, j in pairs
            if hamming64(all_items[i].get("_simhash", 0), all_items[j].get("_simhash", 0)) <= 3
        ]
        simhash_candidates += len(kept)
        candidate_keys.update(kept)

    # N-gram inverted recall — no [:40] fanout truncation; common grams excluded at index time
    co_count: dict[tuple[int, int], int] = defaultdict(int)
    for _g, idxs in ngram_idx.items():
        if len(idxs) < 2:
            continue
        max_bucket_size = max(max_bucket_size, len(idxs))
        if len(idxs) > MAX_DIRECT_BUCKET:
            high_frequency_buckets += 1
            work_lists = list(_secondary_subbuckets(idxs, all_items).values())
        elif len(idxs) > 80:
            work_lists = list(_secondary_subbuckets(idxs, all_items).values())
        else:
            work_lists = [idxs]
        for work in work_lists:
            if len(work) < 2:
                continue
            if len(work) > MAX_SECONDARY_BUCKET:
                # Tertiary residual-hash bucketing instead of silent truncation
                tertiary: dict[str, list[int]] = defaultdict(list)
                for i in work:
                    norm = all_items[i].get("_norm") or ""
                    tertiary[hashlib.sha1(norm.encode("utf-8")).hexdigest()[:8]].append(i)
                for tidxs in tertiary.values():
                    if len(tidxs) < 2:
                        continue
                    if len(tidxs) > MAX_SECONDARY_BUCKET:
                        by_split_n = defaultdict(int)
                        for i in tidxs:
                            by_split_n[all_items[i]["split"]] += 1
                        est = sum(by_split_n.get(a, 0) * by_split_n.get(b, 0) for a, b in split_pairs)
                        if est == 0:
                            continue
                        skipped_candidates.append(
                            {
                                "source": "ngram",
                                "bucket_size": len(tidxs),
                                "estimated_pairs": est,
                                "reason": "ngram_bucket_too_large_after_tertiary",
                            }
                        )
                        continue
                    pairs = _cross_split_pairs(tidxs, all_items, split_pairs)
                    pairwise_spent += len(pairs)
                    for i, j in pairs:
                        key = (i, j)
                        co_count[key] += 1
                        if co_count[key] >= 3:
                            if key not in candidate_keys:
                                ngram_candidates += 1
                            candidate_keys.add(key)
                continue
            pairs = _cross_split_pairs(work, all_items, split_pairs)
            pairwise_spent += len(pairs)
            if pairwise_spent > PAIRWISE_BUDGET:
                skipped_candidates.append(
                    {
                        "source": "ngram",
                        "bucket_size": len(work),
                        "estimated_pairs": len(pairs),
                        "reason": "pairwise_budget_exhausted",
                    }
                )
                continue
            for i, j in pairs:
                key = (i, j)
                co_count[key] += 1
                if co_count[key] >= 3:
                    if key not in candidate_keys:
                        ngram_candidates += 1
                    candidate_keys.add(key)

    before_dedupe = exact_candidates + simhash_candidates + ngram_candidates
    candidates_deduplicated = len(candidate_keys)

    pairs_out: list[dict[str, Any]] = []
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
        pairs_out.append(
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

    fail_n = 0
    for p in pairs_out:
        if p["kind"] in {"same_project_or_document", "severe_business_overlap"}:
            fail_n += 1
        elif p["kind"] == "exact_duplicate" and "boilerplate" not in (p.get("reason") or ""):
            fail_n += 1

    proj_sets = {k: {r.get("project_id") for r in v if r.get("project_id")} for k, v in splits.items()}
    project_leaks = []
    for a, b in split_pairs:
        inter = proj_sets[a] & proj_sets[b]
        if inter:
            project_leaks.append({"splits": f"{a}/{b}", "count": len(inter), "sample": sorted(inter)[:5]})
            fail_n += len(inter)

    full_scan = len(skipped_candidates) == 0
    ok = fail_n == 0 and not project_leaks and full_scan

    report = {
        "full_scan": full_scan,
        "threshold": threshold,
        "split_stats": split_stats,
        "records_indexed": records_indexed,
        "chunks_indexed": chunks_indexed,
        "chunks_scanned": sum(s["chunks"] for s in split_stats.values()),
        "items_scanned": len(all_items),
        "exact_candidates": exact_candidates,
        "simhash_candidates": simhash_candidates,
        "ngram_candidates": ngram_candidates,
        "candidates_before_dedupe_estimate": before_dedupe,
        "candidates_deduplicated": candidates_deduplicated,
        "candidate_pairs": candidates_deduplicated,
        "precise_comparisons": precise_compared,
        "skipped_candidates": skipped_candidates[:50],
        "skipped_candidates_count": len(skipped_candidates),
        "high_frequency_buckets": high_frequency_buckets,
        "max_bucket_size": max_bucket_size,
        "pairwise_comparisons_budget": PAIRWISE_BUDGET,
        "pairwise_spent": pairwise_spent,
        "gram_index": gram_meta,
        "pair_count": len(pairs_out),
        "kind_counts": dict(kind_counts),
        "exact_duplicate": kind_counts.get("exact_duplicate", 0),
        "same_project_or_document": kind_counts.get("same_project_or_document", 0),
        "template_overlap": kind_counts.get("template_overlap", 0),
        "severe_business_overlap": kind_counts.get("severe_business_overlap", 0),
        "severe_train_test_near_duplicates": kind_counts.get("severe_business_overlap", 0)
        + kind_counts.get("same_project_or_document", 0),
        "project_leaks": project_leaks,
        "pairs": pairs_out[:max_pairs_in_report],
        "ok": ok,
        "fail_count": fail_n,
        "scan_duration_seconds": round(time.time() - t0, 3),
        "note": (
            "full_scan requires every candidate set to be processed without skips; "
            "high-frequency buckets are re-bucketed (length/task/hash) instead of silent truncation; "
            "template_overlap requires low residual similarity after boilerplate strip."
        ),
    }
    if write_report:
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
    """Deprecated path retained for tests: prefer re-clustering + re-split instead.

    When a multi-split component is found, destination is majority-record split,
    never unconditionally train. Floors are best-effort only.
    """
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
        scores = {
            "train": sum(1 for m in members if m in train),
            "validation": sum(1 for m in members if m in validation),
            "test": sum(1 for m in members if m in test),
        }
        occupied = [k for k, v in scores.items() if v > 0]
        if len(occupied) < 2:
            continue
        # Majority member count; ties → lexicographic among occupied (deterministic, not always train)
        dest = sorted(occupied, key=lambda k: (-scores[k], k))[0]
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
