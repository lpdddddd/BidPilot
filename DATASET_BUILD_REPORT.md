# BidPilot Dataset Build Report

Generated at: `2026-07-15T07:59:22.366303+00:00`

## Policy

- Real public procurement / public-resource projects only.
- Synthetic companies, virtual qualifications, fictional awards, and fabricated gold answers are **forbidden**.
- Auto extracts are silver/pending; gold requires human review with official URL, page, and quote.

## Discovery & Download

- List hits / notices fetched (batch): `{'attachments_downloaded': 8, 'attachments_failed': 0, 'dry_run': False, 'list_hits': 131, 'notices_fetched': 131, 'notices_kept': 29, 'projects_built': 29, 'restricted_probes': [{'ok': True, 'status': 200, 'url': 'https://search.ccgp.gov.cn/bxsearch'}, {'error': 'access restricted status=403 url=https://gdgpo.czt.gd.gov.cn/', 'ok': False, 'status': None, 'url': 'https://gdgpo.czt.gd.gov.cn/'}, {'error': '[Errno -2] Name or service not known', 'ok': False, 'status': None, 'url': 'https://deal.ggzy.gov.cn/'}, {'ok': True, 'status': 200, 'url': 'https://ygp.gdzwfw.gov.cn/ggzy-portal/center/#/jygg'}]}`
- Downloaded real projects in manifests: **32**
- Official source distribution: `{'www.ccgp.gov.cn': 32, 'download.ccgp.gov.cn': 0}`
- Discovery failures: **3**
- Download pending / failures: **0**

### Failure reasons

- `access_restricted_403`: 1
- `access restricted status=403 url=https://gdgpo.czt.gd.gov.cn/`: 1
- `[Errno -2] Name or service not known`: 1

## Project Bundle Levels

- level_a: **0**
- level_b: **3**
- level_c: **5**
- incomplete: **24**

## Documents

- Total: **49**
- PDF: **8**, DOCX: **7**, HTML: **32**
- Tender documents: **11**
- Award notices: **8**
- Contract notices: **0**
- Evaluation results: **0**

## Labels & Matches

- Requirements silver/gold/pending: {'silver': 3949, 'gold': 0, 'pending_review': 2026, 'quality': {'silver': 3949}, 'review_status': {'pending': 3949}}
- Disclosed suppliers: **17**
- Matches: {'total': 4143, 'unknown': 4143, 'satisfied': 0, 'missing': 0, 'partially_satisfied': 0, 'verifiable': 0}
- RAG questions: **44**
- SFT: {'train': 4754, 'validation': 2423, 'test': 2261, 'quality': {'silver': 9438}, 'train_projects': 18, 'validation_projects': 2, 'test_projects': 13}

## Target Gaps (not filled with fiction)

- projects_collected: **118** remaining
- with_tender_document: **92** remaining
- level_a: **20** remaining
- level_b: **37** remaining
- finely_annotated: **30** remaining
- heldout: **0** remaining
- requirements_gold: **1500** remaining
- rag_eval: **456** remaining
- sft: **3062** remaining

## Human Review TODO

`{'requirements_pending': 2026, 'rag_pending': 44, 'matches_unknown': 4143, 'sft_pending': 9438}`

Validation ok: **True**

