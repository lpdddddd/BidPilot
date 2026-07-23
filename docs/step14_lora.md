# BidPilot LoRA 在线服务（Step 14）

在 Step 13 **course_pilot** 数据与训练交付之上，本步把 Course LoRA 接到 **vLLM 在线推理** 与**结构化条款分析**业务（Ask / 评测中心能力边界已校正）。
**诚实口径**：`course_pilot ≠ human_gold`；仅当 vLLM 实际 **served** 该 LoRA 模块时，UI 才显示「在线」。

## 完成状态分层

| 层级 | 状态 | 说明 |
| --- | --- | --- |
| 配置完成 | **是** | Compose Host/Adapter 路径语义、entrypoint preflight、Base↔Adapter 强校验、capabilities |
| 自动化测试完成 | **是** | capability、结构化路由、Ask 不兼容拒绝、Compose preflight 断言、Eval metadata |
| 当前机器真实执行完成 | **是** | vLLM Base+LoRA；结构化 Base/LoRA 业务请求；extraction Eval compare/export |
| 未执行 / 阻断 | **无环境阻断** | 本机 GPU + Adapter 可用 |

## Course LoRA 真实任务协议

来源：`data_pipeline/configs/sft_tasks.yaml` + `data_pipeline/bidpilot_data/sft/build.py`。

| 项 | 约定 |
| --- | --- |
| 输入 | 条款原文（**无 RAG context**） |
| System | 任务专用中文（如「负责对条款进行分类并判断是否强制」） |
| User 前缀示例 | `判断以下条款的类别与是否强制：\n{text}` |
| 输出 | **紧凑 JSON**（无 Markdown、无 `[S1]`） |
| `requirement_classify` 必填 | `category`, `mandatory`, `risk_level`, `confidence` |
| `risk_detect` 必填 | `risk_level`, `risk_type`, `reason`, `is_rejection_clause` |
| `citation_qa`（训练子集） | JSON 内 chunk UUID，**不是** Ask 的 `[S1]` |

**因此 Course LoRA 不适合 Grounded Ask**（自然语言 + `[S1]`）。此前 Ask 实验记录保留为协议不匹配证据。

## Capability 管理

公开字段 `capabilities`：

| model | capabilities |
| --- | --- |
| `qwen3-8b-base` | `grounded_qa`, `structured_extraction` |
| `qwen3-8b-lora-course` | `structured_extraction` |

- Grounded Ask：前端只列出 `grounded_qa`；后端 `required_capability=grounded_qa`，不兼容返回 **422** `capability_unsupported`，**禁止静默回退**。
- 结构化条款分析 / Eval `extraction`：要求 `structured_extraction`。

## 最终接入的业务功能

**项目 → 要求 →「条款结构化分析（Course LoRA 协议）」**

- API：`POST /api/v1/projects/{id}/requirements/structured-analyses`
- 服务：`StructuredClauseService`（复用训练 prompt/schema）
- 评测：`target=extraction`（`StructuredExtractionAdapter`）

## Adapter 路径语义

| 变量 | 作用 |
| --- | --- |
| `LLM_LORA_HOST_PATH` | **仅** Compose 宿主机 volume source（默认相对 `infra/`：`../training/llamafactory/outputs/qwen3_8b_lora_course`） |
| `LLM_LORA_ADAPTER_PATH` | **运行时** Adapter 路径：宿主脚本默认 `training/...`；**容器内固定** `/models/bidpilot-course-lora` |

Compose 将 `HOST_PATH` → `/models/bidpilot-course-lora`，并强制容器环境 `LLM_LORA_ADAPTER_PATH=/models/bidpilot-course-lora`，避免宿主路径泄漏进容器。

## Compose 强制 preflight

1. Host：`bash scripts/check_lora_adapter.sh`
2. Container entrypoint：`scripts/vllm_compose_entrypoint.sh` → `validate_adapter_for_serving` → 再启动 vLLM
3. `LLM_ENABLE_LORA=false` 时明确跳过 Adapter 校验（Base-only）
4. 自动化测试断言 compose 含 entrypoint 且 target 固定为 `/models/bidpilot-course-lora`

标准命令（仓库根目录）：

```bash
bash scripts/check_lora_adapter.sh
docker compose --env-file .env \
  -f infra/docker-compose.yml -f infra/docker-compose.llm.yml \
  --profile llm config
./scripts/serve_qwen3_vllm.sh   # 或 compose --profile llm up -d
```

## 真实在线验收记录（结构化收尾）

| 项 | 值 |
| --- | --- |
| 验收时间 | 2026-07-23（Asia/Shanghai） |
| 起始 HEAD | `5d8007df8510811e96520d24d28cb76106a57842` |
| vLLM | `0.25.1`；`/v1/models`：`bidpilot-qwen3-8b`，`bidpilot-qwen3-8b-course-lora` |
| Adapter | files_ok；rank=16；`base_model_match=match` |
| Compose config | source=`<repo>/training/.../qwen3_8b_lora_course` → target=`/models/bidpilot-course-lora`；entrypoint=`vllm_compose_entrypoint.sh` |
| Base 结构化 | served=`bidpilot-qwen3-8b`；`fallback=false`；**schema_valid=false**（自然语言散文，符合 Base 行为） |
| LoRA 结构化 | served=`bidpilot-qwen3-8b-course-lora`；`fallback=false`；**schema_valid=true**；JSON `category=technical, mandatory=false, risk_level=medium, confidence=0.55` |
| Ask + LoRA | **422** `capability_unsupported`（预期） |
| Base Eval run | `8ccbd051-d9eb-41d9-9b00-7dabf754b180`（status=`partial`） |
| LoRA Eval run | `1cf3457c-b936-48f7-aaf6-2a5942743027`（status=`completed`） |
| compare | `config_diff` 含 model_id / served_model_name / model_type / adapter_version |
| export | LoRA JSON 含 model 元数据与 git_commit |

演示 case：同一条款「▲号技术参数但不作为无效响应」→ LoRA 输出合法 `requirement_classify` JSON；Base 输出非 JSON 散文。

### 此前 Grounded Ask 实验（保留）

Course LoRA 曾可路由到 Ask，但终态常因缺 `[S1]` 引用校验失败——说明任务协议不匹配，不是服务未启动。现已用 capability 禁止该组合。

## 离线评测（seed=14，n=40，固定 test）

| 指标 | Base | LoRA |
| ---: | ---: | ----: |
| JSON parse rate | 0% | 87.5% |
| Schema validity | 0% | 87.5% |
| Required field coverage | 0% | 72.5% |
| Verdict accuracy | 0% | 66.7% |
| Field exact match | 0% | 45.0% |
| Citation validity | 0% | 0% |
| Parse failures | 40 | 5 |

结论：LoRA **显著改善结构化输出**；字段/部分 verdict 仍有限；citation 未改善；**不足以**宣称完整领域判断能力显著提升。

## 固定演示流程

1. `bash scripts/check_lora_adapter.sh` + 启动 vLLM（含 LoRA）。
2. `GET /api/v1/models`：LoRA `served=true` 且 capabilities 含 `structured_extraction`。
3. 要求页跑 Base / LoRA 条款分析，对比 schema_valid。
4. 评测中心 target=`extraction`，各跑 Base/LoRA，compare + export。
5. 口头说明：Ask 只用 Base；Course LoRA = 结构化 JSON；course_pilot ≠ human_gold。

## 相关文档

- [`step13_lora.md`](step13_lora.md)
- [`course_demo.md`](course_demo.md)
- [`evaluation_center.md`](evaluation_center.md)
