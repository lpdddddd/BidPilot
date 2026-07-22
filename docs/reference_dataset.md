# BidPilot Auto Reference Dataset (Step 1)

Step 1 of BidPilot’s evaluation data path is complete via an **automatic reference dataset builder**.

## What this is

- Deterministic (offline) construction of multi-task reference samples from real `datasets/` artifacts.
- Labels are **`auto_reference`** (or `silver` when reusing silver annotations) — **never `human_gold`**.
- Intended for **course demos** and **automatic evaluation**, not as expert-reviewed gold.

## What this is not

- Not human expert gold / not a substitute for review import.
- Not LoRA training data by itself (SFT remains under `datasets/sft/`).
- Does **not** overwrite `datasets/eval/rag/questions.jsonl` or other existing eval files.

## Tasks

| Task | Description |
|------|-------------|
| `rag` | Grounded Q&A (prefer reuse+normalize existing RAG where quotes validate) |
| `extraction` | Requirement extraction with chunk evidence |
| `matching` | Company-material vs requirement judgments (synthetic profile snippets allowed for eval; evidence quotes are real) |
| `compliance` | Rule checks (mandatory / deadline / invalid-bid style) |
| `drafting` | Response outline from confirmed-like silver evidence; always includes disclaimer |
| `unanswerable` | Abstain / insufficient evidence |

## Build

```bash
cd data_pipeline
python -m bidpilot_data build-reference --seed 42
# or
make -C .. dataset-build-reference
```

Optional LLM second-pass judge (not required):

```bash
python -m bidpilot_data build-reference --seed 42 --use-llm
```

## Outputs (`datasets/eval/reference/`)

- `reference_dataset.jsonl` (combined)
- `rag_reference.jsonl`, `extraction_reference.jsonl`, `matching_reference.jsonl`,
  `compliance_reference.jsonl`, `drafting_reference.jsonl`, `unanswerable_reference.jsonl`
- `rejected_samples.jsonl`
- `reference_dataset_report.json`, `reference_dataset_summary.md`
- `splits.json` (project → train/validation/test; project + document isolation)

## Quality gates

- Citation quotes must be contiguous in chunk text (whitespace-normalized).
- Answerable samples require evidence support; unanswerable must not make definitive unsupported claims.
- Soft-normalized input+output dedupe.
- Failed samples retry up to `max_retries`, then land in `rejected_samples.jsonl`.
- Generator version: `bidpilot-reference-1.0.0`.

## Package

`data_pipeline/bidpilot_data/reference_dataset/` — `schema`, `select`, `generate`, `llm_judge`, `validate`, `split`, `build`, `export`.
