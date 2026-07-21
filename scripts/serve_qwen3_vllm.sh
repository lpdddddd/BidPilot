#!/usr/bin/env bash
# Start Qwen3-14B via vLLM (OpenAI-compatible) for BidPilot grounded RAG.
# Default: single GPU, ~32GB class cards (e.g. RTX 5090). Not part of make infra-up.
set -euo pipefail

MODEL="${LLM_HF_MODEL:-Qwen/Qwen3-14B}"
SERVED_NAME="${LLM_MODEL:-bidpilot-qwen3-14b}"
HOST="${LLM_HOST:-0.0.0.0}"
PORT="${LLM_PORT:-8001}"
TP="${LLM_TENSOR_PARALLEL_SIZE:-1}"
GPU_UTIL="${LLM_GPU_MEMORY_UTILIZATION:-0.90}"
MAX_LEN="${LLM_MAX_MODEL_LEN:-16384}"

echo "Serving ${MODEL} as ${SERVED_NAME} on ${HOST}:${PORT}"
exec vllm serve "${MODEL}" \
  --served-model-name "${SERVED_NAME}" \
  --host "${HOST}" \
  --port "${PORT}" \
  --tensor-parallel-size "${TP}" \
  --gpu-memory-utilization "${GPU_UTIL}" \
  --max-model-len "${MAX_LEN}" \
  --enable-prefix-caching
