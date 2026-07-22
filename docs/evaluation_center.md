# BidPilot 评测中心（Step 12）

项目级自动评测中心：对既有 RAG、需求抽取、供应商匹配、合规检查、响应草稿与 Agent 流程做可复现评测。本步**只做评测/比较/导出**，不构建 LoRA 训练集。

## 数据与 reference 口径

复用 `datasets/eval/reference/`（140 cases，`label_source=auto_reference` 全部；**没有 human_gold**）。

| 维度 | 数量 |
|------|------|
| rag / extraction / matching | 30 / 30 / 30 |
| compliance / drafting / unanswerable | 20 / 20 / 10 |
| train / validation / test | 73 / 40 / 27 |
| direct reference coverage | 140/140 = 100%（分母=总 case；均为 auto_reference） |

Reference kinds：`auto_reference`、`rule_expected`、`human_gold`（仅真实人工审核）、`no_direct_reference`、`executed_without_direct_reference`、`not_applicable`、`metric_error`。

规则：

1. 不得把 auto_reference / rule_expected 称为 human Gold。
2. 待评测 target **只接收** case input（`EvaluationCase.target_input()`）；reference 不进 prompt / RAG / Agent state。
3. 无直接 reference 的指标标记 `not_applicable` / `executed_without_direct_reference`，**不计满分**。
4. 无 reference 指标不进入 overall score 分母（applicable 权重再归一化）。

## 模型

- `EvaluationSuite`：manifest snapshot、dataset hash、profile version
- `EvaluationRun`：status（queued/running/completed/partial/failed/cancelled）、safe target config、seed、进度计数、overall score、claim token
- `EvaluationCaseResult`：唯一 `(run_id, case_key)`
- `EvaluationMetricResult`：唯一 `(case_result_id, metric_name, metric_version)`

Alembic：`o0d4e5f6a7b8`（revises `n9c3d4e5f6a7`）。

## 架构

`suite_loader` → `case_loader` → `targets` → `metrics` → `profiles` / hard gates → `aggregator` → `runner` → `report`；`EvaluationService` 项目作用域 API。

Targets：`deterministic_fake`（CI）、`rag`、`extraction`、`matching`、`compliance`、`drafting`、`agent_pipeline`。未配置 provider 时 capability 标 unavailable，不伪造分数。

## 指标与 hard gates

每类任务有版本化 profile（`bidpilot-eval-profile-1.0.0`）。Hard gates：

1. critical compliance false negative
2. unlocatable citation
3. enterprise fabrication
4. sensitive data leakage

运行指标（latency 等）默认 weight=0，不混入质量分。

可选 AI Judge：接口预留；默认 profile 不含 judge；CI 不调用在线模型。

## API

均在 `/api/v1/projects/{project_id}/...`，跨项目 404：

- evaluation-capabilities / evaluation-suites
- evaluation-runs（CRUD 语义：create/list/get/results/cancel/resume/compare/export）

Export：JSON / CSV / Markdown；敏感模式扫描脱敏。test split 不返回完整 `reference_output`。

## 前端

Workbench「评估中心」→ `/evaluation`：概览、新建、Run 列表/详情（轮询）、Case、Compare、导出。终态与 unmount 清理 timer。

## 限制

- 默认 LLM-backed target 依赖真实 provider；CI 用 deterministic_fake。
- 不启动 LoRA / 不导出训练集（Step 13）。
- 不引入 WebSocket / Celery / Kafka。
