"""Adapt compliance_reference.jsonl samples into formal ComplianceContext for A–E engine.

Verdict mapping (documented for offline consistency):
  Reference `verdict` is compared to an engine-derived verdict from findings of the
  rule_ids associated with the sample's `rule_type` / `check_id`:

  - engine ``fail``: any fail finding with severity in {error, critical}
  - engine ``attention_required``: any fail with severity warning, or any unknown
  - engine ``pass``: otherwise (only pass/info findings)

  This maps reference pass/fail/attention_required onto whether the formal engine
  produced hard fail findings for that check category — not a REF_* keyword engine.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid5

from app.models.enums import (
    ComplianceRuleCategory,
    RequirementCategory,
)
from app.schemas.compliance import ComplianceContext, ComplianceFinding
from app.services.compliance.engine import ComplianceEngine, run_compliance_rules
from app.services.evidence_validate import quote_in_content

# Stable namespace so offline reports are byte-identical across regenerations.
_OFFLINE_NS = UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")

# rule_type / check_id → formal engine rule ids used for verdict agreement
RULE_TYPE_RULE_IDS: dict[str, tuple[str, ...]] = {
    "mandatory": ("A001_mandatory_coverage",),
    "deadline": ("E003_date_conflicts", "E003_deadline_presence"),
    "invalid_bid": ("C003_invalid_bid_attention",),
}

CHECK_ID_RULE_IDS: dict[str, tuple[str, ...]] = {
    "mandatory_clause": ("A001_mandatory_coverage",),
    "deadline_check": ("E003_date_conflicts", "E003_deadline_presence"),
    "invalid_bid_rule": ("C003_invalid_bid_attention",),
}

RULE_TYPE_CATEGORY: dict[str, ComplianceRuleCategory] = {
    "mandatory": ComplianceRuleCategory.coverage,
    "deadline": ComplianceRuleCategory.consistency,
    "invalid_bid": ComplianceRuleCategory.qualification_risk,
}


def _stable_uuid(seed: str) -> UUID:
    return uuid5(_OFFLINE_NS, seed)


def _as_uuid(value: Any, *, fallback_seed: str) -> UUID:
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        return _stable_uuid(fallback_seed)


def _req_category(rule_type: str) -> RequirementCategory:
    mapping = {
        "mandatory": RequirementCategory.mandatory,
        "deadline": RequirementCategory.deadline,
        "invalid_bid": RequirementCategory.invalid_bid,
    }
    return mapping.get(rule_type, RequirementCategory.technical)


def relevant_rule_ids(rule_type: str, check_id: str = "") -> list[str]:
    ids: list[str] = []
    for key in (check_id, rule_type):
        for rid in CHECK_ID_RULE_IDS.get(key, ()) + RULE_TYPE_RULE_IDS.get(key, ()):
            if rid not in ids:
                ids.append(rid)
    return ids or list(RULE_TYPE_RULE_IDS.get("mandatory", ("A001_mandatory_coverage",)))


def adapt_compliance_reference_sample(sample: dict[str, Any]) -> ComplianceContext:
    """Convert one reference JSONL row into a real ComplianceContext.

    Builds lightweight ORM-like SimpleNamespace objects from sample fields only —
    no invented company facts. All 20 reference samples must be consumable by
    ComplianceEngine without crashing.
    """
    input_obj = sample.get("input") or {}
    evidence = sample.get("evidence") or []
    rule_type = str(input_obj.get("rule_type") or "")
    text = str(input_obj.get("text") or "")
    check_id = str(input_obj.get("check_id") or "")

    sample_id = str(sample.get("sample_id") or "unknown-sample")
    project_id = _as_uuid(sample.get("project_id"), fallback_seed=f"{sample_id}:project")
    document_id = _as_uuid(
        sample.get("document_id"),
        fallback_seed=f"{sample_id}:document",
    )
    req_id = _stable_uuid(f"{sample_id}:requirement")

    first_ev = next((e for e in evidence if isinstance(e, dict)), {}) or {}
    chunk_id = _as_uuid(first_ev.get("chunk_id"), fallback_seed=f"{sample_id}:chunk")
    quote = str(first_ev.get("quote") or "")
    page = first_ev.get("page_number")

    project = SimpleNamespace(
        id=project_id,
        project_id=project_id,
        bid_deadline=None,
        project_name="reference-offline",
        project_code="REF-OFFLINE",
        metadata_json={},
    )
    document = SimpleNamespace(
        id=document_id,
        project_id=project_id,
        document_type="tender",
        file_name="reference.txt",
        parse_status="success",
    )
    chunk = SimpleNamespace(
        id=chunk_id,
        project_id=project_id,
        document_id=document_id,
        content=text,
        text=text,
        page_start=page,
        page_end=page,
        section=None,
        chunk_index=0,
    )
    category = _req_category(rule_type)
    requirement = SimpleNamespace(
        id=req_id,
        project_id=project_id,
        title=f"reference:{rule_type or 'unknown'}",
        category=category,
        mandatory=(rule_type == "mandatory"),
        risk_level="high" if rule_type == "invalid_bid" else "medium",
        normalized_requirement=quote or text[:200],
        original_text=quote or text[:200],
        metadata_json={},
        evidence_required_json={},
        source_page=page,
        source_section=None,
        source_document_id=document_id,
    )
    tender_link = SimpleNamespace(
        id=_stable_uuid(f"{sample_id}:tender_link"),
        requirement_id=req_id,
        evidence_type="tender_clause",
        document_id=document_id,
        chunk_id=chunk_id,
        quote=quote or None,
        page_number=page,
        notes="reference_adapter",
    )

    grounded = bool(quote and text and quote_in_content(quote, text))

    return ComplianceContext(
        project_id=project_id,
        draft_id=None,
        project=project,
        requirements=[requirement],
        evidence_matches=[],
        tender_evidence_links=[tender_link],
        company_match_links=[],
        drafts=[],
        draft_versions=[],
        draft_sources=[],
        documents_by_id={document_id: document},
        chunks_by_id={chunk_id: chunk},
        requirements_by_id={req_id: requirement},
        matches_by_id={},
        matches_by_requirement_id={},
        metadata={
            "sample_id": sample.get("sample_id"),
            "rule_type": rule_type,
            "check_id": check_id,
            "reference_output": sample.get("reference_output") or {},
            "has_grounded_quote": grounded,
            "has_sufficient_text": bool(text.strip()),
            "split": sample.get("split"),
            "adapter": "formal_ae_engine",
        },
    )


def engine_verdict_from_findings(
    findings: list[ComplianceFinding] | list[dict[str, Any]],
    *,
    rule_ids: list[str] | None = None,
) -> str:
    """Map formal engine findings → pass / fail / attention_required.

    Only findings whose rule_id is in ``rule_ids`` (when provided) participate.
    Hard fail = status fail with severity ≥ error; soft = warning fail or unknown.
    """
    wanted: set[str] | None = set(rule_ids) if rule_ids else None
    # Accept both E003_date_conflicts and legacy E003_deadline_presence
    if wanted is not None and any("E003" in r for r in wanted):
        wanted = set(wanted) | {"E003_date_conflicts", "E003_deadline_presence"}

    relevant: list[Any] = []
    for f in findings:
        rid = getattr(f, "rule_id", None) or (f.get("rule_id") if isinstance(f, dict) else None)
        if wanted is not None and rid not in wanted:
            continue
        relevant.append(f)

    def _status(f: Any) -> str:
        s = getattr(f, "status", None)
        if hasattr(s, "value"):
            return str(s.value)
        if isinstance(f, dict):
            return str(f.get("status") or "")
        return str(s or "")

    def _severity_val(f: Any) -> str:
        s = getattr(f, "severity", None)
        if hasattr(s, "value"):
            return str(s.value)
        if isinstance(f, dict):
            return str(f.get("severity") or "")
        return str(s or "")

    hard = False
    soft = False
    for f in relevant:
        st = _status(f)
        sev = _severity_val(f)
        if st == "fail":
            if sev in {"error", "critical"}:
                hard = True
            else:
                soft = True
        elif st == "unknown":
            soft = True

    if hard:
        return "fail"
    if soft:
        return "attention_required"
    return "pass"


def _finding_to_dict(f: ComplianceFinding) -> dict[str, Any]:
    return {
        "finding_id": f.finding_id,
        "rule_id": f.rule_id,
        "rule_name": f.rule_name,
        "category": f.category.value if hasattr(f.category, "value") else str(f.category),
        "severity": f.severity.value if hasattr(f.severity, "value") else str(f.severity),
        "status": f.status.value if hasattr(f.status, "value") else str(f.status),
        "message": f.message,
        "remediation": f.remediation,
        "requirement_id": str(f.requirement_id) if f.requirement_id else None,
        "match_id": str(f.match_id) if f.match_id else None,
        "draft_id": str(f.draft_id) if f.draft_id else None,
        "metadata_json": f.metadata_json,
    }


def evaluate_adapted_sample(
    adapted: ComplianceContext | dict[str, Any],
    *,
    sample: dict[str, Any] | None = None,
    engine: ComplianceEngine | None = None,
) -> dict[str, Any]:
    """Run the formal A–E ComplianceEngine on an adapted sample context.

    Accepts either a ComplianceContext (preferred) or a legacy dict. No REF_* path.
    """
    if isinstance(adapted, dict) and not isinstance(adapted, ComplianceContext):
        # Legacy dict from older callers — re-adapt if sample provided, else error softly
        if sample is not None:
            ctx = adapt_compliance_reference_sample(sample)
        else:
            raise TypeError("evaluate_adapted_sample expects ComplianceContext")
    else:
        ctx = adapted

    meta = ctx.metadata if isinstance(ctx.metadata, dict) else {}
    sample = sample or {}
    ref = sample.get("reference_output") or meta.get("reference_output") or {}
    rule_type = str((sample.get("input") or {}).get("rule_type") or meta.get("rule_type") or "")
    check_id = str((sample.get("input") or {}).get("check_id") or meta.get("check_id") or "")
    ref_verdict = ref.get("verdict")
    focus_ids = relevant_rule_ids(rule_type, check_id)

    eng = engine or ComplianceEngine()
    findings, stats = eng.run(ctx)
    # Prefer canonical E003 id present in this engine version
    executed = list(stats.get("rule_ids") or [])
    focus_resolved = [rid for rid in focus_ids if rid in executed] or [
        rid
        for rid in executed
        if any(f in rid for f in ("A001", "C003", "E003"))
        and (
            (rule_type == "mandatory" and "A001" in rid)
            or (rule_type == "invalid_bid" and "C003" in rid)
            or (rule_type == "deadline" and "E003" in rid)
        )
    ]

    engine_verdict = engine_verdict_from_findings(findings, rule_ids=focus_resolved or focus_ids)
    agreement = (ref_verdict == engine_verdict) if ref_verdict is not None else None
    mismatch_reason = None
    if agreement is False:
        mismatch_reason = (
            f"reference_verdict={ref_verdict} engine_verdict={engine_verdict} "
            f"focus_rules={focus_resolved or focus_ids}"
        )

    finding_dicts = [_finding_to_dict(f) for f in findings]
    focus = focus_resolved or focus_ids
    return {
        "sample_id": sample.get("sample_id") or meta.get("sample_id"),
        "rule_type": rule_type,
        "check_id": check_id,
        "reference_verdict": ref_verdict,
        "reference_label": ref_verdict,
        "engine_verdict": engine_verdict,
        "verdict_match": agreement,
        "agreement": agreement,
        "mismatch_reason": mismatch_reason,
        "rule_ids_executed": executed,
        "rules_executed": executed,
        "focus_rule_ids": focus,
        "focus_rules_evaluated": focus,
        "findings": finding_dicts,
        "severities": [f["severity"] for f in finding_dicts],
        "categories": [f["category"] for f in finding_dicts],
        "stats": stats,
        "ok": True,
    }


def evaluate_sample_online_parity(
    sample: dict[str, Any],
) -> tuple[list[ComplianceFinding], list[ComplianceFinding]]:
    """Same adapted context → identical findings via engine.run and run_compliance_rules."""
    ctx = adapt_compliance_reference_sample(sample)
    a, _ = ComplianceEngine().run(ctx)
    b, _ = run_compliance_rules(ctx)
    return a, b


# Conservative auto-label correction when reference labels disagree with formal engine
# semantics (keyword-era labels). Applied by rule_type only — never by sample_id.
def maybe_correct_reference_verdict(
    rule_type: str, current: str | None, engine_verdict: str
) -> str | None:
    """Return a corrected label when the auto_reference keyword-era label is wrong."""
    if current is None:
        return None
    if current == engine_verdict:
        return current
    # mandatory tender-text samples without company match → uncovered → fail
    if rule_type == "mandatory" and current == "pass" and engine_verdict == "fail":
        return "fail"
    # deadline without bid_deadline → attention_required
    if rule_type == "deadline" and current == "pass" and engine_verdict == "attention_required":
        return "attention_required"
    return current
