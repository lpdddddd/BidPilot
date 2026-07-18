# BidPilot Dataset Training Gate Report (Split / Consistency Round)

**Baseline commit:** `22c0c65ecaec481deb96db2c98108caec6aa403c`  
**Generator:** `bidpilot-sft-build-v2`  
**dataset_build_id:** `306263cb7ec117d420e0341999c030a3`  
**Generated from formal artifacts after rebuild (no fiction, no Gold auto-fill, no LoRA launch).**

## 1. Modified files

| Path | Change |
| --- | --- |
| `data_pipeline/bidpilot_data/sft/split_assign.py` | **New** — record-weighted cluster assignment (80/10/10), deterministic seed, floors, ratio repair |
| `data_pipeline/bidpilot_data/sft/publish.py` | **New** — exclusive build lock + staging → atomic publish |
| `data_pipeline/bidpilot_data/sft/build.py` | Cluster → weighted split → leak recluster (no train-dump) → stage → publish; unified reports |
| `data_pipeline/bidpilot_data/sft/cross_split.py` | Remove silent `[:80]/[:40]` truncation; secondary/tertiary buckets; skip ⇒ `full_scan=false` |
| `data_pipeline/bidpilot_data/reporting/artifact_meta.py` | **New** — `dataset_build_id` / hashes / generator version |
| `data_pipeline/bidpilot_data/reporting/consistency.py` | **New** — artifact consistency validator |
| `data_pipeline/bidpilot_data/reporting/training_readiness.py` | Truth from final records; consistency required for human gate |
| `data_pipeline/bidpilot_data/reporting/stats.py` | Stamp `dataset_statistics` with same build_id |
| `data_pipeline/bidpilot_data/validation/validate.py` | Wire consistency + full_scan/skipped gates; re-stamp cross_split meta |
| `training/llamafactory/scripts/validate_sft_real.py` | Full-sample default (`--max-samples 0` / `--all-samples`); per-record tool/normal stats |
| `Makefile` | `validate-sft-smoke`, full-sample `validate-sft-real` / `validate-sft-llamafactory` |
| `data_pipeline/tests/test_split_consistency.py` | **New** — split/consistency/full-scan/lock/LF blocked tests |
| `.gitignore` | Allow `artifact_consistency_report.json` |

## 2. Fixes by problem area

### A. SFT split ratios
- Atomic unit = leak-safe **project cluster** (shared/near-dup chunks), never split across train/val/test.
- Assignment optimizes **record counts** toward 80/10/10 with project floors (val≥5, test≥10).
- Removed unconditional “move all leak conflicts into train”.
- On leak failure: merge pairs into clusters and **re-split**, do not mutate formal reports mid-loop.
- Seed=42 reproducible.

**Achieved (formal records):**

| Split | Records | Ratio | Projects | Abs error vs target |
| --- | ---: | ---: | ---: | ---: |
| train | 2738 | 75.55% | 23 | 4.45 pp |
| validation | 549 | 15.15% | 5 | 5.15 pp |
| test | 337 | 9.30% | 15 | 0.70 pp |

**Why not exact 80/10/10:** one oversized leak-safe cluster (4 projects, **595** records, **16.42%** share) cannot be split. Validation is pinned at the 5-project floor with medium-sized clusters, so val sits just over the 5 pp band. Documented in `sft_build_stats.split_diagnostics` (`ratio_within_5pp=false`, `oversized_clusters`).

### B. Report consistency
- Single source of truth = final `datasets/sft/{train,validation,test}/records.jsonl`.
- Manifest + ShareGPT + LF JSON + all SFT reports written only after split is final (staging publish).
- Shared `dataset_build_id` / `split_manifest_sha256` / `source_records_sha256`.
- `validate_artifact_consistency` hard-fails `validate all` on count/project/task/quality/manifest/LF/rejected/build_id mismatches.

### C. Cross-split full scan
- Exact hash: uncapped.
- SimHash / n-gram: secondary (length/task/hash) + tertiary residual hash; **no silent fanout caps**.
- `skipped_candidates_count>0` ⇒ `full_scan=false` ⇒ gate fail.
- Current formal scan: `full_scan=true`, `skipped=0`, `ok=true`, `records_indexed=3624`, `chunks_indexed=1996`, `precise_comparisons=53316`.

### D. LLaMAFactory validation
- Modes: `internal` / `llamafactory` / `all`.
- Default full sample: `--max-samples 0` or `--all-samples`.
- Smoke: `make validate-sft-smoke` (`--max-samples 64`).
- Tool vs normal counted **per row** (no proportional fabrication).
- This environment: **LLaMAFactory not installed** → `blocked_dependency_missing`, `preprocess_executed=false` (not reported as PASS).

### E. Build order / lock
1. candidates → filter → reject isolate → dedup/balance → clusters → weighted split  
2. cross-split probe → recluster/re-split if needed  
3. staging write → exclusive lock → atomic publish  
4. unified reports → consistency validator (via `validate all`)

## 3. Commands executed

```bash
make dataset-test
make test
make dataset-build-sft
make dataset-validate
make validate-sft-internal
make dataset-report
make dataset-validate
```

## 4. Test results

| Suite | Result |
| --- | --- |
| `make dataset-test` | **75 passed** |
| `make test` (backend) | **13 passed** |

## 5. Cross-split stats (formal)

- `full_scan`: true  
- `ok`: true  
- `fail_count`: 0  
- `skipped_candidates_count`: 0  
- `records_indexed`: 3624  
- `chunks_indexed`: 1996  
- `precise_comparisons`: 53316  
- severe / same_project / exact: 0  

## 6. LLaMAFactory validation

| Mode | Result |
| --- | --- |
| Internal (`make validate-sft-internal`) | **PASS** — train/val/test 2738/549/337, no structure errors |
| External preprocess | **`blocked_dependency_missing`** — not installed; do not claim LF PASS |

Install / reproduce:

```bash
pip install llamafactory
# or: git clone https://github.com/hiyouga/LLaMA-Factory && cd LLaMA-Factory && pip install -e . && export LLAMAFACTORY_HOME=$PWD
cd training/llamafactory
python scripts/validate_sft_real.py --repo-root ../.. --mode all --all-samples
```

## 7. RAG multi_section

Unchanged this round (prior dual-evidence gates retained). Current RAG validate still part of `validate all` (**ok**).

## 8. Quality counts

| Metric | Value |
| --- | ---: |
| structurally_valid_sft | 3624 |
| silver | 3624 |
| gold / reviewed_trainable | **0 / 0** |
| rejected_sft (excluded from splits) | 32 |

## 9. Domains / Match / RAG / Agent / Level A/B

From readiness / stats (unchanged content, refreshed counts):

- RequirementMatch: **0**  
- RAG questions: **215**  
- Agent tasks: present in eval set (see `agent_quality_report.json`)  
- Level A/B: below formal targets (see `training_readiness_report.json` `current_metrics`)  
- Gold=0 ⇒ pilot/formal LoRA **closed**

## 10. Training gates (current)

| Gate | Status |
| --- | --- |
| Enter human review | **YES** (`ready_for_human_review=true`) — consistency + split leak + RAG + structure |
| Pilot LoRA | **NO** (Gold/reviewed=0; LF preprocess missing) |
| Formal LoRA | **NO** |

## 11. Remaining blockers / next actions

1. Install LLaMAFactory + tokenizer; run `make validate-sft-real` until `preprocess_executed=true`.  
2. Human review → Gold / `reviewed_trainable_sft≥500` (do **not** auto-upgrade Silver).  
3. Collect real result-class docs → RequirementMatch > 0.  
4. Grow real coverage (domains, RAG/Agent/Level A/B) without templates/fiction.  
5. Optional: further ratio tuning only if new real projects shrink the 16% oversized cluster (do not split it).

## 12. Consistency snapshot

All of the following agree on **2738 / 549 / 337** records and **23 / 5 / 15** projects:

- `sft/*/records.jsonl`  
- `sft_split_manifest.json`  
- `sft_build_stats.json`  
- `split_distribution.json`  
- `task_distribution.by_split_and_task`  
- `cross_split_similarity_report.split_stats`  
- `dataset_statistics.sft`  
- LLaMAFactory `bidpilot_sft_{train,validation,test}.json`  
- `artifact_consistency_report.json` → **ok=true**
