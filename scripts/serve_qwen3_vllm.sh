#!/usr/bin/env bash
# Start Qwen3-8B via vLLM (OpenAI-compatible) for BidPilot grounded RAG.
# Optional Course LoRA via --enable-lora / --lora-modules (Step 14).
#
# Config (from environment / .env — never printed with secrets):
#   LLM_MODEL              base served name (default bidpilot-qwen3-8b)
#   LLM_MODEL_SOURCE       Hub id (default Qwen/Qwen3-8B)
#   LLM_MODEL_PATH         optional local weights dir; if set must contain config.json
#   LLM_ENABLE_LORA        true|false (default true when course adapter exists)
#   LLM_LORA_MODULE_NAME   LoRA served name (default bidpilot-qwen3-8b-course-lora)
#   LLM_LORA_ADAPTER_PATH  repo-relative or absolute adapter dir
#   LLM_MAX_LORA_RANK      default 16 (must be >= adapter r)
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -f "${ROOT_DIR}/.env" ]]; then
  set -a
  while IFS= read -r line || [[ -n "${line}" ]]; do
    [[ -z "${line}" || "${line}" =~ ^[[:space:]]*# ]] && continue
    if [[ "${line}" =~ ^[A-Za-z_][A-Za-z0-9_]*= ]]; then
      key="${line%%=*}"
      val="${line#*=}"
      if [[ "${val}" =~ ^\".*\"$ || "${val}" =~ ^\'.*\'$ ]]; then
        val="${val:1:${#val}-2}"
      fi
      printf -v "${key}" '%s' "${val}"
      export "${key}"
    fi
  done < "${ROOT_DIR}/.env"
  set +a
fi

LLM_MODEL_SOURCE="${LLM_MODEL_SOURCE:-Qwen/Qwen3-8B}"
SERVED_NAME="${LLM_MODEL:-bidpilot-qwen3-8b}"
HOST="${LLM_HOST:-0.0.0.0}"
PORT="${LLM_PORT:-8001}"
TP="${LLM_TENSOR_PARALLEL_SIZE:-1}"
GPU_UTIL="${LLM_GPU_MEMORY_UTILIZATION:-0.90}"
MAX_LEN="${LLM_MAX_MODEL_LEN:-16384}"
LORA_NAME="${LLM_LORA_MODULE_NAME:-bidpilot-qwen3-8b-course-lora}"
LORA_REL="${LLM_LORA_ADAPTER_PATH:-training/llamafactory/outputs/qwen3_8b_lora_course}"
MAX_LORA_RANK="${LLM_MAX_LORA_RANK:-16}"

resolve_model() {
  if [[ -n "${LLM_MODEL_PATH:-}" ]]; then
    if [[ -f "${LLM_MODEL_PATH}/config.json" ]]; then
      echo "${LLM_MODEL_PATH}"
      return
    fi
    echo "ERROR: LLM_MODEL_PATH has no config.json" >&2
    exit 1
  fi
  echo "${LLM_MODEL_SOURCE}"
}

resolve_adapter() {
  local p="${LORA_REL}"
  if [[ "${p}" != /* ]]; then
    p="${ROOT_DIR}/${p}"
  fi
  echo "${p}"
}

preflight_lora() {
  local adapter
  adapter="$(resolve_adapter)"
  local configured_base="${LLM_MODEL_PATH:-${LLM_MODEL_SOURCE:-Qwen/Qwen3-8B}}"
  # Strong validation via shared Python helper (match / rank / files).
  python - <<PY
import json, sys
from pathlib import Path
sys.path.insert(0, r"${ROOT_DIR}/backend")
from app.services.model_serving import validate_adapter_for_serving
adapter = Path(r"""${adapter}""")
result = validate_adapter_for_serving(
    adapter if adapter.exists() else None,
    configured_base=r"""${configured_base}""",
    max_lora_rank=int("${MAX_LORA_RANK}"),
)
if not result["adapter_exists"]:
    codes = ",".join(result["reason_codes"]) or "adapter_invalid"
    print(
        f"ERROR: LoRA preflight failed reason_code={codes} "
        f"configured_base={result['configured_base_model']!r} "
        f"adapter_base={result['adapter_base_model']!r} "
        f"rank={result['lora_rank']}",
        file=sys.stderr,
    )
    sys.exit(1)
print(
    f"  lora_adapter_ok=1 rank={result['lora_rank']} "
    f"base_match={result['base_model_match']} "
    f"adapter_base={result['adapter_base_model']}",
    file=sys.stderr,
)
PY
  echo "${adapter}"
}

MODEL="$(resolve_model)"
if [[ -d "${MODEL}" ]]; then
  SOURCE_KIND="local_path"
else
  SOURCE_KIND="huggingface_id"
fi

ENABLE_LORA="${LLM_ENABLE_LORA:-}"
if [[ -z "${ENABLE_LORA}" ]]; then
  # Auto-enable when the default course adapter is present.
  if [[ -f "$(resolve_adapter)/adapter_config.json" ]]; then
    ENABLE_LORA=true
  else
    ENABLE_LORA=false
  fi
fi

echo "BidPilot vLLM launch"
echo "  load_from=${MODEL}"
echo "  source_kind=${SOURCE_KIND}"
echo "  served_model_name=${SERVED_NAME}"
echo "  listen=http://${HOST}:${PORT}/v1"
echo "  tensor_parallel=${TP} gpu_mem_util=${GPU_UTIL} max_model_len=${MAX_LEN}"
echo "  enable_lora=${ENABLE_LORA}"

ARGS=(
  "${MODEL}"
  --served-model-name "${SERVED_NAME}"
  --host "${HOST}"
  --port "${PORT}"
  --tensor-parallel-size "${TP}"
  --gpu-memory-utilization "${GPU_UTIL}"
  --max-model-len "${MAX_LEN}"
  --enable-prefix-caching
)

if [[ "${ENABLE_LORA}" == "true" || "${ENABLE_LORA}" == "1" ]]; then
  ADAPTER_ABS="$(preflight_lora)"
  echo "  lora_module=${LORA_NAME}"
  echo "  lora_path_rel=${LORA_REL}"
  ARGS+=(
    --enable-lora
    --max-loras 2
    --max-lora-rank "${MAX_LORA_RANK}"
    --lora-modules "${LORA_NAME}=${ADAPTER_ABS}"
  )
fi

# RTX 5090 (sm_120): FlashInfer sampler JIT can fail capability checks on some stacks.
export VLLM_USE_FLASHINFER_SAMPLER="${VLLM_USE_FLASHINFER_SAMPLER:-0}"

exec vllm serve "${ARGS[@]}"
