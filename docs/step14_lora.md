# BidPilot LoRA 在线服务（Step 14）

在 Step 13 **course_pilot** 数据与训练交付之上，本步把 Course LoRA 接到 **vLLM 在线推理** 与**结构化条款分析**业务（Ask / 评测中心能力边界已校正）。
**诚实口径**：`course_pilot ≠ human_gold`；仅当 vLLM 实际 **served** 该 LoRA 模块时，UI 才显示「在线」。

## 完成状态分层

| 层级 | 状态 | 说明 |
| --- | --- | --- |
| 配置完成 | **是** | Compose Base-only + LoRA overlay、entrypoint strip/append、Base↔Adapter 强校验、target↔capability 权威映射、严格 Pydantic schema、结构化结果持久化 |
| 自动化测试完成 | **是** | capability 映射、严格 schema、Compose Base/LoRA 断言、Ask 不兼容拒绝、Eval metadata、FE 空项目面板与 Eval 模型过滤 |
| 当前机器真实执行完成 | **是** | vLLM Base+LoRA；结构化 Base/LoRA 持久化；extraction Eval compare/export |
| 未执行 / 阻断 | **无环境阻断** | 本机 GPU + Adapter 可用；**不进入 Step 15** |

## Course LoRA 真实任务协议

来源：`data_pipeline/configs/sft_tasks.yaml` + `data_pipeline/bidpilot_data/sft/build.py`。

| 项 | 约定 |
| --- | --- |
| 输入 | 条款原文（**无 RAG context**） |
| System | 任务专用中文（如「负责对条款进行分类并判断是否强制」） |
| User 前缀示例 | `判断以下条款的类别与是否强制：\n{text}` |
| 输出 | **紧凑 JSON**（无 Markdown、无 `[S1]`） |
| 校验 | 各任务 **严格 Pydantic**（`extra=forbid` + 类型约束），不再只查 key 是否存在 |

**因此 Course LoRA 不适合 Grounded Ask**（自然语言 + `[S1]`）。Ask 仅接受 `grounded_qa`。

## Capability 管理

公开字段 `capabilities`：

| model | capabilities |
| --- | --- |
| `qwen3-8b-base` | `grounded_qa`, `structured_extraction`, `agent_pipeline` |
| `qwen3-8b-lora-course` | `structured_extraction` |

Evaluation target → required capability（后端权威：`target_capabilities.py`）：

| target | required_capability |
| --- | --- |
| `rag` | `grounded_qa` |
| `extraction` | `structured_extraction` |
| `agent_pipeline` | `agent_pipeline` |
| `compliance` | `compliance_analysis`（规则适配器；模型选择非主路径） |
| `matching` / `drafting` | `None` |

- Grounded Ask / Eval `rag`：`required_capability=grounded_qa`；Course LoRA → **422** `capability_unsupported`，**禁止静默回退**。
- 结构化条款分析 / Eval `extraction`：要求 `structured_extraction`。
- 前端 Eval 表单按 `required_capability` 过滤模型（Course LoRA 仅出现在 extraction）。

## 最终接入的业务功能

**项目 → 要求 →「条款结构化分析（Course LoRA 协议）」**

- API：`POST /api/v1/projects/{id}/requirements/structured-analyses`（持久化）
- API：`GET /api/v1/projects/{id}/requirements/structured-analyses`（列表）
- 表：`structured_clause_analyses`（模型元数据 + schema_valid + parsed_json）
- 空项目状态也可进入结构化分析（不再被 Empty 挡住）
- 评测：`target=extraction`（`StructuredExtractionAdapter`）

## Adapter 路径语义

| 变量 | 作用 |
| --- | --- |
| `LLM_LORA_HOST_PATH` | **仅** Compose LoRA overlay 的宿主机 volume source（默认相对 `infra/`：`../training/.../qwen3_8b_lora_course`） |
| `LLM_LORA_ADAPTER_PATH` | **运行时** Adapter 路径：宿主脚本默认 `training/...`；**容器内固定** `/models/bidpilot-course-lora` |

## Compose：真实 Base-only + LoRA overlay

| 文件 | 行为 |
| --- | --- |
| `infra/docker-compose.llm.yml` | **Base-only**：无 Adapter volume、无 `--enable-lora` / `--lora-modules`；`LLM_ENABLE_LORA: "false"`（字面量，不受 `.env` 污染） |
| `infra/docker-compose.llm.lora.yml` | overlay：挂载 Adapter、`LLM_ENABLE_LORA: "true"`、追加 LoRA CLI |
| `infra/docker-compose.llm.local.yml` | 仅本地基座权重；LoRA 仍靠 overlay + entrypoint append |
| `scripts/vllm_compose_entrypoint.sh` | `false` 时剥离 LoRA argv；`true` 时 preflight + 补齐 LoRA argv |

标准命令（仓库根目录）：

```bash
# Base-only
docker compose --env-file .env \
  -f infra/docker-compose.yml -f infra/docker-compose.llm.yml \
  --profile llm up -d

# Base + Course LoRA
bash scripts/check_lora_adapter.sh
docker compose --env-file .env \
  -f infra/docker-compose.yml -f infra/docker-compose.llm.yml \
  -f infra/docker-compose.llm.lora.yml \
  --profile llm up -d
```

宿主脚本 `./scripts/serve_qwen3_vllm.sh` 仍可读 `.env` 的 `LLM_ENABLE_LORA`（与 Compose 文件选择独立）。

## 真实在线验收记录（第 14 步最终收尾）

| 项 | 值 |
| --- | --- |
| 验收时间 | 2026-07-23（Asia/Shanghai） |
| 起始 HEAD | `4b9ecaa5be705a8ea3a993cc54eac7296cc74622` |
| vLLM | Base `bidpilot-qwen3-8b` + LoRA `bidpilot-qwen3-8b-course-lora` 均 served |
| Compose Base-only | `LLM_ENABLE_LORA=false`；无 LoRA volume/flags（`.env=true` 亦不污染） |
| Compose + LoRA overlay | Adapter → `/models/bidpilot-course-lora`；含 `--lora-modules` |
| Base 结构化 | id=`6509f600-...`；served=`bidpilot-qwen3-8b`；`fallback=false`；**schema_valid=false**（散文/非 JSON，严格校验拒绝） |
| LoRA 结构化 | id=`ce6215b5-...`；served=`bidpilot-qwen3-8b-course-lora`；`fallback=false`；**schema_valid=true**；JSON `category=technical, mandatory=false, risk_level=medium, confidence=0.55` |
| 持久化 | `GET .../structured-analyses` total≥2，含上述 id |
| Ask + LoRA | **422** `capability_unsupported`（预期） |
| Eval capabilities | `rag→grounded_qa`，`extraction→structured_extraction`，`agent_pipeline→agent_pipeline` |
| Base Eval run | `126dc35d-831d-4538-adf8-85dc482af091`（status=`partial`，extraction，seed=14，limit=3） |
| LoRA Eval run | `69578599-fed7-4456-8da3-dc9930fb8a43`（status=`partial`） |
| compare | `config_diff.changed` 含 model_id / served_model_name / model_type / adapter_version / model_display_name |
| export | LoRA JSON 含 model 元数据与 `source_commit_sha` / git_commit |

演示 case：同一条款「▲号技术参数但不作为无效响应」→ LoRA 输出合法 `requirement_classify` JSON；Base 输出非 JSON 散文。

### 此前 Grounded Ask 实验（保留）

Course LoRA 曾可路由到 Ask，但终态常因缺 `[S1]` 引用校验失败——说明任务协议不匹配。现已用 capability 禁止该组合。

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

结论：LoRA **显著改善结构化输出**；字段/部分 verdict 仍有限；citation 未改善；**不足以**宣称完整领域判断能力显著提升。**未更换** test split / seed=14。

## 固定演示流程

1. Base-only 或加 `llm.lora.yml` 启动 vLLM；Host `check_lora_adapter.sh`（LoRA 时）。
2. `GET /api/v1/models`：capabilities 正确；LoRA `served=true`。
3. 要求页（含空项目）跑 Base / LoRA 条款分析，对比 schema_valid；确认列表 API 有记录。
4. 评测中心 target=`extraction`，各跑 Base/LoRA，compare + export。
5. 口头说明：Ask 只用 Base；Course LoRA = 结构化 JSON；course_pilot ≠ human_gold。

## 相关文档

- [`step13_lora.md`](step13_lora.md)
- [`course_demo.md`](course_demo.md)
- [`evaluation_center.md`](evaluation_center.md)
