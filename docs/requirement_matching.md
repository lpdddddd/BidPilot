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
| `conflicting_evidence` | 企业材料内部存在相互矛盾的直接证据（双侧可定位） |
| `not_applicable` | 仅当 Requirement 明确限定适用范围，且可定位证据证明当前对象在范围外 |

### `not_applicable` 证据规则（收紧）

仅当同时满足：

1. Requirement 原文本身明确限定适用范围；
2. 可定位证据证明当前对象/范围在适用之外。

必须提供完整字段：

- `not_applicable_basis`：`requirement_scope_exclusion` | `project_scope_exclusion`
- `not_applicable_evidence_quote`：可在证据 chunk 中连续定位
- `not_applicable_evidence_chunk_id`

校验：

- `requirement_scope_exclusion` → chunk 必须是该 Requirement 的招标主证据
  （`EvidenceLink` / 本轮 payload 中的 `tender_primary_evidence_chunks`）；
- `project_scope_exclusion` → chunk 必须是本轮传给模型的、当前项目、允许类型的企业侧候选；
- 定位只从 chunk 元数据派生；
- 模型主观判断、常识、缺少企业证据、材料为空 → **禁止** `not_applicable`；
  无法证明则拒绝该项或安全降级为 `insufficient_evidence`（文案「当前材料未找到充分证据」）。

UI 标签：**「明确不适用，待人工审核」**，详情展示 basis + 引文 + 定位。

### `conflicting_evidence` 双侧证据

必须同时提供两段**不同文本位置**的企业侧证据：

| 侧 | 字段 | Link `role` |
|----|------|-------------|
| 支持 | `primary_company_chunk_id` + `company_evidence_quote` | `company_support` |
| 冲突 | `conflicting_company_chunk_id` + `conflicting_company_evidence_quote` | `company_conflict` |

规则：

- 两侧 chunk 均须在本轮该 Requirement 的企业候选集合内，引文可定位；
- 禁止同一 chunk + 同一引文；
- `conflict_note` 中的关键 token 必须能在两侧证据文本中找到；
- 仅单侧 / 重复证据 / 引文失败 → 拒绝 `conflicting_evidence`，可降级为
  `partially_supported` 或 `insufficient_evidence`（summary 消毒，不编造事实）。

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
   （`role`: `company_support` / `company_conflict`）。

文件名、页码、章节、条款一律从企业侧 / 招标侧 chunk 元数据派生，**禁止**采信模型自填定位。  
跳转 Document Center 使用真实定位路径。

## 证据验证与禁止过度结论

复用 `evidence_validate`（空白规范化、引文连续匹配、关键事实 token 等）：

- `requirement_id` 必须属于本次 run；
- `supported` / `partially_supported` 必须有单一主 chunk + 可定位 quote；
- `conflicting_evidence` 必须有双侧不同位置的企业证据；
- `not_applicable` 必须有完整 basis + 可定位 quote + 授权 chunk；
- 主企业 chunk 必须属于本轮实际传给模型的企业侧候选；
- `summary` 不得出现原 Requirement 或企业证据中没有的金额、日期、资质等级、技术参数等；
- 资质等级错配（如「一级」支撑「特级」）拒绝 `supported`，可安全降级；
- 禁止输出「必然中标 / 完全满足 / 一定不符合」等绝对结论；
- UI 对 `insufficient_evidence` 文案固定为「当前材料未找到充分证据」。

检索仅用**当前项目企业侧**文档上的轻量词面重叠缩小候选；**不**修改 Dense / BM25 / RRF / rerank。

## force、幂等、取消与原子提交

- 默认重跑同一 `requirement_ids + document_ids/document_types` 范围幂等，不重复插入自动 Match；
- `force=true` 只替换本次实际匹配 Requirement 范围内、`source=auto_match` 的自动记录；
- 手工 / 导入 / 已人工审核 Match **永不**删除；
- **全部** batch 的模型调用与结构化验证完成后，才在**单一事务**中：
  可选 force 删除范围内可替换旧记录 → 写入全部新 Match / Link → 更新 run；
- 结果语义：
  - `valid_result`：全部 batch 完成且存在合法非纯不足结论；
  - `valid_empty_or_insufficient_result`：全部 batch 完成，合法 `insufficient_evidence` 也算成功；
  - `invalid_or_incomplete_result`：任一 batch LLM/JSON/schema/证据致命失败、全部候选被拒、取消、或持久化错误；
- `invalid_or_incomplete_result`（**无论** `force`）：
  run → `failed`（取消则为 `cancelled`）；
  **零**新自动 Match 写入；**零**旧自动 Match 删除/替换；无 orphan Link；
- 单条合法 `insufficient_evidence` **不是**失败；成功 batch 内被拒的条目可对未覆盖需求合成 `insufficient_evidence`；
  致命失败的 batch **不**合成、不写入；
- 取消：`POST .../runs/{run_id}/cancel`；仅 `queued` / `running` 可取消；
  设置 `status=cancelled` 与 `config_json.cancel_requested=true`；
  终态（succeeded/failed/cancelled）返回 409，不改写状态；
  执行中在 batch 前、LLM 返回后、persist 前检查取消；取消则丢弃内存结果、不写 Match。

## API 与前端

```text
POST /api/v1/projects/{project_id}/requirement-matches/runs
GET  /api/v1/projects/{project_id}/requirement-matches/runs/{run_id}
POST /api/v1/projects/{project_id}/requirement-matches/runs/{run_id}/cancel
GET  /api/v1/projects/{project_id}/requirement-matches
GET  /api/v1/projects/{project_id}/requirement-matches/{match_id}
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

项目详情 → **材料匹配**：空态 / 运行态（真实轮询与真实取消）/ 结果态 / 双侧证据详情。  
结果一律标记**待人工审核**；无自动裁决、通过率、投标建议或提交操作。

## 与第 7 步 force 成功语义的关系

抽取 run 在候选**全部证据校验失败**时必须 `failed` 且 `force=true` **不得**清空旧 Requirement。  
匹配 run 采用同一原则：验证失败或无效结果不得替换旧 Match（`force` 与否均原子失败）。

## 当前限制

- 匹配结论均为**自动生成、待人工审核**；
- **尚未**实现 LoRA / QLoRA / SFT 微调、LangGraph Agent、自动投标方案生成或投标提交；
- Qwen3-8B 仅作基础结构化匹配验证，未微调。

## 本地开发

```bash
cd backend && alembic upgrade head
make test
cd frontend && npm run lint && npm run build
```
