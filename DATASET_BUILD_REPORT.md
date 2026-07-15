# BidPilot Dataset Build Report

Generated at: `2026-07-15T15:02:07.280910+00:00`

## Policy

- Real public procurement / public-resource projects only.
- Synthetic companies, virtual qualifications, fictional awards, and fabricated gold answers are **forbidden**.
- Auto extracts are silver/pending; gold requires human review with official URL, page, and quote.

## Discovery & Download

- List hits / notices fetched (batch): `{'attachments_downloaded': 2, 'attachments_failed': 0, 'award_projects': 25, 'bundle_levels': {'incomplete': 88, 'level_a': 0, 'level_b': 6, 'level_c': 23}, 'categories': ['zbgg', 'cjgg'], 'dry_run': False, 'list_hits': 14, 'notices_fetched': 6, 'notices_kept': 2, 'projects_built': 117, 'require_keyword_in_title': True, 'tender_file_projects': 41}`
- Downloaded real projects in manifests: **120**
- Official source distribution: `{'download.ccgp.gov.cn': 43, 'www.ccgp.gov.cn': 68, 'www.ggzy.gov.cn': 1, 'www.zycg.gov.cn': 1, 'www.gzggzy.cn': 3, 'ygp.gdzwfw.gov.cn': 1, 'www.ccgp-beijing.gov.cn': 1, 'www.ccgp-jiangsu.gov.cn': 1, 'www.ccgp-zhejiang.gov.cn': 1}`
- Discovery failures: **582**
- Download pending / failures: **0**

### Failure reasons

- `http_404_or_empty`: 551
- `access_restricted_403`: 9
- `access restricted status=403 url=https://gdgpo.czt.gd.gov.cn/`: 9
- `[Errno -2] Name or service not known`: 9
- `The read operation timed out`: 4

## Project Bundle Levels

- level_a: **0**
- level_b: **12**
- level_c: **31**
- incomplete: **77**

## Documents

- Total: **205**
- PDF: **42**, DOCX: **24**, HTML: **131**
- Tender documents: **67**
- Award notices: **26**
- Contract notices: **0**
- Evaluation results: **0**

## Labels & Matches

- Requirements: silver=20724, gold=0
- pending (review_status): **20724**
- low_confidence: **10416**
- review_queue (exported CSV source): **10417**
- Definitions: `{'pending': 'quality!=gold and review_status in {pending,unreviewed}', 'low_confidence': 'auto-labeled confidence < 0.55', 'review_queue': 'rows in review/pending/requirements_pending.jsonl exported for human review'}`
- Disclosed suppliers: **62**
- Matches: {'total': 28651, 'unknown': 28651, 'satisfied': 0, 'missing': 0, 'partially_satisfied': 0, 'verifiable': 0}
- RAG questions: **105**
- Agent tasks: **48**
- SFT split counts: {'train': 18779, 'validation': 2285, 'test': 6245, 'quality': {'silver': 27309}, 'train_projects': 26, 'validation_projects': 3, 'test_projects': 14}
- Effective trainable SFT: **27299**

## Target Gaps (not filled with fiction)

- projects_collected: **30** remaining
- with_tender_document: **57** remaining
- level_a: **20** remaining
- level_b: **28** remaining
- finely_annotated: **30** remaining
- heldout: **0** remaining
- requirements_gold: **1500** remaining
- rag_eval: **395** remaining
- sft: **0** remaining

## Human Review TODO

`{'requirements_pending_status': 20724, 'requirements_low_confidence': 10416, 'requirements_review_queue': 10417, 'rag_pending': 105, 'matches_unknown': 28651, 'sft_pending': 27309}`

Validation ok: **True**

## Notes

- incomplete projects stay in raw/manifests; they are excluded from formal SFT train.
- level_c used for clause extraction only; level_a/b for RAG/cross-doc tasks.
- Do not start formal LoRA until effective trainable SFT quality is reviewed.

## Round Quality Checklist (2026-07-15)

### User targets vs current

| Target | Current | Status |
|---|---:|---|
| ≥100 real projects | 120 | met |
| ≥50 tender_document | 67 | met |
| ≥25 level_a + level_b | 12 (0+12) | **gap** |
| ≥5 official domains | 9 | met |
| incomplete out of formal SFT | excluded | met |
| gold only via human review | gold=0 | met |

`level_a/b` gap reason: CCGP list/search pages for recent months rarely expose *both* a tender package and an award/contract notice for the same `project_code`. Contract notices remain **0**, so level_a stays 0 without fabricating links.

### Review counter definitions (reconciled)

- **pending**: `quality != gold` and `review_status in {pending, unreviewed}` → currently 20724
- **low_confidence**: auto-label `confidence < 0.55` → currently 10416
- **review_queue**: rows exported into `review/pending/requirements_pending.jsonl` (subset for human sheet) → currently 10417

These are not the same set; pending ⊇ low_confidence on most runs, and review_queue is the exported subset (not all pending rows).

### SFT rebuild stats

- candidate_raw: **48588**
- after filters / deduped: **27309**
- with_evidence (pre-task accounting): **45213**
- filtered_no_evidence_match: **17156**
- filtered_unknown_cap: **11231** (evidence_match unknown capped; final unknown ratio **0.0**)
- effective_trainable: **27299**
- by task: citation_qa 105, project_info_extract 39, qualification_extract 889, requirement_classify 15194, risk_detect 10964, scoring_extract 103, tool_call 15
- gold/silver: all silver (gold=0)
- splits: train 18779 / validation 2285 / test 6245
- train/val/test projects: 26 / 3 / 14
- domains (SFT sample source): download.ccgp.gov.cn 26435, www.ccgp.gov.cn 874
- bundle_level (SFT): level_b 5719, level_c 21590

### Human review stage-1 exports

- `datasets/review/exported/priority_requirements_review.csv`: 462 rows across 10 highest-completeness projects (target 500–800 gold after review)
- `datasets/review/exported/priority_rag_review.csv`: 105 rows (target 200–300 after more RAG harvest/review)
- Required gold fields on accept: reviewer, reviewed_at, source_url, document_id, chunk_id, source_page, source_quote

### Formal LoRA

**Not started.** Wait for human gold enrichment; effective silver SFT is available for dry-run experimentation only.

