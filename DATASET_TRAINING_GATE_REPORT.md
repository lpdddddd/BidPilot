# BidPilot Dataset Training Gate Report

Generated at: `2026-07-16`  
Baseline commit: `99ab680935a8bdab0e76b1e9ce43b1d97c08d3dc`  
LoRA/QLoRA: **not started**. Silver→Gold auto-promotion: **forbidden / not done**. Human review fields: **not auto-filled**.

## Verdict

| Gate | Status |
| --- | --- |
| Enter human review | **YES** (`ready_for_human_review=true`) |
| Pilot LoRA (experimental) | **NO** (`reviewed_trainable_sft=0`, Gold=0, LF preprocess blocked) |
| Formal LoRA | **NO** |

---

## 1. Modified files

### New
- `data_pipeline/bidpilot_data/sft/cross_split.py` — rewritten full-scan leak detector + coalesce helpers
- `data_pipeline/bidpilot_data/reporting/training_readiness.py` — tiered gate report
- `data_pipeline/tests/test_training_gates.py`
- `datasets/reports/training_readiness_report.json`
- `DATASET_TRAINING_GATE_REPORT.md` (this file)

### Updated
- `training/llamafactory/scripts/validate_sft_real.py` — internal + real LF preprocess modes
- `data_pipeline/bidpilot_data/rag_eval/build.py` — multi_section dual source/answer gates + metrics
- `data_pipeline/bidpilot_data/sft/build.py` — project clusters + train-preferring leak coalesce
- `data_pipeline/bidpilot_data/validation/validate.py` — full_scan gate + dual multi_section + readiness
- `data_pipeline/bidpilot_data/reporting/stats.py` — emits readiness
- `Makefile` — `validate-sft-internal` / `validate-sft-llamafactory` / `validate-sft-real`
- `.gitignore` — allowlist `training_readiness_report.json`
- Regenerated reports under `datasets/reports/*` and `DATASET_BUILD_REPORT.md`

---

## 2. Fix summary (by problem)

### A. Full-scan cross-split leakage
- Removed 400-chunk sampling; scans **all** train/validation/test records + source chunks (+ SFT QA fingerprints).
- Candidate recall: exact SHA1 + SimHash LSH + char n-gram co-occurrence; RapidFuzz confirm.
- Classes: `exact_duplicate` / `same_project_or_document` / `template_overlap` / `severe_business_overlap`.
- Template cannot bypass strong scoring/technical residual overlap.
- SFT split repair collapses leaky project components into **train** to eliminate oscillation.
- Wired into `validate all` (`full_scan=true` required; severe fail ⇒ error).

### B. Real LLaMAFactory preprocess
- Modes: `internal` | `llamafactory` | `all`.
- Detects import / `LLAMAFACTORY_HOME` / `llamafactory-cli`.
- On missing: `blocked_dependency_missing`, `preprocess_executed=false`, **non-zero exit** unless `--allow-missing-llamafactory`.
- Never claims “LF validation passed” without preprocess.

### C. RAG multi_section
- Requires two distinct chunks, different section paths, **both** `source_url`s, pages, quotes.
- Per-part answer support (`answer_part_i` ↔ `quote_i`).
- Quality report counters + fail IDs; validator fails on dual-evidence breach.

### D. Training readiness tiers
- Output: `datasets/reports/training_readiness_report.json`.
- Gold=0 / `reviewed_trainable_sft=0` forcibly closes pilot & formal LoRA.

---

## 3. Commands actually run

```bash
make dataset-test
make test
# iterative while implementing:
python -m bidpilot_data build-rag --limit 300   # via make dataset-build-rag
python -m bidpilot_data build-agent --limit 500
python -m bidpilot_data build-sft               # multiple times during leak coalesce tuning
make dataset-validate
make validate-sft-internal                      # PASS
make validate-sft-llamafactory                  # FAIL expected (LF missing)
make validate-sft-real                          # FAIL expected (LF missing)
make dataset-report
make dataset-test                               # final
make test                                       # final
```

---

## 4. Tests

| Suite | Result |
| --- | --- |
| `make dataset-test` | **63 passed** |
| `make test` (backend) | **13 passed** |

New coverage in `test_training_gates.py`: leak beyond 400th chunk; train/val & val/test; identical SFT QA; template vs business; LF missing status; tool role / empty final; multi_section dual-answer; Gold=0 closes training; project mutex.

---

## 5. Full cross-split stats

| Metric | Value |
| --- | --- |
| `full_scan` | **true** |
| `ok` | **true** |
| items_scanned | 5620 |
| candidate_pairs | 83166 |
| precise_comparisons | 83166 |
| severe_business_overlap | **0** |
| exact_duplicate (failing) | **0** |
| same_project_or_document | **0** |
| template_overlap | **0** (latest scan) |
| project_leaks | **[]** |

---

## 6. LLaMAFactory validation

| Layer | Result |
| --- | --- |
| Internal structure (`validate-sft-internal`) | **PASS** (train 1919 / val 986 / test 719; tool pairing OK; rejected not leaked) |
| External preprocess (`validate-sft-llamafactory`) | **`blocked_dependency_missing`** |
| Combined (`validate-sft-real`) | **FAIL (nonzero)** — correct; not a full PASS |

Install / rerun:

```bash
pip install llamafactory
# or: git clone https://github.com/hiyouga/LLaMA-Factory && cd LLaMA-Factory && pip install -e . && export LLAMAFACTORY_HOME=$PWD
# optional tokenizer override if Qwen3 weights unavailable:
export BIDPILOT_LF_TOKENIZER=Qwen/Qwen2.5-0.5B-Instruct
make validate-sft-llamafactory
make validate-sft-real
```

---

## 7. RAG multi_section dual evidence

| Metric | Value |
| --- | --- |
| questions | 215 |
| max_project_share | 0.0977 |
| multi_section_total | **10** |
| dual_chunk_pass | **10** |
| dual_source_pass | **10** |
| dual_answer_pass | **10** |
| failed_question_ids | [] |
| rag `ok` | **true** |

---

## 8. Gold / Silver / reviewed / rejected

| Metric | Value |
| --- | --- |
| structurally_valid_sft | **3624** |
| silver | **3624** |
| gold | **0** |
| reviewed_trainable_sft | **0** |
| rejected_sft | **32** (`missing_source`) — excluded from splits |
| train / validation / test | **1919 / 986 / 719** (sum=3624) |

---

## 9. Source domains

SFT domains still ~2 effective (`download.ccgp.gov.cn`, `www.ccgp.gov.cn`); portal snapshots do not count. Pilot domain gate (≥5) **blocked**.

---

## 10. Matches / RAG / Agent / Level A/B

| Metric | Value |
| --- | --- |
| RequirementMatch | **0** (no public review-result facts) |
| RAG questions | **215** (target min 500 — gap) |
| Agent tasks | **238** (target min 300 — gap) |
| Level A / Level B | below config floors (see `DATASET_BUILD_REPORT.md`) |

---

## 11. Allow / deny

1. **Human review:** **allowed**
2. **Pilot LoRA:** **denied** (Gold/reviewed=0; LF preprocess missing; domain/task floors)
3. **Formal LoRA:** **denied**

---

## 12. Remaining blockers & next actions

1. Install LLaMAFactory + tokenizer; run `make validate-sft-real` until `preprocess_executed=true` and external=`passed`.
2. Human-review pipeline → produce Gold SFT ≥500 before any pilot LoRA.
3. Collect public evaluation / qualification-result documents → rebuild RequirementMatch > 0.
4. Expand real project harvest (domains ≥5; Level A/B; RAG≥500; Agent≥300) without templates or fiction.
5. Continue reviewing silver requirements / RAG / Agent citations.

Do **not** start LoRA until pilot gates in `training_readiness_report.json` flip to true.
