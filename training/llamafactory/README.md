# BidPilot × LLaMAFactory

This directory keeps **training configuration and data conversion scripts only**.

- Do **not** vendor LLaMAFactory source into this repository.
- Backend code must **not** import `llamafactory`.
- Training runs against an external checkout pointed to by `LLAMAFACTORY_HOME`.

## Layout

```
training/llamafactory/
├── configs/                 # QLoRA / LoRA YAML templates
├── data/                    # dataset_info.json + sample ShareGPT JSON
├── scripts/                 # export + validate helpers
└── outputs/                 # local training outputs (gitignored)
```

## ShareGPT messages format

LLaMAFactory consumes datasets registered in `data/dataset_info.json` with
`formatting: sharegpt` and a `messages` column:

```json
[
  {
    "messages": [
      {"role": "system", "content": "..."},
      {"role": "user", "content": "..."},
      {"role": "assistant", "content": "..."}
    ]
  }
]
```

## Export dataset

```bash
python training/llamafactory/scripts/export_sft_dataset.py \
  --input datasets/gold/annotations.jsonl \
  --output-dir training/llamafactory/data/exported \
  --task-type requirement_classify \
  --require-json-assistant
```

Rules enforced by the exporter:

1. Output messages format
2. Optional `task_type` filter
3. Train / validation / test split
4. Test projects never enter train
5. Per-record format validation
6. Writes `export_stats.json`
7. No online model calls / no auto-train

## Validate dataset

```bash
python training/llamafactory/scripts/validate_sft_dataset.py \
  --dataset-file training/llamafactory/data/sample_sharegpt.json \
  --dataset-info training/llamafactory/data/dataset_info.json \
  --dataset-name bidpilot_sample_sharegpt
```

## Configure model paths

Edit YAML templates before training:

| Key | Meaning |
| --- | --- |
| `model_name_or_path` | Local or Hub model path (e.g. `Qwen/Qwen3-8B` or `/models/Qwen3-8B`) |
| `dataset_dir` | Directory containing `dataset_info.json` |
| `output_dir` | Adapter / checkpoint output directory |

Recommended environment variables on the training host:

```bash
export LLAMAFACTORY_HOME=/path/to/LLaMA-Factory
export CUDA_VISIBLE_DEVICES=0,1,2,3
```

## Launch training (manual, out-of-band)

```bash
cd "$LLAMAFACTORY_HOME"
llamafactory-cli train /path/to/bidpilot/training/llamafactory/configs/qwen3_8b_qlora_sft.yaml
```

Multi-GPU example (DeepSpeed / torchrun depends on your LLaMAFactory install):

```bash
CUDA_VISIBLE_DEVICES=0,1,2,3 llamafactory-cli train \
  /path/to/bidpilot/training/llamafactory/configs/qwen3_8b_qlora_sft.yaml
```

This scaffold intentionally does **not** start LoRA/QLoRA training.
