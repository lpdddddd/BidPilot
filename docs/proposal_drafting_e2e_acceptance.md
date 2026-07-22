# 响应草稿工作区 E2E 验收（脱敏合成样例）

> 本文档仅描述基于**脱敏合成材料**与 **mock LLM** 的验收路径。  
> **不**声称真实 Qwen3-8B 直播调用已成功；如需 live 验收，应在独立环境另行记录。

## 前置

- PostgreSQL 测试库可用；
- `alembic upgrade head` 含 `f1a5b6c7d8e9`；
- 后端使用 FakeLlm 返回结构化 JSON（见 `tests/test_proposal_drafting.py`）。

## 合成场景矩阵

| 场景 | 期望 |
|---|---|
| confirmed + supported | 生成 `supported_response`，带真实 citation/quote |
| confirmed + partially_supported | `partial_response` + 缺口说明 |
| confirmed + insufficient_evidence | 仅 material_gap / warnings |
| confirmed + conflicting_evidence | risk_item + 双侧 citation |
| confirmed + not_applicable | scope_item + 双侧 citation |
| pending / rejected / needs_more_material | disposition=excluded，不进正向正文 |
| 伪造 citation / quote | run failed，零 Version/Source |
| 合法生成 | 原子写入 Draft+Version+Source+Run succeeded |
| 取消竞争 | cancel 获胜，零版本 |
| 人工修订 | 新 `manual_revision` 版本 |
| 无证据人工新增 | 阻止 mark_reviewed 与 export |
| reviewed 导出 | Markdown 与 DOCX 含免责声明；DOCX 首页含「待人工复核，未提交」 |
| Match superseded 后 | 历史草稿来源快照不变 |
| 跨项目 ID | 拒绝 / 404 |

## 免责声明检查点

页面、创建弹窗、详情 Drawer、导出 Markdown 首页与页脚、DOCX 封面与页脚均出现：

```text
本文件为基于已审核材料生成的响应准备草稿，须经人工复核、补充、签署和法务或业务确认后方可使用，不构成投标结论或投标提交文件。
```

## 命令

```bash
cd backend && pytest -q tests/test_proposal_drafting.py tests/test_requirement_match_review.py --tb=short
cd frontend && npm run test && npm run lint
```

## 明确未覆盖

- 真实 Qwen live 成功率；
- 自动投标结论 / 报价 / 签章页 / 对外提交。
