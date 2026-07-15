from __future__ import annotations

from typing import Any

from bidpilot_data.logging import get_logger, log_stats
from bidpilot_data.schemas import AgentTask, QualityLevel, ReviewStatus
from bidpilot_data.settings import get_settings
from bidpilot_data.utils import ensure_dir, read_jsonl, stable_uuid, write_jsonl

log = get_logger(__name__)


def build_agent_tasks(*, dry_run: bool = False, limit: int | None = 36) -> dict[str, Any]:
    """Build agent tasks bound to real project metadata, tools, and evidence."""
    settings = get_settings()
    projects = {
        p["project_id"]: p
        for p in read_jsonl(settings.datasets_root / "manifests" / "projects.jsonl")
        if p.get("bundle_level") in {"level_a", "level_b", "level_c"}
    }
    chunks = read_jsonl(settings.datasets_root / "interim" / "chunks" / "chunks.jsonl")
    reqs = read_jsonl(settings.datasets_root / "silver" / "requirements.jsonl")
    suppliers = read_jsonl(settings.datasets_root / "silver" / "disclosed_suppliers.jsonl")
    evidence = read_jsonl(settings.datasets_root / "silver" / "evidence.jsonl")

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

    tasks: list[AgentTask] = []

    def add_task(
        *,
        project_id: str,
        title: str,
        user_request: str,
        tool_calls: list[dict[str, Any]],
        expected_final: dict[str, Any],
        acceptance: list[str],
    ) -> None:
        if limit is not None and len(tasks) >= limit:
            return
        tasks.append(
            AgentTask(
                task_id=str(stable_uuid(f"agent:{project_id}:{title}:{len(tasks)}")),
                project_id=project_id,
                user_request=user_request,
                initial_state={"project_id": project_id, "step": 0},
                expected_tool_calls=tool_calls,
                expected_final_result=expected_final,
                acceptance_criteria=acceptance,
                quality_level=QualityLevel.silver,
                review_status=ReviewStatus.pending,
            )
        )

    for pid, proj in projects.items():
        if limit is not None and len(tasks) >= limit:
            break
        p_chunks = chunks_by_project.get(pid) or []
        p_reqs = reqs_by_project.get(pid) or []
        p_suppliers = suppliers_by_project.get(pid) or []
        p_evidence = evidence_by_project.get(pid) or []
        if not p_chunks:
            continue

        seed_chunk = p_chunks[0]
        query = (proj.get("project_name") or "项目概况")[:40]
        add_task(
            project_id=pid,
            title="获取项目关键信息",
            user_request=f"请检索并汇总项目「{proj.get('project_name')}」（编号 {proj.get('project_code')}）的关键信息。",
            tool_calls=[
                {
                    "tool_name": "search_chunks",
                    "arguments": {"project_id": pid, "query": query, "top_k": 5},
                },
                {
                    "tool_name": "get_project",
                    "arguments": {"project_id": pid},
                },
            ],
            expected_final={
                "project_id": pid,
                "project_code": proj.get("project_code"),
                "project_name": proj.get("project_name"),
                "purchaser": proj.get("purchaser"),
                "budget_cny": proj.get("budget_cny"),
                "evidence_chunk_ids": [seed_chunk.get("chunk_id")],
                "source_urls": [proj.get("official_project_url")],
            },
            acceptance=[
                "工具参数必须包含真实 project_id",
                "返回字段须能在项目元数据或 chunk 中验证",
                "不得编造预算/采购人",
            ],
        )

        qual = [r for r in p_reqs if r.get("category") == "qualification"][:8]
        if qual:
            add_task(
                project_id=pid,
                title="提取全部资格要求",
                user_request=f"提取项目 {proj.get('project_code')} 的资格要求清单，并给出原文依据。",
                tool_calls=[
                    {
                        "tool_name": "search_chunks",
                        "arguments": {"project_id": pid, "query": "投标人资格要求", "top_k": 8},
                    },
                    {
                        "tool_name": "list_requirements",
                        "arguments": {"project_id": pid, "category": "qualification"},
                    },
                ],
                expected_final={
                    "project_id": pid,
                    "requirement_ids": [r["requirement_id"] for r in qual],
                    "evidence_chunk_ids": [r.get("chunk_id") for r in qual if r.get("chunk_id")],
                },
                acceptance=["每条资格要求可追溯到 chunk_id", "不得引入文件外常识"],
            )

        rejects = [r for r in p_reqs if r.get("category") == "mandatory_rejection"][:6]
        if rejects:
            add_task(
                project_id=pid,
                title="识别否决性条款",
                user_request=f"识别项目 {proj.get('project_code')} 中的否决性/投标无效条款。",
                tool_calls=[
                    {
                        "tool_name": "search_chunks",
                        "arguments": {
                            "project_id": pid,
                            "query": "投标无效 废标 否决",
                            "top_k": 8,
                        },
                    }
                ],
                expected_final={
                    "project_id": pid,
                    "rejection_requirement_ids": [r["requirement_id"] for r in rejects],
                    "evidence_chunk_ids": [r.get("chunk_id") for r in rejects if r.get("chunk_id")],
                },
                acceptance=["否决条款必须来自原文", "无证据不得标注 critical"],
            )

        scoring = [r for r in p_reqs if r.get("category") == "scoring"][:6]
        if scoring:
            add_task(
                project_id=pid,
                title="计算评分项",
                user_request=f"从项目 {proj.get('project_code')} 文件中提取评分办法要点。",
                tool_calls=[
                    {
                        "tool_name": "search_chunks",
                        "arguments": {"project_id": pid, "query": "综合评分法 评分标准", "top_k": 8},
                    },
                    {
                        "tool_name": "extract_scoring",
                        "arguments": {"project_id": pid},
                    },
                ],
                expected_final={
                    "project_id": pid,
                    "scoring_requirement_ids": [r["requirement_id"] for r in scoring],
                    "evidence_chunk_ids": [r.get("chunk_id") for r in scoring if r.get("chunk_id")],
                },
                acceptance=["评分项必须来自评分章节原文"],
            )

        # Company-material match only when supplier disclosed with source documents
        if p_suppliers and any(s.get("source_document_ids") for s in p_suppliers) and p_evidence:
            supplier = p_suppliers[0]
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
                        "arguments": {
                            "project_id": pid,
                            "query": supplier.get("name"),
                            "top_k": 5,
                        },
                    },
                    {
                        "tool_name": "get_disclosed_supplier",
                        "arguments": {
                            "project_id": pid,
                            "supplier_id": supplier.get("supplier_id"),
                        },
                    },
                ],
                expected_final={
                    "project_id": pid,
                    "supplier_id": supplier.get("supplier_id"),
                    "supplier_name": supplier.get("name"),
                    "status": "disclosed_only",
                    "evidence_ids": [e["evidence_id"] for e in p_evidence[:3]],
                    "note": "仅确认公开披露，不推断资格满足",
                },
                acceptance=[
                    "必须使用公开披露证据",
                    "不得因中标推断全部资格满足",
                    "无企业材料包时不得调用 match_company_materials",
                ],
            )

    stats = {
        "tasks": len(tasks),
        "projects": len(projects),
        "with_supplier_tasks": sum(1 for t in tasks if "供应商" in t.user_request),
        "dry_run": dry_run,
    }
    if not dry_run:
        write_jsonl(ensure_dir(settings.datasets_root / "eval" / "agent") / "tasks.jsonl", tasks)
    log_stats(log, "agent_tasks", stats)
    return stats
