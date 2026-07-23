# BidPilot × LLaMAFactory

This directory keeps **training configuration, course-pilot scripts, and data conversion**.

- Do **not** vendor LLaMAFactory source into this repository.
- Backend code must **not** import `llamafactory`.
- Training runs against an external install (`pip install llamafactory` or `LLAMAFACTORY_HOME`).

## Course-pilot track (Step 13 demo)

```bash
# QC + review queue + ShareGPT splits
make course-pilot-prepare

# Smoke (≈20 steps) then formal 1-epoch LoRA
CUDA_VISIBLE_DEVICES=2 make course-lora-smoke
CUDA_VISIBLE_DEVICES=2 make course-lora-train

# Base vs adapter JSON structure eval
CUDA_VISIBLE_DEVICES=3 make course-lora-eval
```

See [`docs/step13_lora.md`](../../docs/step13_lora.md). Adapter weights live under `outputs/` (gitignored). Registration: `model_registry.json`.

**Label policy:** `course_pilot` ≠ human_gold. Formal Gold gates remain separate.

## ShareGPT messages format

LLaMAFactory consumes datasets registered in `data/dataset_info.json` with
`formatting: sharegpt` and a `messages` column.

## Launch training (manual)

```bash
llamafactory-cli train training/llamafactory/configs/qwen3_8b_lora_course.yaml
```
