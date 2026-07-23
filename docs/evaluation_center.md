# BidPilot 评测中心（Step 12）

项目级自动评测中心：对既有 RAG、需求抽取、供应商匹配、合规检查、响应草稿与 Agent 流程做可复现评测。本步**只做评测/比较/导出**，不构建 LoRA 训练集。

## 数据与 reference 口径

复用 `datasets/eval/reference/`（统计由 manifest / suite loader **动态生成**，禁止硬编码）。

当前内置 suite 实测：

| 维度 | 数量 |
|------|------|
| rag / extraction / matching | 30 / 30 / 30 |
| compliance / drafting / unanswerable | 20 / 20 / 10 |
| train / validation / test | 73 / 40 / 27 |
| human_gold | **0** |
| auto_reference | 140 |
| rule_expected / no_direct_reference | 0 / 0 |
| direct reference coverage | 140/140 = 100%（分母=总 case；均为 auto_reference） |

Reference kinds：`auto_reference`、`rule_expected`、`human_gold`（仅真实人工审核）、`no_direct_reference`、`executed_without_direct_reference`、`not_applicable`、`metric_error`。

规则：

1. 不得把 auto_reference / rule_expected 称为 human Gold。
2. 待评测 target **只接收** `EvaluationCase.target_input()`（input + 项目/文档 scope hints）；`private_reference()` 仅供 evaluator。
3. `citation_metadata`、`citation_chunk_ids`、`has_evidence`、`reference_kind`、rule verdict 等不得进入 target。
4. 无直接 reference 的指标标记 `not_applicable` / `executed_without_direct_reference`，**不计满分**。
5. Matching 的 `insufficient_evidence` 表示证据不足，**不是**合法双边供应商证据。

## 模型

- `EvaluationSuite`：manifest snapshot、dataset hash、profile version
- `EvaluationRun`：status（queued/running/completed/partial/failed/cancelled）、safe target config、seed、进度计数、overall score、claim token
- `EvaluationCaseResult`：唯一 `(run_id, case_key)`；API 返回服务端校验后的 `citations[].valid`
- `EvaluationMetricResult`：唯一 `(case_result_id, metric_name, metric_version)`

Alembic：`o0d4e5f6a7b8`（revises `n9c3d4e5f6a7`）。

## 后台生命周期

1. 创建 run：**先持久化 queued**，commit 后返回 `run_id` / `status` / `detail_url`。
2. 生产默认 `sync=false`，经 `BackgroundTasks` → `tasks.run_evaluation`（**独立 Session**）。
3. `sync=true` 仅测试显式使用。
4. 原子 claim：`claimed` / `already_running` / `invalid_state` / `not_found_or_forbidden`。
5. 登记 BackgroundTask 失败时释放 claim，恢复 queued/partial，并写安全错误摘要。
6. 单 case 错误不终止整批；cancel 后未执行 case 跳过；resume 跳过已完成 case。

## 架构

`suite_loader` → `case_loader` → `targets` → `metrics` → `profiles` / hard gates → `aggregator` → `runner` → `report`；`EvaluationService` 项目作用域 API。

### Targets / capability

| Target | 可用性 |
|--------|--------|
| `deterministic_fake` | 仅 `EVALUATION_ALLOW_FAKE=1` 或 app_env∈{dev,test,ci}；**不出现在生产列表** |
| `rag` | Embedding/retrieval 可用时；调用 `RetrievalService` |
| `compliance` | 始终可用；调用正式 `ComplianceEngine` |
| `extraction` / `matching` / `drafting` / `agent_pipeline` | 需 `llm_enabled` 且非 placeholder key |

未配置 provider 时 capability 标 unavailable（含 `reason_code`），不伪造分数。

## API 契约（OpenAPI 为准）

- capabilities：`items`（不用 `targets`）、结构化 `profiles[{id,name,version,enabled_metrics,ai_judge_enabled}]`、`dataset.dataset_hash` + 动态 stats
- 列表统一：`{items,total,page,page_size}`
- 创建：`suite_id`、`target`（兼容 `target_type`）、`task_families`、`evaluator_profile`、`case_limit`、`seed`、`target_config`、`idempotency_key`
- compare：共同 case + deltas + `improved_cases` / `regressed_cases` / `unchanged_cases` / `left_only_cases` / `right_only_cases` + warnings

## 前端

Workbench「评估中心」→ `/evaluation`：概览（含 human Gold=0）、新建、Run 列表/详情（轮询+卸载清理）、Case（citation 有效性来自后端）、Compare、导出。

## 限制

- LLM-backed target 依赖真实 provider；CI 用 deterministic_fake。
- 不启动 LoRA / 不导出训练集（Step 13）。
- 不引入 WebSocket / Celery / Kafka。
