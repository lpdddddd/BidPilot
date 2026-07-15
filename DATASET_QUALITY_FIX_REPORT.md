# BidPilot Dataset Quality Fix Report

Generated at: `2026-07-16`  
Baseline commit (this round): `788e67b31c80ac19620155e15e2961660af1a036`  
Companion: `DATASET_FINAL_PREFLIGHT_REPORT.md`

## Round summary (post-788e67b)

Strict Match rebuild, supplier cleaning, rejected-SFT exclusion, RAG project-share trim, multi_section dual evidence, real LLaMAFactory validation, cross-split template vs severe classification, industry rules, Agent supplier citation + no `"None"`.

### Key numbers after this round

| Item | Value |
| --- | --- |
| RequirementMatch | **0**（旧 654 已归档至 `rejected/requirement_matches_pre_strict.jsonl`） |
| SupplierReviewOutcome | **0** |
| accepted / rejected suppliers | **21 / 178** |
| structurally_valid_sft | **3621** |
| rejected_sft | **32**（未进入 train/val/test） |
| train / val / test | **2418 / 675 / 528**（和=3621） |
| RAG questions / max_project_share | **214 / 0.098** |
| multi_section dual evidence | **9 / 9** |
| Agent tasks / supplier evidence tasks | **238 / 12** |
| severe train/test near-dup | **0**（template_overlap=8 warning） |
| `make validate-sft-real` | **PASS**（external LF=`not_run`） |
| Tests | dataset **54** + backend **13** PASS |

### Modified / added files (this round)

- `labeling/disclosed_matches.py`, `labeling/supplier_names.py`, `labeling/industry.py`
- `schemas/records.py` — `SupplierReviewOutcome`；Match 强制 `supplier_id`
- `rag_eval/build.py` — complementary multi_section；final-count project-share trim；`ok` 聚合
- `sft/build.py` — validate → reject file → split only valid；industry enrich；cross_split hook
- `sft/cross_split.py`
- `agent_data/build.py` — valid suppliers + name-window citations；scrub `"None"`
- `validation/validate.py` — share / multi_section / Match tender ban / severe xsim error
- `training/llamafactory/scripts/validate_sft_real.py`
- `Makefile` / `README.md` — `validate-sft` defaults to real data
- Tests: `test_match_supplier_rag_sft.py`；`test_quality_fixes.py`（generic 句不生成 Match）

### Policy notes

1. tender_document / tender_notice **never** create RequirementMatch.
2. Match only from result-class docs with named supplier fact + requirement bind; else `SupplierReviewOutcome`.
3. Zero matches is allowed when no real public review outcomes exist — **do not** lower evidence bar.
4. `make validate-sft` validates real ShareGPT exports, not only the sample.

Full command log, gaps, and human-review gate: see `DATASET_FINAL_PREFLIGHT_REPORT.md`.

---

## Prior round (788e67b) — retained history

Previous fixes under baseline `0e26c2e`: RAG quote leak, unanswerable band, Match 28651→654, SFT balance/dedup, agent multi-step, validation floors. Those remain in code history; Match counts above supersede the 654 silver product.

### Tests at time of 788e67b

| Suite | Result |
| --- | --- |
| data_pipeline tests | PASS（当时 33） |
| backend tests | PASS（13） |
| validate all / rag | PASS |

### Historical Match table (superseded)

| Metric | Cartesian era | After 788e67b | After this round |
| --- | --- | --- | --- |
| Total matches | 28651 | 654 | **0** |
| unknown | ~28651 | 0 | 0 |
