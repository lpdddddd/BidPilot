"""Adapt compliance_reference.jsonl samples into engine-consumable dicts."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from app.services.compliance.config import (
    REF_FINDING_META,
    REFERENCE_RULE_TYPE_KEYWORDS,
)
from app.services.evidence_validate import quote_in_content


def _annotate_finding(finding: dict[str, Any]) -> dict[str, Any]:
    """Attach severity/category for offline aggregates (REF_* lightweight checks)."""
    rule_id = str(finding.get("rule_id") or "")
    status = str(finding.get("status") or "unknown")
    meta = REF_FINDING_META.get(rule_id)
    if meta is None:
        # prefix match e.g. REF_mandatory_keyword
        for key, value in REF_FINDING_META.items():
            if rule_id.startswith(key.rstrip("_")) or rule_id == key:
                meta = value
                break
        if meta is None and rule_id.startswith("REF_") and "_keyword" in rule_id:
            if "invalid_bid" in rule_id:
                meta = REF_FINDING_META["REF_invalid_bid_keyword"]
            elif "deadline" in rule_id:
                meta = REF_FINDING_META["REF_deadline_keyword"]
            elif "mandatory" in rule_id:
                meta = REF_FINDING_META["REF_mandatory_keyword"]
    severity, category = meta or ("info", "engine")
    if status == "pass":
        severity = "info"
    elif status == "unknown" and severity in {"error", "critical"}:
        severity = "warning"
    out = dict(finding)
    out["severity"] = severity
    out["category"] = category
    return out


def adapt_compliance_reference_sample(sample: dict[str, Any]) -> dict[str, Any]:
    """Convert one reference JSONL row into a ComplianceContext-like dict.

    Does not invent company facts — only uses fields present in the sample.
    """
    input_obj = sample.get("input") or {}
    evidence = sample.get("evidence") or []
    citation = sample.get("citation_metadata") or {}
    rule_type = str(input_obj.get("rule_type") or "")
    text = str(input_obj.get("text") or "")
    check_id = str(input_obj.get("check_id") or "")

    quotes = []
    for item in evidence:
        if not isinstance(item, dict):
            continue
        quote = item.get("quote")
        if quote:
            quotes.append(
                {
                    "quote": quote,
                    "chunk_id": item.get("chunk_id"),
                    "document_id": item.get("document_id"),
                    "page_number": item.get("page_number"),
                    "grounded": quote_in_content(str(quote), text) if text else False,
                }
            )

    keywords = REFERENCE_RULE_TYPE_KEYWORDS.get(rule_type, ())
    keyword_hits = [kw for kw in keywords if kw and kw in text]

    return {
        "sample_id": sample.get("sample_id"),
        "project_id": sample.get("project_id"),
        "document_id": sample.get("document_id"),
        "rule_type": rule_type,
        "check_id": check_id,
        "text": text,
        "instruction": input_obj.get("instruction"),
        "reference_output": sample.get("reference_output") or {},
        "quotes": quotes,
        "keyword_hits": keyword_hits,
        "citation_metadata": citation,
        "split": sample.get("split"),
        "synthetic_requirement_id": str(uuid4()),
        "has_sufficient_text": bool(text.strip()),
        "has_grounded_quote": any(q.get("grounded") for q in quotes),
    }


def evaluate_adapted_sample(adapted: dict[str, Any]) -> dict[str, Any]:
    """Lightweight deterministic checks over an adapted reference sample."""
    rule_type = adapted.get("rule_type") or ""
    ref = adapted.get("reference_output") or {}
    ref_verdict = ref.get("verdict")
    findings: list[dict[str, Any]] = []

    if not adapted.get("has_sufficient_text"):
        findings.append(
            {
                "rule_id": "REF_insufficient_text",
                "status": "unknown",
                "message": "sample text empty; cannot evaluate",
            }
        )
    elif rule_type and not adapted.get("keyword_hits"):
        findings.append(
            {
                "rule_id": f"REF_{rule_type}_keyword",
                "status": "fail",
                "message": f"expected keywords for rule_type={rule_type} not found",
            }
        )
    else:
        findings.append(
            {
                "rule_id": f"REF_{rule_type or 'unknown'}_keyword",
                "status": "pass",
                "message": "keyword pattern check passed or N/A",
            }
        )

    if adapted.get("quotes"):
        if adapted.get("has_grounded_quote"):
            findings.append(
                {
                    "rule_id": "REF_quote_grounding",
                    "status": "pass",
                    "message": "at least one evidence quote grounded in text",
                }
            )
        else:
            findings.append(
                {
                    "rule_id": "REF_quote_grounding",
                    "status": "fail",
                    "message": "evidence quotes not grounded in input text",
                }
            )
    else:
        findings.append(
            {
                "rule_id": "REF_quote_grounding",
                "status": "unknown",
                "message": "no evidence quotes in sample",
            }
        )

    findings = [_annotate_finding(f) for f in findings]

    engine_fail = any(f["status"] == "fail" for f in findings)
    engine_unknown = any(f["status"] == "unknown" for f in findings) and not engine_fail
    if engine_fail:
        engine_verdict = "fail"
    elif engine_unknown:
        engine_verdict = "attention_required"
    else:
        engine_verdict = "pass"

    return {
        "sample_id": adapted.get("sample_id"),
        "rule_type": rule_type,
        "reference_verdict": ref_verdict,
        "engine_verdict": engine_verdict,
        "verdict_match": ref_verdict == engine_verdict
        if ref_verdict is not None
        else None,
        "findings": findings,
        "keyword_hits": adapted.get("keyword_hits") or [],
    }
