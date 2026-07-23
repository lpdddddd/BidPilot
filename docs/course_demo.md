# BidPilot 课程演示走查（Step 11/12 收尾）

固定演示路径：本地基础设施 → 导入/创建项目 → 上传样例招标文件 → 解析与索引 → RAG 带来源问答 → 合规 / Agent 时间线 → 评测中心小跑 → 对比与导出。

不包含密钥或外网爬取。领域微调（course_pilot LoRA）见 [`step13_lora.md`](step13_lora.md)；本走查默认不启动训练。

## 0. 环境准备

```bash
cd bidpilot
cp .env.example .env   # 按需改库密码与服务地址，勿提交真实密钥

conda activate bidpilot   # 或等价 Python 3.11 环境
make backend-install
make frontend-install
make infra-up             # Postgres / Redis / MinIO / Qdrant / OpenSearch
make migrate
```

另开终端启动 API 与前端：

```bash
make backend    # http://localhost:8000/docs
make frontend   # http://localhost:5173
```

RAG / Agent 需要本地 LLM 时（可选，另开终端）：

```bash
# .env: LLM_ENABLED=true，LLM_BASE_URL 指向 vLLM
./scripts/serve_qwen3_vllm.sh
# 或 make llm-up 查看 Compose / 本机启动说明
```

## 1. 创建或导入演示项目

**推荐（演示包）：**

```bash
make import-demo
# 或 dry-run：python scripts/import_demo_data.py --dry-run
```

导入后应出现项目，例如 `DEMO-2026-001` /「智慧园区安防系统采购项目」（见 `demo_data/project_info.json`）。也可在前端「项目」页手动新建项目。

## 2. 上传样例招标文件并解析 + 索引

1. 打开项目详情 → **文档中心**。
2. 上传样例文件（无真实招标附件时用仓库内演示文本）：
   - **`demo_data/sample_tender.txt`**（推荐）
   - 或任意 PDF / DOCX / TXT 招标类文档（类型选 `tender` 等）
3. 等待解析：`pending → processing → success`（扫描 PDF 可能为 `ocr_required`，本演示跳过）。
4. 确认自动切分 Chunk；必要时在文档操作中 **重建 Chunk**。
5. 建立 / 重建索引（Qdrant Dense + OpenSearch BM25）；文档索引状态应为成功。

> 首次索引会加载 Embedding / Reranker（如 `BAAI/bge-small-zh-v1.5`）；国内可用 `HF_ENDPOINT=https://hf-mirror.com`。

## 3. RAG 问答 + 引用

1. 同一项目 → **知识检索** / 「带来源问答」。
2. 示例问题：
   - 「投标人需要提供哪些资格材料？」
   - 「系统视频接入路数要求是多少？」
   - 「本项目是否要求火星采矿许可证？」（证据不足时不应编造结论）
3. 确认回答含 `[S1]` 等引用，并可点击跳到文档 / chunk；检索为空时不应凭空生成实质性结论。

## 4. 合规审查与 Agent 时间线

**规则合规（确定性，可不依赖 LLM）：**

1. 侧栏进入 **智能审查**（`/review`），选择当前项目。
2. 发起合规 run，查看 findings（coverage / evidence / 资格风险等）。

**Agent 闭环 + 时间线（需 LLM 配置）：**

1. 项目详情 → Agent / 执行时间线面板（`AgentLoopPanel`）。
2. 启动 Agent run；观察 Step / ToolCall 状态与事件序列（SSE，失败时可轮询回退）。
3. 可演示 resume / retry（勿在演示中编造资质结论）。

## 5. 评测中心：小跑、结果、对比、导出

1. 侧栏 **评估中心**（`/evaluation`），选中演示项目。
2. **概览**：确认数据集为 auto_reference 口径，**human Gold = 0**。
3. **新建评测**：
   - 选择内置 suite（如 `reference_dataset`）
   - Target：优先 **合规检查**（通常始终可用）；若 Embedding 就绪可选 **RAG**；若 `LLM_ENABLED` 可选 **Agent 全流程**
   - 不可用目标（需求抽取 / 匹配 / 草稿等）显示友好中文原因并保持禁用（如「当前版本暂未开放」），**不要**解读为系统崩溃
   - `Case 数量限制` 设为较小值（如 `5`～`10`）以缩短演示
4. 等待 run 完成 → 打开 **Run 详情** 与 case 结果（指标、Hard Gate、引用校验）。
5. **对比**：选两次 run 做 compare，注意 dataset hash / evaluator version 不一致时的提示。
6. **导出**：JSON / CSV / Markdown（按钮在 Run 详情）。

## 6. 演示口径（诚实限制）

| 能力 | 演示期望 |
| --- | --- |
| 文档上传 / 解析 / Chunk / 索引 | 可用 |
| RAG + 引用 | 索引 +（问答时）vLLM 就绪 |
| 合规规则引擎 | 可用；非法律意见 |
| Agent + 时间线 | LLM 配置后可用；证据不足则 warning / blocked |
| 评测中心 | 可用；reference 为 **auto_reference**，**human Gold=0** |
| extraction / matching / drafting 评测目标 | **未 case 级接线** → UI 显示暂未开放 |
| LoRA / 领域微调 | **course_pilot 已交付**（QC→训练→评测→注册展示）；**非 human_gold**；在线推理默认仍为基座 vLLM，见 [`step13_lora.md`](step13_lora.md) |

## 相关文档

- [`evaluation_center.md`](evaluation_center.md)
- [`agent_workflow.md`](agent_workflow.md)
- [`rag_e2e_acceptance.md`](rag_e2e_acceptance.md)
- 仓库根目录 [`README.md`](../README.md)
