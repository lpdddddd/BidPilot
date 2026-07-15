# BidPilot Dataset Quality Fix Report

Generated at: `2026-07-15`  
Baseline commit: `0e26c2eac09d041391f300f8c1390fc5132f4d6a`

## 1. Modified files

### Code / config
- `data_pipeline/bidpilot_data/rag_eval/build.py` — natural RAG questions, unanswerable templates, leak checks, ratios
- `data_pipeline/bidpilot_data/labeling/disclosed_matches.py` — evidence-only matches (no cartesian unknown)
- `data_pipeline/bidpilot_data/sft/build.py` — new stats, balance, global dedup, multi-step tool SFT, split floors
- `data_pipeline/bidpilot_data/sft/balance.py` *(new)*
- `data_pipeline/bidpilot_data/sft/dedup.py` *(new)*
- `data_pipeline/bidpilot_data/agent_data/build.py` — multi-step trajectories with real tool results
- `data_pipeline/bidpilot_data/validation/validate.py` — `validate rag`, match/PORTAL/tool pairing checks
- `data_pipeline/bidpilot_data/cli.py` — `validate rag`, `review export-priority`, default RAG/agent limits
- `data_pipeline/bidpilot_data/review/priority_export.py` — 500–800 / 200–300 review exports
- `data_pipeline/bidpilot_data/reporting/stats.py` — domain diversity + SFT gate stats
- `data_pipeline/bidpilot_data/schemas/enums.py`, `schemas/records.py` — Match evidence fields; forbid unknown
- `data_pipeline/bidpilot_data/collectors/metadata_extractor.py` — fix 中山医院 false Guangdong
- `data_pipeline/configs/sft_balance.yaml` *(new)*, `configs/pipeline.yaml`
- `Makefile`, `data_pipeline/README.md`
- Tests: `tests/test_quality_fixes.py` *(new)*, `tests/test_label_review_sft.py`, `tests/conftest.py`

### Reports / datasets regenerated
- `DATASET_BUILD_REPORT.md`
- `datasets/reports/{dataset_statistics,sft_build_stats,task_distribution,validation_report,rag_quality_report,agent_quality_report,dedup_report,split_distribution,rag_validation_report}.json`
- `datasets/silver/requirement_matches.jsonl`, `disclosed_suppliers.jsonl`, evidence updates
- `datasets/eval/rag/questions.jsonl`, `datasets/eval/agent/tasks.jsonl`
- `datasets/sft/**`, `datasets/review/exported/priority_*.csv`
- `training/llamafactory/data/dataset_info.json` (+ synced sharegpt files)

## 2. Issues fixed

1. RAG questions no longer paste `原文：` / source quotes (natural questions; LCS≥20 blocked).
2. Unanswerable set rebuilt with ≥50 procurement-realistic templates + corpus absence check; ratio held at 15%.
3. RequirementMatch cartesian unknown (28651) removed; only evidence-backed satisfied/missing/uncertain/partial.
4. SFT stats split into structurally_valid / reviewed_trainable / silver_candidate / rejected (no “effective trainable=27299” as LoRA gate).
5. Task balancer downsamples only; classify+risk share dropped sharply.
6. quality_level vs review_status separated in reports.
7. Global near-dedup (exact hash + SimHash LSH + fuzz), not last-200 window.
8. Agent SFT exports full multi-step assistant/tool/assistant with real results + citations.
9. Domain reporting separates portal snapshots from SFT source domains; gap when <5 SFT domains.
10. Validation ≥5 projects, heldout test ≥10; priority review CSV expanded.

## 3. Tests and pass status

| Suite | Result |
| --- | --- |
| `make dataset-test` (data_pipeline, 33 tests) | **PASS** |
| `make test` (backend, 13 tests) | **PASS** |
| `make validate-sft` (sample ShareGPT) | **PASS** |
| `python -m bidpilot_data validate all` | **PASS** (`ok=true`) |
| `python -m bidpilot_data validate rag` | **PASS** |

New coverage in `test_quality_fixes.py`: RAG leak markers/quote copy, unanswerable template count, match schema (no evidence / unknown banned), name-only supplier → 0 matches, satisfied/missing with evidence, far-apart near-dedup, gold>silver, cross-project body keep, balance max_ratio, split≥5/10, agent tool pairing+citations, tool role schema, etc.

## 4. RequirementMatch before / after

| Metric | Before | After |
| --- | --- | --- |
| Total matches | **28651** | **654** |
| unknown | ~28651 | **0** |
| disclosed_suppliers | (name-only product) | **62** |
| evidence_supported_matches | ~0 meaningful | **654** |
| satisfied / missing / uncertain | n/a | **111 / 537 / 6** |

## 5–7. RAG before / after, type mix, unanswerable ratio

| Metric | Before | After |
| --- | --- | --- |
| Questions | 105 | **220** (target 300; remaining gap) |
| Leaky `原文：` questions | yes | **0** |
| Unanswerable ratio | n/a / invalid templates | **0.15** (band 0.10–0.15) |

Type distribution (after):  
`qualification=46, technical=25, scoring=26, commercial=24, project_basic=20, time_location=13, evidence=13, multi_section=11, rejection=9, unanswerable=33`.

## 8. SFT balance before / after

| | classify | risk | classify+risk share |
| --- | ---: | ---: | ---: |
| Before balance | 14819 | 10710 | **~94.5%** |
| After balance | 1318 | 942 | **~60.1%** |

Other after-balance counts: `qualification_extract=870, tool_call=237, citation_qa=218, scoring_extract=102, project_info_extract=41, evidence_match=32`.  
Task gaps (no cloning): scoring / project_info / citation_qa / tool_call under `target_ratio` in `task_distribution.json`.

## 9–11. SFT quality gates

```json
{
  "structurally_valid_sft": 3727,
  "reviewed_trainable_sft": 0,
  "silver_candidate_sft": 3727,
  "rejected_sft": 33
}
```

**Formal LoRA must not start** until `reviewed_trainable_sft` reaches the gold review target after human accept/correct import.

## 12. Dedup

| Metric | Count |
| --- | ---: |
| exact_duplicates_removed | 6494 |
| near_duplicates_removed | 1076 |
| cross_project_template_duplicates | 681 |
| conflicting_gold_records | 0 |

## 13–14. Splits

| Split | projects | records (approx) |
| --- | ---: | ---: |
| train | **26** |  (see sft_build_stats) |
| validation | **7** (≥5) |  |
| test / heldout | **10** (≥10) |  |

Per-split task / domain / bundle_level detail: `datasets/reports/split_distribution.json`.

## 15. Actual SFT source domains

- `download.ccgp.gov.cn`, `www.ccgp.gov.cn` (**2 domains**)
- `sft_source_domain_gap.met = false` (need ≥5); portal homepage domains are **not** counted as training coverage

## 16. Agent multi-step trajectories

- Agent tasks: **238** (target 300–500; gap_to_min=62, no cloning)
- Multi-step (≥2 tools): **204**
- With error/retry: 43; clarify: 43
- SFT `tool_call` records: **237** with assistant↔tool pairing and final citations

## 17. Human review tables

| Export | Rows | Band |
| --- | ---: | --- |
| `priority_requirements_review.csv` | **602** | 500–800 ✓ |
| `priority_rag_review.csv` | **220** | 200–300 ✓ |

Reviewer / decision left blank — **no auto-accept / forged gold**.

## 18. Remaining gaps

1. `reviewed_trainable_sft = 0` — human gold review not done.
2. RAG high-quality candidates **220 < 300** (limited unique natural questions under project/chunk caps).
3. Agent tasks **238 < 300**.
4. SFT source domains **2 < 5** (CCGP-dominated harvest).
5. `level_a = 0`, `level_b` still below pipeline targets.
6. Gold requirements still **0**.
7. Under-represented extract tasks (scoring / project_info) lack real volume — reported in `task_gaps`, not filled by cloning.
8. Some projects still missing `project_code` (warnings only).

## 19. Commands actually executed

```bash
make dataset-parse          # resume: parse/clean/chunk already present
python -m bidpilot_data label requirements --mode rules --resume
python -m bidpilot_data label matches
python -m bidpilot_data build-rag --limit 300
python -m bidpilot_data validate rag
python -m bidpilot_data build-agent --limit 500
python -m bidpilot_data review export-priority
python -m bidpilot_data build-sft   # (re-run after balance fix)
python -m bidpilot_data validate all
python -m bidpilot_data report
make dataset-test           # 33 passed
make test                   # backend 13 passed
make validate-sft           # sample sharegpt ok
```

## 20. Not executed (and why)

| Step | Reason |
| --- | --- |
| Formal LoRA / QLoRA training | Explicitly forbidden until reviewed gold gate |
| Auto-filling review decisions / forging gold | Forbidden |
| New large-scale crawl to fill domain/level_a gaps | Out of scope for this repair round; gaps reported honestly |
| Forcing RAG to exactly 300 via clones | Cloning/light rewrite forbidden |

## Finish-condition checklist

| # | Condition | Status |
| --- | --- | --- |
| 1 | RAG question no quote leak | **Met** |
| 2 | Unanswerable 10–15% | **Met (15%)** |
| 3 | No evidence-less unknown Match | **Met** |
| 4 | No cartesian Match inflation | **Met (28651→654)** |
| 5 | SFT balancer active | **Met** |
| 6 | classify+risk share clearly down | **Met (94.5%→60.1%)** |
| 7 | quality_level ≠ review_status in stats | **Met** |
| 8 | Global near-dedup | **Met** |
| 9 | Agent multi-step + tool result + citations | **Met** |
| 10 | validation ≥5 projects | **Met (7)** |
| 11 | PORTAL_SNAPSHOT out of train artifacts | **Met** |
| 12 | Priority req review 500–800 | **Met (602)** |
| 13 | Priority RAG review 200–300 | **Met (220)** |
| 14 | New tests pass | **Met** |
| 15 | Backend tests no regression | **Met** |
| 16 | Reports updated | **Met** |
