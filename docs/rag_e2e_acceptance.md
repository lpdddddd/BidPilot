# RAG E2E Acceptance (Step 6)

**Date:** 2026-07-22  
**Baseline commit:** `4b33e33`  
**Live smoke after semantics fix:** PASS (`docs/acceptance/rag_smoke_20260722T025624Z.json`, gitignored)

## Environment

| Item | Value |
|------|--------|
| GPU | NVIDIA GeForce RTX 5090 ×6 (serve on GPU 0, ~31 GiB used) |
| Model | Local `Qwen3-8B` via `LLM_MODEL_PATH` → served as `bidpilot-qwen3-8b` |
| vLLM | 0.25.1, `VLLM_USE_FLASHINFER_SAMPLER=0` |
| Infra | Postgres / Redis / Qdrant / OpenSearch healthy |
| API | `http://127.0.0.1:8000` (`LLM_ENABLED=true`, `HF_HUB_OFFLINE=1`) |
| Project (anonymized) | `E2E-RETR-001` / `6655b01f-…` |
| Indexed source | `e2e_tender.txt` (3 chunks, indexing=success) |

## Cases

### 1. Qualification (JSON + SSE)

- **Q:** 投标人需要具备哪些资质？
- **Answer (preview):** 营业执照；电子与智能化工程专业承包贰级及以上；近三年无重大违法记录 `[S1]`
- **Citation:** `S1` → `e2e_tender.txt` / 第一章 招标公告
- **Latency:** JSON ~753 ms；SSE ~834 ms；events `retrieval → generation_started → final`

### 2. Amount / process (SSE)

- **Q:** 投标保证金是多少？
- **Answer:** 人民币伍万元整 `[S1]`
- **Citation:** `S1` → `e2e_tender.txt` / 第二章 投标人须知
- **Latency:** ~249 ms

### 3. Duration (SSE)

- **Q:** 质保期是多长时间？
- **Answer:** 验收合格之日起贰年 `[S1]`
- **Citation:** `S1` → `e2e_tender.txt` / 第三章 合同主要条款
- **Latency:** ~280 ms

### 4. Insufficient evidence (SSE)

- **Q:** 本项目是否要求投标人具备火星采矿许可证？
- **Answer:** 含「当前资料不足以确认」安全表述
- **Citations:** empty allowed
- **Events:** `final` present；no `error` (infra/LLM failures are not treated as insufficient)

## Frontend

- Workspace tab「知识检索 → 带来源问答」uses the same SSE contract (`retrieval` / `generation_started` / `final`).
- Answer text is shown only after `final`; status copy indicates citation verification.
- Live UI check: API + model reachable during this acceptance window; answers match the JSON/SSE probes above for the same project.

## Verdict

| Check | Result |
|-------|--------|
| Strict live smoke semantics (no false PASS on SSE `error`) | Implemented + unit tested |
| Real Qwen3-8B JSON ask | PASS |
| Real Qwen3-8B SSE ask | PASS |
| Insufficient-evidence safety path | PASS |
| Overall live smoke | **PASS** |

## Reproduce

```bash
export LLM_MODEL_PATH=/absolute/path/to/Qwen3-8B
export LLM_ENABLED=true
export HF_HUB_OFFLINE=1   # if Hub unreachable but caches exist
./scripts/serve_qwen3_vllm.sh   # VLLM_USE_FLASHINFER_SAMPLER=0 by default
# API on :8000
RAG_SMOKE_LIVE=1 \
RAG_SMOKE_PROJECT_ID=<indexed-project-uuid> \
RAG_SMOKE_QUESTIONS='投标人需要具备哪些资质？|投标保证金是多少？|质保期是多长时间？' \
RAG_SMOKE_INSUFFICIENT_QUESTION='本项目是否要求投标人具备火星采矿许可证？' \
make rag-smoke-live
```
