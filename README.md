# BidPilot

BidPilot 是一个基于 RAG、LangGraph Agent 与领域模型微调的招投标文件分析与合规审查平台。

本仓库当前为 **工程脚手架阶段**：已打通目录结构、PostgreSQL 模型与 Alembic 迁移、基础 API、前端骨架，以及独立的 LLaMAFactory 训练目录。完整 RAG / Agent / 大规模采集 / LoRA 训练不在本阶段。

> 布局说明：工作区根目录 `/root/autodl-tmp` 已存在其他项目，因此本工程创建在 **`bidpilot/`** 子目录，而非再嵌套一层 `bidpilot/bidpilot`。

## 系统架构

- **FastAPI backend**：业务 API、健康检查、repository/service 分层
- **React frontend**：项目列表 / 创建 / 详情与文档占位页
- **PostgreSQL**：业务与文件元数据
- **MinIO**：原始文件对象存储
- **Qdrant**：向量检索预留
- **Redis**：缓存 / 任务预留
- **OpenSearch**：BM25 预留（本轮未启动）
- **training/llamafactory**：外部 LLaMAFactory 的配置与数据导出脚本

详见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) 与 [docs/DATABASE.md](docs/DATABASE.md)。

## 目录结构

```text
bidpilot/
├── backend/                 # FastAPI + SQLAlchemy + Alembic
├── frontend/                # React + Vite + Ant Design
├── data_pipeline/           # 采集/解析/切块/标注（骨架）
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

## 前后端启动

```bash
make backend    # http://localhost:8000/docs
make frontend   # http://localhost:5173
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
  - **Compose · Hub**：
    ```bash
    docker compose --env-file .env \
      -f infra/docker-compose.yml -f infra/docker-compose.llm.yml \
      --profile llm up -d
    ```
  - **Compose · 本地挂载**（`LLM_MODEL_PATH` 必须非空）：
    ```bash
    export LLM_MODEL_PATH=/absolute/path/to/Qwen3-8B
    docker compose --env-file .env \
      -f infra/docker-compose.yml -f infra/docker-compose.llm.yml \
      -f infra/docker-compose.llm.local.yml \
      --profile llm up -d
    ```
  - Qwen3-8B 仅用于基础 RAG 验证；**未完成微调**
- API：
  - `POST /api/v1/projects/{pid}/ask`（`stream=false` JSON /
    `stream=true` SSE：`retrieval` → `generation_started` → `final` / `error`）
  - `GET /api/v1/health/llm`（真实连通性 + `load_target`）
- 验收：
  - Mock（无 GPU）：`make rag-smoke`
  - Health / ask / SSE live smoke（5090 + 已索引项目）：
    ```bash
    make infra-up && make backend
    export LLM_MODEL_PATH=/absolute/path/to/Qwen3-8B   # 若用本地权重
    ./scripts/serve_qwen3_vllm.sh                      # 另开终端
    # API .env: LLM_ENABLED=true
    RAG_SMOKE_LIVE=1 make rag-smoke-live
    ```
  - 脱敏摘要写入 `docs/acceptance/rag_smoke_*.json`（已 gitignore，不入库原文）
- 前端：「检索证据」保留；「带来源问答」仅在 `final` 后展示确认答案
- 真实联调记录：`docs/rag_e2e_acceptance.md`
- **本步未包含**：微调 / LoRA、企业材料匹配、LangGraph Agent

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

## 匹配结果人工审核（第 9 步）

对自动 Match 建立可审计审核闭环（confirm / reject / needs_more_material / reopen）。
详见 `docs/requirement_match_review.md`。

- 追加式 `RequirementMatchReview` 审计；Match 保留原始自动结果与证据链
- 已审核 Match 受 `force` 保护；reopen 后可 supersede 再匹配且不丢历史
- 无完整认证：`actor_authn=unverified_local_operator` + 本地 `actor_label`

## 可追溯响应准备草稿（第 10 步）

基于已确认 Match 与可定位证据，生成待人工复核的响应准备草稿（非投标提交文件）。
详见 `docs/proposal_drafting.md`。

- 正向正文仅用 `confirmed` + `active` + `supported|partially_supported`
- 不可变版本 / 来源快照 / 人工修订与审核；Markdown·DOCX 仅 reviewed 可导出
- **尚未实现**：LoRA / Agent / 自动投标结论 / 价格与法律承诺生成 / 投标提交

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

3. 在外部 LLaMAFactory 中手动启动（本仓库不自动训练）：

```bash
export LLAMAFACTORY_HOME=/path/to/LLaMA-Factory
cd "$LLAMAFACTORY_HOME"
llamafactory-cli train /absolute/path/to/bidpilot/training/llamafactory/configs/qwen3_8b_qlora_sft.yaml
```

在 YAML 中将 `dataset` 设为 `bidpilot_sft_train_qwen3`（或 `bidpilot_sft_train`），`dataset_dir` 指向 `training/llamafactory/data`。

详细说明见 [training/llamafactory/README.md](training/llamafactory/README.md)。

## 当前已完成功能

1. 完整工程目录与模块隔离
2. 17 张核心业务表 + Alembic 初始迁移
3. Docker Compose：Postgres / Redis / MinIO（自动建桶）/ Qdrant / OpenSearch（单节点、关闭安全插件）
4. `GET /health`、`GET /ready`
5. 项目与文档元数据最小 API
6. 前端基础页面骨架
7. 演示数据导入脚本
8. LLaMAFactory ShareGPT 导出 / 校验与 QLoRA 配置模板
9. **可安装 data_pipeline**：采集/解析/切块/标注/审核/RAG&Agent 评测/SFT/校验/DB 导入
10. pytest 覆盖 backend 与 data_pipeline
11. 结构感知 Chunk 与字符/页码溯源
12. 混合检索：Qdrant Dense + OpenSearch BM25 + RRF 融合 + Cross-Encoder 重排
13. 带来源引用的文档问答（RAG：检索证据约束 + Qwen3-8B + 引用校验 + SSE）

## 后续开发顺序建议

1. 人工审核沉淀 gold 需求 / Match / RAG / SFT
2. 合规公开源采集规模化 + OCR
3. LangGraph Agent 合规审查工作流
4. 认证授权与组织权限
5. 多 GPU QLoRA 正式训练
6. 自动投标方案生成与投标提交（远期）

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
