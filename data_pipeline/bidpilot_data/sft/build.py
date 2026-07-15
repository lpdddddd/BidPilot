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


def _split_projects(
    project_ids: list[str],
    seed: int,
    train_r: float,
    val_r: float,
    heldout: int,
    *,
    min_validation: int = 5,
    min_test: int = 10,
) -> DatasetSplitManifest:
    """Project-level split with validation/test floors. Never sample-level random split."""
    ids = sorted(set(project_ids))
    rng = random.Random(seed)
    rng.shuffle(ids)
    n = len(ids)
    if n < min_validation + min_test + 1:
        # Best-effort: still enforce mutual exclusion; report may flag gaps.
        min_test = min(min_test, max(1, n // 3))
        min_validation = min(min_validation, max(1, (n - min_test) // 3))
    held_n = min(max(heldout, min_test), max(0, n - min_validation - 1))
    held = ids[:held_n]
    remain = ids[held_n:]
    n_remain = len(remain)
    n_val = max(min_validation, int(n_remain * val_r))
    n_val = min(n_val, max(0, n_remain - 1))
    n_train = max(0, n_remain - n_val)
    # Prefer ratio if it still meets floor
    ratio_train = int(n_remain * train_r)
    if ratio_train >= 1 and n_remain - ratio_train >= min_validation:
        n_train = ratio_train
        n_val = n_remain - n_train
    train = remain[:n_train]
    val = remain[n_train:]
    test = sorted(set(held))
    # Ensure min test by moving from train if needed
    while len(test) < min_test and train:
        test.append(train.pop())
    test = sorted(set(test))
    while len(val) < min_validation and train:
        val.append(train.pop())
    return DatasetSplitManifest(
        seed=seed,
        created_at=datetime.now(timezone.utc),
        train_project_ids=sorted(train),
        validation_project_ids=sorted(val),
        test_project_ids=test,
        heldout_project_ids=sorted(held),
        counts={
            "train_projects": len(train),
            "validation_projects": len(val),
            "test_projects": len(test),
            "total_projects": n,
        },
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

    # Project-level split ONLY on structurally valid records
    project_ids = [r.project_id for r in structurally_valid if r.project_id and r.project_id != "unknown"]
    manifest = _split_projects(
        project_ids,
        seed=seed,
        train_r=float(sft_cfg.get("train_ratio", 0.8)),
        val_r=float(sft_cfg.get("validation_ratio", 0.1)),
        heldout=int(cfg.get("splits", {}).get("heldout_project_count", 10)),
        min_validation=int(split_cfg.get("min_validation_projects", 5)),
        min_test=int(split_cfg.get("min_test_projects", 10)),
    )
    train_set = set(manifest.train_project_ids)
    val_set = set(manifest.validation_project_ids)
    test_set = set(manifest.test_project_ids)
    gold_test_projects = {
        r.project_id for r in structurally_valid if r.quality_level == QualityLevel.gold and r.project_id in test_set
    }
    train_set -= gold_test_projects
    manifest.train_project_ids = sorted(train_set)

    splits: dict[str, list[SFTRecord]] = {"train": [], "validation": [], "test": []}
    for rec in structurally_valid:
        if rec.project_id in train_set:
            rec.split = SplitName.train
            rec.is_test_project = False
            splits["train"].append(rec)
        elif rec.project_id in val_set:
            rec.split = SplitName.validation
            splits["validation"].append(rec)
        else:
            rec.split = SplitName.test
            rec.is_test_project = rec.project_id in test_set
            splits["test"].append(rec)

    if {r.project_id for r in splits["train"]} & {r.project_id for r in splits["test"]}:
        raise RuntimeError("train/test project leakage detected")
    if {r.project_id for r in splits["train"]} & {r.project_id for r in splits["validation"]}:
        raise RuntimeError("train/validation project leakage detected")
    if len(splits["train"]) + len(splits["validation"]) + len(splits["test"]) != len(structurally_valid):
        raise RuntimeError("split sum must equal structurally_valid_sft")

    records = structurally_valid  # downstream stats/export use valid-only
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

    # Consistency assertions (valid-only)
    for task, n in by_task.items():
        ql = by_task_quality_level.get(task, {})
        assert ql.get("gold", 0) + ql.get("silver", 0) == n, f"quality_level sum mismatch for {task}"
        rs = by_task_review_status.get(task, {})
        assert sum(rs.values()) == n, f"review_status sum mismatch for {task}"
    assert len(splits["train"]) + len(splits["validation"]) + len(splits["test"]) == len(structurally_valid)
    assert sum(by_task.values()) == len(structurally_valid)

    domain_counter: Counter[str] = Counter()  # record_count (primary domain per record)
    domain_ref_counter: Counter[str] = Counter()  # reference_count across all urls
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
        # Deprecated alias — do not treat as formally train-ready gold
        "effective_trainable_deprecated_alias_of_structurally_valid": len(structurally_valid),
        "train": len(splits["train"]),
        "validation": len(splits["validation"]),
        "test": len(splits["test"]),
        "split_sum_equals_structurally_valid": (
            len(splits["train"]) + len(splits["validation"]) + len(splits["test"]) == len(structurally_valid)
        ),
        "gold": sum(1 for r in records if r.quality_level == QualityLevel.gold),
        "silver": sum(1 for r in records if r.quality_level == QualityLevel.silver),
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
        "dry_run": dry_run,
        "note": "reviewed_trainable_sft must be gold+reviewed before formal LoRA",
    }

    task_distribution = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
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

    # Per-split distribution report
    def _split_stats(name: str, items: list[SFTRecord]) -> dict[str, Any]:
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

    split_distribution = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "train": _split_stats("train", splits["train"]),
        "validation": _split_stats("validation", splits["validation"]),
        "test": _split_stats("test", splits["test"]),
        "gaps": {
            "validation_projects_below_5": max(0, 5 - len(val_set)),
            "test_projects_below_10": max(0, 10 - len(test_set)),
        },
    }

    dedup_report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "exact_duplicates_removed": dedup_stats.exact_duplicates_removed,
        "near_duplicates_removed": dedup_stats.near_duplicates_removed,
        "cross_project_template_duplicates": dedup_stats.cross_project_template_duplicates,
        "conflicting_gold_records": dedup_stats.conflicting_gold_records[:200],
        "method": "exact_sha1 + simhash64 LSH bands + rapidfuzz token_set_ratio",
    }

    if not dry_run:
        src = ensure_dir(settings.datasets_root / "sft" / "source")
        write_jsonl(src / "all.jsonl", structurally_valid)
        write_jsonl(src / "structurally_valid.jsonl", structurally_valid)
        write_jsonl(src / "reviewed_trainable.jsonl", reviewed_trainable)
        write_jsonl(src / "silver_candidate.jsonl", silver_candidate)
        write_jsonl(src / "effective.jsonl", structurally_valid)
        write_jsonl(ensure_dir(settings.datasets_root / "rejected") / "sft.jsonl", rejected_sft)
        if dedup_stats.conflicting_gold_records:
            write_json(
                ensure_dir(settings.datasets_root / "review" / "pending") / "conflicting_gold_sft.json",
                dedup_stats.conflicting_gold_records,
            )
        for name, items in splits.items():
            payload = [{"messages": [m.model_dump() for m in r.messages]} for r in items]
            out_dir = ensure_dir(settings.datasets_root / "sft" / name)
            write_json(out_dir / "sharegpt.json", payload)
            write_jsonl(out_dir / "records.jsonl", items)
        write_json(settings.datasets_root / "manifests" / "sft_split_manifest.json", manifest)
        write_json(settings.datasets_root / "reports" / "sft_build_stats.json", stats)
        write_json(settings.datasets_root / "reports" / "task_distribution.json", task_distribution)
        write_json(settings.datasets_root / "reports" / "dedup_report.json", dedup_report)
        write_json(settings.datasets_root / "reports" / "split_distribution.json", split_distribution)
        from bidpilot_data.sft.cross_split import analyze_cross_split_similarity

        stats["cross_split_similarity"] = analyze_cross_split_similarity()
        _update_dataset_info(settings, stats)

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
        },
    )
    return stats


def _update_dataset_info(settings: Any, stats: dict[str, Any]) -> None:
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
