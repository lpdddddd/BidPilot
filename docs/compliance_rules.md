# 规则合规检查工具（确定性引擎）

BidPilot 提供**不调用 LLM** 的确定性合规规则引擎，对项目已有要求、企业匹配、招标证据链接与响应草稿做结构化检查。数据不足时输出 `unknown`/`warning`，**从不编造企业事实或投标结论**。

> **定位**：流程表第 9 步「规则检查工具」。产品上的「匹配结果人工审核」仍是独立能力（仓库 README 中保留为先序产品步骤）；本引擎消费审核后的 Match / 草稿等结构化结果，**不是法律意见或人工 gold 替代品**。

## 引擎版本

`compliance-rules-1.1.0`

## 表结构

- `compliance_runs`：一次检查执行（`queued` → `running` → `succeeded`/`failed`），支持 `Idempotency-Key`
- `compliance_findings`：稳定 `finding_id` 的结构化发现项

## 规则目录

| Rule ID | Category | 说明 |
|---------|----------|------|
| `A001_mandatory_coverage` | coverage | 强制要求须有正向匹配 |
| `A002_match_presence` | coverage | 每条要求应有 active 匹配 |
| `A003_tender_evidence_link` | coverage | 要求应有招标侧 EvidenceLink |
| `A004_uncovered_match_status` | coverage | insufficient / conflicting 视为未覆盖 |
| `A005_high_priority_uncovered` | coverage | high/critical 要求须有正向匹配 |
| `A006_draft_missing_mandatory` | coverage | 当前草稿须引用强制要求 |
| `B001_quote_grounding` | evidence | 企业引文须 `quote_in_content` 接地 |
| `B002_company_doc_scope` | evidence | 企业证据不得引用招标侧文档 |
| `B003_supported_needs_quote` | evidence | 正向匹配须有引文 |
| `B004_dangling_evidence` | evidence | 悬空 document/chunk 或非法页码/字符区间 |
| `B005_conflicting_evidence_citation` | evidence | conflicting 状态或招标文档冒充企业证据 |
| `C001_qualification_insufficient` | qualification_risk | 资格类材料不足/冲突 |
| `C002_high_risk_unconfirmed` | qualification_risk | 高风险匹配未确认 |
| `C003_invalid_bid_attention` | qualification_risk | 废标条款人工关注 |
| `C004_definitive_negative` | qualification_risk | 强制/资格明确负面（conflicting / not_supported）→ critical |
| `C005_structured_thresholds` | qualification_risk | 有 expiry/金额字段则检查；否则 unknown，不编造 |
| `D001_unevidenced_manual` | draft_safety | 无证据人工增补 |
| `D002_forbidden_claims` | draft_safety | 禁止性承诺措辞 |
| `D003_citation_integrity` | draft_safety | 草稿来源引用完整性 |
| `D004_placeholders` | draft_safety | TODO / 待补充 / 占位符 / `{{` |
| `D005_empty_or_short` | draft_safety | 空或过短草稿 |
| `D006_strong_claim_without_support` | draft_safety | 强满足表述须有正向匹配 |
| `D007_cross_project_source` | draft_safety | 草稿来源不得跨项目 |
| `E001_status_vs_links` | consistency | 正向匹配与证据链接一致 |
| `E002_review_lifecycle` | consistency | 审核状态与生命周期一致 |
| `E003_deadline_presence` | consistency | deadline 要求与 `bid_deadline` |
| `E004_exclusive_match_statuses` | consistency | 同一要求互斥 active 状态 |
| `E005_project_ownership` | consistency | 实体 project_id 归属一致 |
| `E006_gap_match_definitive_draft` | consistency | 材料不足却写明确满足 |

## API

前缀：`/api/v1/projects`

- `POST /{project_id}/compliance/runs` — 全项目检查（可选 `draft_id` / `rule_ids` / `categories`，`Idempotency-Key`）
- `POST /{project_id}/proposal-drafts/{draft_id}/compliance/runs` — 草稿聚焦
- `GET /{project_id}/compliance/runs/{run_id}`
- `GET /{project_id}/compliance/runs/{run_id}/report`
- `GET /{project_id}/compliance/latest`
- `GET /{project_id}/compliance/findings` — 过滤 severity/category/rule_id/…
- `GET /compliance/rules` 与 `GET /{project_id}/compliance/rules`
- 再次 `POST` = 新 run（历史保留）；相同 Idempotency-Key + 相同 payload 返回原 run

## Tools（非 LangGraph）

`backend/app/tools/compliance_tools.py`：

1. `check_requirement_coverage`
2. `check_evidence_integrity`
3. `check_draft_compliance`
4. `run_project_compliance_check`
5. `get_compliance_report`

## 离线评估

```bash
cd backend
python -m app.services.compliance.offline_eval
# 写出 datasets/reports/compliance_rule_offline_eval.json
```

报告字段：`sample_count` / `rule_trigger_counts` / `severity_distribution` /
`category_distribution` / `label_consistency_rate`（= verdict_match_rate）。

适配器将 `compliance_reference.jsonl` 转为轻量 REF_* 检查（关键词 + 引文接地），并映射 severity/category 供聚合。**完整 A–E DB 规则仅适用于线上项目**，报告 `note` 字段会说明此限制。

## 限制（必读）

- **不是**法律意见、投标结论或人工 gold 替代品
- 数据不足 → `unknown`/`warning`，绝不编造公司资质/金额/有效期
- 不含 LangGraph Agent、LoRA 审查或自动「建议投标」
