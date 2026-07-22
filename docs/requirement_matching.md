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
企业材料为空时**不调用 LLM**，创建真实失败（或无材料）状态的 run，并**保留**已有 Match。

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
| `conflicting_evidence` | 企业材料内部存在相互矛盾的直接证据 |
| `not_applicable` | 仅当 Requirement 明确不适用且可由原文支持 |

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

文件名、页码、章节、条款一律从企业侧 chunk 元数据派生，**禁止**采信模型自填定位。  
跳转 Document Center 使用真实定位路径。

## 证据验证与禁止过度结论

复用 `evidence_validate`（空白规范化、引文连续匹配、关键事实 token 等）：

- `requirement_id` 必须属于本次 run；
- `supported` / `partially_supported` / `conflicting_evidence` 必须有单一主 chunk + 可定位 quote；
- 主 chunk 必须属于本轮实际传给模型的企业侧候选；
- `summary` 不得出现原 Requirement 或企业证据中没有的金额、日期、资质等级、技术参数等；
- 资质等级错配（如「一级」支撑「特级」）拒绝 `supported`，可安全降级；
- 禁止输出「必然中标 / 完全满足 / 一定不符合」等绝对结论；
- UI 对 `insufficient_evidence` 文案固定为「当前材料未找到充分证据」。

检索仅用**当前项目企业侧**文档上的轻量词面重叠缩小候选；**不**修改 Dense / BM25 / RRF / rerank。

## force、幂等与事务安全

- 默认重跑同一 `requirement_ids + document_ids/document_types` 范围幂等，不重复插入自动 Match；
- `force=true` 只替换本次实际匹配 Requirement 范围内、`source=auto_match` 的自动记录；
- 手工 / 导入 / 已人工审核 Match **永不**删除；
- 全部模型调用与结构化验证完成后，才在**单一事务**中删除范围内可替换旧记录、写入新 Match / Link、更新 run；
- 任一 batch 致命失败、全部候选被拒、任务取消 → run `failed`，旧 Match 完整保留；
- 合法「企业资料为空」或合法 `insufficient_evidence` 与异常失败严格区分；
- 无 orphan EvidenceLink / MatchLink。

## API 与前端

```text
POST /api/v1/projects/{project_id}/requirement-matches/runs
GET  /api/v1/projects/{project_id}/requirement-matches/runs/{run_id}
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

项目详情 → **材料匹配**：空态 / 运行态（真实轮询）/ 结果态 / 双侧证据详情。  
结果一律标记**待人工审核**；无自动裁决、通过率、投标建议或提交操作。

## 与第 7 步 force 成功语义的关系

抽取 run 在候选**全部证据校验失败**时必须 `failed` 且 `force=true` **不得**清空旧 Requirement。  
匹配 run 采用同一原则：验证失败或无效结果不得替换旧 Match。

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
