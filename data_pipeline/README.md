# BidPilot Data Pipeline

独立可安装的招投标数据处理工程（`bidpilot-data`）。

## 安装

```bash
cd data_pipeline
pip install -e ".[dev]"
# 可选 OCR
# pip install -e ".[ocr]"
```

## 快速开始（本地 demo，无需外网/API）

```bash
python -m bidpilot_data run-demo
# 或
make -C .. dataset-bootstrap
make -C .. dataset-parse
make -C .. dataset-label
make -C .. dataset-build-sft
make -C .. dataset-validate
```

## 主要 CLI

```bash
python -m bidpilot_data collect --manifest datasets/manifests/source_manifest.jsonl
python -m bidpilot_data download --resume
python -m bidpilot_data deduplicate
python -m bidpilot_data parse --resume
python -m bidpilot_data clean --resume
python -m bidpilot_data chunk --resume
python -m bidpilot_data label requirements --mode rules
python -m bidpilot_data label matches
python -m bidpilot_data review export
python -m bidpilot_data review export-priority
python -m bidpilot_data review import --file reviewed_requirements.csv
python -m bidpilot_data build-rag --limit 300
python -m bidpilot_data validate rag
python -m bidpilot_data build-agent --limit 500
python -m bidpilot_data build-sft
python -m bidpilot_data validate all
python -m bidpilot_data report
python -m bidpilot_data db import-requirements
```

Makefile wrappers: `dataset-label`, `dataset-build-rag`, `dataset-build-agent`, `dataset-build-sft`, `dataset-review-priority`, `dataset-validate`, `dataset-validate-rag`, `dataset-report`.

## 质量规则

- 模型/`rules` 产出默认 `quality_level=silver`，`review_status=pending`
- 只有 review import 且 `decision=accept|corrected` 且 `reviewer` 非空才能升为 `gold`
- 禁止自动把模型结果标为 gold
- train/validation/test 按 `project_id` 划分（validation ≥5、heldout test ≥10 projects）
- RAG 问题禁止粘贴 `原文：` / source_quote；不可回答占比 10%–15%
- RequirementMatch 仅在有公开资格/符合性审查证据时生成（禁止供应商×条款笛卡尔积）
- SFT 统计使用 `structurally_valid_sft` / `reviewed_trainable_sft` / `silver_candidate_sft`；正式 LoRA 以 reviewed gold 为准
- 任务平衡见 `configs/sft_balance.yaml`（只允许下采样）
- Portal homepage snapshot 不计入训练来源多样性
- OpenAI-compatible：`OPENAI_API_KEY` / `OPENAI_BASE_URL` / `DATASET_MODEL_NAME`

## 配置

- `configs/pipeline.yaml` — 规模目标与切块/下载参数
- `configs/taxonomy.yaml` — 需求分类与否决词
- `configs/sft_tasks.yaml` — SFT 任务系统提示
- `configs/sft_balance.yaml` — 任务比例上限与去重阈值
- `configs/source_sites.example.yaml` — 合规采集站点示例
