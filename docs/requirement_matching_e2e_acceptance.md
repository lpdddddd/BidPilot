# Requirement Matching E2E Acceptance (Step 8)

**Date:** 2026-07-22  
**Baseline:** after `fix: preserve requirements when forced extraction validation fails` + this feature commit

## Environment

| Item | Status |
|------|--------|
| GPU | 6× RTX 5090 (vLLM on GPU 0) |
| Model | local Qwen3-8B via OpenAI-compatible endpoint (`VLLM_USE_FLASHINFER_SAMPLER=0`) |
| Postgres / API | healthy; migration `d9e3f4a5b6c7` applied |
| Project (anonymized) | `E2E-RETR-001` |
| Tender source | `e2e_tender.txt` (existing indexed Requirements) |
| Company scope | synthetic desensitized `e2e_company_qual.txt` (`qualification`, 5 chunks) |
| Match scope | 2 qualification + 2 material Requirements; `force=true` |

## Run stats

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

## Coverage checklist

| Target | Result | Notes (desensitized) |
|--------|--------|----------------------|
| 资格：营业执照 / 民事责任 | PASS `supported` | Company quote grounded in section「一、主体资格」; tender EvidenceLink present; `needs_review=true` |
| 资格：电子与智能化贰级资质 | PASS `supported` | Quote grounded in section「二、专业资质」; file `e2e_company_qual.txt` |
| 材料：预付款比例条款 | PASS `insufficient_evidence` | No payment-schedule evidence in company doc; UI semantics = 当前材料未找到充分证据 |
| 材料：质保期贰年 | PASS `insufficient_evidence` | No warranty-period evidence; risk elevated for mandatory-like material |

## Spot checks

- Dual evidence: tender EvidenceLink count ≥1 on sampled `supported`; company link `role=company_support` with Document Center path containing `documentId` + `chunkId`.
- No absolute “企业不符合 / 必然满足” wording in summaries.
- Empty company materials path previously verified: run `failed`, `result_kind=empty_company_materials`, no LLM call, old matches retained.
- Project isolation and tender-as-company-evidence rejection covered by automated tests (not re-run live here).

## Verdict

**Real Qwen3-8B requirement↔company matching: PASS** on this anonymized fixture (supported + insufficient_evidence covered; partially_supported not observed in this 4-item scope).

## Blockers / N/A

- Live `partially_supported` / `conflicting_evidence` not forced in this fixture (covered by mock LLM unit tests).
- Uploaded company file is a **synthetic desensitized sample** for acceptance only; not production customer data.

## Reproduce

```bash
export LLM_ENABLED=true LLM_MODEL=bidpilot-qwen3-8b
./scripts/serve_qwen3_vllm.sh
# API with HF_HUB_OFFLINE=1 if Hub unreachable
curl -X POST "$API/api/v1/projects/$PID/requirement-matches/runs" \
  -H 'Content-Type: application/json' \
  -d '{"requirement_ids":[...],"document_ids":[...],"document_types":["qualification"],"force":true}'
```
