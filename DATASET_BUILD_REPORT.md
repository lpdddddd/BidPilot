# BidPilot Dataset Build Report

Generated at: `2026-07-15T17:22:06.095095+00:00`

## Policy

- Real public procurement / public-resource projects only.
- Synthetic companies, virtual qualifications, fictional awards, and fabricated gold answers are **forbidden**.
- Auto extracts are silver/pending; gold requires human review with official URL, page, and quote.
- Portal homepage snapshots are **not** training coverage.

## Discovery & Download

- List hits / notices fetched (batch): `{'attachments_downloaded': 2, 'attachments_failed': 0, 'award_projects': 25, 'bundle_levels': {'incomplete': 88, 'level_a': 0, 'level_b': 6, 'level_c': 23}, 'categories': ['zbgg', 'cjgg'], 'dry_run': False, 'list_hits': 14, 'notices_fetched': 6, 'notices_kept': 2, 'projects_built': 117, 'require_keyword_in_title': True, 'tender_file_projects': 41}`
- Downloaded real projects in manifests: **120** (portal snapshots excluded)
- Portal snapshot projects (not training): **0**
- Project source domains: `['download.ccgp.gov.cn', 'www.ccgp-beijing.gov.cn', 'www.ccgp-jiangsu.gov.cn', 'www.ccgp-zhejiang.gov.cn', 'www.ccgp.gov.cn', 'www.ggzy.gov.cn', 'www.gzggzy.cn', 'www.zycg.gov.cn', 'ygp.gdzwfw.gov.cn']`
- Tender document domains: `['download.ccgp.gov.cn']`
- SFT source domains (record coverage): `['download.ccgp.gov.cn', 'www.ccgp.gov.cn']` gap=`{'sft_source_domains_count': 2, 'sft_source_domains_min': 5, 'met': False, 'gap': 3, 'note': 'Portal homepage snapshots do not count toward SFT source diversity'}`
- Reachable portal domains (homepage only): `[]`
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

- Total (non-portal): **205**
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
- Disclosed suppliers: **21**
- Matches: {'total': 0, 'evidence_supported_matches': 0, 'unknown': 0, 'satisfied': 0, 'missing': 0, 'partially_satisfied': 0, 'uncertain': 0, 'verifiable': 0}
- RAG questions: **215**
- Agent tasks: **238** (multi-step=204)

## SFT Quality Gates

- structurally_valid_sft: **3624**
- reviewed_trainable_sft: **0** (formal LoRA gate)
- silver_candidate_sft: **3624**
- rejected_sft: **32**
- split counts: train=1919, validation=986, test=719
- projects: train=28, validation=5, test=10
- quality_level: `{'silver': 3624}`
- review_status: `{'pending': 3624}`

## Target Gaps (not filled with fiction)

- projects_collected: **30** remaining
- with_tender_document: **57** remaining
- level_a: **20** remaining
- level_b: **28** remaining
- finely_annotated: **30** remaining
- heldout: **0** remaining
- requirements_gold: **1500** remaining
- rag_eval: **285** remaining
- sft: **8876** remaining
- sft_source_domains: **3** remaining
- reviewed_trainable_sft: **500** remaining

## Human Review TODO

`{'requirements_pending_status': 20724, 'requirements_low_confidence': 10416, 'requirements_review_queue': 10417, 'rag_pending': 215, 'matches_unknown': 0, 'sft_pending': 3624}`

Validation ok: **True**

## Notes

- incomplete projects stay in raw/manifests; they are excluded from formal SFT.
- level_c used for clause extraction only; level_a/b for RAG/cross-doc tasks.
- Do **not** start formal LoRA until reviewed_trainable_sft meets the gold review target.
- Do not count portal homepage snapshots as SFT source diversity.

