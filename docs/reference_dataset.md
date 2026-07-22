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
| `matching` | Company-material vs requirement judgments using **real** disclosed supplier / match evidence only; otherwise `insufficient_evidence` |
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

### Reproducible builds

Pin RNG seed **and** a fixed UTC timestamp so sample `created_at` / report timestamps (and thus file bytes) are identical across runs:

```bash
cd data_pipeline
PYTHONPATH=. python -m bidpilot_data build-reference \
  --seed 42 \
  --build-timestamp 2026-07-22T00:00:00Z
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

Report fields include:

- `matching_with_real_bilateral_evidence`
- `matching_with_tender_evidence_only`
- `matching_with_company_evidence_but_not_requirement_aligned`
- `matching_insufficient_evidence`
- `matching_status_histogram`

## Quality gates

- Citation quotes must be contiguous in chunk text (whitespace-normalized).
- Citation metadata is validated **independently of the evidence list** (empty evidence no longer skips citation checks).
- Answerable samples require evidence support; unanswerable must not make definitive unsupported claims.
- Matching never invents company profiles; missing company-side evidence uses status `insufficient_evidence`.
- Soft-normalized input+output dedupe.
- Failed samples retry up to `max_retries`, then land in `rejected_samples.jsonl`.
- Generator version: `bidpilot-reference-1.0.0`.

### Matching diversity (bilateral vs name-only vs tender-only)

**Strict bilateral definition** (`matching_with_real_bilateral_evidence`) requires ALL of:

1. Real tender requirement + tender-side grounded quote
2. Real company material (chunk/document)
3. Company evidence that is **clause-level related** to that requirement — **not** mere supplier name appearance

Conservative rule: if deterministic logic cannot prove clause-level alignment →
`insufficient_evidence` / `unknown`. **Never** `supported` / `partially_supported` for name-only.

In practice, only silver `disclosed_match` rows with grounded quotes and
supported/partial status count as bilateral (empty silver file → bilateral may be **0**, OK).

Supplier-name attestation samples are emitted as:

- status `insufficient_evidence`
- provenance `company_name_only_not_requirement_aligned`
- counted under `matching_with_company_evidence_but_not_requirement_aligned`

Overall matching target remains **≥30** via tender-only insufficient pads.

Report counters:

- `matching_with_real_bilateral_evidence`
- `matching_with_tender_evidence_only`
- `matching_with_company_evidence_but_not_requirement_aligned`
- `matching_insufficient_evidence`
- `matching_status_histogram`

Reproduce (versioned summary/report/splits + JSONL):

```bash
cd data_pipeline
PYTHONPATH=. python -m bidpilot_data build-reference \
  --seed 42 \
  --build-timestamp 2026-07-22T00:00:00Z
```

### Compliance offline eval

Formal A–E `ComplianceEngine` on adapted `compliance_reference.jsonl` samples
(no REF_* keyword engine):

```bash
cd backend
python -m app.services.compliance.offline_eval
# → datasets/reports/compliance_rule_offline_eval.json
# Clean-env fallback fixture (versioned):
python -m app.services.compliance.offline_eval \
  --reference ../datasets/eval/reference/fixtures/compliance_reference.min.jsonl
```

Reports distinguish `rules_executed`, `focus_rules_evaluated`, and
`rules_without_direct_reference_coverage` (marked `not_directly_evaluated` in
`coverage_matrix` — do not claim 100% for those rules).

## Package

`data_pipeline/bidpilot_data/reference_dataset/` — `schema`, `select`, `generate`, `llm_judge`, `validate`, `split`, `build`, `export`.
