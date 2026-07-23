# BidPilot LoRA 在线服务（Step 14）

在 Step 13 **course_pilot** 数据与训练交付之上，本步把 Course LoRA 接到 **vLLM 在线推理** 与业务选择器（Ask / 评测中心）。  
**诚实口径**：`course_pilot ≠ human_gold`；仅当 vLLM 实际 **served** 该 LoRA 模块时，UI 才显示「在线」——注册表或 Adapter 文件就绪不等于在线。

## Adapter 信息

| 项 | 说明 |
| --- | --- |
| 公共 `model_id` | `qwen3-8b-lora-course`（基座为 `qwen3-8b-base`） |
| 展示名 | BidPilot Course LoRA |
| Adapter 路径（仓库相对） | `training/llamafactory/outputs/qwen3_8b_lora_course` |
| 注册表 | `training/llamafactory/model_registry.json` |
| 默认 served 名 | `bidpilot-qwen3-8b-course-lora` |
| 训练轨道 | `course_pilot`（自动 QC，**非** human_gold） |

必需文件：`adapter_config.json` + `adapter_model.safetensors`（或 `.bin`）。

## 启动 vLLM（含可选 LoRA）

```bash
# .env 中 LLM_ENABLED=true，LLM_BASE_URL 指向本机 vLLM（如 http://127.0.0.1:8001/v1）
./scripts/serve_qwen3_vllm.sh
```

脚本在 Adapter 存在时默认开启 `--enable-lora` / `--lora-modules`。也可显式控制：

| 环境变量 | 含义 |
| --- | --- |
| `LLM_MODEL` | 基座 served 名（默认 `bidpilot-qwen3-8b`） |
| `LLM_MODEL_SOURCE` | Hub id（默认 `Qwen/Qwen3-8B`） |
| `LLM_MODEL_PATH` | 可选本地权重目录（须含 `config.json`） |
| `LLM_ENABLE_LORA` | `true` / `false`；未设时若默认 Adapter 存在则自动 `true` |
| `LLM_LORA_MODULE_NAME` | LoRA served 名（默认 `bidpilot-qwen3-8b-course-lora`） |
| `LLM_LORA_ADAPTER_PATH` | Adapter 目录（默认 `training/llamafactory/outputs/qwen3_8b_lora_course`） |
| `LLM_MAX_LORA_RANK` | 须 ≥ adapter `r`（默认 16） |

**限制**：在线 LoRA **必须** `--enable-lora`；仅加载基座权重时，API 会将 Course LoRA 标为未 served，业务侧拒绝静默回退（除非显式 `allow_base_fallback`）。

## 探测 `/v1/models`

```bash
curl -s "${LLM_BASE_URL:-http://127.0.0.1:8001/v1}/models" | jq .
# 期望同时看到基座与 LoRA served 名（若已 --enable-lora）
```

业务目录（不暴露本机绝对路径）：

```bash
curl -s http://localhost:8000/api/v1/models | jq .
curl -s http://localhost:8000/api/v1/models/active | jq .
```

`items[].served` / `status_label` 以实时 probe 为准；**不要**把 `registered` 或 `adapter_exists` 当成在线。

## 业务 Ask：模型选择

1. 项目 → **带来源问答**。
2. 下拉选择 **Base**（默认）或 **BidPilot Course LoRA**。
3. LoRA 未 served 时选项禁用，提示「模型尚未启动在线服务」。
4. 回答后的 `generation_trace` 展示 `requested_model_id` / `resolved_model_id` / `served_model_name` 等。

`POST .../ask` 接受 `model_id`、`allow_base_fallback`（默认 false，前端不静默回退）。

## 评测中心：Base vs LoRA 对比

1. **评估中心** → 新建评测，Target 选 **RAG** 或 **Agent 全流程**。
2. 选择评测模型 Base / Course LoRA（写入 `target_config.model_id`）。
3. 未 served 的模型不可提交；后端亦返回 `reason_code=model_not_served`（文案：模型尚未启动在线服务）。
4. 各跑一次 Base 与（在线）LoRA，在 **对比** 页比较指标。

### 离线评测（固定 test split，seed=14，n=40）

| 指标 | Base | LoRA | 变化 |
|---|---:|---:|---:|
| JSON parse rate | 0.00 | 0.875 | +0.875 |
| Schema validity | 0.00 | 0.875 | +0.875 |
| Required field coverage | 0.00 | 0.725 | +0.725 |
| Verdict accuracy | 0.00 | 0.667 | +0.667 |
| Field-level accuracy | 0.00 | 0.450 | +0.450 |
| Evidence support | 0.00 | 0.00 | 0（本样本 gold 字段支持有限） |
| Citation validity | 0.00 | 0.00 | 0（同上） |
| Average latency (ms) | 5554 | 3116 | −2438 |
| Failed cases | 40 | 5 | −35 |

原始汇总：`datasets/reports/course_lora_eval_summary.json`、`datasets/reports/course_lora_eval_report.md`。

**真实结论**：LoRA 主要改善结构化 JSON / schema / 必填字段覆盖；字段值准确率有一定提升但仍是 course_pilot（非 human_gold）。Evidence / citation 指标在本批 gold 上基本不适用或未拉开差距，**不能**据此宣称领域判断能力已显著提升。

## 固定演示步骤

1. 确认 Step 13 Adapter 与 `model_registry.json` 存在。  
2. `./scripts/serve_qwen3_vllm.sh`（`LLM_ENABLE_LORA=true`）。  
3. `curl …/v1/models` 与 `GET /api/v1/models`：LoRA `served=true` 才宣称在线。  
4. 工作台 Base / LoRA 状态芯片；Ask 选模型问答。  
5. 评测中心各跑 Base / LoRA（小 `case_limit`），对比导出。  
6. 口头说明：course_pilot ≠ human_gold；未 `--enable-lora` 则 LoRA 不得显示在线。

## 相关文档

- [`step13_lora.md`](step13_lora.md) — 数据 / 训练 / 注册  
- [`course_demo.md`](course_demo.md) — 课程走查  
- [`evaluation_center.md`](evaluation_center.md) — 评测中心
