from __future__ import annotations

import json
import random
from datetime import datetime, timezone
from typing import Any

from rapidfuzz import fuzz

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


def _msg_fp(messages: list[ChatMessage]) -> str:
    return content_fingerprint(json.dumps([m.model_dump() for m in messages], ensure_ascii=False, sort_keys=True))


def _assistant_json(obj: dict[str, Any]) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def _split_projects(project_ids: list[str], seed: int, train_r: float, val_r: float, heldout: int) -> DatasetSplitManifest:
    ids = sorted(set(project_ids))
    rng = random.Random(seed)
    rng.shuffle(ids)
    held = ids[: min(heldout, max(0, len(ids) // 3))] if heldout else []
    remain = [i for i in ids if i not in held]
    n = len(remain)
    n_train = int(n * train_r)
    n_val = int(n * val_r)
    train = remain[:n_train]
    val = remain[n_train : n_train + n_val]
    test = remain[n_train + n_val :] + held
    return DatasetSplitManifest(
        seed=seed,
        created_at=datetime.now(timezone.utc),
        train_project_ids=train,
        validation_project_ids=val,
        test_project_ids=sorted(set(test)),
        heldout_project_ids=held,
    )


def build_sft_dataset(*, dry_run: bool = False) -> dict[str, Any]:
    settings = get_settings()
    cfg = load_pipeline_config()
    sft_cfg = cfg.get("sft", {})
    tasks_cfg = load_sft_tasks().get("tasks", {})
    seed = int(cfg.get("random_seed", 42))

    reqs = read_jsonl(settings.datasets_root / "gold" / "requirements.jsonl") + read_jsonl(
        settings.datasets_root / "silver" / "requirements.jsonl"
    )
    # Dedup by requirement_id preferring gold
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

    records: list[SFTRecord] = []

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
    ) -> None:
        q = QualityLevel(quality)
        rs = ReviewStatus(review)
        # Model/silver cannot become gold here.
        if q == QualityLevel.gold and rs != ReviewStatus.reviewed:
            q = QualityLevel.silver
            rs = ReviewStatus.pending
        messages = [
            ChatMessage(role="system", content=system),
            ChatMessage(role="user", content=user),
            ChatMessage(role="assistant", content=_assistant_json(assistant_obj)),
        ]
        record = SFTRecord(
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
        records.append(record)

    for r in reqs:
        sys = tasks_cfg.get("requirement_classify", {}).get("system", "你是招投标文件分析助手。")
        src_docs = [r["document_id"]] if r.get("document_id") else []
        src_chunks = [r["chunk_id"]] if r.get("chunk_id") else []
        src_urls = [r["source_url"]] if r.get("source_url") else []
        add(
            SFTTaskType.requirement_classify,
            r["project_id"],
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
                r["project_id"],
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
                r["project_id"],
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
                r["project_id"],
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
        if r.get("category") == "project_info":
            add(
                SFTTaskType.project_info_extract,
                r["project_id"],
                tasks_cfg["project_info_extract"]["system"],
                f"抽取项目信息：\n{r.get('original_text')}",
                {
                    "project_name": None,
                    "purchaser": None,
                    "budget_cny": None,
                    "deadline": None,
                    "region": None,
                    "raw": r.get("normalized_requirement"),
                },
                r.get("quality_level", "silver"),
                r.get("review_status", "pending"),
                source_document_ids=src_docs,
                source_chunk_ids=src_chunks,
                source_urls=src_urls,
            )

    for m in matches:
        req = by_req.get(m["requirement_id"], {})
        add(
            SFTTaskType.evidence_match,
            req.get("project_id") or "unknown",
            tasks_cfg["evidence_match"]["system"],
            f"要求：{req.get('normalized_requirement', m['requirement_id'])}\n请判断匹配状态。",
            {"status": m["status"], "reason": m["reason"], "confidence": m.get("confidence", 0.5)},
            m.get("quality_level", "silver"),
            m.get("review_status", "pending"),
            source_document_ids=[m["evidence_document_id"]] if m.get("evidence_document_id") else [],
            source_chunk_ids=[m["evidence_chunk_id"]] if m.get("evidence_chunk_id") else [],
            derivation_method=DerivationMethod.extract,
        )

    for q in ragqs:
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

    for t in agents:
        tools = t.get("expected_tool_calls") or []
        first = tools[0] if tools else {"tool_name": "search_chunks", "arguments": {}}
        add(
            SFTTaskType.tool_call,
            t["project_id"],
            tasks_cfg["tool_call"]["system"],
            t["user_request"],
            {"tool_name": first.get("tool_name"), "arguments": first.get("arguments", {})},
            t.get("quality_level", "silver"),
            t.get("review_status", "pending"),
            derivation_method=DerivationMethod.tool_trace,
        )

    # Exact dedup
    unique: dict[str, SFTRecord] = {}
    for rec in records:
        unique[_msg_fp(rec.messages)] = rec
    records = list(unique.values())

    # Near-dedup within same task_type
    kept: list[SFTRecord] = []
    for rec in records:
        user = next(m.content for m in rec.messages if m.role == "user")
        if any(
            k.task_type == rec.task_type and fuzz.token_set_ratio(user, next(m.content for m in k.messages if m.role == "user")) >= 97
            for k in kept
        ):
            continue
        kept.append(rec)
    records = kept

    project_ids = [r.project_id for r in records if r.project_id and r.project_id != "unknown"]
    manifest = _split_projects(
        project_ids,
        seed=seed,
        train_r=float(sft_cfg.get("train_ratio", 0.8)),
        val_r=float(sft_cfg.get("validation_ratio", 0.1)),
        heldout=int(cfg.get("splits", {}).get("heldout_project_count", 10)),
    )
    train_set = set(manifest.train_project_ids)
    val_set = set(manifest.validation_project_ids)
    test_set = set(manifest.test_project_ids)

    # Gold test projects must not enter train
    gold_test_projects = {
        r.project_id
        for r in records
        if r.quality_level == QualityLevel.gold and r.project_id in test_set
    }
    train_set -= gold_test_projects
    manifest.train_project_ids = sorted(train_set)

    splits: dict[str, list[SFTRecord]] = {"train": [], "validation": [], "test": []}
    for rec in records:
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

    # Leakage assert
    if {r.project_id for r in splits["train"]} & {r.project_id for r in splits["test"]}:
        raise RuntimeError("train/test project leakage detected")

    stats = {
        "total": len(records),
        "train": len(splits["train"]),
        "validation": len(splits["validation"]),
        "test": len(splits["test"]),
        "gold": sum(1 for r in records if r.quality_level == QualityLevel.gold),
        "silver": sum(1 for r in records if r.quality_level == QualityLevel.silver),
        "by_task": {},
        "train_projects": len(train_set),
        "validation_projects": len(val_set),
        "test_projects": len(test_set),
        "preferred_target": sft_cfg.get("preferred_target"),
        "gap_to_preferred": max(0, int(sft_cfg.get("preferred_target", 12500)) - len(records)),
        "dry_run": dry_run,
    }
    for t in SFTTaskType:
        stats["by_task"][t.value] = sum(1 for r in records if r.task_type == t)

    if not dry_run:
        src = ensure_dir(settings.datasets_root / "sft" / "source")
        write_jsonl(src / "all.jsonl", records)
        for name, items in splits.items():
            # LLaMAFactory ShareGPT list JSON
            payload = [{"messages": [m.model_dump() for m in r.messages]} for r in items]
            out_dir = ensure_dir(settings.datasets_root / "sft" / name)
            write_json(out_dir / "sharegpt.json", payload)
            write_jsonl(out_dir / "records.jsonl", items)
        write_json(settings.datasets_root / "manifests" / "sft_split_manifest.json", manifest)
        _update_dataset_info(settings, stats)

    log_stats(log, "build_sft", stats)
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
    }
    # Register relative to datasets export mirrored into training data via path notes
    for split in ("train", "validation", "test"):
        name = f"bidpilot_sft_{split}"
        # Copy lightweight JSON into training/llamafactory/data for LF convenience
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
    write_json(settings.datasets_root / "reports" / "sft_build_stats.json", stats)
