# BidPilot Dataset Final Preflight Report

Generated at: `2026-07-16`  
Baseline commit (start of this round): `788e67b31c80ac19620155e15e2961660af1a036`  
Training: **not started** (no LoRA / QLoRA). Reviewer / decision / Gold fields: **not filled**.

## Verdict

**可以进入人工审核阶段（requirements / RAG / Agent / silver SFT），但正式 LoRA 训练门禁未满足。**

- 本轮质量硬条件已基本满足：Match 严格化、脏供应商剔除、rejected SFT 不进 split、RAG `max_project_share≤0.10`、multi_section 双证据、严重 train/test near-dup=0、真实 LLaMAFactory 文件验证通过。
- **不允许**仅凭本轮结果启动正式 LoRA/QLoRA：`reviewed_trainable_sft` 仍为 0，Gold=0，公开评标结果文件几乎缺失导致 `RequirementMatch=0`。

---

## 1. 本轮修改文件

### 新增
- `data_pipeline/bidpilot_data/labeling/supplier_names.py`
- `data_pipeline/bidpilot_data/labeling/industry.py`
- `data_pipeline/bidpilot_data/sft/cross_split.py`
- `data_pipeline/tests/test_match_supplier_rag_sft.py`
- `training/llamafactory/scripts/validate_sft_real.py`
- `datasets/reports/supplier_cleaning_report.json`
- `datasets/reports/cross_split_similarity_report.json`
- `datasets/reports/llamafactory_real_validation.json`
- `DATASET_FINAL_PREFLIGHT_REPORT.md`（本文件）

### 修改（代码 / 配置）
- `data_pipeline/bidpilot_data/labeling/disclosed_matches.py`
- `data_pipeline/bidpilot_data/schemas/records.py`（`supplier_id` 必填；`SupplierReviewOutcome`）
- `data_pipeline/bidpilot_data/rag_eval/build.py`
- `data_pipeline/bidpilot_data/sft/build.py`
- `data_pipeline/bidpilot_data/agent_data/build.py`
- `data_pipeline/bidpilot_data/validation/validate.py`
- `data_pipeline/bidpilot_data/cli.py`
- `data_pipeline/tests/test_quality_fixes.py`
- `Makefile`（`validate-sft` → real；`validate-sft-sample`）
- `README.md`、`.gitignore`

### 同步更新的报告 / 数据产物
- `DATASET_BUILD_REPORT.md`、`DATASET_QUALITY_FIX_REPORT.md`
- `datasets/reports/{dataset_statistics,validation_report,rag_quality_report,agent_quality_report,sft_build_stats,task_distribution,split_distribution}.json`
- `datasets/rejected/requirement_matches_pre_strict.jsonl`（归档旧 654 条）
- `datasets/rejected/sft.jsonl`
- `datasets/silver/{requirement_matches,disclosed_suppliers,supplier_review_outcomes,evidence}.jsonl`
- `datasets/eval/rag/questions.jsonl`、`datasets/eval/agent/tasks.jsonl`
- `datasets/sft/**`、`training/llamafactory/data/bidpilot_sft_*.json`

---

## 2–5. RequirementMatch / Supplier / cleaning

| Metric | Before (788e67b) | After |
| --- | --- | --- |
| RequirementMatch | **654** (satisfied 111 / missing 537 / uncertain 6) | **0** |
| Archived previous matches | — | **654** → `datasets/rejected/requirement_matches_pre_strict.jsonl` |
| SupplierReviewOutcome | 0 | **0** |
| disclosed_suppliers (accepted) | 62（含脏名） | **21** |
| rejected_supplier_candidates | n/a | **178** |
| raw_candidates | n/a | **248** |
| duplicate_suppliers_removed | n/a | **49** |

**为何 Match=0 可接受：** 结果类文档中几乎没有“供应商名 + 审查结论 + 可绑定具体条款”的事实句；`evaluation_result` / 资格审查结果表数量为 0。`tender_document` / `tender_notice` 已禁止用于 Match。通用条件句（如“不合格则废标”）不再生成 Match。

**Rejected supplier 示例：** `一次`、`服务名称`、`资格性审查`、以及含“交易服务费/需注册”的长句伪名称。  
**Accepted 示例：** `中国交通信息科技集团有限公司`、`安徽蓝科信息科技有限公司`、`深圳职业技术大学` 等。

---

## 6–9. SFT rejected / splits

| Metric | Value |
| --- | --- |
| structurally_valid_sft | **3621** |
| rejected_sft | **32**（全部 `missing_source`） |
| train / validation / test | **2418 / 675 / 528** |
| train+val+test | **3621** == structurally_valid |
| rejected 是否进入 split / LLaMAFactory | **否**（写入 `datasets/rejected/sft.jsonl`） |

---

## 10–13. RAG

| Metric | Value |
| --- | --- |
| questions | **214**（target 300，未用虚构补齐） |
| max_project_share | **0.09813** ≤ 0.10 |
| unanswerable_ratio | **0.14953**（10%–15% 带内） |
| multi_section | **9**；双 chunk + 双 quote + 答案覆盖两份证据：**9/9** |
| leaky questions | **0** |
| rag_quality_report.ok | **true** |

---

## 14–15. Agent

| Metric | Value |
| --- | --- |
| tasks | **238** |
| supplier tasks with exact name evidence | **12** |
| invalid_supplier_tasks_removed | **0**（本轮重建后） |
| agent_final_answers_with_none_string | **0** |

---

## 16–18. Cross-split / industry

| Metric | Value |
| --- | --- |
| severe train/test near-duplicates | **0** |
| template_overlap | **8**（warning，非 error） |
| industry | 规则分类已写入；split 中不再全是 unknown |

Train industry（记录数）：cloud_service 104, consulting_service 654, cybersecurity 127, data_governance 14, information_system_maintenance 5, non_it 161, system_integration 64, **unknown 1289**。  
Validation/test 仍偏 unknown / non_it / hardware — **未伪造行业填覆盖**。

---

## 19–20. LLaMAFactory 真实文件验证

| Check | Result |
| --- | --- |
| `make validate-sft-sample` | **PASS** |
| `make validate-sft-real` / `make validate-sft` | **PASS**（train/val/test/qwen3，error_count=0） |
| external_llamafactory_validation | **not_run**（`LLAMAFACTORY_HOME` 未设置） |

后续命令（未执行）：

```bash
export LLAMAFACTORY_HOME=/path/to/LLaMA-Factory
# 使用其数据 preview / preprocess（do_train=false），确认 Qwen3 template 解析 tool role
```

---

## 21. 测试

| Suite | Result |
| --- | --- |
| `make dataset-test` | **54 passed** |
| `make test`（backend） | **13 passed** |
| `python -m bidpilot_data validate rag` | **ok=true** |
| `python -m bidpilot_data validate all` | **ok=true**（14 warnings） |

---

## 22. 仍然存在的数据缺口

1. **RequirementMatch=0 / SupplierReviewOutcome=0**：缺少公开评标/资格审查结果文件；需继续采集结果类文档后再标。
2. **Gold=0**、**reviewed_trainable_sft=0**：正式 LoRA 未开门。
3. **RAG/Agent 未达数量目标**（RAG 214/300，Agent 238/300–500），禁止模板克隆补量。
4. **SFT 源域仍偏少**（约 2 个有效域，目标 ≥5；PORTAL 不计入）。
5. **industry 仍大量 unknown**：公告品目字段不全时规则分类保守。
6. **bundle 不全**：大量 incomplete；level_a 偏少。
7. **template_overlap=8**：中山大学等通用格式条款跨项目相似 — 已降级为 warning，不进严重泄漏。
8. **外部 LLaMAFactory preprocessing 未跑**。

---

## 23. 实际执行的全部命令

```bash
make dataset-test
make test
cd data_pipeline
python -m bidpilot_data label matches
python -m bidpilot_data build-rag --limit 300
python -m bidpilot_data validate rag
python -m bidpilot_data build-agent --limit 500
python -m bidpilot_data build-sft
# （中间重跑：label matches / build-agent / build-sft / cross_split 以消化修复）
python -m bidpilot_data validate all
python -m bidpilot_data report
cd ..
make validate-sft-sample
make validate-sft-real
make dataset-test
make test
```

---

## 24. 是否允许进入人工审核阶段

| 阶段 | 是否允许 |
| --- | --- |
| 人工审核（requirements / evidence / RAG / Agent / silver SFT） | **是** |
| 自动填 reviewer / decision / Gold | **否** |
| 正式 LoRA / QLoRA | **否**（等 Gold 与 reviewed_trainable 门禁） |

### 完成条件对照

| # | 条件 | 状态 |
| --- | --- | --- |
| 1 | tender 不再生成供应商 Match | **PASS** |
| 2 | Match 须供应商+证据+结果明确 | **PASS**（规则已落地；当前计数为 0） |
| 3 | 旧 654 条已归档并重生 | **PASS** |
| 4 | 脏供应商不进入正式数据/Agent | **PASS** |
| 5 | rejected SFT 排除出 LLaMAFactory split | **PASS** |
| 6 | split 和 == structurally_valid | **PASS**（3621） |
| 7 | RAG max_project_share ≤ 0.10 | **PASS**（0.098） |
| 8 | multi_section 双证据 | **PASS**（9/9） |
| 9 | 严重 train/test near-dup = 0 | **PASS** |
| 10 | 真实 SFT 文件验证通过 | **PASS** |
| 11 | Agent 答案无 `"None"` | **PASS** |
| 12 | 新增测试通过 | **PASS**（54） |
| 13 | 后端测试无回归 | **PASS**（13） |
| 14 | 报告已更新 | **PASS** |
