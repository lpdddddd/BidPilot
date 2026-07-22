# BidPilot LangGraph Agent 业务闭环（Step 10）

BidPilot Step 10 用 **LangGraph StateGraph** 编排既有业务能力，形成可恢复的招投标分析闭环。节点只做编排，**不写 SQL**；合规检查**不调用 LLM**，复用 Step 9 的 `ComplianceService` / `app.tools.compliance_tools`。

## 图版本

`GRAPH_VERSION = bidpilot-agent-1.0.0`

## 节点顺序

```text
initialize_run
  → load_project_context
  → retrieve_evidence
  → extract_requirements
  → match_company_evidence
  → run_compliance_check
  → generate_response_draft
  → validate_draft
  → (revise_draft × max 2) ←──┐
  → finalize_run              │
       ↑──────────────────────┘
```

条件路由集中在 `backend/app/agent/routing.py`。`MAX_DRAFT_REVISE=2`，可用 `metadata.max_draft_revise` 覆盖。

## 关键策略（`block_on_critical_qualification`）

默认 **`true`**：出现 critical 资格类 finding 时，状态 **`blocked`**，禁止生成含「完全满足」等满足性承诺的草稿。

设为 **`false`**：生成 **risk-only** 草稿（文案明确不含满足性承诺），状态 **`completed_with_warnings`**。

## 草稿校验（正式合规路径）

`validate_draft` 对每个 `draft_id` 调用 `check_draft_compliance`（默认 categories：`draft_safety` + `consistency`），经 `ComplianceService.start_run` 跑 D*/E* 规则（含 E005 跨项目归属）。结构化 finding 写入 `draft_findings`；仅当存在 `status=fail` 且 `severity ∈ {error, critical}` 时 `draft_validation_ok=False`（warnings 默认不失败）。在 `critical_qualification` / `forbid_satisfaction_claims` 下若正文仍含强满足性措辞，追加 agent 级补充 finding `AGENT_SUPPLEMENT_strong_claim`。`force_draft_validation` 仅保留给旧单测。

`revise_draft` 读取 `draft_findings`，写入 remediation / risk-only 元数据后重生成，并清掉 `validate_draft` 的 completed 标记以便再次正式校验。

## Checkpoint / Resume / Retry

- **thread_id**：始终 `str(run.id)`（start / resume / retry 相同，禁止随机）。
- **completed_nodes**：每个节点成功结束后写入；resume 时已完成节点直接 `skipped_completed`，不再调用下游服务。
- **DbCheckpointStore**：每节点后持久化 `current_node`、`completed_nodes`、`retry_counts`、业务对象 ID（`compliance_run_id` / `draft_ids` 等），并尽量把 LangGraph `MemorySaver` 序列化为 `lg_memory`（JSON-safe / base64，`checkpoint_seq` 打破同事务 `created_at` 平局）。
- **resume**：加载最近 DB checkpoint → 清 interrupt 标志 → `status=running` → 同 `thread_id` 继续；以 `completed_nodes` 跳过已完成节点（避免重复调用服务）。进程重启后使用新的 in-process `MemorySaver`，业务恢复依赖 DB checkpoint + `completed_nodes`（`lg_memory` 仍会落库，`restore_memory_saver` 可供将来兼容恢复）。二次 resume 对已完成 run 幂等返回。
- **retry（同 run_id）**：`metadata.retry_attempt++`，记录 `retry_of_status`，清 errors，仅从 `completed_nodes` 移除失败节点（`current_node`）后重跑；保留更早 completed 与已有业务对象，避免重复创建。
- 扩展表 `agent_runs` / `agent_checkpoints`；事件：`AgentStep.step_index`（从 0 递增）+ `ToolCall`（tool_events 摘要，无密钥/全文）。
- `Idempotency-Key`：同 project 同 key 返回已有 run。

## API

| Method | Path |
|--------|------|
| POST | `/api/v1/projects/{project_id}/agent-runs` |
| GET | `/api/v1/projects/{project_id}/agent-runs` |
| GET | `/api/v1/projects/{project_id}/agent-runs/latest` |
| GET | `/api/v1/projects/{project_id}/agent-runs/{run_id}` |
| GET | `/api/v1/projects/{project_id}/agent-runs/{run_id}/events` |
| GET | `/api/v1/projects/{project_id}/agent-runs/{run_id}/result` |
| POST | `/api/v1/projects/{project_id}/agent-runs/{run_id}/resume` |
| POST | `/api/v1/projects/{project_id}/agent-runs/{run_id}/retry` |
| GET | `/api/v1/agent-runs/{run_id}` (+ events/result/resume/retry) |
| GET | `.../events/stream` | SSE **stub**（完整实时时间线见 Step 11） |

## Tools

`search_evidence` / `get_project_context` / `extract_requirements` / `match_company_evidence` / 既有 compliance tools（含 `check_draft_compliance`） / `generate_proposal_draft` / `get_proposal_draft` / `list_proposal_drafts`

## 限制

- 非法律意见、非人工 gold
- 不编造企业资质；证据不足则 warning / blocked
- **尚未实现**：Step 11 实时时间线 UI、Step 12 评测中心、LoRA

## 前端

项目详情 Tab「Agent 闭环」：启动 / 状态 / 当前节点 / 合规摘要 / 草稿与警告错误；刷新加载 latest。
