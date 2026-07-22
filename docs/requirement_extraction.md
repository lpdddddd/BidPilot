# 招标要求结构化抽取（第 7 步）

将项目招标资料转为可追溯、可检索、**待人工审核**的 Requirement 数据。
每条 Requirement 必须能定位回真实 Document / DocumentChunk；严禁脱离原文编造。

## 输入范围

默认仅扫描：

- `tender`（招标文件）
- `announcement`（公告）
- `amendment`（补遗 / 澄清）
- `contract`（合同）

**不会**进入抽取上下文：`company_profile`、`qualification`、`case`、`personnel`、`product`。

调用方可按 `document_ids` / `document_types` 收窄。抽取直接扫描项目内真实 chunks，
**不**借用 RAG top-k 问答结果作为全量来源。

## 结构化字段与类别

`RequirementCategory`：

`project_info` · `qualification` · `commercial` · `technical` · `scoring` ·
`material` · `deadline` · `mandatory` · `invalid_bid` · `contract`

持久化要点：

| 字段 | 说明 |
|------|------|
| `quality_level` | 自动抽取固定为 `pending` |
| `review_status` | 固定为 `unreviewed`（未人工确认） |
| `requirement_code` | 稳定码 `auto-{category}-{sha1…}`，非 LLM 自由文本主键 |
| `EvidenceLink` | 每个真实 chunk 一条证据 |
| `metadata_json` | `source=auto_extraction`、run id、引文、冲突组等 |

风险等级（确定性规则，非模型臆造）：

- `invalid_bid` → `critical`
- `mandatory` / `deadline` → `high`
- `qualification` / `scoring` / `material` / `contract` → `medium`
- 其他 → `low`
- `potential_conflict=true` → 至少提升到 `high`

## 证据校验

每个候选必须：

1. `source_chunk_ids` 全部属于本轮 batch；
2. `evidence_quote` 经空白规范化后可在对应 chunk 原文中匹配；
3. `source_page` / `source_section` / `source_clause_id` 与 chunk 元数据一致或为空；
4. `category` 属于既有枚举。

无证据、未知 chunk、虚构页码/章节/条款、无效 JSON 的条目一律拒绝。
单 batch 失败不回滚已成功写入的结果；失败摘要不含文档全文。

## 去重与冲突

- **去重**：同一 extraction run 内，仅合并「类别相同且规范化文本完全一致」的候选；保留全部 EvidenceLink。不做激进语义去重。
- **幂等**：相同自动抽取 `requirement_code` 不重复插入；`force=true` 仅替换 `metadata_json.source=auto_extraction` 的记录，**永不删除**手工/导入 Requirement。
- **冲突**：同类不同数值/日期/资质/评分；同条款号互相矛盾；`amendment` 与 `tender` 明显差异 → 标记 `potential_conflict`，UI「需人工确认」，系统**不自动裁决**。

## API

```text
POST /api/v1/projects/{project_id}/requirements/extractions
GET  /api/v1/projects/{project_id}/requirements/extractions/{run_id}
GET  /api/v1/projects/{project_id}/requirements
GET  /api/v1/projects/{project_id}/requirements/{requirement_id}
```

启动体示例：

```json
{
  "document_ids": [],
  "document_types": ["tender", "announcement", "amendment", "contract"],
  "force": false
}
```

抽取异步执行（FastAPI `BackgroundTasks` + `requirement_extraction_runs` 表）。
前端轮询真实 `status` 与计数，禁止伪造进度百分比。

## 前端使用流程

项目详情 → **需求清单**：

1. 空态：说明范围 →「开始抽取」；
2. 运行态：已处理 chunks / 候选 / 创建 / 合并 / 冲突 / 失败；
3. 结果态：统计、过滤、列表；`pending/unreviewed` 明确为待复核；
4. 详情：结构化字段 + 引文 + 定位，跳转文档中心。

## 当前限制

- 结果为**自动抽取、待人工审核**；
- **尚未**实现 RequirementMatch、企业材料匹配、LoRA/SFT 微调、LangGraph Agent；
- Qwen3-8B 仅作基础结构化抽取验证，未微调。

## 本地开发

```bash
cd backend && alembic upgrade head
# LLM_ENABLED=true + vLLM 可选；无 LLM 时可用单测 FakeLlm
make test
cd frontend && npm run lint && npm run build
```
