# BidPilot LangGraph Agent 业务闭环（Step 10 + 11）

BidPilot Step 10 用 **LangGraph StateGraph** 编排既有业务能力，形成可恢复的招投标分析闭环。Step 11 在此之上实现 **Realtime Agent 执行时间线**（异步启动、真实 tool 生命周期、短提交可见性、SSE / 轮询、前端时间线面板）。节点只做编排，**不写 SQL**；合规检查**不调用 LLM**，复用 Step 9 的 `ComplianceService` / `app.tools.compliance_tools`。

---

## Step 10 — LangGraph 业务闭环

### 图版本

`GRAPH_VERSION = bidpilot-agent-1.0.0`

### 节点顺序

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

### 阻断策略（`block_on_critical_qualification`）

默认 **`true`**：出现 critical 资格类 finding 时，状态 **`blocked`**，禁止生成含「完全满足」等满足性承诺的草稿。

设为 **`false`**：生成 **risk-only** 草稿（文案明确不含满足性承诺），状态 **`completed_with_warnings`**。

### 草稿校验（正式合规路径）

`validate_draft` 对每个 `draft_id` 调用 `check_draft_compliance`（默认 categories：`draft_safety` + `consistency`），经 `ComplianceService.start_run` 跑 D*/E* 规则（含 E005 跨项目归属）。结构化 finding 写入 `draft_findings`；仅当存在 `status=fail` 且 `severity ∈ {error, critical}` 时 `draft_validation_ok=False`（warnings 默认不失败）。在 `critical_qualification` / `forbid_satisfaction_claims` 下若正文仍含强满足性措辞，追加 agent 级补充 finding `AGENT_SUPPLEMENT_strong_claim`。`force_draft_validation` 仅保留给旧单测。

`revise_draft` 读取 `draft_findings`，写入 remediation / risk-only 元数据后重生成，并清掉 `validate_draft` 的 completed 标记以便再次正式校验。

### Checkpoint / Resume / Retry

- **thread_id**：始终 `str(run.id)`（start / resume / retry 相同，禁止随机）。
- **completed_nodes**：每个节点成功结束后写入；resume 时已完成节点直接 `skipped_completed`，不再调用下游服务。这是**跨进程可恢复的主机制**。
- **DbCheckpointStore**：每节点后持久化 `current_node`、`completed_nodes`、`retry_counts`、业务对象 ID（`compliance_run_id` / `draft_ids` 等），并尽量把 LangGraph `MemorySaver` 序列化为 `lg_memory`（`checkpoint_seq` 打破同事务 `created_at` 平局）。
- **lg_memory**：体积允许时写入 `mode=full` 完整 dump；过大则退化为 `mode=compact` 观测摘要。`completed_nodes` 始终是耐久恢复路径；full dump 仅用于加速真正的 checkpointer continue。
- **resume**：同 run / 同 `thread_id`，从 checkpoint 续跑（清 interrupt → `status=running`）。
  - 若 `lg_memory` 可完整还原：`graph.update_state` 合并清理后的状态后 `stream(None)` 从 checkpointer 位置续跑。
  - 若仅有 compact / 还原失败：新 `MemorySaver` + 从 START 重放，靠 `completed_nodes` 跳过已完成节点（避免重复调用服务）。
  - 二次 resume 对已完成 run 幂等返回。
- **retry（同 run_id）**：`metadata.retry_attempt++`，记录 `retry_of_status`，清 errors，仅从 `completed_nodes` 移除失败节点（`current_node`）后**从 START 重跑并 skip**；保留更早 completed 与已有业务对象，避免重复创建。
- **异步默认**：resume / retry API 默认 **prepare + `BackgroundTasks`**（先写状态再后台续跑）；测试可用 **`?sync=true`** 请求内同步执行。
- 扩展表 `agent_runs` / `agent_checkpoints` / **`agent_events`**（统一时间线）。
- **统一事件模型**（`AgentEvent`）：同一 run 内所有可展示事件共享严格单调递增的 `sequence`，由 `AgentRun.event_sequence` 在行锁下原子分配；唯一约束 `(agent_run_id, sequence)`；冲突有限重试。
- **事件类型**：`node_started` / `tool_started` / `tool_completed` / `tool_failed` / `node_completed` / `node_failed` / `run_resumed` / `run_completed` / `run_failed`。
- **ToolCall ↔ AgentStep**：每次工具调用写入 `ToolCall`（含逻辑 `call_id`、`agent_step_id`、`node_name`、`attempt`、起止时间、安全摘要），并在统一事件流中夹在对应 `node_started` 与 `node_completed` / `node_failed` 之间。不保存密钥、连接串、完整 PDF 或大段敏感正文。
- **resume 后 sequence**：继续原 run 计数器，禁止从 0 重开；已完成节点跳过时不重复产生执行事件。
- `AgentStep.step_index` 仍表示节点执行序号；**时间线排序唯一来源是 `AgentEvent.sequence`**（禁止 `10000+i` 等临时偏移）。
- `Idempotency-Key`：同 project 同 key 返回已有 run。
- **项目作用域**：项目路径与裸 `/api/v1/agent-runs/...`（必填 `project_id`）均校验归属；`project_id` 不匹配 → **404**。

### Tools

`search_evidence` / `get_project_context` / `extract_requirements` / `match_company_evidence` / 既有 compliance tools（含 `check_draft_compliance`） / `generate_proposal_draft` / `get_proposal_draft` / `list_proposal_drafts`

### API（Step 10 核心）

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
| GET | `/api/v1/agent-runs/{run_id}` (+ events/result/resume/retry/stream；**必填** `project_id`，不匹配 404) |

`events` 默认按 `sequence` 升序，并以 `occurred_at` / `id` 兜底。每条含：`sequence`、`event_type`、`node_name`、`tool_name`、`status`、`timestamp`、`duration_ms`、`safe_summary`、`agent_step_id`、`tool_call_id`、`attempt`。

---

## Step 11 — Realtime Agent 执行时间线

Realtime Agent 执行时间线**已实现**：异步跑图、真实 tool / 节点生命周期、短 DB 提交的中途可见性、统一 `AgentEvent` 序列、SSE 流与轮询回退、前端 `AgentLoopPanel`。以下含 Step 11 wrap-up 硬化约定。

### 异步启动

- `POST .../agent-runs` **先**创建并持久化 `AgentRun`，快速返回 `run_id` / `thread_id`（= `str(run.id)`）/ `events_stream_path`；图在 FastAPI **`BackgroundTasks`** 中执行（`agent_tasks.run_agent_execute`）。
- **resume / retry** 同样默认：先 prepare（写状态）再 `BackgroundTasks`；测试可用 **`?sync=true`** 请求内同步执行。
- 同一 `run_id` 的重复启动由进程内 `is_execute_running` **去重**，不会开第二个 executor。
- 请求头 `Idempotency-Key`：同 project 同 key 仍返回已有 run（Step 10 语义）。

### 事件持久化失败（`EventPersistError`）

`*_started`（`node_started` / `tool_started`）持久化失败时：

- **不执行** tool / node 业务体
- **rollback** 当前事务
- run 置 **failed**，仅写入 **`safe_error_summary`**
- 抛出 **`EventPersistError`**，**不得 suppress**

### 节点生命周期

每次尝试（含重试）均发 **`node_started`**（`attempt` 从 **1** 起）：

| 结果 | 事件 |
|------|------|
| 成功 | `node_completed` |
| 失败 / 可重试 | 先发 `node_failed`，再进入下一 attempt |
| 未捕获异常 | `node_failed` + `run_failed` |

失败 attempt **不会**发 `node_completed`。

### 真实 tool 生命周期（`run_tool`）

`backend/app/agent/nodes/_helpers.py` 的 **`run_tool()`** 包裹真实调用：

1. **`tool_started` BEFORE** 调用体；短 `commit`，其他 Session 可见（persist 失败则见上，不调 body）
2. 执行工具函数
3. **`tool_completed` / `tool_failed` AFTER**；再短 commit

约定：

- **`attempt` 从 1 起**（与节点 attempt 对齐）
- **逻辑 `call_id`** 跨重试稳定（同 run / 节点 / 工具 / 调用序号）
- 每个 attempt 一条物理 **`ToolCall`** 行
- 事件 **`idempotency_key` 含 attempt**（配合 `(agent_run_id, idempotency_key)` 部分唯一约束）

`ToolCall` 通过 `agent_step_id` **关联 `AgentStep`**。

### `safe_error_summary`

统一安全摘要：状态 / checkpoint / 事件 / API / SSE **均不得**泄露 secrets、tokens、URLs、绝对路径、traceback、prompts、原始 tool args。

### 中途可见性

`node_started`、tool 起止、`node_completed` / `node_failed` 后均做短 DB commit。第二个 SQLAlchemy Session 可在 run 进行中读到已提交事件。**真实图 mid-run 可见性测试用 barrier 同步，不用 `sleep`**（见 `test_real_graph_midrun_visibility_with_barrier` 等）。

### AgentEvent 序列

- 行锁 + 原子计数器：`AgentRun.event_sequence`
- 唯一约束 `(agent_run_id, sequence)`
- **resume** 继续原计数器；已完成节点 `skipped_completed`，**不重发**生命周期事件

### 项目作用域

项目路径 `.../projects/{project_id}/agent-runs/...` 与裸路径 `/api/v1/agent-runs/...`（查询参数 **`project_id` 必填**）均校验 run 归属；不匹配 → **404**。

### SSE

`GET .../agent-runs/{run_id}/events/stream`（项目路径与 `/api/v1/agent-runs/{run_id}/events/stream`）

- SSE `id` = `sequence`；支持 **`Last-Event-ID`** 与查询参数 **`after_sequence`**
- 先 catch-up 历史，再短会话轮询等待新事件
- **`heartbeat`**（约 5s，**不占** sequence）
- 短生命周期 DB session，不长期持锁
- 终态（completed / completed_with_warnings / blocked / failed / cancelled）刷尾事件后发 `run_status` + `done` 并关闭流
- 仅安全字段：`sequence` / `event_type` / `node_name` / `tool_name` / `status` / `timestamp` / `duration_ms` / `safe_summary` / `agent_step_id` / `tool_call_id` / `attempt`

前端（`useAgentEventStream`）：断线 **backoff 重连最多 3 次** → 降级 **轮询** → 轮询中 **周期性尝试恢复 SSE**；终态或 unmount 时清理连接 / 定时器。

### 轮询回退

`GET .../events?after_sequence=` 与 SSE **同一事件模型**；前端在 SSE 不可用时降级轮询。

### 前端 `AgentLoopPanel`

项目详情 Tab「Agent 闭环」：

- **状态栏**：状态、run id、开始时间、耗时、当前节点/工具、已完成步骤、连接态（live / reconnecting / polling / …）、进度
- **执行时间线**：按 `sequence` 展示节点与嵌套 tool 事件
- **结果区**：合规摘要、草稿、findings、警告/错误
- **引用深度链接**（见下）
- **恢复 / 重试**按钮（语义见下）

### Resume vs Retry

| | Resume | Retry |
|---|--------|-------|
| 典型场景 | `waiting_for_user` 中断后续跑 | `failed` / `blocked` / `cancelled` |
| 行为 | 同 run / 同 `thread_id`，从 checkpoint 续跑；跳过 `completed_nodes` | `retry_attempt++`，从 `completed_nodes` 清掉失败节点后 **从 START 重跑并 skip**；保留更早 completed |
| 执行 | 默认 prepare + `BackgroundTasks`；`?sync=true` 供测试 | 同左 |

### App 重启

Checkpoint 持久化在 DB，**跨进程可恢复**。但 FastAPI **`BackgroundTasks` 不会在进程重启后自动续跑**——需用户显式调用 **resume**（或 UI「恢复」）。

### 限制（Step 11）

- **无** WebSocket（仅 SSE + 轮询）
- **无** Chain-of-Thought / 思维链流式展示
- **尚未实现**：评测中心、LoRA

### 通用限制

- 非法律意见、非人工 gold
- 不编造企业资质；证据不足则 warning / blocked

---

## 引用深度链接（真实定位）

Agent / 合规引用 URL 形如：

`/projects/{project_id}?tab=documents&document_id=...&page=...&chunk_id=...`

`ProjectDetailPage` 消费这些参数：切换到文档中心 → 打开本项目文档的 Chunk 抽屉 → 定位页码 → 高亮对应 chunk。支持首次加载、路由跳转、浏览器前进后退与刷新恢复。无效 / 跨项目 `document_id` 显示安全提示，不白屏、不无限请求；定位完成后保留可分享 URL。跨项目文档因列表 API 仅返回本项目文档而被拒绝。
