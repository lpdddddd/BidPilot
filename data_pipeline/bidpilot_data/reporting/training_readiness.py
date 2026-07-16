"""Tiered training readiness gates (human review / pilot LoRA / formal LoRA)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from bidpilot_data.settings import get_settings, load_pipeline_config
from bidpilot_data.utils import ensure_dir, read_jsonl, write_json


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def build_training_readiness_report() -> dict[str, Any]:
    settings = get_settings()
    root = settings.datasets_root
    cfg = load_pipeline_config()
    reports = root / "reports"

    sft = _load_json(reports / "sft_build_stats.json")
    rag = _load_json(reports / "rag_quality_report.json")
    rag_val = _load_json(reports / "rag_validation_report.json")
    agent = _load_json(reports / "agent_quality_report.json")
    val = _load_json(reports / "validation_report.json")
    xsim = _load_json(reports / "cross_split_similarity_report.json")
    lf = _load_json(reports / "llamafactory_real_validation.json")
    stats = _load_json(reports / "dataset_statistics.json")

    matches = read_jsonl(root / "silver" / "requirement_matches.jsonl")
    projects = [
        p
        for p in read_jsonl(root / "manifests" / "projects.jsonl")
        if p.get("project_code") != "PORTAL_SNAPSHOT"
    ]
    level_a = sum(1 for p in projects if p.get("bundle_level") == "level_a")
    level_b = sum(1 for p in projects if p.get("bundle_level") == "level_b")

    structurally_valid = int(sft.get("structurally_valid_sft") or 0)
    reviewed = int(sft.get("reviewed_trainable_sft") or 0)
    rejected = int(sft.get("rejected_sft") or 0)
    gold_req = int(stats.get("gold_requirements") or 0) if stats else 0
    # Prefer sft gold counts if present
    gold_sft = 0
    ql = sft.get("quality_level") or {}
    if isinstance(ql, dict):
        gold_sft = int(ql.get("gold") or 0)

    train = int(sft.get("train") or 0)
    validation = int(sft.get("validation") or 0)
    test = int(sft.get("test") or 0)
    split_ok = (train + validation + test == structurally_valid) and structurally_valid > 0

    # Domains from split_distribution or sft stats
    split_dist = _load_json(reports / "split_distribution.json")
    domains: set[str] = set()
    for split_name in ("train", "validation", "test"):
        block = split_dist.get(split_name) or {}
        for d in (block.get("source_domain") or {}):
            if d and "portal" not in d.lower():
                domains.add(d)

    task_types = set((sft.get("task_distribution_after_balance") or sft.get("by_task") or {}).keys())
    if not task_types and split_dist.get("train"):
        task_types = set((split_dist["train"].get("task_type") or {}).keys())

    lf_external = lf.get("external_llamafactory_validation") or "not_run"
    lf_preprocess = bool(lf.get("preprocess_executed"))
    lf_internal_ok = bool((lf.get("internal") or {}).get("ok") if lf.get("internal") is not None else lf.get("ok"))
    # When new merged schema present
    if "internal" in lf:
        lf_internal_ok = bool((lf.get("internal") or {}).get("ok"))
    else:
        # legacy flat report without preprocess
        lf_internal_ok = bool(lf.get("ok")) and lf_external not in {
            "blocked_dependency_missing",
            "not_run",
            "tags_checked_only_no_training",
        }

    rag_ok = bool(rag.get("ok")) and bool(rag_val.get("ok", True))
    xsim_ok = bool(xsim.get("ok")) and bool(xsim.get("full_scan", False))
    val_ok = bool(val.get("ok"))

    target_metrics = {
        "pilot_reviewed_gold_sft_min": 500,
        "pilot_source_domains_min": 5,
        "pilot_task_types_min": 5,
        "formal_reviewed_gold_sft_min": int((cfg.get("sft") or {}).get("target_min") or 10000),
        "formal_requirement_matches_min": 1,
        "formal_rag_min": int((cfg.get("rag_eval") or {}).get("target_min") or 500),
        "formal_agent_min": int((cfg.get("agent_tasks") or {}).get("target_min") or 300),
        "formal_level_a_min": int((cfg.get("projects") or {}).get("level_a_min") or 20),
        "formal_level_b_min": int((cfg.get("projects") or {}).get("level_b_min") or 40),
    }

    current_metrics = {
        "structurally_valid_sft": structurally_valid,
        "reviewed_trainable_sft": reviewed,
        "rejected_sft": rejected,
        "gold_sft": gold_sft,
        "gold_requirements": gold_req,
        "train": train,
        "validation": validation,
        "test": test,
        "split_sum_equals_structurally_valid": split_ok,
        "source_domains": sorted(domains),
        "source_domain_count": len(domains),
        "task_types": sorted(task_types),
        "task_type_count": len(task_types),
        "requirement_matches": len(matches),
        "rag_questions": int(rag.get("questions") or 0),
        "agent_tasks": int(agent.get("tasks") or 0),
        "level_a": level_a,
        "level_b": level_b,
        "rag_ok": rag_ok,
        "validation_ok": val_ok,
        "cross_split_full_scan_ok": xsim_ok,
        "llamafactory_internal_ok": lf_internal_ok,
        "llamafactory_external_status": lf_external,
        "llamafactory_preprocess_executed": lf_preprocess,
        "multi_section_dual_answer_pass": rag.get("multi_section_dual_answer_pass"),
    }

    gates: dict[str, bool] = {}
    warnings: list[str] = []
    blocked: list[str] = []
    passed: list[str] = []

    def gate(name: str, cond: bool, *, block_msg: str | None = None) -> None:
        gates[name] = bool(cond)
        if cond:
            passed.append(name)
        else:
            blocked.append(name if not block_msg else f"{name}: {block_msg}")

    # Human review stage
    gate("human_structurally_valid_sft", structurally_valid > 0)
    gate("human_rejected_excluded_from_splits", split_ok and rejected >= 0)
    gate("human_project_split_no_leak", not (xsim.get("project_leaks") or []) and val_ok)
    gate("human_rag_quality_ok", rag_ok)
    gate("human_sft_internal_format_ok", lf_internal_ok or bool((lf.get("internal") or {}).get("ok", False)))

    ready_for_human_review = all(
        gates[k]
        for k in (
            "human_structurally_valid_sft",
            "human_rejected_excluded_from_splits",
            "human_rag_quality_ok",
        )
    ) and structurally_valid > 0
    # Soft-fail internal if report missing but validate ok
    if not gates.get("human_sft_internal_format_ok") and val_ok:
        warnings.append("llamafactory internal report incomplete; using validation_report.ok as soft signal")
        ready_for_human_review = ready_for_human_review and val_ok

    # Pilot LoRA
    gate("pilot_reviewed_gold_ge_500", reviewed >= 500 and gold_sft >= 500, block_msg="Gold/reviewed_trainable=0 forbidden for training")
    gate("pilot_domains_ge_5", len(domains) >= 5)
    gate("pilot_task_types_ge_5", len(task_types) >= 5)
    gate("pilot_project_mutex", not (xsim.get("project_leaks") or []))
    gate(
        "pilot_llamafactory_preprocess",
        lf_preprocess and lf_external == "passed",
        block_msg=f"external={lf_external}",
    )
    ready_for_pilot_lora = all(
        gates[k]
        for k in (
            "pilot_reviewed_gold_ge_500",
            "pilot_domains_ge_5",
            "pilot_task_types_ge_5",
            "pilot_project_mutex",
            "pilot_llamafactory_preprocess",
        )
    )

    # Formal LoRA
    gate("formal_reviewed_gold_ge_target", reviewed >= target_metrics["formal_reviewed_gold_sft_min"] and gold_sft > 0)
    gate("formal_requirement_matches_with_evidence", len(matches) >= target_metrics["formal_requirement_matches_min"])
    gate("formal_rag_agent_mins", int(rag.get("questions") or 0) >= target_metrics["formal_rag_min"] and int(agent.get("tasks") or 0) >= target_metrics["formal_agent_min"])
    gate("formal_level_ab", level_a >= target_metrics["formal_level_a_min"] and level_b >= target_metrics["formal_level_b_min"])
    gate("formal_full_cross_split", xsim_ok)
    gate("formal_lf_preprocess", lf_preprocess and lf_external == "passed")
    ready_for_formal_lora = all(
        gates[k]
        for k in (
            "formal_reviewed_gold_ge_target",
            "formal_requirement_matches_with_evidence",
            "formal_rag_agent_mins",
            "formal_level_ab",
            "formal_full_cross_split",
            "formal_lf_preprocess",
        )
    ) and ready_for_pilot_lora

    # Hard rule: Gold=0 / reviewed=0 never trainable
    if gold_sft == 0 or reviewed == 0:
        ready_for_pilot_lora = False
        ready_for_formal_lora = False
        warnings.append("Gold=0 or reviewed_trainable_sft=0 ⇒ training gates closed")

    if lf_external in {"blocked_dependency_missing", "not_run", "tags_checked_only_no_training"}:
        warnings.append(f"LLaMAFactory external preprocess not completed ({lf_external})")

    if ready_for_formal_lora:
        stage = "ready_for_formal_lora"
        next_action = "Proceed to formal LoRA only after reconfirming human Gold review lock."
    elif ready_for_pilot_lora:
        stage = "ready_for_pilot_lora"
        next_action = "Run small-scale pilot LoRA with reviewed Gold≥500; keep formal blocked."
    elif ready_for_human_review:
        stage = "ready_for_human_review"
        next_action = (
            "Start human review of silver requirements/RAG/Agent/SFT; "
            "collect result-class docs for RequirementMatch; install LLaMAFactory and rerun preprocess."
        )
    else:
        stage = "blocked"
        next_action = "Fix failing human-review gates before labeling."

    report = {
        "stage": stage,
        "ready_for_human_review": ready_for_human_review,
        "ready_for_pilot_lora": ready_for_pilot_lora,
        "ready_for_formal_lora": ready_for_formal_lora,
        "passed_gates": passed,
        "blocked_gates": blocked,
        "warnings": warnings,
        "current_metrics": current_metrics,
        "target_metrics": target_metrics,
        "recommended_next_action": next_action,
        "gates": gates,
    }
    write_json(ensure_dir(reports) / "training_readiness_report.json", report)
    return report
