#!/usr/bin/env bash
# Start Qwen3-8B via vLLM (OpenAI-compatible) for BidPilot grounded RAG.
# Default: single GPU (~32GB, e.g. RTX 5090). Not part of make infra-up.
#
# Config (from environment / .env — never printed in full):
#   LLM_MODEL          served name (default bidpilot-qwen3-8b)
#   LLM_MODEL_SOURCE   Hub id (default Qwen/Qwen3-8B)
#   LLM_MODEL_PATH     optional local weights dir; if set must contain config.json
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -f "${ROOT_DIR}/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "${ROOT_DIR}/.env"
  set +a
fi

LLM_MODEL_SOURCE="${LLM_MODEL_SOURCE:-Qwen/Qwen3-8B}"
SERVED_NAME="${LLM_MODEL:-bidpilot-qwen3-8b}"
HOST="${LLM_HOST:-0.0.0.0}"
PORT="${LLM_PORT:-8001}"
TP="${LLM_TENSOR_PARALLEL_SIZE:-1}"
GPU_UTIL="${LLM_GPU_MEMORY_UTILIZATION:-0.90}"
MAX_LEN="${LLM_MAX_MODEL_LEN:-16384}"

resolve_model() {
  if [[ -n "${LLM_MODEL_PATH:-}" ]]; then
    if [[ -f "${LLM_MODEL_PATH}/config.json" ]]; then
      echo "${LLM_MODEL_PATH}"
      return
    fi
    echo "ERROR: LLM_MODEL_PATH=${LLM_MODEL_PATH} has no config.json" >&2
    exit 1
  fi
  echo "${LLM_MODEL_SOURCE}"
}

MODEL="$(resolve_model)"
if [[ -d "${MODEL}" ]]; then
  SOURCE_KIND="local_path"
else
  SOURCE_KIND="huggingface_id"
fi

echo "BidPilot vLLM launch"
echo "  load_from=${MODEL}"
echo "  source_kind=${SOURCE_KIND}"
echo "  served_model_name=${SERVED_NAME}"
echo "  listen=http://${HOST}:${PORT}/v1"
echo "  tensor_parallel=${TP} gpu_mem_util=${GPU_UTIL} max_model_len=${MAX_LEN}"

exec vllm serve "${MODEL}" \
  --served-model-name "${SERVED_NAME}" \
  --host "${HOST}" \
  --port "${PORT}" \
  --tensor-parallel-size "${TP}" \
  --gpu-memory-utilization "${GPU_UTIL}" \
  --max-model-len "${MAX_LEN}" \
  --enable-prefix-caching
