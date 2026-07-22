# 企业材料与招标要求匹配（第 8 步）

将**已通过证据校验**的招标 Requirement，与当前项目的企业侧真实文档 chunks
逐项对照，生成**待人工审核**的匹配结论（Match）。  
本阶段**不**生成新的招标要求，**不**断言企业必然满足或不满足。

## 输入范围与项目隔离

### 招标侧

- 只读取当前项目、可用、未删除的 Requirement；
- 招标证据沿用既有 `source_document_id`、`EvidenceLink` 与定位元数据；
- 默认匹配全部合法 Requirement；可用 `requirement_ids` 收窄；
- 带 `potential_conflict` 的 Requirement 可参与，但 Match 必须 `needs_review=true`，
  并提示「招标要求本身存在待确认冲突」。

### 企业侧（仅允许）

```text
company_profile · qualification · case · personnel · product
```

**严禁**将以下招标侧类型当作企业证据：

```text
tender · announcement · amendment · contract
```

项目 A 的 Requirement / 企业 chunks **绝不可**被项目 B 使用。  
企业材料为空时**不调用 LLM**，进入真实失败（或无材料）状态的 run，并**保留**已有 Match。  
空材料路径**禁止**产出 `not_applicable`。

## 数据模型

独立于演示用遗留 `RequirementMatch`（company_profile 关联）表，新增：

| 表 | 作用 |
|----|------|
| `requirement_match_runs` | 异步匹配 run 与统计 |
| `requirement_evidence_matches` | 自动匹配结论 |
| `requirement_evidence_match_links` | 企业侧证据链（不破坏 Requirement / RAG 的 EvidenceLink） |

### Match `status`（固定枚举，禁止 LLM 自由造类）

| status | 含义 |
|--------|------|
| `supported` | 当前材料有直接、充分、可定位的支持证据；仍需人工审核 |
| `partially_supported` | 仅支持一部分，或主体/期限/范围仍不清 |
| `insufficient_evidence` | **当前已选材料范围内**证据不足；**不等于**企业不具备 |
| `conflicting_evidence` | 企业材料内部存在相互矛盾的直接证据（双侧可定位 + 互斥证明） |
| `not_applicable` | 仅当 Requirement 明确限定适用范围，且双范围证据证明当前对象在范围外 |

### 全局原子语义（force=false 与 force=true 相同）

Match run **仅当**下列之一成立才可提交写入：

1. 每个选中的 Requirement 都有一条合法、完整、证据校验通过的结果
   （`insufficient_evidence` 算合法）；或
2. 企业材料范围合法但为空 → 不调用 LLM，走既有 `empty_company_materials`
   失败/无 LLM 路径（**永不** `not_applicable`）。

下列任一发生 → 整次 run `failed` / `cancelled`，**零**新 Match / EvidenceLink 写入，
**保留**全部既有 Match：

- 任一 batch LLM / JSON / schema / 超时失败；
- **任一**条目校验拒绝（未知 req/chunk、坏引文、越权、跨项目、编造事实、
  错误 status 语义、缺少必要证据等）；
- LLM 路径后任一 Requirement 缺少合法结果（**禁止**对拒绝/遗漏项合成
  `insufficient_evidence`）；
- 取消；
- 持久化失败。

已移除的旧行为：

- 非法 `conflicting_evidence` 降级为 `partially_supported` 并继续；
- `_synthesize_insufficient` 填充 LLM 遗漏或校验拒绝项；
- summary「消毒」掩盖编造关键 token（现改为 `fabricated_summary` → 整 run 失败）；
- 同一 batch 中保留合法项、丢弃非法项。

保留的合法业务结果：资质等级弱于要求时，确定性降级
`supported → partially_supported`（非 silent insufficient 合成）。

拒绝原因计入 accumulator（如 `quote_not_found`、`invalid_conflict`、
`out_of_scope_chunk`、`missing_requirement_result`、`unknown_requirement`、
`fabricated_summary`、`invalid_not_applicable` 等），并写入
`error_summary` / `config_json.reject_reason_counts`。

### `not_applicable` 双范围证据

必须同时提供：

| 侧 | 字段 | Link `role` |
|----|------|-------------|
| 招标范围限定 | `requirement_scope_chunk_id` + `requirement_scope_quote` | （招标主证据；写入 metadata） |
| 当前对象范围 | `current_scope_chunk_id` + `current_scope_quote` | `company_scope_exclusion` |

另需 `not_applicable_basis`（`requirement_scope_exclusion` |
`project_scope_exclusion`）与可选 `not_applicable_note`。

校验：

- 招标侧 chunk 必须是该 Requirement 的主证据；引文连续且含范围限定语
  （仅适用/仅限/本标段/适用范围…）；
- 当前侧 chunk 必须在本轮企业候选、允许类型、同项目；
- 确定性互斥（如「仅适用于海淀区」vs「服务范围为朝阳区」）；
- 缺任一侧 / 无法证明 / 跨项目 / 招标文件冒充当前侧 → 校验拒绝 → 整 run 失败；
- 旧单侧 `not_applicable_evidence_*` 单独出现 → 拒绝；
- 空企业材料：无 LLM、无 `not_applicable`。

UI 标签：**「明确不适用，待人工审核」**；详情展示两侧引文 + 定位 + 跳转。

### `conflicting_evidence` 直接冲突证明

必须同时提供两段**不同文本位置**的企业侧证据，以及互斥证明字段：

| 侧 / 字段 | 说明 | Link `role` |
|-----------|------|-------------|
| 支持 | `primary_company_chunk_id` + `company_evidence_quote` | `company_support` |
| 冲突 | `conflicting_company_chunk_id` + `conflicting_company_evidence_quote` | `company_conflict` |
| 证明 | `conflict_dimension`、`conflict_subject`、`primary_claim_value`、`conflicting_claim_value` | metadata |

规则（`match_conflict_validate.validate_direct_company_conflict`）：

- 两侧均为企业侧、同项目、在该 Requirement 候选集合内；
- 引文空白规范化后连续可定位；禁止同一 chunk+同一引文；
- `conflict_subject` 经 soft normalize 后须出现在两侧证据文本；
- 主张值须为对应引文的精确子串；
- 按维度证明互斥（资质等级 / 数量 / 覆盖范围 / 肯定否定 / 证书有效性等）；
- 无关材料或非互斥范围 → `invalid_conflict` → 整 run 失败（**不降级**）。

### 风险规则（确定性后端规则）

- `Requirement.risk_level=critical` → Match 至少 `high`
- `mandatory` / `deadline` / `qualification` / `invalid_bid` 且非 `supported` → 至少 `high`
- `partially_supported` 默认至少 `medium`
- `conflicting_evidence` 至少 `high`
- `insufficient_evidence` 默认 `medium`，强制项为 `high`
- `supported` 继承 Requirement 风险下限
- `needs_review` **始终为 true**（直至未来人工审核功能明确改变）

## 双侧证据链

每个 Match 同时保留：

1. **招标侧**：Requirement + 既有 EvidenceLink / 引文 / 定位；
2. **企业侧**：主 chunk + 短引文 + `RequirementEvidenceMatchLink`
   （`role`: `company_support` / `company_conflict` / `company_scope_exclusion`）。

文件名、页码、章节、条款一律从企业侧 / 招标侧 chunk 元数据派生，**禁止**采信模型自填定位。  
跳转 Document Center 使用真实定位路径。

## 证据验证与禁止过度结论

复用 `evidence_validate`（空白规范化、引文连续匹配、关键事实 token 等）：

- `requirement_id` 必须属于本次 run，且每个 Requirement 恰好一条结果；
- `supported` / `partially_supported` 必须有单一主 chunk + 可定位 quote；
- `conflicting_evidence` 必须有双侧不同位置 + 直接冲突证明字段；
- `not_applicable` 必须有双范围证据；
- 主企业 chunk 必须属于本轮实际传给模型的企业侧候选；
- `summary` / `conflict_note` 不得出现原 Requirement 或企业证据中没有的关键 token
  → `fabricated_summary` → 整 run 失败；
- 资质等级错配（如「一级」支撑「特级」）拒绝 `supported`，确定性降为
  `partially_supported`；
- 禁止输出「必然中标 / 完全满足 / 一定不符合」等绝对结论；
- UI 对 `insufficient_evidence` 文案固定为「当前材料未找到充分证据」。

检索仅用**当前项目企业侧**文档上的轻量词面重叠缩小候选；**不**修改 Dense / BM25 / RRF / rerank。

## force、幂等、取消与原子提交

- 默认重跑同一 `requirement_ids + document_ids/document_types` 范围幂等，不重复插入自动 Match；
- `force=true` 只替换本次实际匹配 Requirement 范围内、`source=auto_match` 的自动记录；
- 手工 / 导入 / 已人工审核 Match **永不**删除；
- **全部** batch 的模型调用与结构化验证完成后，才在**单一事务**中：
  `SELECT … FOR UPDATE` run → 可选 force 删除 → 写入全部新 Match / Link → 更新 run succeeded；
- 结果语义：
  - `valid_result`：全部 batch 完成且存在合法非纯不足结论；
  - `valid_empty_or_insufficient_result`：全部 batch 完成，合法 `insufficient_evidence` 也算成功；
  - `invalid_or_incomplete_result`：任一 batch 致命失败、任一校验拒绝、遗漏结果、取消、或持久化错误；
- `invalid_or_incomplete_result`（**无论** `force`）：
  run → `failed`（取消则为 `cancelled`）；
  **零**新自动 Match 写入；**零**旧自动 Match 删除/替换；无 orphan Link；
- 取消：`POST .../runs/{run_id}/cancel`；`SELECT … FOR UPDATE`；仅 `queued` / `running`；
  设置 `status=cancelled` 与 `config_json.cancel_requested=true`；
  终态（succeeded/failed/cancelled）返回 409「任务已结束，无法取消」；
  persist 路径再次 `FOR UPDATE`：若已取消则零写入并保持 cancelled；
  worker **永不**把 cancelled 覆盖为 failed/succeeded。

## API 与前端

```text
POST /api/v1/projects/{project_id}/requirement-matches/runs
GET  /api/v1/projects/{project_id}/requirement-matches/runs/{run_id}
POST /api/v1/projects/{project_id}/requirement-matches/runs/{run_id}/cancel
GET  /api/v1/projects/{project_id}/requirement-matches
GET  /api/v1/projects/{project_id}/requirement-matches/review-queue
GET  /api/v1/projects/{project_id}/requirement-matches/{match_id}
GET  /api/v1/projects/{project_id}/requirement-matches/{match_id}/reviews
POST /api/v1/projects/{project_id}/requirement-matches/{match_id}/review
POST /api/v1/projects/{project_id}/requirement-matches/{match_id}/reopen
```

启动体示例：

```json
{
  "requirement_ids": [],
  "document_ids": [],
  "document_types": [
    "company_profile",
    "qualification",
    "case",
    "personnel",
    "product"
  ],
  "force": false
}
```

项目详情 → **材料匹配**：空态 / 运行态（真实轮询与真实取消）/ 结果态 / 双侧证据详情 /
人工审核闭环。  
结果一律可进入**人工审核**；无自动裁决、通过率、投标建议或提交操作。

人工审核、force 保护与 supersede 语义见
[requirement_match_review.md](./requirement_match_review.md)（第 9 步）。

### Force 与已审核保护（第 9 步衔接）

- 已人工确认 / 驳回 / 需补充材料，或 `is_review_protected` 的 active Match：**跳过 LLM**，
  计入 `protected_requirement_count` / `skipped_reviewed_requirement_count`；
- `force=true` **不得**删除受保护 Match；有审核历史的 pending（如 reopen 后）改为
  `lifecycle_status=superseded` 并链接新旧 Match，而非硬删；
- 全部 Requirement 均被保护跳过 → run `succeeded`、零写入。

## 与第 7 步 force 成功语义的关系

抽取 run 在候选**全部证据校验失败**时必须 `failed` 且 `force=true` **不得**清空旧 Requirement。  
匹配 run 采用同一原则：验证失败或无效结果不得替换旧 Match（`force` 与否均原子失败）。

## 当前限制

- 匹配结论为自动生成，经第 9 步人工审核后方可标记为受保护；
- **尚未**实现 LoRA / QLoRA / SFT 微调、LangGraph Agent、自动投标方案生成或投标提交；
- Qwen3-8B 仅作基础结构化匹配验证，未微调。

## 本地开发

```bash
cd backend && alembic upgrade head
make test
cd frontend && npm run lint && npm run build
```
