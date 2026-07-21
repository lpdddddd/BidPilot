#!/usr/bin/env bash
# Start Qwen3-8B via vLLM (OpenAI-compatible) for BidPilot grounded RAG.
# Default: single GPU, ~32GB class cards (e.g. RTX 5090). Not part of make infra-up.
#
# Single config source (from environment / .env):
#   LLM_MODEL          served name (default bidpilot-qwen3-8b)
#   LLM_MODEL_SOURCE   Hub id fallback (default Qwen/Qwen3-8B)
#   LLM_MODEL_PATH     local weights dir (optional; auto-detects default local)
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -f "${ROOT_DIR}/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "${ROOT_DIR}/.env"
  set +a
fi

DEFAULT_LOCAL="${LLM_DEFAULT_LOCAL_PATH:-/root/autodl-tmp/models/Qwen3-8B}"
LLM_MODEL_SOURCE="${LLM_MODEL_SOURCE:-Qwen/Qwen3-8B}"

resolve_model() {
  if [[ -n "${LLM_MODEL_PATH:-}" ]]; then
    if [[ -f "${LLM_MODEL_PATH}/config.json" ]]; then
      echo "${LLM_MODEL_PATH}"
      return
    fi
    # Allow Hub id placed in LLM_MODEL_PATH by mistake.
    if [[ "${LLM_MODEL_PATH}" == */* && ! -e "${LLM_MODEL_PATH}" ]]; then
      echo "${LLM_MODEL_PATH}"
      return
    fi
    echo "LLM_MODEL_PATH=${LLM_MODEL_PATH} has no config.json" >&2
    exit 1
  fi
  if [[ -f "${DEFAULT_LOCAL}/config.json" ]]; then
    echo "${DEFAULT_LOCAL}"
    return
  fi
  echo "${LLM_MODEL_SOURCE}"
}

MODEL="$(resolve_model)"
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
