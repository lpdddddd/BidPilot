#!/usr/bin/env bash
# Host-side LoRA Adapter preflight (Compose / serve). Exit non-zero on failure.
# Usage (from repo root):
#   bash scripts/check_lora_adapter.sh
#   LLM_LORA_HOST_PATH=... bash scripts/check_lora_adapter.sh
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

if [[ -f "${ROOT_DIR}/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source <(grep -E '^[A-Za-z_][A-Za-z0-9_]*=' "${ROOT_DIR}/.env" | sed 's/\r$//' || true)
  set +a
fi

# Prefer explicit host path; else repo-relative adapter path; else compose-default relative to infra/.
if [[ -n "${LLM_LORA_HOST_PATH:-}" ]]; then
  ADAPTER="${LLM_LORA_HOST_PATH}"
  if [[ "${ADAPTER}" != /* ]]; then
    ADAPTER="${ROOT_DIR}/${ADAPTER}"
  fi
elif [[ -n "${LLM_LORA_ADAPTER_PATH:-}" ]]; then
  ADAPTER="${LLM_LORA_ADAPTER_PATH}"
  if [[ "${ADAPTER}" != /* ]]; then
    ADAPTER="${ROOT_DIR}/${ADAPTER}"
  fi
else
  ADAPTER="${ROOT_DIR}/training/llamafactory/outputs/qwen3_8b_lora_course"
fi

CONFIGURED_BASE="${LLM_MODEL_PATH:-${LLM_MODEL_SOURCE:-Qwen/Qwen3-8B}}"
MAX_RANK="${LLM_MAX_LORA_RANK:-16}"

python - <<PY
import json
import sys
from pathlib import Path

# Import without requiring backend install path tricks
sys.path.insert(0, str(Path("${ROOT_DIR}") / "backend"))
from app.services.model_serving import validate_adapter_for_serving

adapter = Path(r"""${ADAPTER}""")
result = validate_adapter_for_serving(
    adapter if adapter.exists() else None,
    configured_base=r"""${CONFIGURED_BASE}""",
    max_lora_rank=int("${MAX_RANK}"),
)
print(json.dumps({
    "adapter_rel_hint": "training/llamafactory/outputs/qwen3_8b_lora_course",
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
