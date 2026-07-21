#!/usr/bin/env bash
# Start Qwen3-8B via vLLM (OpenAI-compatible) for BidPilot grounded RAG.
# Default: single GPU, ~32GB class cards (e.g. RTX 5090). Not part of make infra-up.
#
# Prefers a local snapshot if present (ModelScope/HF download target):
#   /root/autodl-tmp/models/Qwen3-8B
# Override with LLM_HF_MODEL=/path/or/hub-id
set -euo pipefail

DEFAULT_LOCAL="/root/autodl-tmp/models/Qwen3-8B"
if [[ -z "${LLM_HF_MODEL:-}" ]]; then
  if [[ -f "${DEFAULT_LOCAL}/config.json" ]]; then
    MODEL="${DEFAULT_LOCAL}"
  else
    MODEL="Qwen/Qwen3-8B"
  fi
else
  MODEL="${LLM_HF_MODEL}"
fi
SERVED_NAME="${LLM_MODEL:-bidpilot-qwen3-8b}"
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
