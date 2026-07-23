#!/usr/bin/env bash
# Run BidPilot course LoRA smoke or formal train via external LLaMA-Factory.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
MODE="${1:-smoke}"
export LLAMAFACTORY_HOME="${LLAMAFACTORY_HOME:-/root/autodl-tmp/LLaMA-Factory}"
export CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-2}"
export PYTHONUNBUFFERED=1

if ! command -v llamafactory-cli >/dev/null 2>&1; then
  if [[ -x "${LLAMAFACTORY_HOME}/src/llamafactory/cli.py" ]] || [[ -d "${LLAMAFACTORY_HOME}" ]]; then
    export PATH="${LLAMAFACTORY_HOME}:$PATH"
  fi
fi
if ! command -v llamafactory-cli >/dev/null 2>&1; then
  echo "llamafactory-cli not found. Install LLaMA-Factory and set LLAMAFACTORY_HOME." >&2
  exit 1
fi

python "${ROOT}/training/llamafactory/scripts/prepare_course_pilot.py" --repo-root "${ROOT}"

if [[ "${MODE}" == "smoke" ]]; then
  CFG="${ROOT}/training/llamafactory/configs/qwen3_8b_lora_course_smoke.yaml"
else
  CFG="${ROOT}/training/llamafactory/configs/qwen3_8b_lora_course.yaml"
fi

echo "Training mode=${MODE} cfg=${CFG} CUDA_VISIBLE_DEVICES=${CUDA_VISIBLE_DEVICES}"
llamafactory-cli train "${CFG}"
echo "Done. Outputs under training/llamafactory/outputs/"
