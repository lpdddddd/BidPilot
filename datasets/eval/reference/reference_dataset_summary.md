# BidPilot Auto Reference Dataset Summary

- Generator: `bidpilot-reference-1.0.0`
- Label source: auto_reference / silver only (never human_gold)
- Total accepted samples: **140**
- Rejected samples: **0**
- Seed: `42`
- use_llm: `False`

## Counts by task

- `rag`: 30
- `extraction`: 30
- `matching`: 30
- `compliance`: 20
- `drafting`: 20
- `unanswerable`: 10

## Splits

- `train`: 98
- `validation`: 14
- `test`: 28

## Label sources

- `auto_reference`: 140

## Target checklist

- `rag`: 30 / 30 ✓
- `extraction`: 30 / 30 ✓
- `matching`: 30 / 30 ✓
- `compliance`: 20 / 20 ✓
- `drafting`: 20 / 20 ✓
- `unanswerable`: 10 / 10 ✓

## Notes

- This is an **auto reference** set for course demos and automatic evaluation.
- It is **not** expert human gold.
- All citation quotes are validated against real chunk text (whitespace-normalized).
