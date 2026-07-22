# Requirement Matching E2E Acceptance (Step 8)

**Date:** 2026-07-22  
**Baseline:** after atomic Match semantics (mixed-batch fail / direct conflict proof /
dual-scope `not_applicable` / cancel-vs-persist race)

## Environment

| Item | Status |
|------|--------|
| GPU | 6× RTX 5090 (vLLM on GPU 0) |
| Model | local Qwen3-8B via OpenAI-compatible endpoint (`VLLM_USE_FLASHINFER_SAMPLER=0`) |
| Postgres / API | healthy; migration `d9e3f4a5b6c7` applied |
| Project (anonymized) | `E2E-RETR-001` |
| Tender source | `e2e_tender.txt` (existing indexed Requirements) |
| Company scope | **脱敏合成样例** `e2e_company_qual.txt`（无真实客户企业数据时使用） |
| Match scope | 2 qualification + 2 material Requirements; `force=true` |

## Run stats (prior live fixture)

| Metric | Value |
|--------|--------|
| Status | **succeeded** |
| Processed | 4 / 4 |
| supported | 2 |
| partially_supported | 0 |
| insufficient_evidence | 2 |
| conflicting_evidence | 0 |
| failed | 0 |
| Wall time | ~6 s |

> **Note:** Qwen3-8B endpoint was reachable during final close-out, but **no new live
> Match run** was executed for the four remaining safety gaps (would need dedicated
> synthetic conflict / dual-scope fixtures). Prior **脱敏合成样例** still applies for
> basic supported / insufficient paths only — **not** a real enterprise-data
> acceptance. Dual-conflict proof, dual-scope `not_applicable`, mixed-batch fail,
> and cancel-vs-persist race are covered by mock LLM unit/integration tests
> (`tests/test_requirement_matching.py`), marked N/A for live E2E.

## Coverage checklist

| Target | Result | Notes (desensitized) |
|--------|--------|----------------------|
| 资格：营业执照 / 民事责任 | PASS `supported` | Company quote grounded; tender EvidenceLink present; `needs_review=true` |
| 资格：电子与智能化贰级资质 | PASS `supported` | Quote grounded; file `e2e_company_qual.txt` |
| 材料：预付款比例条款 | PASS `insufficient_evidence` | UI = 当前材料未找到充分证据 |
| 材料：质保期贰年 | PASS `insufficient_evidence` | No warranty evidence; risk elevated |

## Security / atomic hardenings (unit / N/A live)

| Hardening | Live E2E | Automated |
|-----------|----------|-----------|
| Mixed valid+invalid batch → whole run fail, zero writes | N/A | Covered |
| Direct conflict proof fields + no illegal downgrade | N/A | Covered |
| Dual-scope `not_applicable` (req + current) | N/A | Covered |
| Fabricated summary tokens → run fail | N/A | Covered |
| Cancel vs persist race (FOR UPDATE) | N/A | Covered |
| Empty company materials → no LLM, never `not_applicable` | Prior | Covered |
| Global atomic failure (any batch fatal → zero writes) | N/A | Covered |

## Spot checks

- Dual evidence: tender EvidenceLink count ≥1 on sampled `supported`; company link
  `role=company_support` with Document Center path containing `documentId` + `chunkId`.
- No absolute “企业不符合 / 必然满足” wording in summaries.
- Empty company materials: run `failed`, `result_kind=empty_company_materials`, no LLM,
  old matches retained.
- Project isolation and tender-as-company-evidence rejection → run failed + zero writes
  (automated).

## Verdict

**Real Qwen3-8B requirement↔company matching: PASS** on the prior anonymized
**脱敏合成样例** fixture (supported + insufficient_evidence).  
Atomic / dual-evidence hardenings: **PASS via automated tests**; live re-acceptance
**N/A**（无真实企业数据时仅用脱敏合成样例；未做新 live run）。

## Blockers / N/A

- Live `partially_supported` / `conflicting_evidence` / `not_applicable` / cancel race
  not forced in the prior fixture (covered by mock LLM unit tests).
- Uploaded company file is a **脱敏合成样例** for acceptance only; not production
  customer data.

## Reproduce

```bash
export LLM_ENABLED=true LLM_MODEL=bidpilot-qwen3-8b
./scripts/serve_qwen3_vllm.sh
# API with HF_HUB_OFFLINE=1 if Hub unreachable
curl -X POST "$API/api/v1/projects/$PID/requirement-matches/runs" \
  -H 'Content-Type: application/json' \
  -d '{"requirement_ids":[...],"document_ids":[...],"document_types":["qualification"],"force":true}'
```
