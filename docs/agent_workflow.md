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

条件路由集中在 `backend/app/agent/routing.py`。

## 关键策略（`block_on_critical_qualification`）

默认 **`true`**：出现 critical 资格类 finding 时，状态 **`blocked`**，禁止生成含「完全满足」等满足性承诺的草稿。

设为 **`false`**：生成 **risk-only** 草稿（文案明确不含满足性承诺），状态 **`completed_with_warnings`**。

## 持久化与恢复

- 扩展表 `agent_runs`（`current_node` / `graph_version` / `idempotency_key` / `input_json` / `output_summary_json` / `error_code` / `error_summary`）
- 自定义表 `agent_checkpoints`（`thread_id = run_id`，JSON blob）— 单元测试用 LangGraph `MemorySaver`，生产恢复走 DB checkpoint
- 事件：`AgentStep`（`step_index`）+ `ToolCall`（tool_events 摘要，无密钥/全文）
- `Idempotency-Key`：同 project 同 key 返回已有 run
- `POST .../resume`：从最近 checkpoint 继续；已产生的 `compliance_run_id` / `draft_ids` 会复用，避免重复业务对象

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

`search_evidence` / `get_project_context` / `extract_requirements` / `match_company_evidence` / 既有 compliance tools / `generate_proposal_draft` / `get_proposal_draft` / `list_proposal_drafts`

## 限制

- 非法律意见、非人工 gold
- 不编造企业资质；证据不足则 warning / blocked
- **尚未实现**：Step 11 实时时间线 UI、Step 12 评测中心、LoRA

## 前端

项目详情 Tab「Agent 闭环」：启动 / 状态 / 当前节点 / 合规摘要 / 草稿与警告错误；刷新加载 latest。
