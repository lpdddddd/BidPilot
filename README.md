# BidPilot

BidPilot 面向招投标场景：帮助团队管理招标/企业文档，基于 **RAG + 引用** 做证据约束问答，用确定性规则与 **LangGraph Agent** 做合规审查与可恢复业务闭环，并用 **评测中心** 对能力做可复现评估。

### 当前能力（诚实口径）

| 能力 | 状态 |
| --- | --- |
| 项目 / 文档上传、解析、结构感知 Chunk、混合检索索引 | 可用 |
| 带来源 RAG 问答（本地 **Qwen3-8B @ vLLM**） | 可用（需启动 LLM） |
| 需求抽取 / 材料匹配 / 人工审核 / 响应草稿 | 业务路径可用；**评测 target 未 case 级接线** |
| 合规规则引擎 + Agent（Step/ToolCall 状态、SSE 时间线） | 可用 |
| 评测中心（suite / run / case、对比、导出 JSON·CSV·MD） | 可用；reference 为 auto_reference，**human Gold=0** |
| 领域微调 / LoRA（Step 13–14） | **course_pilot 可用**：QC→训练→评测→注册→**结构化条款分析**在线对比；Ask 仅 Base（`grounded_qa`）；Course LoRA 仅 `structured_extraction`；Compose Host/Adapter 路径分离且 entrypoint 强制 preflight；**非 human_gold** → [`docs/step13_lora.md`](docs/step13_lora.md)、[`docs/step14_lora.md`](docs/step14_lora.md) |

**课程演示走查**（创建项目 → 上传样例 → 解析索引 → RAG → 合规/Agent → 评测对比导出）：[`docs/course_demo.md`](docs/course_demo.md)。

> 布局说明：工作区根目录 `/root/autodl-tmp` 已存在其他项目，因此本工程位于 **`bidpilot/`** 子目录。

## 系统架构

- **FastAPI backend**：业务 API、文档管道、RAG、合规、Agent、评测中心
- **React frontend**：项目 / 文档中心、检索与问答、智能审查、Agent 时间线、评估中心
- **PostgreSQL**：业务与文件元数据、Agent / 评测 run
- **MinIO**：原始与解析文件对象存储
- **Qdrant + OpenSearch**：Dense + BM25 混合检索
- **Redis**：缓存 / 任务协调预留
- **本地 vLLM**：`Qwen/Qwen3-8B`（served name `bidpilot-qwen3-8b`）
- **training/llamafactory**：ShareGPT 导出与 QLoRA 配置（训练在外部 LLaMAFactory）

详见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) 与 [docs/DATABASE.md](docs/DATABASE.md)。

## 目录结构

```text
bidpilot/
├── backend/                 # FastAPI + SQLAlchemy + Alembic
├── frontend/                # React + Vite + Ant Design
├── data_pipeline/           # 采集/解析/切块/标注/SFT 流水线
├── training/llamafactory/   # QLoRA 配置与 ShareGPT 导出
├── datasets/                # raw/interim/processed/gold/silver/eval
├── demo_data/               # 本地演示数据包（可被 import 脚本导入）
├── infra/docker-compose.yml # postgres/redis/minio/qdrant
├── docs/
└── scripts/
```

## 环境要求

- Python 3.11+
- Node.js 18+（前端）
- Docker / Docker Compose（基础设施；若不可用可使用本地 PostgreSQL）
- 外部 LLaMAFactory 安装目录（通过 `LLAMAFACTORY_HOME` 配置）

## 初始化步骤

```bash
cd bidpilot
cp .env.example .env
# 编辑 .env 中的密码与服务地址（不要提交真实密钥）

# 推荐：conda 环境
conda create -n bidpilot python=3.11 -y
conda activate bidpilot

make backend-install
make frontend-install   # 需要 Node/npm
make infra-up           # 需要可用的 Docker
make migrate
```

一条命令迁移：

```bash
make migrate
# 或
bash scripts/init_db.sh
```

## 演示数据导入

若存在 `bidpilot_demo_pack` 或 `demo_data/`（含 `project_info.json` 等）：

```bash
make import-demo
# dry-run
python scripts/import_demo_data.py --dry-run
```

脚本特性：

- 不修改原始文件
- 导入 projects / requirements / company_profiles / requirement_matches
- 尽量保留原始 `requirement_id`
- 可重复执行，避免重复插入

上传样例招标文本：`demo_data/sample_tender.txt`。完整 UI 走查见 [`docs/course_demo.md`](docs/course_demo.md)。

## 前后端启动

```bash
make backend    # http://localhost:8000/docs
make frontend   # http://localhost:5173
make infra-up   # 基础设施（若尚未启动）
```

## 文档上传与解析

前置条件：PostgreSQL 已迁移（`make migrate`）、MinIO 可用（`make infra-up` 或本地 MinIO，
bucket 由 compose 的 minio-init 自动创建；`.env` 中配置 `MINIO_*`）。

- 上传入口：项目详情页「文档中心」，或 `POST /api/v1/projects/{id}/documents/upload`
- 支持格式：PDF、DOCX、TXT、HTML/HTM、XLSX（单文件默认最大 50MB）
- 不支持自动解析：DOC、WPS；扫描 PDF 会如实标记为 `ocr_required`（OCR 后续步骤接入）
- 解析状态：`pending → processing → success / ocr_required / failed`，失败可在 UI 重新解析
- 原文与解析文本均存 MinIO：`projects/{project_id}/documents/{document_id}/{original,parsed}/...`
- 预览接口只返回前 N 字符（默认 5000）；下载走 MinIO 预签名 URL，密钥不暴露给前端

## Chunk 与元数据（结构感知切分）

解析成功的文档会自动构建 Chunk（也可在文档中心手动触发/重建）：

- 切分器：`bidpilot-structure-chunker 1.1.0`（`backend/app/services/chunker.py`），规则式、无 LLM
- 参数与 `data_pipeline/configs/pipeline.yaml` 对齐：`min_tokens=40`、`target_tokens=500`、
  `max_tokens=800`、`overlap_tokens=80`；token 数由 tiktoken `cl100k_base` 实际计算；
  overlap 动态收缩，最终 Chunk（含 overlap 前缀）token 数始终 `<= max_tokens`
- 边界优先级：标题 → 段落 → 列表/表格行 → 中文句末标点 → 硬切分；识别「第X章 / 一、/（一）/
  1.1 / 第X条 / 附件」等中文招投标结构，输出 `section` 与 `section_path`，识别不出不编造
- 溯源：每个 Chunk 记录相对 `extracted.txt` 的字符区间（`source_char_start/end`、
  `core_char_start/end`、`overlap_prefix_chars`）与内容 SHA-256；PDF 解析同时生成
  `parsed/page_index.json`（每页真实字符区间），Chunk 页码由区间求交得出，
  TXT/DOCX/HTML/XLSX 无可靠页码时保持 null
- 状态：`document.metadata_json.chunking`（pending/processing/success/failed），
  重建在单事务内删旧写新，失败保留旧 Chunk
- API：`POST .../documents/{id}/chunk`（触发/重建，未解析成功返回 409）、
  `GET .../chunks?skip&limit`、`GET .../chunk-summary`

## 混合检索：Qdrant Dense + OpenSearch BM25 + 重排

Chunk 构建成功后自动建立索引（也可在文档中心手动建立/重建），随后可在项目详情页
「知识检索」中进行真实混合检索，并可选进入「带来源问答」模式。

流程：query → bge 向量化 → Qdrant dense top_k ∥ OpenSearch BM25 top_k（并行召回）
→ 按 chunk_id 合并 → RRF 融合（`score = Σ weight / (rrf_k + rank)`，默认 k=60、双路权重 1）
→ 取融合 top 20 → Cross-Encoder 真实重排 → 返回 top 8。

- 模型（`.env` 可替换，进程内单例加载，检测到 CUDA 自动用 GPU）：
  - Embedding：`BAAI/bge-small-zh-v1.5`（512 维，cosine，查询侧带 bge 指令前缀）
  - Reranker：`BAAI/bge-reranker-base`（Cross-Encoder，输入 query-chunk pair）
- Qdrant：collection `bidpilot_chunks_v1`，point id 由 `document_id + chunk_index +
  content_hash` 确定性生成（uuid5），payload 含 project/document/chunk 全部溯源元数据；
  检索强制按 `project_id` 过滤，项目间数据不串
- OpenSearch：index `bidpilot_chunks_v1`，`chunk_id` 为稳定 `_id`，字符 1~2 gram
  analyzer 支持中文 BM25（无付费插件），可检索 content/section/clause_id/file_name，
  可按 project/document/type 过滤，不存 embedding；PostgreSQL 亦不存向量
- 一致性：重建索引先删旧 Qdrant points 与 OpenSearch docs 再写新；Chunk 重建成功后
  自动触发重新索引；索引状态与错误记录在 `document.metadata_json.indexing`
  （独立于 `parse_status` 与 `chunking.status`）
- 降级与错误：Qdrant/OpenSearch 未启动、模型不可用、索引未建立均返回真实错误
  （`{"message","detail"}`，503/409）；reranker 不可用时显式降级为 RRF 排序，
  `rerank_score` 置空并在 trace 标记 `reranker_unavailable`，绝不伪造分数
- API：
  - `POST /api/v1/projects/{pid}/documents/{did}/index`（建立/重建，需 parse 与 chunk 均成功）
  - `GET  /api/v1/projects/{pid}/documents/{did}/index-summary`
  - `POST /api/v1/projects/{pid}/search`（body：`query`、`top_k`、`document_types`、`document_ids`）
  - `POST /api/v1/projects/{pid}/reindex`（项目级批量重建）
- 响应含每条结果的 dense/bm25 rank+score、`rrf_score`、`rerank_score`、章节/条款/页码
  与 `retrieval_trace`（各阶段真实耗时、候选数、模型与索引名、降级标记）

首次使用需下载模型（国内可 `export HF_ENDPOINT=https://hf-mirror.com`）。

## 带来源文档问答（Grounded RAG，第 6 步）

在混合检索之上增加受证据约束的问答：**只使用本轮检索到的 chunks 作为上下文**，
由本地 vLLM 上的 `Qwen/Qwen3-8B`（served name: `bidpilot-qwen3-8b`）生成带 `[S1]`
引用的 Markdown 回答。后端校验引用只能映射到本轮 source id；未知引用或无来源的
实质性结论会返回 validation error。检索为空时**不调用** LLM。

SSE 采用证据优先语义（Scheme A）：服务端可从 vLLM 流式读 token，但**不会**把未校验
正文以 `delta` 推给前端；校验通过后才发 `final`（含完整 answer / citations / trace），
校验失败只发 `error`。

- 统一模型配置（脚本与 Compose 共用，**不硬编码机器路径**）：
  - `LLM_MODEL`：服务暴露名 / 后端调用名（默认 `bidpilot-qwen3-8b`）
  - `LLM_MODEL_SOURCE`：Hugging Face repo id（默认 `Qwen/Qwen3-8B`）
  - `LLM_MODEL_PATH`：可选本地权重目录（非空且含 `config.json` 时优先；默认为空）
  - 另有 `LLM_ENABLED`、`LLM_BASE_URL`、`LLM_API_KEY`、超时与 RAG 截断参数
- 启动模型（不纳入 `make infra-up`，可选 profile `llm`）：
  - **本机脚本（5090）**：
    ```bash
    # Hub 下载/缓存
    unset LLM_MODEL_PATH
    ./scripts/serve_qwen3_vllm.sh

    # 或本地权重
    export LLM_MODEL_PATH=/absolute/path/to/Qwen3-8B
    ./scripts/serve_qwen3_vllm.sh
    ```
  - **Compose · Hub Base-only**（无 LoRA volume / flags）：
    ```bash
    docker compose --env-file .env \
      -f infra/docker-compose.yml -f infra/docker-compose.llm.yml \
      --profile llm up -d
    ```
  - **Compose · Hub + Course LoRA**（叠加 overlay）：
    ```bash
    bash scripts/check_lora_adapter.sh
    docker compose --env-file .env \
      -f infra/docker-compose.yml -f infra/docker-compose.llm.yml \
      -f infra/docker-compose.llm.lora.yml \
      --profile llm up -d
    ```
  - **Compose · 本地挂载**（`LLM_MODEL_PATH` 必须非空；LoRA 仍加 `llm.lora.yml`）：
    ```bash
    export LLM_MODEL_PATH=/absolute/path/to/Qwen3-8B
    docker compose --env-file .env \
      -f infra/docker-compose.yml -f infra/docker-compose.llm.yml \
      -f infra/docker-compose.llm.local.yml \
      --profile llm up -d
    ```
  - Qwen3-8B 用于 RAG Ask；Course LoRA（course_pilot）用于结构化条款分析（非 Ask），见 [`docs/step14_lora.md`](docs/step14_lora.md)
- API：
  - `POST /api/v1/projects/{pid}/ask`（`stream=false` JSON /
    `stream=true` SSE：`retrieval` → `generation_started` → `final` / `error`；可选 `model_id`）
  - `GET /api/v1/models`、`GET /api/v1/models/active`（含 served 探测）
  - `GET /api/v1/health/llm`（真实连通性 + `load_target`）
- 验收：
  - Mock（无 GPU）：`make rag-smoke`
  - Health / ask / SSE live smoke（5090 + 已索引项目）：
    ```bash
    make infra-up && make backend
    export LLM_MODEL_PATH=/absolute/path/to/Qwen3-8B   # 若用本地权重
    ./scripts/serve_qwen3_vllm.sh                      # 另开终端；可启用 LoRA
    # API .env: LLM_ENABLED=true
    RAG_SMOKE_LIVE=1 make rag-smoke-live
    ```
  - 脱敏摘要写入 `docs/acceptance/rag_smoke_*.json`（已 gitignore，不入库原文）
- 前端：「检索证据」保留；「带来源问答」仅 Base（`grounded_qa`）；Course LoRA 在「要求」页做条款结构化分析
- 真实联调记录：`docs/rag_e2e_acceptance.md`
- 后续能力（匹配 / Agent / 评测）见下文各节；LoRA 训练见 [`docs/step13_lora.md`](docs/step13_lora.md)，在线服务见 [`docs/step14_lora.md`](docs/step14_lora.md)

## 招标要求结构化抽取（第 7 步）

从项目招标类文档的真实 chunks 抽取可追溯 Requirement + EvidenceLink（待人工审核）。
详见 `docs/requirement_extraction.md`。

- 文档范围：`tender` / `announcement` / `amendment` / `contract`（不含企业侧材料）
- 异步 run 表：`requirement_extraction_runs`；证据校验 + 幂等去重 + 冲突标记（不自动裁决）
- `force=true` 成功语义三分：合法空结果 / 已校验非空 / 无效或不完整（含「候选全部证据校验失败」→失败且保留旧数据）
- API：`POST/GET .../requirements/extractions`、`GET .../requirements`
- 前端：项目详情「需求清单」Tab

## 企业材料与招标要求匹配（第 8 步）

将已验证 Requirement 与当前项目企业侧材料对照，生成待人工审核的 Match（双侧证据）。
详见 `docs/requirement_matching.md`。

- 企业侧范围：`company_profile` / `qualification` / `case` / `personnel` / `product`（严禁招标侧文档）
- 新表：`requirement_match_runs`、`requirement_evidence_matches`、`requirement_evidence_match_links`（遗留演示用 `requirement_matches` 不动）
- 固定状态：`supported` / `partially_supported` / `insufficient_evidence` / `conflicting_evidence` / `not_applicable`
- API：`POST/GET .../requirement-matches/runs`、`GET .../requirement-matches`
- 前端：项目详情「材料匹配」Tab；`insufficient_evidence` 文案为「当前材料未找到充分证据」

## 匹配结果人工审核（产品步骤 · 审核闭环）

对自动 Match 建立可审计审核闭环（confirm / reject / needs_more_material / reopen）。
详见 `docs/requirement_match_review.md`。

- 追加式 `RequirementMatchReview` 审计；Match 保留原始自动结果与证据链
- 已审核 Match 受 `force` 保护；reopen 后可 supersede 再匹配且不丢历史
- 无完整认证：`actor_authn=unverified_local_operator` + 本地 `actor_label`

## 流程表第 9 步：规则检查工具

对项目要求 / 匹配 / 草稿做**确定性**合规检查（无 LLM、无 LangGraph）。
详见 `docs/compliance_rules.md`。

> 编号说明：流程表将「规则检查工具」列为第 9 步；上文「匹配结果人工审核」为先序产品能力，二者并存。

- 表：`compliance_runs`、`compliance_findings`；引擎版本 `compliance-rules-1.2.0`
- 规则分类：coverage / evidence / qualification_risk / draft_safety / consistency（A001–E006）
- API：`POST/GET .../compliance/runs`、`.../latest`、`.../findings`、`.../rules`；失败 run 仍可查询（含 `error_code`）
- Tools：`check_requirement_coverage` 等 5 个 Pydantic I/O 包装
- 前端：「智能审查」`/review`（`ComplianceReviewPage`，含 info StatCard）；Dashboard 能力标记 ready
- 离线报告（确定性；正式 A–E 引擎，无 REF_*）：
  ```bash
  cd backend && python -m app.services.compliance.offline_eval
  ```
  产出含 `coverage_matrix`（含 `focus_sample_count`）；当前正式规则 **29 条已执行**，其中通常 **3 条**有直接 reference 可对照（如 A001 / C003 / E003）。**自动 reference 不是人工 Gold**；无 focus 样本的规则不得宣称 100% 一致率。
- **Step 9 修复**：双边证据严格定义、C005/E003/E005 语义、失败 run 持久化、离线一致性评估
- **限制**：非法律意见 / 非人工 gold；不足则 unknown，不编造
- LoRA 审查 / 自动投标结论：审查类产品能力未交付；course_pilot LoRA 训练与在线服务见 [`docs/step13_lora.md`](docs/step13_lora.md)、[`docs/step14_lora.md`](docs/step14_lora.md)

## LangGraph Agent 业务闭环（第 10 步）

用 LangGraph 编排检索 → 抽取 → 匹配 → 合规 → 草稿校验/修订，形成可恢复业务闭环。
详见 [`docs/agent_workflow.md`](docs/agent_workflow.md)。

- 图版本 `bidpilot-agent-1.0.0`；节点只编排、调用既有 Services/Tools（合规无 LLM）
- **草稿校验**：`validate_draft` 正式调用 `check_draft_compliance`（默认 `draft_safety` + `consistency`）；`draft_findings` 入状态；`force_draft_validation` 仅兼容旧单测
- **Checkpoint**：`thread_id=str(run.id)`；`completed_nodes` 跳过已完成节点；可选还原 `lg_memory` 后 `stream(None)` 续跑，否则 START + skip
- **事件**：统一 `AgentEvent.sequence`（`AgentRun.event_sequence` 行锁分配）；`AgentStep` / `ToolCall` 关联
- 表：扩展 `agent_runs` + `agent_checkpoints` + `agent_events`
- 关键资格 finding：默认 `block_on_critical_qualification=true` → `blocked`；否则 risk-only 草稿 + `completed_with_warnings`
- API：`POST/GET .../agent-runs`、`.../latest`、`.../events`、`.../result`、`.../resume`、`.../retry`
- **限制**：非法律意见 / 非人工 gold；证据不足则 warning / blocked；不编造资质

## Agent 实时执行时间线（第 11 步）

异步跑图 + 真实 tool / 节点生命周期 + SSE/轮询时间线 UI。详见 [`docs/agent_workflow.md`](docs/agent_workflow.md)（Step 11 节）。

收尾硬化（已落地）：

1. **持久化 attempt**：DB `FOR UPDATE` 分配，`(run, node, attempt)` 唯一；API retry 接续最大值。
2. **原子 claim**：resume/retry 数据库 claim；仅 claimed 才挂 BackgroundTask。
3. **完整 Graph+Service 中途可见性**：成功 / 失败 / retry 路径见 `test_agent_persist_attempt_graph.py`（含 HTTP retry、并发 retry claim、BackgroundTask 登记失败释放）。
4. CI：`ruff format --check` + `mypy app`。

- **异步启动**：`POST` 持久化 `AgentRun` 后立即返回；图在 `BackgroundTasks` 执行；resume/retry 默认 prepare + 后台；`?sync=true` 同步；同 run 去重
- **事件/节点/tool 生命周期**：attempt 从 1；失败 attempt 无 `node_completed`；逻辑 `call_id` 稳定；幂等键含 attempt
- **中途可见**：短提交；完整 Graph 证明见 `test_full_graph_service_midrun_visibility` / `test_full_graph_tool_failure_midrun_visibility`（barrier，无 sleep）
- **项目作用域 / SSE / 前端**：归属不匹配 404；SSE + 轮询回退；`AgentLoopPanel`
- Agent 侧未做：WebSocket、CoT 流式展示

## 评测中心（第 12 步）

项目级自动评测：复用 `datasets/eval/reference/`（**140 auto_reference，human Gold=0**，统计由 loader 动态生成），确定性指标 + hard gates，前端评测中心。详见 [`docs/evaluation_center.md`](docs/evaluation_center.md)。

- 生产 API **始终后台**执行；公开 schema 无 `sync` / `fixture_path` / `fail_case_keys`
- FE/BE 契约统一：`items` 分页、`target`/`case_limit`/`evaluator_profile`、结构化 profiles
- Target 结构隔离：`TargetCaseInput` + `TargetExecutionContext`；`PrivateReferenceBundle` 仅 evaluator
- RAG scope=`EvaluationRun.project_id`；每 case 独立 Session；有界 cancel；幂等唯一约束兜底
- `deterministic_fake` 不进生产；extraction / matching / drafting 评测 target **未 case 级接线**（前端显示「当前版本暂未开放」等友好文案，不暴露 reason_code）
- Citation 深链由后端校验 `valid` / `invalid_reason` / `detail_url`
- 结构化抽取 / Agent 评测可经 `target_config.model_id` 选择 Base 或 Course LoRA（须 served 且 capability 匹配）；Ask 仅 Base。见 [`docs/step13_lora.md`](docs/step13_lora.md)、[`docs/step14_lora.md`](docs/step14_lora.md)

## 可追溯响应准备草稿

基于已确认 Match 与可定位证据，生成待人工复核的响应准备草稿（非投标提交文件）。
详见 `docs/proposal_drafting.md`。

- 正向正文仅用 `confirmed` + `active` + `supported|partially_supported`
- 不可变版本 / 来源快照 / 人工修订与审核；Markdown·DOCX 仅 reviewed 可导出
- 未交付：自动投标结论 / 价格与法律承诺生成 / 投标提交；LoRA 训练与在线见 [`docs/step13_lora.md`](docs/step13_lora.md)、[`docs/step14_lora.md`](docs/step14_lora.md)

## 测试 / 静态检查

```bash
make format
make lint
make test
make rag-smoke                 # mock RAG acceptance (no vLLM)
# RAG_SMOKE_LIVE=1 make rag-smoke-live   # real API + vLLM (5090)
make dataset-test
make validate-sft-internal     # ShareGPT structure only
make validate-sft-smoke        # internal + LF preprocess smoke (64/split)
make validate-sft-llamafactory # real LLaMAFactory full preprocess (fails if LF missing)
make validate-sft-real         # internal + full LLaMAFactory preprocess
make validate-sft              # alias of validate-sft-real
make validate-sft-sample       # sample_sharegpt.json only
```

### PostgreSQL 集成测试

统一环境变量：`TEST_DATABASE_URL`（兼容 `DATABASE_URL_TEST`）。默认连接 `bidpilot_test`；库名须含 `_test`，禁止误连开发/生产库。

```bash
./scripts/start_test_postgres.sh   # docker compose -f infra/docker-compose.test.yml（5433）
export TEST_DATABASE_URL='postgresql+psycopg://bidpilot:bidpilot_test@127.0.0.1:5433/bidpilot_test'
cd backend && alembic upgrade head && pytest
```

本地已有 `bidpilot_test` 时可直接 `export TEST_DATABASE_URL=postgresql+psycopg://bidpilot@127.0.0.1:5432/bidpilot_test`。数据库不可达时测试会 **明确失败**（不再大量 skip）。详见 `docs/DATABASE.md`。

### Agent 引用定位

前端引用链接：`/projects/{id}?tab=documents&document_id=&page=&chunk_id=`。项目详情页会切换文档 Tab、打开本项目文档并高亮 chunk；无效/跨项目来源显示安全提示。Agent 实时时间线（SSE / 轮询、`AgentLoopPanel`）见 [`docs/agent_workflow.md`](docs/agent_workflow.md)（统一 `AgentEvent.sequence`）。

### CI（GitHub Actions）

`.github/workflows/ci.yml`：

- **frontend**：Node 20 — `npm ci` / lint / **`npm test` 跑 3 次** / build
- **backend-postgres**：Postgres **16**（`postgres:16-alpine` service）— ruff、alembic upgrade/downgrade、Agent/合规/可见性等 pytest 后再跑全量 backend suite

## 数据流水线：从原始文件到 LLaMAFactory

`data_pipeline/` 是独立 Python 包（`bidpilot-data`），与业务后端解耦；后端不 import `llamafactory`。

```bash
make dataset-install

# 本地 demo（不依赖外网/模型 API，使用 demo_data + 规则标注）
make dataset-demo

# 分步执行
make dataset-bootstrap
make dataset-parse          # parse -> clean -> chunk
make dataset-label          # rules 模式；LLM 模式需配置 DATASET_MODEL_NAME
make dataset-review-export  # 导出人工审核 CSV
# 人工填写 decision/reviewer 后：
# python -m bidpilot_data review import --file datasets/review/exported/requirements_review.csv
make dataset-build-sft
make dataset-validate
make dataset-report
```

环境变量（见 `.env.example`）：

- `OPENAI_API_KEY` / `OPENAI_BASE_URL` / `DATASET_MODEL_NAME` — OpenAI-compatible 标注
- `DATABASE_URL` — 可选 `python -m bidpilot_data db import-*`

产出目录：

- `datasets/silver|gold|review|eval|sft/`
- `datasets/eval/reference/` — Step 1 auto reference eval set（`build-reference` / `make dataset-build-reference`）
- `datasets/reports/{dataset_statistics,validation_report,build_manifest}.json`
- `training/llamafactory/data/dataset_info.json`（注册 `bidpilot_sft_train(_qwen3)` 等）

质量约束：自动标注只能是 silver/pending；accept/corrected + reviewer 才能升 gold；按 `project_id` 划分 train/val/test。Auto reference 标签仅为 `auto_reference`/`silver`，**不是** human gold。

详情见 [data_pipeline/README.md](data_pipeline/README.md)、[docs/reference_dataset.md](docs/reference_dataset.md) 与 [DATASET_BUILD_REPORT.md](DATASET_BUILD_REPORT.md)。

## LLaMAFactory 数据导出与训练流程

1. 通过流水线生成 `datasets/sft/{train,validation,test}/sharegpt.json`，或：

```bash
python training/llamafactory/scripts/export_sft_dataset.py \
  --input datasets/sft/source/all.jsonl \
  --output-dir training/llamafactory/data/exported \
  --task-type requirement_classify \
  --require-json-assistant
```

2. 校验：

```bash
make validate-sft-internal     # ShareGPT structure only
make validate-sft-smoke        # internal + LF preprocess smoke (64/split)
make validate-sft-llamafactory # real LLaMAFactory full preprocess (fails if LF missing)
make validate-sft-real         # internal + full LLaMAFactory preprocess
make validate-sft              # alias of validate-sft-real
make validate-sft-sample       # sample_sharegpt.json only
make dataset-validate
```

3. 课程演示 LoRA（Step 13，course_pilot；**非** human_gold）可用仓库脚本启动；通用 ShareGPT 导出仍可在外部 LLaMAFactory 手动训练：

```bash
# Course pilot（推荐演示）
python training/llamafactory/scripts/prepare_course_pilot.py
CUDA_VISIBLE_DEVICES=0 bash training/llamafactory/scripts/run_course_train.sh smoke
# 详见 docs/step13_lora.md

# 或外部 LLaMAFactory + 通用配置模板
export LLAMAFACTORY_HOME=/path/to/LLaMA-Factory
cd "$LLAMAFACTORY_HOME"
llamafactory-cli train /absolute/path/to/bidpilot/training/llamafactory/configs/qwen3_8b_qlora_sft.yaml
```

在 YAML 中将 `dataset` 设为 `bidpilot_sft_train_qwen3`（或 `bidpilot_sft_train`），`dataset_dir` 指向 `training/llamafactory/data`。

在线挂载 Course LoRA：[`docs/step14_lora.md`](docs/step14_lora.md)（`./scripts/serve_qwen3_vllm.sh` + `--enable-lora`）。

详细说明见 [training/llamafactory/README.md](training/llamafactory/README.md)。
## 当前已完成功能

1. 工程目录与模块隔离；Alembic 迁移；Docker Compose（Postgres / Redis / MinIO / Qdrant / OpenSearch）
2. 项目与文档上传 / 解析 / Chunk / 混合检索索引
3. 带来源 RAG 问答（本地 Qwen3-8B @ vLLM + 引用校验 + SSE）
4. 需求抽取、材料匹配、人工审核、响应准备草稿
5. 确定性合规规则引擎；LangGraph Agent 闭环与实时时间线（Step / ToolCall）
6. 评测中心（capability、小跑、case 结果、compare、导出）
7. 演示数据导入（`make import-demo`）与课程走查 [`docs/course_demo.md`](docs/course_demo.md)
8. data_pipeline + course_pilot LoRA（Step 13）与在线服务（Step 14）；另保留通用 ShareGPT 导出 / QLoRA 配置模板
9. 要求页 / 评测 extraction：Base vs Course LoRA；Ask 仅 Base（须 `served=true` 才显示在线）

## 已知限制

- 评测 reference：**auto_reference**，**human Gold=0**；不得称为人工 Gold
- course_pilot ≠ human_gold；正式量产 LoRA 仍依赖人工审核 Gold
- LoRA「在线」仅当 vLLM 实际 served 该模块；注册或 Adapter 就绪 ≠ 在线
- extraction / matching / drafting：**业务功能存在**，但评测中心 target **未 case 级接线**
- Agent：无 WebSocket / 无 CoT 流式展示；非法律意见；证据不足则 warning / blocked
- OCR、完整认证授权、大规模采集尚未交付；自动投标结论 / 投标提交未交付

## 后续开发顺序建议

1. 人工审核沉淀 human gold（需求 / Match / RAG / SFT）
2. 合规公开源采集规模化 + OCR
3. 认证授权与组织权限
4. 多 GPU / 更大 rank 正式训练；LoRA 审查 / 自动投标结论 / 投标提交（远期）
5. 评测中心补齐 extraction / matching / drafting case 级接线
## Makefile 命令

| Command | 说明 |
| --- | --- |
| `make infra-up` | 启动基础设施 |
| `make infra-down` | 停止基础设施 |
| `make backend-install` | 安装后端依赖 |
| `make frontend-install` | 安装前端依赖 |
| `make migrate` | Alembic upgrade head |
| `make seed` | 演示导入 dry-run 提示 |
| `make backend` | 启动 API |
| `make frontend` | 启动前端 |
| `make test` | pytest |
| `make lint` | ruff + mypy |
| `make format` | ruff format/fix |
| `make import-demo` | 导入演示数据 |
| `make dataset-install` | 安装 data_pipeline |
| `make dataset-test` | 数据流水线测试 |
| `make dataset-demo` | 本地 demo 全流程 |
| `make dataset-build-sft` | 构建 ShareGPT SFT |
| `make dataset-validate` | 数据集校验 |
