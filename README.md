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

## 测试 / 静态检查

```bash
make format
make lint
make test
make validate-sft
```

## LLaMAFactory 数据导出与训练流程

1. 准备 JSON/JSONL 标注
2. 导出 ShareGPT messages：

```bash
python training/llamafactory/scripts/export_sft_dataset.py \
  --input datasets/gold/annotations.jsonl \
  --output-dir training/llamafactory/data/exported \
  --task-type requirement_classify \
  --require-json-assistant
```

3. 校验：

```bash
make validate-sft
```

4. 在外部 LLaMAFactory 中手动启动（本仓库不自动训练）：

```bash
export LLAMAFACTORY_HOME=/path/to/LLaMA-Factory
cd "$LLAMAFACTORY_HOME"
llamafactory-cli train /absolute/path/to/bidpilot/training/llamafactory/configs/qwen3_8b_qlora_sft.yaml
```

详细说明见 [training/llamafactory/README.md](training/llamafactory/README.md)。

## 当前已完成功能

1. 完整工程目录与模块隔离
2. 17 张核心业务表 + Alembic 初始迁移
3. Docker Compose：Postgres / Redis / MinIO（自动建桶）/ Qdrant
4. `GET /health`、`GET /ready`
5. 项目与文档元数据最小 API
6. 前端基础页面骨架
7. 演示数据导入脚本
8. LLaMAFactory ShareGPT 导出 / 校验与 QLoRA 配置模板
9. pytest 覆盖 health、模型、API、约束、导入幂等、ShareGPT、泄漏检查

## 后续开发顺序建议

1. 文档解析流水线（PDF/OCR）与 chunk 入库
2. Qdrant 向量化与 Dense RAG
3. OpenSearch BM25 与混合检索
4. LangGraph Agent 合规审查工作流
5. 认证授权与组织权限
6. 领域微调数据规模化与多 GPU QLoRA 训练
7. 人机协同标注与 gold/silver 质量管理

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
