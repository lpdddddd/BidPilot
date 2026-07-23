# BidPilot 领域微调（Step 13）

完整课程可演示链路：业务 SFT → 自动质检 → 审核队列 → 固定切分 → LlamaFactory LoRA → smoke/正式训练 → 评测 → adapter 注册 → BidPilot 展示模型版本。

## 诚实口径

| 轨道 | 含义 |
|------|------|
| `course_pilot` | 自动结构质检 + 均衡抽样后的课程演示微调数据；**不是** human_gold |
| 正式 human Gold 门禁 | `reviewed_trainable_sft` 仍可为 0；正式量产 LoRA 仍要求人工审核 Gold |

本仓库 Step 13 交付的是 **course_pilot 可复现链路**，并在文档中标明与正式 Gold 门禁的区别。

## 目录

```text
training/llamafactory/
  configs/qwen3_8b_lora_course_smoke.yaml   # smoke（max_steps=20）
  configs/qwen3_8b_lora_course.yaml         # 正式课程 LoRA（1 epoch）
  data/bidpilot_course_pilot_*.json         # QC 后 ShareGPT
  scripts/prepare_course_pilot.py           # QC + 审核队列 + 切分
  scripts/run_course_train.sh               # smoke | formal
  scripts/eval_course_lora.py               # base vs LoRA 小样本评测
  outputs/                                  # adapter（gitignore）
  model_registry.json                       # BidPilot 注册表
datasets/review/course_pilot/review_queue.csv
datasets/reports/course_pilot_sft_report.json
```

## 命令

```bash
# 1) 从 datasets/sft 构建 course_pilot ShareGPT + 审核队列
python training/llamafactory/scripts/prepare_course_pilot.py

# 2) Smoke（验证链路，约 1 分钟）
CUDA_VISIBLE_DEVICES=2 bash training/llamafactory/scripts/run_course_train.sh smoke

# 3) 正式课程训练（1 epoch，course_pilot train）
CUDA_VISIBLE_DEVICES=2 bash training/llamafactory/scripts/run_course_train.sh formal

# 4) 评测（base vs adapter，小样本）
python training/llamafactory/scripts/eval_course_lora.py \
  --adapter-path training/llamafactory/outputs/qwen3_8b_lora_course \
  --device cuda:3 --limit 20

# 5) 查看注册信息
curl -s http://localhost:8000/api/v1/models/active | jq .
```

依赖：外部 `llamafactory-cli`（`pip install llamafactory` 或 `LLAMAFACTORY_HOME`）、本地 `Qwen3-8B` 权重、空闲 GPU。

## BidPilot 接入

- 注册表：`training/llamafactory/model_registry.json`
- API：`GET /api/v1/models/active`、`GET /api/v1/health/llm`（detail 含 finetune 版本）
- 在线推理仍走 vLLM OpenAI 兼容口（基座 `bidpilot-qwen3-8b`）。Adapter 默认用于离线评测与注册展示；合并权重后可将 `LLM_MODEL_PATH` 指向合并目录以在线切换。

## 演示建议

1. 展示 `course_pilot_sft_report.json` 与 `review_queue.csv`
2. 展示 smoke / formal `train_results.json` 与 `adapter_model.safetensors`
3. 展示 `eval` 报告中 base vs LoRA 的 `json_ok_rate`
4. 前端工作台展示当前模型版本（`/api/v1/models/active`）

课程演示总流程见 [`course_demo.md`](course_demo.md)。
