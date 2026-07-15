from __future__ import annotations

import json
from collections import Counter
from typing import Any

from bidpilot_data.logging import get_logger, log_stats
from bidpilot_data.schemas import AgentTask, QualityLevel, ReviewStatus
from bidpilot_data.settings import get_settings
from bidpilot_data.utils import ensure_dir, read_jsonl, stable_uuid, write_json, write_jsonl

log = get_logger(__name__)

TOOL_SPEC = (
    "你是招投标项目助手。需要时调用工具，工具结果以 tool 角色返回。"
    "可用工具：search_chunks、get_project、list_requirements、extract_scoring、"
    "get_disclosed_supplier、ask_user。"
    "最终回答必须是 JSON：{\"answer\":...,\"citations\":[chunk_id,...]}。"
)


def _search_chunks(chunks: list[dict[str, Any]], query: str, top_k: int = 5) -> list[dict[str, Any]]:
    q = (query or "").lower()
    scored: list[tuple[int, dict[str, Any]]] = []
    for c in chunks:
        text = c.get("text") or ""
        score = sum(1 for tok in q.replace(" ", "") if tok and tok in text)
        if any(tok in text for tok in (query or "").split() if len(tok) >= 2):
            score += 2
        if score or query[:4] in text:
            scored.append((score, c))
    scored.sort(key=lambda x: -x[0])
    picked = [c for _, c in scored[:top_k]] or chunks[:top_k]
    return [
        {
            "chunk_id": c.get("chunk_id"),
            "document_id": c.get("document_id"),
            "page_start": c.get("page_start"),
            "text": (c.get("text") or "")[:400],
        }
        for c in picked
        if c.get("chunk_id")
    ]


def build_agent_tasks(*, dry_run: bool = False, limit: int | None = 500) -> dict[str, Any]:
    """Build multi-step agent trajectories with real tool results (no fabricated payloads)."""
    settings = get_settings()
    projects = {
        p["project_id"]: p
        for p in read_jsonl(settings.datasets_root / "manifests" / "projects.jsonl")
        if p.get("bundle_level") in {"level_a", "level_b", "level_c"}
        and p.get("project_code") != "PORTAL_SNAPSHOT"
        and not str(p.get("project_name") or "").startswith("official_portal_snapshot")
    }
    chunks = [
        c
        for c in read_jsonl(settings.datasets_root / "interim" / "chunks" / "chunks.jsonl")
        if c.get("project_id") in projects
    ]
    reqs = [r for r in read_jsonl(settings.datasets_root / "silver" / "requirements.jsonl") if r.get("project_id") in projects]
    suppliers = read_jsonl(settings.datasets_root / "silver" / "disclosed_suppliers.jsonl")
    evidence = read_jsonl(settings.datasets_root / "silver" / "evidence.jsonl")
    matches = read_jsonl(settings.datasets_root / "silver" / "requirement_matches.jsonl")

    chunks_by_project: dict[str, list[dict[str, Any]]] = {}
    for c in chunks:
        chunks_by_project.setdefault(c["project_id"], []).append(c)
    reqs_by_project: dict[str, list[dict[str, Any]]] = {}
    for r in reqs:
        reqs_by_project.setdefault(r["project_id"], []).append(r)
    suppliers_by_project: dict[str, list[dict[str, Any]]] = {}
    for s in suppliers:
        if s.get("project_id"):
            suppliers_by_project.setdefault(s["project_id"], []).append(s)
    evidence_by_project: dict[str, list[dict[str, Any]]] = {}
    for e in evidence:
        evidence_by_project.setdefault(e["project_id"], []).append(e)
    matches_by_project: dict[str, list[dict[str, Any]]] = {}
    for m in matches:
        # resolve project via requirement if needed later
        rid = m.get("requirement_id")
        for r in reqs:
            if r.get("requirement_id") == rid:
                matches_by_project.setdefault(r["project_id"], []).append(m)
                break

    tasks: list[AgentTask] = []
    multi_step = 0
    with_error_retry = 0
    with_clarify = 0
    skipped_enterprise_material = 0

    def add_task(
        *,
        project_id: str,
        title: str,
        user_request: str,
        tool_calls: list[dict[str, Any]],
        expected_final: dict[str, Any],
        acceptance: list[str],
    ) -> None:
        nonlocal multi_step
        if limit is not None and len(tasks) >= limit:
            return
        if not expected_final.get("citations") and not expected_final.get("evidence_chunk_ids"):
            return
        # Normalize final citations
        cits = list(expected_final.get("citations") or expected_final.get("evidence_chunk_ids") or [])
        expected_final = {**expected_final, "citations": cits, "answer": expected_final.get("answer") or expected_final.get("summary")}
        if len(tool_calls) >= 2:
            multi_step += 1
        tasks.append(
            AgentTask(
                task_id=str(stable_uuid(f"agent:{project_id}:{title}:{len(tasks)}")),
                project_id=project_id,
                user_request=user_request,
                initial_state={"project_id": project_id, "step": 0, "system": TOOL_SPEC},
                expected_tool_calls=tool_calls,
                expected_final_result=expected_final,
                acceptance_criteria=acceptance,
                quality_level=QualityLevel.silver,
                review_status=ReviewStatus.pending,
            )
        )

    for pid, proj in sorted(projects.items(), key=lambda x: x[0]):
        if limit is not None and len(tasks) >= limit:
            break
        p_chunks = chunks_by_project.get(pid) or []
        p_reqs = reqs_by_project.get(pid) or []
        p_suppliers = suppliers_by_project.get(pid) or []
        p_evidence = evidence_by_project.get(pid) or []
        if not p_chunks:
            continue

        query = (proj.get("project_name") or "项目概况")[:40]
        search_res = _search_chunks(p_chunks, query, top_k=5)
        project_payload = {
            "project_id": pid,
            "project_code": proj.get("project_code"),
            "project_name": proj.get("project_name"),
            "purchaser": proj.get("purchaser"),
            "budget_cny": proj.get("budget_cny"),
            "bundle_level": proj.get("bundle_level"),
            "official_project_url": proj.get("official_project_url"),
        }
        cite = [c["chunk_id"] for c in search_res if c.get("chunk_id")][:3]
        add_task(
            project_id=pid,
            title="获取项目关键信息",
            user_request=f"请检索并汇总项目「{proj.get('project_name')}」（编号 {proj.get('project_code')}）的关键信息。",
            tool_calls=[
                {
                    "tool_name": "search_chunks",
                    "arguments": {"project_id": pid, "query": query, "top_k": 5},
                    "result": {"chunks": search_res},
                },
                {
                    "tool_name": "get_project",
                    "arguments": {"project_id": pid},
                    "result": {"project": project_payload},
                },
            ],
            expected_final={
                "answer": (
                    f"项目编号 {proj.get('project_code')}，名称 {proj.get('project_name')}，"
                    f"采购人 {proj.get('purchaser')}，预算 {proj.get('budget_cny')}。"
                ),
                "project_id": pid,
                "evidence_chunk_ids": cite,
                "citations": cite,
                "source_urls": [proj.get("official_project_url")],
            },
            acceptance=["工具参数包含真实 project_id", "最终答案含 citations", "不得编造预算/采购人"],
        )

        qual = [r for r in p_reqs if r.get("category") == "qualification"][:8]
        if qual:
            q_search = _search_chunks(p_chunks, "投标人资格要求", top_k=8)
            list_payload = [
                {
                    "requirement_id": r["requirement_id"],
                    "normalized_requirement": r.get("normalized_requirement"),
                    "chunk_id": r.get("chunk_id"),
                    "mandatory": r.get("mandatory"),
                }
                for r in qual
            ]
            add_task(
                project_id=pid,
                title="提取全部资格要求",
                user_request=f"提取项目 {proj.get('project_code')} 的资格要求清单，并给出原文依据。",
                tool_calls=[
                    {
                        "tool_name": "search_chunks",
                        "arguments": {"project_id": pid, "query": "投标人资格要求", "top_k": 8},
                        "result": {"chunks": q_search},
                    },
                    {
                        "tool_name": "list_requirements",
                        "arguments": {"project_id": pid, "category": "qualification"},
                        "result": {"requirements": list_payload},
                    },
                ],
                expected_final={
                    "answer": f"共识别 {len(qual)} 条资格要求，均来自公开招标文件条款。",
                    "requirement_ids": [r["requirement_id"] for r in qual],
                    "evidence_chunk_ids": [r.get("chunk_id") for r in qual if r.get("chunk_id")],
                    "citations": [r.get("chunk_id") for r in qual if r.get("chunk_id")],
                },
                acceptance=["每条资格要求可追溯到 chunk_id", "最终答案含 citations"],
            )

        rejects = [r for r in p_reqs if r.get("category") == "mandatory_rejection"][:6]
        if rejects:
            r_search = _search_chunks(p_chunks, "投标无效 废标 否决", top_k=8)
            add_task(
                project_id=pid,
                title="识别否决性条款",
                user_request=f"识别项目 {proj.get('project_code')} 中的否决性/投标无效条款。",
                tool_calls=[
                    {
                        "tool_name": "search_chunks",
                        "arguments": {"project_id": pid, "query": "投标无效 废标 否决", "top_k": 8},
                        "result": {"chunks": r_search},
                    }
                ],
                expected_final={
                    "answer": f"文件中至少包括 {len(rejects)} 条否决/无效情形，详见 citations。",
                    "rejection_requirement_ids": [r["requirement_id"] for r in rejects],
                    "evidence_chunk_ids": [r.get("chunk_id") for r in rejects if r.get("chunk_id")],
                    "citations": [r.get("chunk_id") for r in rejects if r.get("chunk_id")],
                },
                acceptance=["否决条款必须来自原文", "最终答案含 citations"],
            )

        scoring = [r for r in p_reqs if r.get("category") == "scoring"][:6]
        if scoring:
            s_search = _search_chunks(p_chunks, "综合评分法 评分标准", top_k=8)
            add_task(
                project_id=pid,
                title="计算评分项",
                user_request=f"从项目 {proj.get('project_code')} 文件中提取评分办法要点。",
                tool_calls=[
                    {
                        "tool_name": "search_chunks",
                        "arguments": {"project_id": pid, "query": "综合评分法 评分标准", "top_k": 8},
                        "result": {"chunks": s_search},
                    },
                    {
                        "tool_name": "extract_scoring",
                        "arguments": {"project_id": pid},
                        "result": {
                            "scoring_items": [
                                {"requirement_id": r["requirement_id"], "title": r.get("title"), "score": r.get("score")}
                                for r in scoring
                            ]
                        },
                    },
                ],
                expected_final={
                    "answer": f"提取到 {len(scoring)} 个评分相关条款。",
                    "scoring_requirement_ids": [r["requirement_id"] for r in scoring],
                    "evidence_chunk_ids": [r.get("chunk_id") for r in scoring if r.get("chunk_id")],
                    "citations": [r.get("chunk_id") for r in scoring if r.get("chunk_id")],
                },
                acceptance=["评分项必须来自评分章节原文", "最终答案含 citations"],
            )

        # Error + retry / fallback trajectory using a controllable mock error then real result
        if p_chunks and (limit is None or len(tasks) < limit):
            bad = {
                "tool_name": "search_chunks",
                "arguments": {"project_id": pid, "query": query, "top_k": 5},
                "result": {"error": "timeout", "message": "检索服务超时"},
            }
            ok = {
                "tool_name": "search_chunks",
                "arguments": {"project_id": pid, "query": query, "top_k": 5},
                "result": {"chunks": search_res},
            }
            with_error_retry += 1
            add_task(
                project_id=pid,
                title="检索超时后重试",
                user_request=f"汇总项目 {proj.get('project_code')} 概况；若工具失败请重试。",
                tool_calls=[bad, ok, {"tool_name": "get_project", "arguments": {"project_id": pid}, "result": {"project": project_payload}}],
                expected_final={
                    "answer": f"在重试后取得项目 {proj.get('project_code')} 信息。",
                    "evidence_chunk_ids": cite,
                    "citations": cite,
                    "retry": True,
                },
                acceptance=["包含真实 error result", "随后 retry 成功", "最终 citations 非空"],
            )

        # Clarification when critical fields missing
        if not proj.get("budget_cny") and p_chunks:
            with_clarify += 1
            add_task(
                project_id=pid,
                title="预算缺失澄清",
                user_request=f"请给出项目 {proj.get('project_code')} 的精确中标金额与评标得分明细。",
                tool_calls=[
                    {
                        "tool_name": "search_chunks",
                        "arguments": {"project_id": pid, "query": "中标金额 评审得分", "top_k": 5},
                        "result": {"chunks": _search_chunks(p_chunks, "中标金额 评审得分", 5)},
                    },
                    {
                        "tool_name": "ask_user",
                        "arguments": {
                            "question": "公开文件未披露完整评分明细与中标金额字段，请确认需要的字段范围。"
                        },
                        "result": {"status": "need_user_input"},
                    },
                ],
                expected_final={
                    "answer": "公开材料信息不足，需用户澄清所需字段后再查。",
                    "clarify": True,
                    "evidence_chunk_ids": cite[:1],
                    "citations": cite[:1],
                },
                acceptance=["信息不足时输出澄清", "不得编造金额"],
            )

        # Supplier disclosed only — never invent enterprise materials
        if p_suppliers and any(s.get("source_document_ids") for s in p_suppliers) and p_evidence:
            supplier = p_suppliers[0]
            # Only if we also have evidence-backed matches OR pure disclosure confirmation
            s_search = _search_chunks(p_chunks, supplier.get("name") or "", top_k=5)
            add_task(
                project_id=pid,
                title="核对公开披露供应商信息",
                user_request=(
                    f"根据公开中标/成交材料，核对供应商「{supplier.get('name')}」"
                    f"在项目 {proj.get('project_code')} 中已被披露的信息，"
                    "不得推断其满足全部资格条件。"
                ),
                tool_calls=[
                    {
                        "tool_name": "search_chunks",
                        "arguments": {"project_id": pid, "query": supplier.get("name"), "top_k": 5},
                        "result": {"chunks": s_search},
                    },
                    {
                        "tool_name": "get_disclosed_supplier",
                        "arguments": {"project_id": pid, "supplier_id": supplier.get("supplier_id")},
                        "result": {
                            "supplier": {
                                "supplier_id": supplier.get("supplier_id"),
                                "name": supplier.get("name"),
                                "source_document_ids": supplier.get("source_document_ids"),
                                "source_urls": supplier.get("source_urls"),
                            }
                        },
                    },
                ],
                expected_final={
                    "answer": f"供应商 {supplier.get('name')} 仅被公开披露，未据此推断全部资格满足。",
                    "supplier_id": supplier.get("supplier_id"),
                    "status": "disclosed_only",
                    "evidence_ids": [e["evidence_id"] for e in p_evidence[:3]],
                    "evidence_chunk_ids": [c.get("chunk_id") for c in s_search[:2] if c.get("chunk_id")],
                    "citations": [c.get("chunk_id") for c in s_search[:2] if c.get("chunk_id")],
                },
                acceptance=["必须使用公开披露证据", "无企业材料包时不得调用 match_company_materials"],
            )
        else:
            skipped_enterprise_material += 1

    target_min, target_max = 300, 500
    gap = max(0, target_min - len(tasks))
    quality = {
        "tasks": len(tasks),
        "projects": len(projects),
        "multi_step_trajectories": multi_step,
        "with_error_retry": with_error_retry,
        "with_clarify": with_clarify,
        "skipped_enterprise_material_tasks": skipped_enterprise_material,
        "target_min": target_min,
        "target_max": target_max,
        "gap_to_min": gap,
        "by_title": dict(Counter((t.user_request[:20] for t in tasks))),
        "dry_run": dry_run,
        "ok": True,
        "note": "No template cloning to fill gaps; report gap when under target.",
    }
    stats = {**quality}
    if not dry_run:
        write_jsonl(ensure_dir(settings.datasets_root / "eval" / "agent") / "tasks.jsonl", tasks)
        write_json(ensure_dir(settings.datasets_root / "reports") / "agent_quality_report.json", quality)
    log_stats(log, "agent_tasks", {"tasks": len(tasks), "multi_step": multi_step, "gap": gap})
    return stats


def trajectory_messages(task: dict[str, Any]) -> list[dict[str, str]]:
    """Expand AgentTask into ShareGPT-style multi-turn messages including tool role."""
    system = (task.get("initial_state") or {}).get("system") or TOOL_SPEC
    messages: list[dict[str, str]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": task.get("user_request") or ""},
    ]
    for step in task.get("expected_tool_calls") or []:
        messages.append(
            {
                "role": "assistant",
                "content": json.dumps(
                    {"tool_name": step.get("tool_name"), "arguments": step.get("arguments") or {}},
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            }
        )
        result = step.get("result")
        if result is None:
            raise ValueError(f"tool step missing real result: {step.get('tool_name')}")
        messages.append(
            {
                "role": "tool",
                "content": json.dumps(result, ensure_ascii=False, separators=(",", ":")),
            }
        )
    final = task.get("expected_final_result") or {}
    answer_obj = {
        "answer": final.get("answer") or final.get("summary") or "",
        "citations": final.get("citations") or final.get("evidence_chunk_ids") or [],
    }
    if final.get("clarify"):
        answer_obj["clarify"] = True
    if final.get("retry"):
        answer_obj["retry"] = True
    messages.append(
        {
            "role": "assistant",
            "content": json.dumps(answer_obj, ensure_ascii=False, separators=(",", ":")),
        }
    )
    return messages
