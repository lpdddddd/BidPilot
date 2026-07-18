from __future__ import annotations

import json
import random
import re
from collections import Counter
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from bidpilot_data.logging import get_logger, log_stats
from bidpilot_data.schemas import (
    ChatMessage,
    DatasetSplitManifest,
    DerivationMethod,
    QualityLevel,
    ReviewStatus,
    SFTRecord,
    SFTTaskType,
    SplitName,
)
from bidpilot_data.settings import get_settings, load_pipeline_config, load_sft_tasks
from bidpilot_data.utils import content_fingerprint, ensure_dir, read_jsonl, stable_uuid, write_json, write_jsonl

log = get_logger(__name__)

CLAUSE_TASKS = {
    SFTTaskType.requirement_classify,
    SFTTaskType.qualification_extract,
    SFTTaskType.scoring_extract,
    SFTTaskType.risk_detect,
    SFTTaskType.project_info_extract,
}
CROSS_DOC_TASKS = {
    SFTTaskType.evidence_match,
    SFTTaskType.citation_qa,
    SFTTaskType.tool_call,
}


def _msg_fp(messages: list[ChatMessage]) -> str:
    return content_fingerprint(json.dumps([m.model_dump() for m in messages], ensure_ascii=False, sort_keys=True))


def _assistant_json(obj: dict[str, Any]) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def _cluster_projects_by_shared_chunks(
    records: list[Any],
    chunks: dict[str, dict[str, Any]],
) -> dict[str, str]:
    """Union projects that share exact normalized chunk hashes (potential split leaks).

    Returns mapping project_id -> cluster_root.
    """
    import hashlib
    from bidpilot_data.sft.dedup import normalize_user_text

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

    from bidpilot_data.sft.dedup import hamming64, simhash64

    hash_to_projects: dict[str, set[str]] = {}
    sim_items: list[tuple[int, str, str]] = []  # simhash, norm, pid
    for r in records:
        pid = r.project_id if hasattr(r, "project_id") else r.get("project_id")
        if not pid:
            continue
        find(pid)
        cids = r.source_chunk_ids if hasattr(r, "source_chunk_ids") else (r.get("source_chunk_ids") or [])
        for cid in cids or []:
            ch = chunks.get(cid)
            if not ch:
                continue
            text = (ch.get("text") or "").strip()
            if len(text) < 80:
                continue
            norm = normalize_user_text(text)
            h = hashlib.sha1(norm.encode("utf-8")).hexdigest()
            hash_to_projects.setdefault(h, set()).add(pid)
            if len(norm) >= 100:
                sim_items.append((simhash64(norm), norm[:200], pid))
    for _h, pids in hash_to_projects.items():
        if len(pids) < 2:
            continue
        root = sorted(pids)[0]
        for p in pids:
            union(root, p)
    # Near-duplicate business chunks across projects (SimHash LSH)
    buckets: dict[int, list[tuple[int, str, str]]] = {}
    for sh, norm, pid in sim_items:
        for b in range(4):
            key = (b << 16) | ((sh >> (b * 16)) & 0xFFFF)
            buckets.setdefault(key, []).append((sh, norm, pid))
    for group in buckets.values():
        if len(group) < 2:
            continue
        # Secondary bucket by length prefix to avoid silent [:40] truncation
        subs: dict[int, list[tuple[int, str, str]]] = {}
        for sh, norm, pid in group:
            subs.setdefault(len(norm) // 50, []).append((sh, norm, pid))
        for sub in subs.values():
            for i in range(len(sub)):
                for j in range(i + 1, len(sub)):
                    sh1, n1, p1 = sub[i]
                    sh2, n2, p2 = sub[j]
                    if p1 == p2:
                        continue
                    if hamming64(sh1, sh2) <= 2:
                        union(p1, p2)
    return {p: find(p) for p in parent}


def _split_projects(
    project_ids: list[str],
    seed: int,
    train_r: float,
    val_r: float,
    heldout: int,
    *,
    min_validation: int = 5,
    min_test: int = 10,
    cluster_of: dict[str, str] | None = None,
    test_r: float | None = None,
) -> DatasetSplitManifest:
    """Compatibility wrapper: one synthetic record per project, cluster-weighted assign."""
    from bidpilot_data.sft.split_assign import (
        assign_clusters_weighted,
        build_project_clusters,
        expand_assignment_to_projects,
        make_manifest,
    )

    ids = sorted(set(project_ids))
    cluster_of = cluster_of or {p: p for p in ids}

    class _R:
        def __init__(self, pid: str) -> None:
            self.project_id = pid
            self.task_type = type("T", (), {"value": "requirement_classify"})()
            self.source_urls = []

    records = [_R(p) for p in ids]
    clusters = build_project_clusters(project_ids=ids, cluster_of=cluster_of, records=records, projects={})
    assignment, _diag = assign_clusters_weighted(
        clusters,
        seed=seed,
        train_r=train_r,
        val_r=val_r,
        test_r=float(test_r if test_r is not None else max(0.0, 1.0 - train_r - val_r)),
        min_validation_projects=min_validation,
        min_test_projects=min_test,
        heldout_project_count=heldout,
    )
    train, val, test = expand_assignment_to_projects(clusters, assignment)
    return make_manifest(
        seed=seed,
        train=train,
        validation=val,
        test=test,
        heldout=test,
        extra_counts={"leak_clusters": len(clusters)},
    )


def _extract_deadline(text: str) -> str | None:
    m = re.search(r"(投标截止|递交.*截止|响应文件递交截止)[^。\n]{0,40}?(20\d{2}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日[^\n。]{0,20})", text)
    if m:
        return re.sub(r"\s+", "", m.group(2))
    m = re.search(r"(20\d{2})-(\d{1,2})-(\d{1,2})\s+(\d{1,2}:\d{2})", text)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d} {m.group(4)}"
    return None


def _project_info_answer(project: dict[str, Any], req_text: str) -> dict[str, Any] | None:
    answer = {
        "project_name": project.get("project_name"),
        "purchaser": project.get("purchaser"),
        "budget_cny": project.get("budget_cny"),
        "deadline": _extract_deadline(req_text) or _extract_deadline(json.dumps(project, ensure_ascii=False)),
        "region": project.get("province"),
        "project_code": project.get("project_code"),
    }
    non_null = {k: v for k, v in answer.items() if v not in (None, "", [], {})}
    if len(non_null) < 2:
        return None
    # Keep schema keys but require at least 2 concrete fields filled.
    return answer


def _assistant_nonempty(obj: dict[str, Any]) -> bool:
    if not obj:
        return False
    values = list(obj.values())
    if all(v in (None, "", [], {}) for v in values):
        return False
    return True


def _has_match_evidence(m: dict[str, Any]) -> bool:
    return bool(m.get("evidence_document_id") or m.get("evidence_chunk_id") or m.get("evidence_ids"))


def build_sft_dataset(*, dry_run: bool = False) -> dict[str, Any]:
    settings = get_settings()
    cfg = load_pipeline_config()
    sft_cfg = cfg.get("sft", {})
    tasks_cfg = load_sft_tasks().get("tasks", {})
    seed = int(cfg.get("random_seed", 42))

    from bidpilot_data.labeling.industry import enrich_projects_industry

    projects_raw = read_jsonl(settings.datasets_root / "manifests" / "projects.jsonl")
    chunks_for_ind: dict[str, list[str]] = {}
    for c in read_jsonl(settings.datasets_root / "interim" / "chunks" / "chunks.jsonl"):
        chunks_for_ind.setdefault(c.get("project_id") or "", []).append((c.get("text") or "")[:400])
    projects_enriched = enrich_projects_industry(projects_raw, chunks_for_ind)
    if not dry_run:
        write_jsonl(settings.datasets_root / "manifests" / "projects.jsonl", projects_enriched)
    projects = {p["project_id"]: p for p in projects_enriched}
    docs = {d["document_id"]: d for d in read_jsonl(settings.datasets_root / "manifests" / "documents.jsonl")}

    reqs = read_jsonl(settings.datasets_root / "gold" / "requirements.jsonl") + read_jsonl(
        settings.datasets_root / "silver" / "requirements.jsonl"
    )
    by_req: dict[str, dict[str, Any]] = {}
    for r in reqs:
        rid = r["requirement_id"]
        prev = by_req.get(rid)
        if prev is None or (r.get("quality_level") == "gold" and prev.get("quality_level") != "gold"):
            by_req[rid] = r
    reqs = list(by_req.values())

    matches = read_jsonl(settings.datasets_root / "silver" / "requirement_matches.jsonl")
    ragqs = read_jsonl(settings.datasets_root / "eval" / "rag" / "questions.jsonl")
    agents = read_jsonl(settings.datasets_root / "eval" / "agent" / "tasks.jsonl")

    stats_filter = {
        "candidate_raw": 0,
        "filtered_incomplete_project": 0,
        "filtered_level_c_cross_doc": 0,
        "filtered_no_evidence_match": 0,
        "filtered_unknown_cap": 0,
        "filtered_null_project_info": 0,
        "filtered_empty_assistant": 0,
        "filtered_near_dup": 0,
        "filtered_exact_dup": 0,
        "with_evidence": 0,
        "without_evidence_kept": 0,
    }

    records: list[SFTRecord] = []
    evidence_match_records: list[SFTRecord] = []
    unknown_evidence_match: list[SFTRecord] = []

    def allowed_for_level(task: SFTTaskType, level: str | None) -> bool:
        if level in {None, "incomplete"}:
            return False
        if level == "level_c":
            # Clause tasks + same-project tool traces / citation QA (no cross-project)
            return task in CLAUSE_TASKS or task in {SFTTaskType.tool_call, SFTTaskType.citation_qa}
        if level in {"level_a", "level_b"}:
            return True
        return False

    def add(
        task: SFTTaskType,
        project_id: str,
        system: str,
        user: str,
        assistant_obj: dict[str, Any],
        quality: str,
        review: str,
        *,
        source_document_ids: list[str] | None = None,
        source_chunk_ids: list[str] | None = None,
        source_urls: list[str] | None = None,
        derivation_method: DerivationMethod = DerivationMethod.extract,
        force_unknown_bucket: bool = False,
        messages_override: list[ChatMessage] | None = None,
    ) -> None:
        nonlocal stats_filter
        stats_filter["candidate_raw"] += 1
        # Portal snapshots never enter SFT
        proj_row = projects.get(project_id) or {}
        if proj_row.get("project_code") == "PORTAL_SNAPSHOT":
            stats_filter["filtered_incomplete_project"] += 1
            return
        level = proj_row.get("bundle_level")
        if not allowed_for_level(task, level):
            if level in {None, "incomplete"}:
                stats_filter["filtered_incomplete_project"] += 1
            else:
                stats_filter["filtered_level_c_cross_doc"] += 1
            return
        if messages_override is None and not _assistant_nonempty(assistant_obj):
            stats_filter["filtered_empty_assistant"] += 1
            return
        q = QualityLevel(quality)
        rs = ReviewStatus(review)
        if q == QualityLevel.gold and rs != ReviewStatus.reviewed:
            q = QualityLevel.silver
            rs = ReviewStatus.pending
        if q == QualityLevel.pending:
            q = QualityLevel.silver
        messages = messages_override or [
            ChatMessage(role="system", content=system),
            ChatMessage(role="user", content=user),
            ChatMessage(role="assistant", content=_assistant_json(assistant_obj)),
        ]
        rec = SFTRecord(
            record_id=str(stable_uuid(f"sft:{task.value}:{project_id}:{_msg_fp(messages)}")),
            project_id=project_id,
            source_project_id=project_id,
            source_document_ids=source_document_ids or [],
            source_chunk_ids=source_chunk_ids or [],
            source_urls=source_urls or [],
            derivation_method=derivation_method,
            task_type=task,
            quality_level=q,
            review_status=rs,
            messages=messages,
        )
        has_ev = bool(rec.source_document_ids or rec.source_chunk_ids or rec.source_urls)
        if has_ev:
            stats_filter["with_evidence"] += 1
        else:
            stats_filter["without_evidence_kept"] += 1

        if task == SFTTaskType.evidence_match:
            if force_unknown_bucket:
                unknown_evidence_match.append(rec)
            else:
                evidence_match_records.append(rec)
            return
        records.append(rec)

    for r in reqs:
        pid = r["project_id"]
        proj = projects.get(pid) or {}
        src_docs = [r["document_id"]] if r.get("document_id") else []
        src_chunks = [r["chunk_id"]] if r.get("chunk_id") else []
        src_urls = [r["source_url"]] if r.get("source_url") else []
        sys = tasks_cfg.get("requirement_classify", {}).get("system", "你是招投标文件分析助手。")
        add(
            SFTTaskType.requirement_classify,
            pid,
            sys,
            f"判断以下条款的类别与是否强制：\n{r.get('original_text')}",
            {
                "category": r.get("category"),
                "mandatory": r.get("mandatory"),
                "risk_level": r.get("risk_level"),
                "confidence": r.get("confidence", 0.5),
            },
            r.get("quality_level", "silver"),
            r.get("review_status", "pending"),
            source_document_ids=src_docs,
            source_chunk_ids=src_chunks,
            source_urls=src_urls,
            derivation_method=DerivationMethod.classify,
        )
        if r.get("category") in {"qualification", "performance", "certification", "personnel"}:
            add(
                SFTTaskType.qualification_extract,
                pid,
                tasks_cfg["qualification_extract"]["system"],
                f"抽取资格要求：\n{r.get('original_text')}",
                {
                    "requirements": [r.get("normalized_requirement")],
                    "mandatory": r.get("mandatory"),
                    "evidence_required": r.get("evidence_required", []),
                },
                r.get("quality_level", "silver"),
                r.get("review_status", "pending"),
                source_document_ids=src_docs,
                source_chunk_ids=src_chunks,
                source_urls=src_urls,
            )
        if r.get("category") == "scoring":
            add(
                SFTTaskType.scoring_extract,
                pid,
                tasks_cfg["scoring_extract"]["system"],
                f"抽取评分条目：\n{r.get('original_text')}",
                {"item": r.get("title"), "score": r.get("score"), "method": "综合评分法"},
                r.get("quality_level", "silver"),
                r.get("review_status", "pending"),
                source_document_ids=src_docs,
                source_chunk_ids=src_chunks,
                source_urls=src_urls,
            )
        if r.get("category") in {"mandatory_rejection", "legal"} or r.get("risk_level") in {"high", "critical"}:
            add(
                SFTTaskType.risk_detect,
                pid,
                tasks_cfg["risk_detect"]["system"],
                f"识别风险：\n{r.get('original_text')}",
                {
                    "risk_level": r.get("risk_level"),
                    "risk_type": r.get("category"),
                    "reason": r.get("normalized_requirement"),
                    "is_rejection_clause": r.get("category") == "mandatory_rejection",
                },
                r.get("quality_level", "silver"),
                r.get("review_status", "pending"),
                source_document_ids=src_docs,
                source_chunk_ids=src_chunks,
                source_urls=src_urls,
            )

    # project_info_extract from project metadata + tender text, not null-only dicts
    for pid, proj in projects.items():
        level = proj.get("bundle_level")
        if level not in {"level_a", "level_b", "level_c"}:
            continue
        # Prefer a project_info requirement text; else project name blob
        req_text = next(
            (r.get("original_text") or "" for r in reqs if r.get("project_id") == pid and r.get("category") == "project_info"),
            "",
        )
        if not req_text:
            req_text = f"项目名称：{proj.get('project_name')}\n项目编号：{proj.get('project_code')}\n采购人：{proj.get('purchaser')}\n预算：{proj.get('budget_cny')}"
        answer = _project_info_answer(proj, req_text)
        if answer is None:
            stats_filter["filtered_null_project_info"] += 1
            continue
        src_urls = [proj.get("official_project_url")] if proj.get("official_project_url") else []
        doc_ids = []
        for dref in proj.get("documents") or []:
            if dref.get("document_id"):
                doc_ids.append(dref["document_id"])
            elif dref.get("source_url"):
                for did, d in docs.items():
                    if d.get("source_url") == dref.get("source_url"):
                        doc_ids.append(did)
                        break
        add(
            SFTTaskType.project_info_extract,
            pid,
            tasks_cfg["project_info_extract"]["system"],
            f"从以下官方项目材料中抽取项目要素：\n{req_text[:1200]}",
            answer,
            "silver",
            "pending",
            source_document_ids=doc_ids[:3],
            source_urls=src_urls,
            derivation_method=DerivationMethod.extract,
        )

    # evidence_match: require evidence refs; cap unknown to 10%
    for m in matches:
        req = by_req.get(m["requirement_id"], {})
        pid = req.get("project_id") or m.get("project_id")
        if not pid or pid == "unknown":
            continue
        status = m.get("status")
        has_ev = _has_match_evidence(m)
        if status == "unknown" and not has_ev:
            stats_filter["filtered_no_evidence_match"] += 1
            continue
        if not has_ev and status != "unknown":
            stats_filter["filtered_no_evidence_match"] += 1
            continue
        add(
            SFTTaskType.evidence_match,
            pid,
            tasks_cfg["evidence_match"]["system"],
            f"要求：{req.get('normalized_requirement', m['requirement_id'])}\n请判断匹配状态（仅依据公开证据）。",
            {"status": status, "reason": m.get("reason"), "confidence": m.get("confidence", 0.5)},
            m.get("quality_level", "silver"),
            m.get("review_status", "pending"),
            source_document_ids=[m["evidence_document_id"]] if m.get("evidence_document_id") else [],
            source_chunk_ids=[m["evidence_chunk_id"]] if m.get("evidence_chunk_id") else [],
            derivation_method=DerivationMethod.extract,
            force_unknown_bucket=(status == "unknown"),
        )

    # Cap unknown evidence_match at <=10% of final evidence_match task.
    # If there are no non-unknown matches, keep 0 unknown samples.
    rng = random.Random(seed)
    rng.shuffle(unknown_evidence_match)
    if evidence_match_records:
        max_unknown = max(0, int(len(evidence_match_records) / 9))  # unknown <= 10% => u <= n/9
    else:
        max_unknown = 0
    kept_unknown = unknown_evidence_match[:max_unknown]
    stats_filter["filtered_unknown_cap"] += max(0, len(unknown_evidence_match) - len(kept_unknown))
    records.extend(evidence_match_records)
    records.extend(kept_unknown)

    for q in ragqs:
        # Drop moon-base leftovers if any
        if "月球基地" in (q.get("question") or ""):
            continue
        add(
            SFTTaskType.citation_qa,
            q["project_id"],
            tasks_cfg["citation_qa"]["system"],
            q["question"],
            {
                "answer": q.get("answer"),
                "citations": q.get("gold_chunk_ids", []),
                "answerable": q.get("answerable", False),
            },
            q.get("quality_level", "silver"),
            q.get("review_status", "pending"),
            source_document_ids=list(q.get("source_document_ids") or q.get("gold_document_ids") or []),
            source_chunk_ids=list(q.get("gold_chunk_ids") or []),
            source_urls=list(q.get("source_urls") or []),
            derivation_method=DerivationMethod.grounded_qa,
        )

    from bidpilot_data.agent_data.build import trajectory_messages

    for t in agents:
        tools = t.get("expected_tool_calls") or []
        if not tools:
            continue
        # All tool args must bind project_id when present
        if any((step.get("arguments") or {}).get("project_id") not in {None, t.get("project_id")} for step in tools):
            continue
        expected = t.get("expected_final_result") or {}
        try:
            traj = trajectory_messages(t)
            messages = [ChatMessage.model_validate(m) for m in traj]
        except Exception:  # noqa: BLE001
            continue
        add(
            SFTTaskType.tool_call,
            t["project_id"],
            tasks_cfg.get("tool_call", {}).get("system", "你是招投标工具调用助手。"),
            t["user_request"],
            expected,
            t.get("quality_level", "silver"),
            t.get("review_status", "pending"),
            source_urls=list(expected.get("source_urls") or []),
            source_chunk_ids=list(expected.get("evidence_chunk_ids") or expected.get("citations") or []),
            derivation_method=DerivationMethod.tool_trace,
            messages_override=messages,
        )

    raw_count = len(records)

    from bidpilot_data.sft.dedup import global_near_dedup
    from bidpilot_data.sft.balance import balance_records, load_sft_balance_config

    bal_cfg = load_sft_balance_config()
    sim_cfg = bal_cfg.get("similarity") or {}
    split_cfg = bal_cfg.get("splits") or {}

    def _user_text(rec: SFTRecord) -> str:
        return next(m.content for m in rec.messages if m.role == "user")

    records, dedup_stats = global_near_dedup(
        records,
        get_task=lambda r: r.task_type.value,
        get_user=_user_text,
        get_quality=lambda r: r.quality_level.value,
        get_project=lambda r: r.project_id,
        get_id=lambda r: r.record_id,
        near_threshold=int(sim_cfg.get("near_dup_threshold", 95)),
        simhash_hamming_max=int(sim_cfg.get("simhash_hamming_max", 3)),
        cross_project_template_check=bool(sim_cfg.get("cross_project_template_check", True)),
    )
    stats_filter["filtered_exact_dup"] += dedup_stats.exact_duplicates_removed
    stats_filter["filtered_near_dup"] += dedup_stats.near_duplicates_removed

    before_balance_counts = Counter(r.task_type.value for r in records)

    def _conf(rec: SFTRecord) -> float:
        if rec.quality_level == QualityLevel.gold:
            return 1.0
        try:
            asst = next(m.content for m in rec.messages if m.role == "assistant")
            obj = json.loads(asst)
            return float(obj.get("confidence") or 0.5)
        except Exception:  # noqa: BLE001
            return 0.5

    # Balance before structural filter (downsample only)
    records, balance_report = balance_records(
        records,
        get_task=lambda r: r.task_type.value,
        get_quality=lambda r: r.quality_level.value,
        get_review=lambda r: r.review_status.value,
        get_confidence=_conf,
        has_complete_source=lambda r: bool(r.source_urls and (r.source_chunk_ids or r.source_document_ids)),
        is_test_split_record=lambda r: False,
        protect_gold_in_test=True,
    )

    def _reject_reason(r: SFTRecord) -> str | None:
        assistants = [m.content for m in r.messages if m.role == "assistant"]
        if not assistants:
            return "empty_answer"
        try:
            obj = json.loads(assistants[-1])
        except json.JSONDecodeError:
            return "invalid_assistant_json"
        if r.task_type != SFTTaskType.tool_call and not _assistant_nonempty(obj):
            return "empty_answer"
        if r.task_type == SFTTaskType.tool_call:
            if not (obj.get("answer") or obj.get("citations") or obj.get("clarify")):
                return "empty_answer"
            roles = [m.role for m in r.messages]
            for i, role in enumerate(roles):
                if role == "tool" and (i == 0 or roles[i - 1] != "assistant"):
                    return "invalid_tool_sequence"
        if not (r.source_urls or r.source_document_ids or r.source_chunk_ids):
            return "missing_source"
        if r.task_type in CROSS_DOC_TASKS and not (r.source_chunk_ids or r.source_document_ids):
            return "missing_evidence"
        level = (projects.get(r.project_id) or {}).get("bundle_level")
        if level in {None, "incomplete"}:
            return "incomplete_project"
        if level == "level_c" and r.task_type in CROSS_DOC_TASKS - {SFTTaskType.tool_call, SFTTaskType.citation_qa}:
            return "unsupported_task"
        return None

    rejected_rows: list[dict[str, Any]] = []
    structurally_valid: list[SFTRecord] = []
    reject_reason_counts: Counter[str] = Counter()
    for r in records:
        reason = _reject_reason(r)
        if reason:
            reject_reason_counts[reason] += 1
            rejected_rows.append({**r.model_dump(mode="json"), "reject_reason": reason})
        else:
            structurally_valid.append(r)

    reviewed_trainable = [
        r
        for r in structurally_valid
        if r.review_status == ReviewStatus.reviewed and r.quality_level == QualityLevel.gold
    ]
    silver_candidate = [
        r
        for r in structurally_valid
        if r.quality_level == QualityLevel.silver and r.review_status != ReviewStatus.reviewed
    ]
    rejected_sft = rejected_rows

    # --- Stage: build leak-safe project clusters, weighted split, leak verify, atomic publish ---
    from bidpilot_data.reporting.artifact_meta import (
        attach_artifact_meta,
        make_dataset_build_id,
        sha256_json_obj,
        sha256_jsonl_file,
        try_commit_sha,
        utc_now_iso,
    )
    from bidpilot_data.sft.cross_split import analyze_cross_split_similarity, collect_leaky_project_pairs
    from bidpilot_data.sft.publish import (
        BuildLockError,
        cleanup_staging,
        exclusive_build_lock,
        make_staging_dir,
        publish_staging_to_formal,
        write_split_bundle,
    )
    from bidpilot_data.sft.split_assign import (
        assign_clusters_weighted,
        build_project_clusters,
        expand_assignment_to_projects,
        make_manifest,
        merge_cluster_roots,
    )

    project_ids = [r.project_id for r in structurally_valid if r.project_id and r.project_id != "unknown"]
    chunk_map = {c["chunk_id"]: c for c in read_jsonl(settings.datasets_root / "interim" / "chunks" / "chunks.jsonl")}
    cluster_of = _cluster_projects_by_shared_chunks(structurally_valid, chunk_map)

    train_r = float(sft_cfg.get("train_ratio", 0.8))
    val_r = float(sft_cfg.get("validation_ratio", 0.1))
    test_r = float(sft_cfg.get("test_ratio", 0.1))
    min_validation = int(split_cfg.get("min_validation_projects", 5))
    min_test = int(split_cfg.get("min_test_projects", 10))
    heldout = int(cfg.get("splits", {}).get("heldout_project_count", 10))

    split_diagnostics: dict[str, Any] = {}
    xsim: dict[str, Any] = {}
    train_set: set[str] = set()
    val_set: set[str] = set()
    test_set: set[str] = set()
    splits: dict[str, list[SFTRecord]] = {"train": [], "validation": [], "test": []}

    def _assign_from_clusters(c_of: dict[str, str]) -> tuple[set[str], set[str], set[str], dict[str, Any]]:
        clusters = build_project_clusters(
            project_ids=project_ids,
            cluster_of=c_of,
            records=structurally_valid,
            projects=projects,
        )
        assignment, diag = assign_clusters_weighted(
            clusters,
            seed=seed,
            train_r=train_r,
            val_r=val_r,
            test_r=test_r,
            min_validation_projects=min_validation,
            min_test_projects=min_test,
            heldout_project_count=heldout,
        )
        tr, va, te = expand_assignment_to_projects(clusters, assignment)
        return tr, va, te, diag

    def _materialize(tr: set[str], va: set[str], te: set[str]) -> dict[str, list[SFTRecord]]:
        out: dict[str, list[SFTRecord]] = {"train": [], "validation": [], "test": []}
        for rec in structurally_valid:
            if rec.project_id in tr:
                rec.split = SplitName.train
                rec.is_test_project = False
                out["train"].append(rec)
            elif rec.project_id in va:
                rec.split = SplitName.validation
                rec.is_test_project = False
                out["validation"].append(rec)
            elif rec.project_id in te:
                rec.split = SplitName.test
                rec.is_test_project = True
                out["test"].append(rec)
            else:
                # Orphan → train (must not silently invent test membership)
                rec.split = SplitName.train
                rec.is_test_project = False
                out["train"].append(rec)
                tr.add(rec.project_id)
        return out

    # Iteratively merge leak pairs into clusters and re-split (never dump all conflicts to train).
    for attempt in range(8):
        train_set, val_set, test_set, split_diagnostics = _assign_from_clusters(cluster_of)
        # Protect gold-in-test carve: gold test projects must not also appear in train
        gold_test_projects = {
            r.project_id
            for r in structurally_valid
            if r.quality_level == QualityLevel.gold and r.project_id in test_set
        }
        train_set -= gold_test_projects
        splits = _materialize(train_set, val_set, test_set)
        if {r.project_id for r in splits["train"]} & {r.project_id for r in splits["test"]}:
            raise RuntimeError("train/test project leakage detected")
        if {r.project_id for r in splits["train"]} & {r.project_id for r in splits["validation"]}:
            raise RuntimeError("train/validation project leakage detected")
        if len(splits["train"]) + len(splits["validation"]) + len(splits["test"]) != len(structurally_valid):
            raise RuntimeError("split sum must equal structurally_valid_sft")

        probe = {
            "train": [r.model_dump(mode="json") for r in splits["train"]],
            "validation": [r.model_dump(mode="json") for r in splits["validation"]],
            "test": [r.model_dump(mode="json") for r in splits["test"]],
        }
        xsim = analyze_cross_split_similarity(splits_override=probe, write_report=False)
        if xsim.get("ok") and xsim.get("full_scan"):
            break
        pairs = collect_leaky_project_pairs(xsim)
        if not pairs:
            # full_scan false without project pairs — cannot fix by recluster
            break
        cluster_of = merge_cluster_roots(cluster_of, pairs)
        split_diagnostics = {**split_diagnostics, "recluster_attempt": attempt + 1, "leak_pairs_merged": len(pairs)}

    records = structurally_valid
    by_task = Counter(r.task_type.value for r in records)
    by_task_quality_level: dict[str, dict[str, int]] = {}
    by_task_review_status: dict[str, dict[str, int]] = {}
    for r in records:
        by_task_quality_level.setdefault(r.task_type.value, {"gold": 0, "silver": 0})
        ql = r.quality_level.value if r.quality_level.value in {"gold", "silver"} else "silver"
        by_task_quality_level[r.task_type.value][ql] = by_task_quality_level[r.task_type.value].get(ql, 0) + 1
        by_task_review_status.setdefault(r.task_type.value, {"pending": 0, "reviewed": 0, "rejected": 0})
        rs = r.review_status.value
        if rs not in by_task_review_status[r.task_type.value]:
            by_task_review_status[r.task_type.value][rs] = 0
        by_task_review_status[r.task_type.value][rs] += 1

    for task, n in by_task.items():
        ql = by_task_quality_level.get(task, {})
        assert ql.get("gold", 0) + ql.get("silver", 0) == n, f"quality_level sum mismatch for {task}"
        rs = by_task_review_status.get(task, {})
        assert sum(rs.values()) == n, f"review_status sum mismatch for {task}"

    domain_counter: Counter[str] = Counter()
    domain_ref_counter: Counter[str] = Counter()
    level_counter: Counter[str] = Counter()
    for r in records:
        proj = projects.get(r.project_id) or {}
        level_counter[proj.get("bundle_level") or "unknown"] += 1
        domains_for_rec: list[str] = []
        for url in r.source_urls or []:
            d = urlparse(url).netloc.lower().split(":")[0] or "unknown"
            domain_ref_counter[d] += 1
            domains_for_rec.append(d)
        if not domains_for_rec and proj.get("source_domain"):
            domains_for_rec = [str(proj.get("source_domain"))]
            domain_ref_counter[domains_for_rec[0]] += 1
        if domains_for_rec:
            domain_counter[domains_for_rec[0]] += 1

    by_split_and_task = {
        name: dict(Counter(r.task_type.value for r in items)) for name, items in splits.items()
    }

    em_total = by_task.get("evidence_match", 0)
    em_unknown = sum(
        1
        for r in records
        if r.task_type == SFTTaskType.evidence_match
        and '"status":"unknown"' in next((m.content for m in r.messages if m.role == "assistant"), "")
    )

    manifest = make_manifest(
        seed=seed,
        train=train_set,
        validation=val_set,
        test=test_set,
        heldout=test_set,
        extra_counts={
            "leak_clusters": len(set(cluster_of.values())),
            "split_ratio_within_5pp": 1 if split_diagnostics.get("ratio_within_5pp") else 0,
        },
    )

    def _split_stats(items: list[SFTRecord]) -> dict[str, Any]:
        pids = {r.project_id for r in items}
        return {
            "project_count": len(pids),
            "record_count": len(items),
            "bundle_level": dict(
                Counter((projects.get(pid) or {}).get("bundle_level") or "unknown" for pid in pids)
            ),
            "task_type": dict(Counter(r.task_type.value for r in items)),
            "source_domain": dict(
                Counter(
                    urlparse((r.source_urls or [""])[0]).netloc.lower().split(":")[0] or "unknown"
                    for r in items
                    if r.source_urls
                )
            ),
            "industry": dict(
                Counter((projects.get(r.project_id) or {}).get("industry") or "unknown" for r in items)
            ),
            "quality_level": dict(Counter(r.quality_level.value for r in items)),
        }

    generated_at = utc_now_iso()
    commit_sha = try_commit_sha(settings.repo_root)

    # Provisional hashes from in-memory content for build_id (stable for same records/manifest)
    provisional_source_sha = sha256_json_obj(
        {
            "train": [r.record_id for r in splits["train"]],
            "validation": [r.record_id for r in splits["validation"]],
            "test": [r.record_id for r in splits["test"]],
        }
    )
    manifest_sha = sha256_json_obj(manifest.model_dump(mode="json"))
    dataset_build_id = make_dataset_build_id(
        seed=seed, source_records_sha256=provisional_source_sha, commit_sha=commit_sha
    )

    stats = {
        "candidate_raw": stats_filter["candidate_raw"],
        "after_task_filters": raw_count,
        "deduped": len(records),
        "with_evidence": stats_filter["with_evidence"],
        "filtered_no_evidence": stats_filter["filtered_no_evidence_match"],
        "filters": stats_filter,
        "total": len(records),
        "structurally_valid_sft": len(structurally_valid),
        "reviewed_trainable_sft": len(reviewed_trainable),
        "silver_candidate_sft": len(silver_candidate),
        "rejected_sft": len(rejected_sft),
        "rejected_sft_reasons": dict(reject_reason_counts),
        "effective_trainable_deprecated_alias_of_structurally_valid": len(structurally_valid),
        "train": len(splits["train"]),
        "validation": len(splits["validation"]),
        "test": len(splits["test"]),
        "split_sum_equals_structurally_valid": (
            len(splits["train"]) + len(splits["validation"]) + len(splits["test"]) == len(structurally_valid)
        ),
        "gold": sum(1 for r in records if r.quality_level == QualityLevel.gold),
        "silver": sum(1 for r in records if r.quality_level == QualityLevel.silver),
        "quality_level": {
            "gold": sum(1 for r in records if r.quality_level == QualityLevel.gold),
            "silver": sum(1 for r in records if r.quality_level == QualityLevel.silver),
        },
        "by_task": dict(by_task),
        "by_task_quality_level": by_task_quality_level,
        "by_task_review_status": by_task_review_status,
        "train_projects": len(train_set),
        "validation_projects": len(val_set),
        "test_projects": len(test_set),
        "source_domain_distribution": {
            "record_count": dict(domain_counter),
            "reference_count": dict(domain_ref_counter),
        },
        "bundle_level_distribution": dict(level_counter),
        "evidence_match_unknown_ratio": (em_unknown / em_total) if em_total else 0.0,
        "preferred_target": sft_cfg.get("preferred_target"),
        "gap_to_preferred": max(0, int(sft_cfg.get("preferred_target", 12500)) - len(records)),
        "dedup": {
            "exact_duplicates_removed": dedup_stats.exact_duplicates_removed,
            "near_duplicates_removed": dedup_stats.near_duplicates_removed,
            "cross_project_template_duplicates": dedup_stats.cross_project_template_duplicates,
            "conflicting_gold_records": dedup_stats.conflicting_gold_records[:100],
        },
        "balance": balance_report,
        "split_diagnostics": split_diagnostics,
        "cross_split_similarity": {
            "ok": xsim.get("ok"),
            "full_scan": xsim.get("full_scan"),
            "fail_count": xsim.get("fail_count"),
            "skipped_candidates_count": xsim.get("skipped_candidates_count"),
        },
        "dry_run": dry_run,
        "note": "reviewed_trainable_sft must be gold+reviewed before formal LoRA",
    }

    task_distribution = {
        "before_balance": dict(before_balance_counts),
        "after_balance": dict(by_task),
        "by_task": dict(by_task),
        "by_task_quality_level": by_task_quality_level,
        "by_task_review_status": by_task_review_status,
        "by_split_and_task": by_split_and_task,
        "task_gaps": balance_report.get("task_gaps") or {},
        "dropped_by_balance": balance_report.get("dropped_by_balance") or {},
        "bundle_level_distribution": dict(level_counter),
        "source_domain_distribution": {
            "record_count": dict(domain_counter),
            "reference_count": dict(domain_ref_counter),
        },
        "structurally_valid_sft": len(structurally_valid),
        "reviewed_trainable_sft": len(reviewed_trainable),
        "silver_candidate_sft": len(silver_candidate),
        "rejected_sft": len(rejected_sft),
    }

    split_distribution = {
        "train": _split_stats(splits["train"]),
        "validation": _split_stats(splits["validation"]),
        "test": _split_stats(splits["test"]),
        "gaps": {
            "validation_projects_below_5": max(0, 5 - len(val_set)),
            "test_projects_below_10": max(0, 10 - len(test_set)),
        },
        "split_diagnostics": split_diagnostics,
    }

    dedup_report = {
        "exact_duplicates_removed": dedup_stats.exact_duplicates_removed,
        "near_duplicates_removed": dedup_stats.near_duplicates_removed,
        "cross_project_template_duplicates": dedup_stats.cross_project_template_duplicates,
        "conflicting_gold_records": dedup_stats.conflicting_gold_records[:200],
        "method": "exact_sha1 + simhash64 LSH bands + rapidfuzz token_set_ratio",
    }

    meta_kwargs = {
        "dataset_build_id": dataset_build_id,
        "split_manifest_sha256": manifest_sha,
        "source_records_sha256": provisional_source_sha,
        "commit_sha": commit_sha,
        "generated_at": generated_at,
    }
    stats = attach_artifact_meta(stats, **meta_kwargs)
    task_distribution = attach_artifact_meta(task_distribution, **meta_kwargs)
    split_distribution = attach_artifact_meta(split_distribution, **meta_kwargs)
    dedup_report = attach_artifact_meta(dedup_report, **meta_kwargs)
    xsim = attach_artifact_meta(xsim, **meta_kwargs)

    if dry_run:
        log_stats(
            log,
            "build_sft",
            {
                "total": stats["total"],
                "train": stats["train"],
                "validation": stats["validation"],
                "test": stats["test"],
                "dry_run": True,
            },
        )
        return stats

    lock_path = settings.datasets_root / "reports" / "checkpoints" / "sft_build.lock"
    staging = None
    try:
        with exclusive_build_lock(lock_path):
            staging = make_staging_dir(settings.datasets_root)
            write_split_bundle(
                staging,
                splits=splits,
                rejected=rejected_sft,
                source_bundles={
                    "all": structurally_valid,
                    "structurally_valid": structurally_valid,
                    "reviewed_trainable": reviewed_trainable,
                    "silver_candidate": silver_candidate,
                    "effective": structurally_valid,
                },
            )
            if dedup_stats.conflicting_gold_records:
                write_json(
                    ensure_dir(staging / "review" / "pending") / "conflicting_gold_sft.json",
                    dedup_stats.conflicting_gold_records,
                )

            # Finalize cross_split against staged records (still not formal)
            staged_probe = {
                name: [r.model_dump(mode="json") for r in items] for name, items in splits.items()
            }
            xsim_final = analyze_cross_split_similarity(splits_override=staged_probe, write_report=False)
            xsim_final = attach_artifact_meta(xsim_final, **meta_kwargs)
            stats["cross_split_similarity"] = {
                "ok": xsim_final.get("ok"),
                "full_scan": xsim_final.get("full_scan"),
                "fail_count": xsim_final.get("fail_count"),
                "skipped_candidates_count": xsim_final.get("skipped_candidates_count"),
                "achieved_ratios": split_diagnostics.get("achieved_ratios"),
                "absolute_errors_pp": split_diagnostics.get("absolute_errors_pp"),
                "oversized_clusters": split_diagnostics.get("oversized_clusters"),
            }

            reports_payload = {
                "sft_build_stats.json": stats,
                "task_distribution.json": task_distribution,
                "split_distribution.json": split_distribution,
                "dedup_report.json": dedup_report,
                "cross_split_similarity_report.json": xsim_final,
            }
            publish_staging_to_formal(
                staging=staging,
                datasets_root=settings.datasets_root,
                reports=reports_payload,
                manifest=manifest,
                llamafactory_data_dir=settings.repo_root / "training" / "llamafactory" / "data",
            )
            # Refresh live file hashes after publish (informational; build_id stays stable)
            live_source = {
                "train": sha256_jsonl_file(settings.datasets_root / "sft" / "train" / "records.jsonl"),
                "validation": sha256_jsonl_file(settings.datasets_root / "sft" / "validation" / "records.jsonl"),
                "test": sha256_jsonl_file(settings.datasets_root / "sft" / "test" / "records.jsonl"),
            }
            stats["live_source_records_sha256"] = live_source
            write_json(settings.datasets_root / "reports" / "sft_build_stats.json", stats)
    except BuildLockError:
        raise
    finally:
        if staging is not None:
            cleanup_staging(staging)

    log_stats(
        log,
        "build_sft",
        {
            "total": stats["total"],
            "structurally_valid_sft": stats["structurally_valid_sft"],
            "reviewed_trainable_sft": stats["reviewed_trainable_sft"],
            "train": stats["train"],
            "validation": stats["validation"],
            "test": stats["test"],
            "ratios": split_diagnostics.get("achieved_ratios"),
        },
    )
    return stats


def _update_dataset_info(settings: Any, stats: dict[str, Any]) -> None:
    """Legacy helper retained for imports; publish path now updates dataset_info atomically."""
    info_path = settings.repo_root / "training" / "llamafactory" / "data" / "dataset_info.json"
    info = {}
    if info_path.exists():
        info = json.loads(info_path.read_text(encoding="utf-8"))
    sharegpt_tags = {
        "role_tag": "role",
        "content_tag": "content",
        "user_tag": "user",
        "assistant_tag": "assistant",
        "system_tag": "system",
        "tool_tag": "tool",
    }
    for split in ("train", "validation", "test"):
        name = f"bidpilot_sft_{split}"
        src = settings.datasets_root / "sft" / split / "sharegpt.json"
        dest = settings.repo_root / "training" / "llamafactory" / "data" / f"bidpilot_sft_{split}.json"
        if src.exists():
            dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        info[name] = {
            "file_name": f"bidpilot_sft_{split}.json",
            "formatting": "sharegpt",
            "columns": {"messages": "messages"},
            "tags": sharegpt_tags,
        }
    info["bidpilot_sft_train_qwen3"] = info["bidpilot_sft_train"]
    info_path.write_text(json.dumps(info, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
