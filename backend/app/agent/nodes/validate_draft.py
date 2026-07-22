from __future__ import annotations

import re
import time
from typing import Any
from uuid import UUID

from app.agent.nodes._helpers import (
    begin_node,
    finish_node,
    get_runtime,
    record_tool_event,
)
from app.agent.state import NODE_VALIDATE, AgentState, append_warning
from app.services.compliance.config import STRONG_SATISFACTION_PATTERNS
from app.tools.agent_tools import GetProposalDraftInput, get_proposal_draft
from app.tools.compliance_tools import DraftComplianceInput, check_draft_compliance

_SATISFACTION_RE = re.compile("|".join(STRONG_SATISFACTION_PATTERNS))
_MSG_MAX = 240
_FAIL_SEVERITIES = frozenset({"error", "critical"})


def _enum_val(value: Any) -> str:
    if value is None:
        return ""
    return str(value.value if hasattr(value, "value") else value)


def _truncate(text: str | None, limit: int = _MSG_MAX) -> str | None:
    if text is None:
        return None
    s = str(text)
    if len(s) <= limit:
        return s
    return s[: limit - 1] + "…"


def _finding_dict(finding: Any, *, draft_id: str | None = None) -> dict[str, Any]:
    return {
        "finding_id": str(getattr(finding, "finding_id", None) or ""),
        "rule_id": str(getattr(finding, "rule_id", None) or ""),
        "category": _enum_val(getattr(finding, "category", None)),
        "severity": _enum_val(getattr(finding, "severity", None)),
        "status": _enum_val(getattr(finding, "status", None)),
        "message": _truncate(getattr(finding, "message", None)) or "",
        "remediation": _truncate(getattr(finding, "remediation", None)),
        "requirement_id": (
            str(finding.requirement_id) if getattr(finding, "requirement_id", None) else None
        ),
        "match_id": str(finding.match_id) if getattr(finding, "match_id", None) else None,
        "draft_id": (str(finding.draft_id) if getattr(finding, "draft_id", None) else draft_id),
    }


def _text_from_draft(data: dict) -> str:
    parts: list[str] = []
    draft = data.get("draft") or {}
    current = draft.get("current_version") or {}
    md = current.get("content_markdown") or ""
    if md:
        parts.append(md)
    content = current.get("content_json") or {}
    if isinstance(content, dict):
        # Structured fields only — avoid dumping huge blobs into agent state.
        for section in content.get("sections") or []:
            if not isinstance(section, dict):
                continue
            for block in section.get("blocks") or []:
                if isinstance(block, dict) and block.get("content"):
                    parts.append(str(block["content"])[:_MSG_MAX])
    return "\n".join(parts)


def _rule_summary(findings: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for f in findings:
        rid = f.get("rule_id") or "?"
        st = f.get("status") or "?"
        parts.append(f"{rid}:{st}")
    return ",".join(parts[:20])


def _validation_failed(findings: list[dict[str, Any]], meta: dict[str, Any]) -> bool:
    if meta.get("warnings_fail_validation"):
        return any(f.get("status") == "fail" for f in findings)
    return any(
        f.get("status") == "fail" and f.get("severity") in _FAIL_SEVERITIES for f in findings
    )


def validate_draft(state: AgentState) -> AgentState:
    state, skipped = begin_node(state, NODE_VALIDATE)
    if skipped:
        return state

    runtime = get_runtime()
    meta = dict(state.get("metadata") or {})

    # Deterministic test hook — keep for backward-compat unit tests only.
    if "force_draft_validation" in meta:
        ok = bool(meta["force_draft_validation"])
        state["draft_validation_ok"] = ok
        state["draft_findings"] = []
        if not ok:
            append_warning(state, "draft validation forced fail")
        record_tool_event(
            state,
            name="validate_draft",
            status="ok" if ok else "error",
            summary=f"forced={ok}",
        )
        return finish_node(state, NODE_VALIDATE)

    project_id = state.get("project_id")
    draft_ids = [str(d) for d in (state.get("draft_ids") or [])]
    all_findings: list[dict[str, Any]] = []
    draft_texts: list[str] = []

    if meta.get("risk_draft_preview"):
        draft_texts.append(str(meta["risk_draft_preview"]))
    if meta.get("draft_text_override"):
        draft_texts.append(str(meta["draft_text_override"]))

    if project_id and draft_ids:
        for draft_id in draft_ids:
            started = time.perf_counter()
            try:
                result = check_draft_compliance(
                    runtime.db,
                    DraftComplianceInput(
                        project_id=UUID(project_id),
                        draft_id=UUID(draft_id),
                        idempotency_key=f"agent-{state['run_id']}-draft-check-{draft_id}",
                    ),
                )
                duration_ms = int((time.perf_counter() - started) * 1000)
                findings_raw = list((result.report.findings if result.report else []) or [])
                structured = [_finding_dict(f, draft_id=draft_id) for f in findings_raw]
                all_findings.extend(structured)
                record_tool_event(
                    state,
                    name="check_draft_compliance",
                    status="ok" if result.ok else "error",
                    duration_ms=duration_ms,
                    summary=(
                        f"status={'ok' if result.ok else 'error'};"
                        f"finding_count={len(structured)};"
                        f"rules={_rule_summary(structured)}"
                    ),
                )
            except Exception as exc:  # noqa: BLE001
                duration_ms = int((time.perf_counter() - started) * 1000)
                record_tool_event(
                    state,
                    name="check_draft_compliance",
                    status="error",
                    duration_ms=duration_ms,
                    summary=f"status=error;finding_count=0;error={type(exc).__name__}",
                )
                append_warning(state, f"draft compliance failed for {draft_id}: {exc}")
                all_findings.append(
                    {
                        "finding_id": f"agent-compliance-error-{draft_id}",
                        "rule_id": "AGENT_SUPPLEMENT_compliance_error",
                        "category": "engine",
                        "severity": "error",
                        "status": "fail",
                        "message": _truncate(f"{type(exc).__name__}: {exc}") or "error",
                        "remediation": "Fix compliance engine / draft linkage and re-validate.",
                        "requirement_id": None,
                        "match_id": None,
                        "draft_id": draft_id,
                    }
                )

            # Load truncated text for agent-level strong-claim guard only.
            try:
                loaded = get_proposal_draft(
                    runtime.db,
                    GetProposalDraftInput(
                        project_id=UUID(project_id),
                        draft_id=UUID(draft_id),
                    ),
                )
                if loaded.ok:
                    draft_texts.append(_text_from_draft(loaded.data))
            except Exception:  # noqa: BLE001
                pass
    elif (
        not draft_ids
        and not draft_texts
        and not meta.get("allow_empty_draft")
        and state.get("status") not in {"blocked", "failed"}
    ):
        # No draft ids — empty content fails unless allow_empty_draft / risk preview.
        all_findings.append(
            {
                "finding_id": "agent-empty-draft",
                "rule_id": "AGENT_SUPPLEMENT_empty_draft",
                "category": "draft_safety",
                "severity": "error",
                "status": "fail",
                "message": "draft content empty",
                "remediation": "Generate a proposal draft before validation.",
                "requirement_id": None,
                "match_id": None,
                "draft_id": None,
            }
        )

    forbid_claims = bool(
        state.get("critical_qualification") or meta.get("forbid_satisfaction_claims")
    )
    combined = "\n".join(draft_texts)
    if forbid_claims and combined and _SATISFACTION_RE.search(combined):
        # Agent-level guard only — do not reimplement other D-class rules here.
        all_findings.append(
            {
                "finding_id": "agent-supplement-strong-claim",
                "rule_id": "AGENT_SUPPLEMENT_strong_claim",
                "category": "draft_safety",
                "severity": "error",
                "status": "fail",
                "message": _truncate(
                    "draft still contains strong satisfaction claim "
                    "(e.g. 完全满足) under forbid_satisfaction / critical_qualification"
                )
                or "strong claim",
                "remediation": "Remove satisfaction claims; use risk-only wording.",
                "requirement_id": None,
                "match_id": None,
                "draft_id": draft_ids[-1] if draft_ids else None,
            }
        )

    state["draft_findings"] = all_findings
    failed = _validation_failed(all_findings, meta)
    state["draft_validation_ok"] = not failed
    if failed:
        for f in all_findings:
            if f.get("status") == "fail" and f.get("severity") in _FAIL_SEVERITIES:
                append_warning(
                    state,
                    f"draft finding {f.get('rule_id')}: {f.get('message')}",
                )
    record_tool_event(
        state,
        name="validate_draft",
        status="ok" if not failed else "error",
        summary=(
            f"pass findings={len(all_findings)}"
            if not failed
            else f"fail findings={len(all_findings)} rules={_rule_summary(all_findings)}"
        ),
    )
    return finish_node(state, NODE_VALIDATE)
