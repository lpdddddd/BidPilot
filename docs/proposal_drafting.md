# 可追溯响应准备草稿工作区（第 10 步）

在已人工确认的 RequirementMatch 与可定位证据之上，生成**待人工复核**的响应准备草稿。  
本阶段**不**输出投标结论、报价、工期/法律承诺，**不**生成可直接对外提交的投标文件。

## 边界

输入仅限当前项目：

- Requirement 原文；
- 已验证 EvidenceLink（招标侧）与 confirmed Match 的企业侧证据链；
- 服务端组装的受限白名单（requirement / match / citation / quote ID）。

输出：

- 结构化响应正文（按区块）；
- 合规准备矩阵；
- 缺失材料、冲突风险、范围待核验清单；
- 不可变版本与来源快照；
- 人工复核后的 Markdown / DOCX 导出（服务端渲染，**不再调用 LLM**）。

固定免责声明（UI、导出首页与页脚）：

```text
本文件为基于已审核材料生成的响应准备草稿，须经人工复核、补充、签署和法务或业务确认后方可使用，不构成投标结论或投标提交文件。
```

## 证据准入

正向正文仅允许：

```text
review_status = confirmed
lifecycle_status = active
match_status ∈ {supported, partially_supported}
```

| Match 情况 | 草稿处理 |
|---|---|
| confirmed + supported | `supported_response` 带引用 |
| confirmed + partially_supported | `partial_response` 必须说明缺口 |
| confirmed + insufficient_evidence | 仅 `material_gap` 清单 |
| confirmed + conflicting_evidence | 仅风险清单，保留双侧冲突证据 |
| confirmed + not_applicable | 仅范围清单，保留双侧范围证据 |
| pending / rejected / needs_more_material | 不进入正向正文 |

## 数据模型

| 表 | 作用 |
|---|---|
| `proposal_drafts` | 草稿头与当前版本指针 |
| `proposal_draft_versions` | 不可变版本（generated / manual_revision） |
| `proposal_draft_sources` | 版本来源快照（Requirement/Match/citation/quote） |
| `proposal_draft_reviews` | 审核 / reopen 审计 |
| `proposal_draft_generation_runs` | 异步生成 run |

硬性语义：

1. 生成与人工修订均新增不可变 Version；禁止覆盖旧版本。
2. 成功时 Draft + Version + Source + Run 原子提交；失败/取消零新 Version/Source。
3. `mark_reviewed` 锁定当前版本；修改前必须 `reopen`（非空原因）。
4. 含「人工新增，尚未提供证据」的内容不可审核、不可导出。
5. 后续 Match reopen / superseded / 重跑不得改写历史草稿来源快照。
6. 严格项目隔离。

## API

```text
POST/GET /api/v1/projects/{id}/proposal-drafts
GET      /api/v1/projects/{id}/proposal-drafts/eligibility
GET      /api/v1/projects/{id}/proposal-drafts/{draft_id}
GET      .../versions 与 .../versions/{version_id}
POST     .../manual-revisions
POST     .../review 与 .../reopen
GET/POST .../proposal-draft-runs/{run_id} 与 .../cancel
GET      .../export?format=markdown|docx
```

写操作支持 `Idempotency-Key`；并发冲突返回 HTTP 409。

## 前端

项目详情页「响应草稿」Tab：列表、创建（区分可用/排除）、run 状态与取消、详情分区、版本历史、人工修订、审核/重开、仅 reviewed 可导出。

## 与第 8–9 步的关系

本步**只读** confirmed active Match 作为证据输入，不修改匹配/审核原子语义、不触碰 RAG Dense/BM25/RRF/rerank。  
详见 [requirement_matching.md](./requirement_matching.md) 与 [requirement_match_review.md](./requirement_match_review.md)。

## 当前限制

- 本地操作人标识为 `unverified_local_operator`（尚未接完整认证）；
- **未实现**自动投标结论、价格生成、法律承诺、投标提交、外部平台操作、LoRA/Agent。

## 本地开发

```bash
cd backend && alembic upgrade head
pytest -q tests/test_proposal_drafting.py
cd frontend && npm run test && npm run lint
```
