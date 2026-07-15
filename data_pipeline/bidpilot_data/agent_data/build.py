from __future__ import annotations

from typing import Any

from bidpilot_data.logging import get_logger, log_stats
from bidpilot_data.schemas import AgentTask, QualityLevel, ReviewStatus
from bidpilot_data.settings import get_settings
from bidpilot_data.utils import ensure_dir, read_jsonl, stable_uuid, write_jsonl

log = get_logger(__name__)

TEMPLATES = [
    ("获取项目关键信息", ["search_chunks", "get_project"], {"summary_fields": ["project_name", "purchaser", "budget"]}),
    ("提取全部资格要求", ["search_chunks", "list_requirements"], {"category": "qualification"}),
    ("检查企业材料是否齐全", ["list_requirements", "match_company_materials"], {"status_filter": ["missing", "partially_satisfied"]}),
    ("识别否决性条款", ["search_chunks"], {"keywords": ["投标无效", "废标", "否决"]}),
    ("计算评分项", ["search_chunks", "extract_scoring"], {"need_scores": True}),
    ("比较多个企业方案", ["list_company_profiles", "match_company_materials"], {"compare": True}),
    ("请求缺失材料", ["match_company_materials", "ask_user"], {"ask_for": "missing_evidence"}),
    ("调用检索工具", ["search_chunks"], {"top_k": 5}),
    ("调用数据库工具", ["db_query_requirements"], {"table": "requirements"}),
    ("多步骤审查", ["search_chunks", "list_requirements", "match_company_materials", "summarize_risks"], {"pipeline": "compliance"}),
    ("工具异常后的重试或降级", ["search_chunks", "fallback_keyword_search"], {"retry": 1, "fallback": True}),
    ("信息不足时向用户澄清", ["ask_user"], {"clarify": True}),
]


def build_agent_tasks(*, dry_run: bool = False, limit: int | None = 36) -> dict[str, Any]:
    settings = get_settings()
    projects = sorted({d.get("project_id") for d in read_jsonl(settings.datasets_root / "manifests" / "documents.jsonl") if d.get("project_id")})
    if not projects:
        projects = sorted({r.get("project_id") for r in read_jsonl(settings.datasets_root / "silver" / "requirements.jsonl") if r.get("project_id")})
    tasks: list[AgentTask] = []
    idx = 0
    target = limit if limit is not None else len(TEMPLATES) * max(len(projects), 1)
    while len(tasks) < target and projects:
        for project_id in projects:
            if len(tasks) >= target:
                break
            title, tools, result = TEMPLATES[idx % len(TEMPLATES)]
            idx += 1
            tasks.append(
                AgentTask(
                    task_id=str(stable_uuid(f"agent:{project_id}:{title}:{idx}")),
                    project_id=project_id,
                    user_request=f"请针对项目 {project_id}：{title}。",
                    initial_state={"project_id": project_id, "step": 0},
                    expected_tool_calls=[{"tool_name": t, "arguments": {"project_id": project_id}} for t in tools],
                    expected_final_result=result,
                    acceptance_criteria=[
                        "工具调用顺序合理",
                        "最终结果可追溯到检索/数据库证据",
                        "信息不足时发起澄清而非臆造",
                    ],
                    quality_level=QualityLevel.silver,
                    review_status=ReviewStatus.pending,
                )
            )

    stats = {"tasks": len(tasks), "projects": len(projects), "dry_run": dry_run}
    if not dry_run:
        write_jsonl(ensure_dir(settings.datasets_root / "eval" / "agent") / "tasks.jsonl", tasks)
    log_stats(log, "agent_tasks", stats)
    return stats
