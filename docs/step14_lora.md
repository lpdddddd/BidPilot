# BidPilot LoRA 在线服务（Step 14）

在 Step 13 **course_pilot** 数据与训练交付之上，本步把 Course LoRA 接到 **vLLM 在线推理** 与业务选择器（Ask / 评测中心）。
**诚实口径**：`course_pilot ≠ human_gold`；仅当 vLLM 实际 **served** 该 LoRA 模块时，UI 才显示「在线」——注册表或 Adapter 文件就绪不等于在线。

## 完成状态分层

| 层级 | 状态 | 说明 |
| --- | --- | --- |
| 配置完成 | **是** | Compose Adapter bind、env 变量、启动脚本 preflight、Base↔Adapter 强校验 |
| 自动化测试完成 | **是** | 后端 model_serving / Ask / Eval metadata；前端状态文案、模型选择、compare、export |
| 当前机器真实执行完成 | **是** | vLLM 同时 served Base+LoRA；Base Ask；LoRA Ask（流式已路由到 LoRA，终态因引用校验失败）；Eval Base/LoRA/compare/export |
| 因环境未执行 | **无阻断项**（本机 GPU + Adapter + 本地 Base 可用） | 若 Adapter 缺失 / GPU 不可用，不得伪造 `served=true` |

## Adapter 信息

| 项 | 说明 |
| --- | --- |
| 公共 `model_id` | `qwen3-8b-lora-course`（基座为 `qwen3-8b-base`） |
| 展示名 | BidPilot Course LoRA |
| Adapter 路径（仓库相对） | `training/llamafactory/outputs/qwen3_8b_lora_course` |
| 注册表 | `training/llamafactory/model_registry.json` |
| 默认 served 名 | `bidpilot-qwen3-8b-course-lora` |
| 训练轨道 | `course_pilot`（自动 QC，**非** human_gold） |
| Adapter `base_model_name_or_path` | 本地 snapshot（等价于 `Qwen/Qwen3-8B` / `Qwen3-8B`） |
| LoRA rank `r` | 16（须 ≤ `LLM_MAX_LORA_RANK`） |

必需文件：`adapter_config.json` + `adapter_model.safetensors`（或 `.bin`）。**勿提交权重。**

## Compose Adapter 路径（重要）

Compose 文件位于 `infra/`。**相对路径相对 compose 文件所在目录解析**，不是相对仓库根。

| 变量 | 含义 |
| --- | --- |
| `LLM_LORA_HOST_PATH` | 宿主机 Adapter 目录（可绝对路径；推荐在本地 `.env` 设置） |
| `LLM_LORA_CONTAINER_PATH` | 容器内固定挂载点（默认 `/models/bidpilot-course-lora`） |
| `LLM_BASE_SERVED_NAME` | Base served 名（默认 `bidpilot-qwen3-8b`） |
| `LLM_LORA_SERVED_NAME` | LoRA served 名（默认 `bidpilot-qwen3-8b-course-lora`） |

默认未设置 `LLM_LORA_HOST_PATH` 时，compose 使用：

```text
../training/llamafactory/outputs/qwen3_8b_lora_course
```

该路径相对 `infra/`，解析到仓库根下真实产物：

```text
<repo>/training/llamafactory/outputs/qwen3_8b_lora_course
→ 容器 /models/bidpilot-course-lora
```

**错误示例（旧）**：在 `infra/` 下写 `./training/...` 会解析到不存在的 `<repo>/infra/training/...`。

校验：

```bash
# 必须从仓库根执行
bash scripts/check_lora_adapter.sh
docker compose --env-file .env \
  -f infra/docker-compose.yml -f infra/docker-compose.llm.yml \
  --profile llm config
# 确认 volumes 中 source 指向 <repo>/training/.../qwen3_8b_lora_course
# target 为 /models/bidpilot-course-lora
```

宿主机 Adapter 目录不存在或 Base 不匹配时，`check_lora_adapter.sh` / `serve_qwen3_vllm.sh` **立即非零退出**。

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

### Base ↔ Adapter 强校验

| 情况 | registered | adapter_exists | served |
| --- | ---: | ---: | ---: |
| registry 有配置，Adapter 缺失 | true | false | false |
| Adapter 完整且 Base 匹配，服务未加载 | true | true | false |
| Adapter 完整但 Base 不匹配 | true | false | false |
| Adapter 完整但 Base 无法确认 | true | false | false |
| Adapter 完整、匹配且 `/v1/models` 返回 LoRA | true | true | true |

公开字段含 `reason_code` / `base_model_match` / `configured_base_model` / `adapter_base_model` / `served_model_name` / `last_probe_at`。**API 不返回 Adapter 绝对路径。**

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

核验：`datasets/reports/course_lora_eval_summary.json` 与 Markdown 报告一致；样本来自固定 test 文件，未按效果筛选。

| 指标 | Base | LoRA |
| --- | ---: | ----: |
| JSON parse rate | 0% | 87.5% |
| Schema validity | 0% | 87.5% |
| Required field coverage | 0% | 72.5% |
| Verdict accuracy | 0% | 66.7% |
| Field exact match | 0% | 45.0% |
| Citation validity | 0% | 0% |
| Parse failures | 40 | 5 |

**真实结论**：

1. LoRA **明显改善**结构化输出（JSON / schema / 必填覆盖）。
2. requirement classification 等字段准确率仍有限（field EM ~45%）。
3. 部分 verdict 子任务准确率仍较低（如 requirement_classify verdict 0.25）。
4. **citation 没有改善**（仍为 0%）。
5. 当前结果**不足以**证明完整领域判断能力显著提升；仍是 course_pilot，非 human_gold。

## 真实在线验收记录

| 项 | 值 |
| --- | --- |
| 验收时间 | 2026-07-23（Asia/Shanghai） |
| 起始 HEAD | `12fb55ae00cb75489aedc405b13def92aa40e4dd` |
| GPU | NVIDIA GeForce RTX 5090 |
| CUDA / PyTorch | torch `2.11.0+cu130` |
| vLLM | `0.25.1` |
| Adapter 检查 | files_ok；rank=16 ≤ max_lora_rank；`base_model_match=match` |
| 配置 Base | 本地 `Qwen3-8B` snapshot / Hub 等价 `Qwen/Qwen3-8B` |
| Adapter Base | 与配置 Base 匹配（canonical `Qwen3-8B`） |
| Compose config | source=`<repo>/training/llamafactory/outputs/qwen3_8b_lora_course` → target=`/models/bidpilot-course-lora`；`--enable-lora`；lora-modules=`bidpilot-qwen3-8b-course-lora=/models/bidpilot-course-lora` |
| vLLM 启动 | 本机已运行同一服务同时提供 Base+LoRA（非 mock） |
| `/v1/models` | `bidpilot-qwen3-8b`，`bidpilot-qwen3-8b-course-lora` |
| Base Ask | **完成**：`requested=qwen3-8b-base` → `served=bidpilot-qwen3-8b`；`fallback_used=false`；status=`answered`；latency≈2.5s |
| LoRA Ask | **路由真实完成**：SSE `generation_started` 使用 `bidpilot-qwen3-8b-course-lora`；`fallback_used=false`。终态因 grounded 引用校验失败（Course LoRA 偏向结构化 SFT，未必产出 `[S1]`），**不得**记为 Base 输出 |
| Base Eval run | `52025c7e-ef0f-49f3-bd0e-bcd2e6614394`（status=`completed`；`served_model_name=bidpilot-qwen3-8b`） |
| LoRA Eval run | `f8356c3a-1f4b-40ce-9b3b-bfb95cbd8536`（status=`partial`；`served_model_name=bidpilot-qwen3-8b-course-lora`） |
| compare | `GET .../evaluation-runs/compare?left=<base>&right=<lora>`；`config_diff.changed` 含 `model_id` / `served_model_name` / `model_type` / `adapter_version` |
| export | Base Markdown 含 model 元数据与 git_commit；LoRA JSON `model.served_model_name=bidpilot-qwen3-8b-course-lora` |
| 失败 / 限制 | LoRA Ask 终态常因缺 `[S1]` 引用校验失败；RAG Eval 上 LoRA 易 partial；citation 离线指标仍为 0；course_pilot ≠ 领域完备性证明 |

演示 case：同一资格要求问题下，Base Ask 可返回带 `[S1]`/`[S6]` 的自然语言答案；LoRA 请求已绑定 Course LoRA served name，但输出形态更偏 SFT JSON，与 grounded Ask 校验冲突——这是真实产品限制，不是服务未启动。

## 固定演示步骤

1. 确认 Step 13 Adapter 与 `model_registry.json` 存在。
2. `bash scripts/check_lora_adapter.sh` 后 `./scripts/serve_qwen3_vllm.sh`（`LLM_ENABLE_LORA=true`）。
3. `curl …/v1/models` 与 `GET /api/v1/models`：LoRA `served=true` 才宣称在线。
4. 工作台 Base / LoRA 状态芯片；Ask 选模型问答。
5. 评测中心各跑 Base / LoRA（小 `case_limit`），对比导出。
6. 口头说明：course_pilot ≠ human_gold；未 `--enable-lora` 则 LoRA 不得显示在线。

## 相关文档

- [`step13_lora.md`](step13_lora.md) — 数据 / 训练 / 注册
- [`course_demo.md`](course_demo.md) — 课程走查
- [`evaluation_center.md`](evaluation_center.md) — 评测中心
