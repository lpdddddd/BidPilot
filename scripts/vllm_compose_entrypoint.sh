#!/usr/bin/env bash
# Container / host entrypoint wrapper for vLLM with optional LoRA preflight.
# Usage (compose): entrypoint runs this script; remaining args are passed to vllm.
# Env:
#   LLM_ENABLE_LORA          true|false (compose base defaults false; lora overlay true)
#   LLM_LORA_ADAPTER_PATH    runtime adapter dir (container: /models/bidpilot-course-lora)
#   LLM_MODEL_PATH / LLM_MODEL_SOURCE  configured base for match check
#   LLM_MAX_LORA_RANK        default 16
#   LLM_LORA_SERVED_NAME     must be non-empty when LoRA enabled
set -euo pipefail

ROOT_HINT="${BIDPILOT_ROOT:-/bidpilot}"
BACKEND_HINT="${BIDPILOT_BACKEND:-${ROOT_HINT}/backend}"

ENABLE_LORA="$(echo "${LLM_ENABLE_LORA:-false}" | tr '[:upper:]' '[:lower:]')"
ADAPTER="${LLM_LORA_ADAPTER_PATH:-/models/bidpilot-course-lora}"
MAX_RANK="${LLM_MAX_LORA_RANK:-16}"
SERVED_LORA="${LLM_LORA_SERVED_NAME:-${LLM_LORA_MODULE_NAME:-bidpilot-qwen3-8b-course-lora}}"
CONFIGURED_BASE="${LLM_MODEL_PATH:-${LLM_MODEL_SOURCE:-Qwen/Qwen3-8B}}"

# Normalize argv: strip LoRA flags when disabled; ensure they exist when enabled
# (covers compose merge where local.yml replaces command without LoRA bits).
ARGS=("$@")
KEPT=()
i=0
while ((i < ${#ARGS[@]})); do
  case "${ARGS[$i]}" in
    --enable-lora)
      ((i += 1))
      ;;
    --max-loras | --max-lora-rank | --lora-modules)
      ((i += 1))
      if ((i < ${#ARGS[@]})); then
        ((i += 1))
      fi
      ;;
    *)
      KEPT+=("${ARGS[$i]}")
      ((i += 1))
      ;;
  esac
done

if [[ "${ENABLE_LORA}" == "true" || "${ENABLE_LORA}" == "1" || "${ENABLE_LORA}" == "yes" ]]; then
  if [[ -z "${SERVED_LORA}" ]]; then
    echo "ERROR: LLM_LORA_SERVED_NAME is empty (reason_code=served_name_empty)" >&2
    exit 1
  fi
  if [[ ! -d "${ADAPTER}" ]]; then
    echo "ERROR: adapter dir missing path_hint=${ADAPTER} reason_code=adapter_missing" >&2
    exit 1
  fi
  PYTHONPATH="${BACKEND_HINT}${PYTHONPATH:+:${PYTHONPATH}}" python3 - <<PY
import json, os, sys
from pathlib import Path
sys.path.insert(0, os.environ.get("BIDPILOT_BACKEND", "${BACKEND_HINT}"))
from app.services.model_serving import validate_adapter_for_serving

adapter = Path(os.environ.get("LLM_LORA_ADAPTER_PATH", "${ADAPTER}"))
result = validate_adapter_for_serving(
    adapter if adapter.exists() else None,
    configured_base=os.environ.get("LLM_MODEL_PATH")
    or os.environ.get("LLM_MODEL_SOURCE")
    or "${CONFIGURED_BASE}",
    max_lora_rank=int(os.environ.get("LLM_MAX_LORA_RANK", "${MAX_RANK}")),
)
print(json.dumps({
    "files_ok": result["files_ok"],
    "adapter_exists": result["adapter_exists"],
    "base_model_match": result["base_model_match"],
    "configured_base_model": result["configured_base_model"],
    "adapter_base_model": result["adapter_base_model"],
    "lora_rank": result["lora_rank"],
    "rank_ok": result["rank_ok"],
    "reason_codes": result["reason_codes"],
}, ensure_ascii=False))
if not result["adapter_exists"]:
    codes = ",".join(result["reason_codes"]) or "adapter_invalid"
    print(
        f"ERROR: LoRA preflight failed reason_code={codes} "
        f"configured_base={result['configured_base_model']!r} "
        f"adapter_base={result['adapter_base_model']!r}",
        file=sys.stderr,
    )
    sys.exit(1)
print("OK: compose/container LoRA preflight passed", file=sys.stderr)
PY
  KEPT+=(
    --enable-lora
    --max-loras "2"
    --max-lora-rank "${MAX_RANK}"
    --lora-modules "${SERVED_LORA}=${ADAPTER}"
  )
  set -- "${KEPT[@]}"
else
  echo "INFO: LLM_ENABLE_LORA=false — base-only mode, skipping adapter preflight" >&2
  set -- "${KEPT[@]}"
fi

# Prefer image entrypoint binary when present.
if command -v vllm >/dev/null 2>&1; then
  exec vllm "$@"
fi
if [[ -x /usr/local/bin/vllm ]]; then
  exec /usr/local/bin/vllm "$@"
fi
# OpenAI image often uses python -m vllm.entrypoints.openai.api_server
if python3 -c "import vllm" >/dev/null 2>&1; then
  exec python3 -m vllm.entrypoints.openai.api_server "$@"
fi
echo "ERROR: vllm binary not found after preflight" >&2
exit 1
