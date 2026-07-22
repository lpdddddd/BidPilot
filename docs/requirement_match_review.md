# 匹配结果人工审核（第 9 步）

对第 8 步产出的 `RequirementEvidenceMatch` 建立**可审计**的人工审核闭环。  
本步**不**实现 LoRA / Agent / 自动投标 / 标书提交；**不**改写匹配结论的
`EvidenceMatchStatus`、summary、企业证据链或 Dense/BM25/RRF/rerank/RAG ask。

## 状态机

| from | action | to |
|------|--------|-----|
| `pending` | `confirm` | `confirmed` |
| `pending` | `reject` | `rejected` |
| `pending` | `needs_more_material` | `needs_more_material` |
| `confirmed` / `rejected` / `needs_more_material` | `reopen` | `pending` |

规则：

- 终态上再次 `confirm` / `reject` / `needs_more_material` → **HTTP 409**
- `reject`、`needs_more_material`、`reopen` 必须提供非空 comment（空白归一化，最长约 2000）
- `confirm` 的 comment 可选
- 终态动作：`needs_review=false`，`is_review_protected=true`，写入 `reviewed_at` / `reviewed_by`
- `reopen`：`needs_review=true`，`is_review_protected=false`，`review_status=pending`；
  **保留全部** `RequirementMatchReview` 历史；**不**改 match.status / summary / 证据
- 审核 API **永不**改 `EvidenceMatchStatus`、summary、EvidenceLink、match run 记录
- 并发：行级 `SELECT FOR UPDATE` + 请求体 `review_lock_version`；版本不匹配 → 409
- `Idempotency-Key`：同 project+match+key+同语义 body → 返回同一审核结果；同 key 不同 body → 409
- `actor_authn` 当前固定为 `unverified_local_operator`；`actor_label` 必填（1–64，可打印）

## Force 保护与跳过已审核

`_is_protected_match`：

1. `is_review_protected`
2. `review_status != pending`
3. `lifecycle_status == superseded`
4. `metadata.source != auto_match`
5. legacy `metadata.review_status == reviewed`

`_needs_new_auto_version(match, force=)`（pre-LLM 过滤与 `_persist_matches` 共用）：

- `None` → 需要新建
- protected / non-pending / superseded → 跳过
- pending auto、无审核历史 → `force=false` 幂等跳过；`force=true` 可删除替换
- pending + **有审核历史**（reopen 后）→ **两种 force 均创建 successor**（`supersedes_match_id` /
  `lifecycle_status=superseded`）；**永不删除**有审核历史的旧行

执行匹配 run 时：

- 对不需要新版本的 Requirement **跳过 LLM**（与 persist 同规则）
- 计入 `protected_requirement_count` / `skipped_reviewed_requirement_count`（及 `config_json`）
- 合法跳过不是失败；若范围内全部被跳过 → run `succeeded`，零写入
- persist 事务内：`pg_advisory_xact_lock` + Match/Run `FOR UPDATE`，并发对同一 reopen
  Requirement 至多一个 active successor

`force=true`：

- 无审核历史的 pending auto → 可删除替换（与第 8 步一致）
- 有审核历史（含 reopen 后再次 pending）→ **不删除**；将旧行标记
  `lifecycle_status=superseded`，写入 `superseded_by_match_id` / `supersedes_match_id`
- 已保护的终态匹配 → 永不删除、不参与 rematch

> 说明：文档中的 `record_status` 即模型字段 `lifecycle_status`（`active` | `superseded`）。

## 审核队列 API

`GET .../requirement-matches/review-queue`

默认：`review_status=pending`、`include_superseded=false`（仅 `lifecycle_status=active`）。

可选过滤：`match_status` / `status`、`requirement_category` / `category`、`risk_level`、
`has_conflict`、`has_scope_exclusion`、`include_superseded`、`page` / `page_size` / `limit`、`sort`。
`review_status=all` 表示不过滤审核状态。

返回：分页 items + totals + `counts`（按 review_status / match status / risk 聚合）。

## API

```
GET  /{project_id}/requirement-matches/review-queue
GET  /{project_id}/requirement-matches/{match_id}
GET  /{project_id}/requirement-matches/{match_id}/reviews
POST /{project_id}/requirement-matches/{match_id}/review
POST /{project_id}/requirement-matches/{match_id}/reopen
```

路由顺序：`review-queue`、`/{match_id}/reviews|review|reopen` 在裸 `/{match_id}` 之前注册。

## 数据模型

- `RequirementEvidenceMatch` 扩展：`review_status`、`reviewed_at`、`reviewed_by`、
  `review_lock_version`、`is_review_protected`、`lifecycle_status`、
  `superseded_by_match_id`、`supersedes_match_id`
- `RequirementMatchReview`：不可变审计行（action / from→to / comment / reason_code /
  actor_* / idempotency_key）
- `RequirementMatchRun`：`protected_requirement_count`、`skipped_reviewed_requirement_count`

迁移：`e0f4a5b6c7d8_add_requirement_match_review.py`（revises `d9e3f4a5b6c7`）。

## 免责声明（UI）

> 当前结果仅为企业材料与招标 Requirement 的可追溯匹配及人工审核记录，不构成自动投标结论。
