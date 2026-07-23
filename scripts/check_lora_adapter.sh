#!/usr/bin/env bash
# Host-side LoRA Adapter preflight (Compose / serve). Exit non-zero on failure.
# Usage (from repo root):
#   bash scripts/check_lora_adapter.sh
#
# Path rules:
#   LLM_LORA_ADAPTER_PATH — runtime path checked here and by vLLM / compose entrypoint.
#                           Host default: training/llamafactory/outputs/qwen3_8b_lora_course
#                           Container default: /models/bidpilot-course-lora
#   LLM_LORA_HOST_PATH    — Compose volume source only. When set AND ADAPTER_PATH is the
#                           container mount default, host preflight checks HOST_PATH instead
#                           so `compose up` can validate files before the container starts.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

if [[ -f "${ROOT_DIR}/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source <(grep -E '^[A-Za-z_][A-Za-z0-9_]*=' "${ROOT_DIR}/.env" | sed 's/\r$//' || true)
  set +a
fi

CONTAINER_DEFAULT="/models/bidpilot-course-lora"
ADAPTER="${LLM_LORA_ADAPTER_PATH:-}"
if [[ -z "${ADAPTER}" ]]; then
  ADAPTER="${ROOT_DIR}/training/llamafactory/outputs/qwen3_8b_lora_course"
elif [[ "${ADAPTER}" != /* ]]; then
  ADAPTER="${ROOT_DIR}/${ADAPTER}"
fi

# Host compose preflight: if ADAPTER points at the in-container mount, check HOST_PATH.
if [[ "${ADAPTER}" == "${CONTAINER_DEFAULT}" && -n "${LLM_LORA_HOST_PATH:-}" ]]; then
  ADAPTER="${LLM_LORA_HOST_PATH}"
  if [[ "${ADAPTER}" != /* ]]; then
    # Relative host paths in compose are vs infra/; resolve from repo root equivalent.
    if [[ "${ADAPTER}" == ../* ]]; then
      ADAPTER="${ROOT_DIR}/${ADAPTER#../}"
    else
      ADAPTER="${ROOT_DIR}/${ADAPTER}"
    fi
  fi
fi

CONFIGURED_BASE="${LLM_MODEL_PATH:-${LLM_MODEL_SOURCE:-Qwen/Qwen3-8B}}"
MAX_RANK="${LLM_MAX_LORA_RANK:-16}"

python - <<PY
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path("${ROOT_DIR}") / "backend"))
from app.services.model_serving import validate_adapter_for_serving

adapter = Path(r"""${ADAPTER}""")
result = validate_adapter_for_serving(
    adapter if adapter.exists() else None,
    configured_base=r"""${CONFIGURED_BASE}""",
    max_lora_rank=int("${MAX_RANK}"),
)
print(json.dumps({
    "checked_path_basename": adapter.name,
    "files_ok": result["files_ok"],
    "adapter_exists": result["adapter_exists"],
    "base_model_match": result["base_model_match"],
    "configured_base_model": result["configured_base_model"],
    "adapter_base_model": result["adapter_base_model"],
    "lora_rank": result["lora_rank"],
    "rank_ok": result["rank_ok"],
    "reason_codes": result["reason_codes"],
}, ensure_ascii=False, indent=2))
if not result["adapter_exists"]:
    codes = ",".join(result["reason_codes"]) or "adapter_invalid"
    print(
        f"ERROR: LoRA adapter preflight failed reason_code={codes} "
        f"configured_base={result['configured_base_model']!r} "
        f"adapter_base={result['adapter_base_model']!r}",
        file=sys.stderr,
    )
    sys.exit(1)
print("OK: adapter ready for serving", file=sys.stderr)
PY
