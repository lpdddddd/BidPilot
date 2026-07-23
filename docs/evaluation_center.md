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
2. Target 只接收 `TargetCaseInput` + `TargetExecutionContext`；`PrivateReferenceBundle` 仅在 target 返回后进入 evaluator。
3. `citation_metadata`、`reference_output`、`evidence`、`label_source`、expected/gold/verdict/scorer 等不得进入 target。
4. 无直接 reference 的指标标记 `not_applicable` / `executed_without_direct_reference`，**不计满分**。
5. Matching 的 `insufficient_evidence` 表示证据不足，**不是**合法双边供应商证据。

### 结构隔离数据流

```text
manifest case
  -> TargetCaseInput (+ TargetExecutionContext.project_id = EvaluationRun.project_id)
  -> target execution -> TargetResult
  -> PrivateReferenceBundle + TargetResult -> evaluator
```

## 模型

- `EvaluationSuite`：manifest snapshot、dataset hash、profile version
- `EvaluationRun`：status（queued/running/completed/partial/failed/cancelled）、safe target config、seed、进度计数、overall score、claim token
- `EvaluationCaseResult`：唯一 `(run_id, case_key)`；API 返回服务端校验后的 `citations[].valid` / `invalid_reason` / `detail_url`
- `EvaluationMetricResult`：唯一 `(case_result_id, metric_name, metric_version)`

Alembic：`o0d4e5f6a7b8`（revises `n9c3d4e5f6a7`）。

## 后台生命周期

1. 创建 run：**先持久化 queued**，commit 后返回 `run_id` / `status` / `detail_url`。
2. **生产 API 始终后台执行**（无公开 `sync` / `fixture_path` / `fail_case_keys`）。
3. 同步执行仅经内部 `EvaluationService(..., execute=True)` 或测试 fixture。
4. 原子 claim：`claimed` / `already_running` / `invalid_state` / `not_found_or_forbidden`。
5. 登记 BackgroundTask 失败时释放 claim，恢复 queued/partial，并写安全错误摘要。
6. **有界调度**：同时 in-flight ≤ worker 数；每完成一个 future 后检查 DB cancel；cancel 后不再提交新 case；已开始 case 允许安全结束；未执行 case 保持未写入（resume 再 claim）。
7. Runner：每个 case 独立 `SessionLocal()` + 独立 target 实例，禁止跨线程共享 Session。
8. 幂等：`(project_id, idempotency_key)` 唯一约束；`IntegrityError` 回滚后返回既有 run，不再登记 BackgroundTask。

## Targets / capability

| Target | available | reason_code |
|--------|-----------|-------------|
| `deterministic_fake` | 仅测试/CI（`EVALUATION_ALLOW_FAKE` 或 app_env∈{dev,test,ci}）；**不出现在生产 capability** | `fake_disabled` |
| `rag` | Embedding/retrieval 可用；检索 scope=`EvaluationRun.project_id` | `project_dependency_missing` |
| `compliance` | 始终；`ComplianceEngine` | — |
| `extraction` / `matching` / `drafting` | **unavailable**（无 case-level 正式入口） | `service_not_wired` |
| `agent_pipeline` | `llm_enabled` + base_url；`AgentRunService` | `provider_not_configured` |

创建 run 时后端再次校验 capability，禁止绕过前端提交 unavailable target。

## API 契约（OpenAPI 为准）

- capabilities：`items`（不用 `targets`）、结构化 `profiles`、`dataset` 动态 stats
- 列表统一：`{items,total,page,page_size}`
- 创建：`suite_id`、`target`/`target_type`、`task_families`、`evaluator_profile`、`case_limit`、`seed`、`target_config`、`idempotency_key`；`extra=forbid`
- compare：deltas + improved/regressed/unchanged + warnings

## 前端

Workbench「评估中心」→ `/evaluation`：概览（human Gold=0）、新建（unavailable 禁用）、Run 列表/详情（轮询+卸载清理）、Case（citation 以服务端 `valid`/`invalid_reason` 为准）、Compare、导出。

## 限制

- LLM-backed `agent_pipeline` 依赖真实 provider（本地可用 vLLM）；CI 用 deterministic_fake。
- extraction/matching/drafting 尚未 case-level 接线。
- 不启动 LoRA / 不导出训练集（Step 13）。
- 不引入 WebSocket / Celery / Kafka。
