# Requirement Extraction E2E Acceptance (Step 7 security hardening)

**Date:** 2026-07-22  
**Baseline:** `50c0cbb` + fix commit  
**Live run artifact (gitignored JSON):** `docs/acceptance/requirement_extraction_live.json`

## Environment

| Item | Status |
|------|--------|
| GPU | 6× RTX 5090 (vLLM on GPU 0) |
| Model | `bidpilot-qwen3-8b` (local Qwen3-8B), `VLLM_USE_FLASHINFER_SAMPLER=0` |
| Postgres / Qdrant / OpenSearch / API | healthy |
| Project (anonymized) | `E2E-RETR-001` |
| Scope | `tender` / `announcement` / `amendment` / `contract` (`force=true`) |
| Indexed source | `e2e_tender.txt` (3 chunks) |

## Run stats

| Metric | Value |
|--------|--------|
| Status | **succeeded** |
| Chunks | 3 / 3 |
| Candidates accepted | 11 |
| Created | 11 |
| Failed chunks | 0 |
| Conflicts | 0 (no amendment doc in this fixture) |
| Wall time | ~14.2 s |

## Six-category checklist

| Category | Result | Notes (desensitized) |
|----------|--------|----------------------|
| 资格要求 | PASS | Multiple qualification rows; quote grounded in 第一章 招标公告 |
| 截止/流程 | PASS | Deposit-before-deadline row; 第二章 投标人须知 |
| 技术参数 | N/A | Fixture has no numeric technical-spec clause; none invented |
| 评分项 | PASS | 综合评分法 50/30/20；第二章 |
| 废标条件 | PARTIAL | “未按时缴纳…拒收” captured under deadline/commercial; no dedicated `invalid_bid` label in fixture wording |
| 补遗冲突 | N/A | No amendment document in project; system did not fabricate conflicts |

## Spot checks

- `normalized_requirement` is contiguous soft-grounded against primary chunk (fabricated grades/amounts rejected in unit tests).
- Evidence quote length > 0; primary `document` / `section` / `clause` / `page` match EvidenceLink locators (`locator_ok=true` on sampled rows).
- All rows `review_status=unreviewed`, `quality_level=pending`; conflicts only marked when present (none here).
- UI「需求清单」reads the same list/detail APIs used above.

## Verdict

**Real Qwen3-8B structured extraction: PASS** on this indexed tender fixture, with N/A for categories absent from source text (no invention).

## Reproduce

```bash
export LLM_ENABLED=true LLM_MODEL=bidpilot-qwen3-8b
export LLM_MODEL_PATH=/absolute/path/to/Qwen3-8B
./scripts/serve_qwen3_vllm.sh
# API with HF_HUB_OFFLINE=1 if Hub unreachable
curl -X POST "$API/api/v1/projects/$PID/requirements/extractions" \
  -H 'Content-Type: application/json' \
  -d '{"document_types":["tender","announcement","amendment","contract"],"force":true}'
```
