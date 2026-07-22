from __future__ import annotations

import re
from uuid import UUID

from app.agent.nodes._helpers import (
    get_runtime,
    mark_node_start,
    maybe_interrupt,
    record_tool_event,
)
from app.agent.state import NODE_VALIDATE, AgentState, append_warning, touch
from app.services.compliance.config import FORBIDDEN_DRAFT_CLAIM_PATTERNS, STRONG_SATISFACTION_PATTERNS
from app.tools.agent_tools import GetProposalDraftInput, get_proposal_draft

_SATISFACTION_RE = re.compile("|".join(STRONG_SATISFACTION_PATTERNS))
_FORBIDDEN_RE = re.compile("|".join(FORBIDDEN_DRAFT_CLAIM_PATTERNS))


def _text_from_draft(data: dict) -> str:
    parts: list[str] = []
    draft = data.get("draft") or {}
    current = draft.get("current_version") or {}
    md = current.get("content_markdown") or ""
    if md:
        parts.append(md)
    content = current.get("content_json") or {}
    if isinstance(content, dict):
        parts.append(str(content))
    preview = (data.get("metadata") or {}).get("risk_draft_preview")
    if preview:
        parts.append(str(preview))
    return "\n".join(parts)


def validate_draft(state: AgentState) -> AgentState:
    state = mark_node_start(state, NODE_VALIDATE)
    runtime = get_runtime()
    meta = dict(state.get("metadata") or {})

    # Deterministic test hooks.
    if "force_draft_validation" in meta:
        ok = bool(meta["force_draft_validation"])
        state["draft_validation_ok"] = ok
        if not ok:
            append_warning(state, "draft validation forced fail")
        record_tool_event(
            state,
            name="validate_draft",
            status="ok" if ok else "error",
            summary=f"forced={ok}",
        )
        maybe_interrupt(state, NODE_VALIDATE)
        return touch(state)

    texts: list[str] = []
    if meta.get("risk_draft_preview"):
        texts.append(str(meta["risk_draft_preview"]))
    if meta.get("draft_text_override"):
        texts.append(str(meta["draft_text_override"]))

    for draft_id in state.get("draft_ids") or []:
        try:
            result = get_proposal_draft(
                runtime.db,
                GetProposalDraftInput(
                    project_id=UUID(state["project_id"]),  # type: ignore[arg-type]
                    draft_id=UUID(draft_id),
                ),
            )
            record_tool_event(
                state,
                name="get_proposal_draft",
                status="ok" if result.ok else "error",
                summary=result.summary or result.detail,
            )
            if result.ok:
                texts.append(_text_from_draft(result.data))
        except Exception as exc:  # noqa: BLE001
            record_tool_event(
                state, name="get_proposal_draft", status="error", summary=str(exc)
            )
            append_warning(state, f"could not load draft {draft_id}: {exc}")

    combined = "\n".join(texts)
    forbid_claims = bool(
        state.get("critical_qualification") or meta.get("forbid_satisfaction_claims")
    )

    problems: list[str] = []
    if forbid_claims and combined and _SATISFACTION_RE.search(combined):
        problems.append("draft contains forbidden satisfaction claim (e.g. 完全满足)")
    if combined and _FORBIDDEN_RE.search(combined):
        problems.append("draft contains forbidden claim pattern")

    # Empty draft when not risk-only and not blocked → fail validation to trigger revise.
    if (
        not combined.strip()
        and not state.get("draft_ids")
        and not meta.get("risk_draft_preview")
        and state.get("status") not in {"blocked", "failed"}
    ):
        # Risk-only path may have preview only in metadata; already handled.
        if not meta.get("allow_empty_draft"):
            problems.append("draft content empty")

    ok = len(problems) == 0
    state["draft_validation_ok"] = ok
    for p in problems:
        append_warning(state, p)
    record_tool_event(
        state,
        name="validate_draft",
        status="ok" if ok else "error",
        summary="pass" if ok else "; ".join(problems),
    )
    maybe_interrupt(state, NODE_VALIDATE)
    return touch(state)
