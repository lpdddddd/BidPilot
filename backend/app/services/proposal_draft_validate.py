"""Strict validation for proposal draft LLM / manual structured content."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from app.models.enums import EvidenceMatchStatus
from app.schemas.proposal_draft import UNEVIDENCED_MARKER

_MAX_CONTENT_LEN = 4000
_MAX_TITLE_LEN = 512
_FORBIDDEN_CLAIM_RE = re.compile(
    r"(建议投标|不建议投标|保证中标|必然满足|已承诺|盖章承诺|报价|投标函|签章页)"
)

FACTUAL_BLOCK_KINDS = frozenset({"supported_response", "partial_response"})
GAP_BLOCK_KINDS = frozenset({"material_gap", "risk_item", "scope_item"})
ALLOWED_BLOCK_KINDS = FACTUAL_BLOCK_KINDS | GAP_BLOCK_KINDS | {"manual_unreferenced"}

STATUS_TO_BLOCK: dict[EvidenceMatchStatus, str] = {
    EvidenceMatchStatus.supported: "supported_response",
    EvidenceMatchStatus.partially_supported: "partial_response",
    EvidenceMatchStatus.insufficient_evidence: "material_gap",
    EvidenceMatchStatus.conflicting_evidence: "risk_item",
    EvidenceMatchStatus.not_applicable: "scope_item",
}

BLOCK_TO_STATUS: dict[str, EvidenceMatchStatus] = {v: k for k, v in STATUS_TO_BLOCK.items()}


@dataclass
class CitationMeta:
    citation_id: UUID
    requirement_id: UUID
    match_id: UUID
    match_status: EvidenceMatchStatus
    source_role: str
    quote: str
    quote_id: str
    location: dict[str, Any]


@dataclass
class WhitelistContext:
    project_id: UUID
    requirement_ids: set[UUID]
    match_ids: set[UUID]
    # requirement_id -> match status (confirmed active only)
    requirement_match_status: dict[UUID, EvidenceMatchStatus]
    requirement_match_id: dict[UUID, UUID]
    citation_ids: set[UUID]
    citations: dict[UUID, CitationMeta]
    quote_ids: set[str]
    quotes: dict[str, CitationMeta]
    # Requirements that are confirmed but not positive (gap/risk/scope)
    gap_requirement_ids: set[UUID] = field(default_factory=set)
    risk_requirement_ids: set[UUID] = field(default_factory=set)
    scope_requirement_ids: set[UUID] = field(default_factory=set)
    excluded_requirement_ids: set[UUID] = field(default_factory=set)


class DraftValidationError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def normalize_text(value: str | None, *, max_len: int = _MAX_CONTENT_LEN) -> str:
    if value is None:
        return ""
    cleaned = " ".join(str(value).split())
    if len(cleaned) > max_len:
        cleaned = cleaned[:max_len]
    return cleaned


def escape_for_export(value: str) -> str:
    return html.escape(normalize_text(value), quote=True)


def content_has_unevidenced_manual(content: dict[str, Any]) -> bool:
    marker = UNEVIDENCED_MARKER
    for section in content.get("sections") or []:
        if not isinstance(section, dict):
            continue
        for block in section.get("blocks") or []:
            if not isinstance(block, dict):
                continue
            kind = block.get("block_kind")
            text = normalize_text(block.get("content"))
            citations = block.get("citation_ids") or []
            if (kind == "manual_unreferenced" or marker in text) and not citations:
                return True
            flags = block.get("flags") or []
            if "unevidenced_manual" in flags:
                return True
    return bool(content.get("has_unevidenced_manual_content"))


def _parse_uuid(value: Any, *, field_name: str) -> UUID:
    try:
        return UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise DraftValidationError(f"invalid {field_name}: {value}") from exc


def validate_structured_content(
    data: dict[str, Any],
    whitelist: WhitelistContext,
    *,
    allow_manual_unreferenced: bool = False,
) -> dict[str, Any]:
    """Validate and normalize structured draft JSON against the whitelist."""
    if not isinstance(data, dict):
        raise DraftValidationError("LLM output must be a JSON object")

    title = normalize_text(data.get("title"), max_len=_MAX_TITLE_LEN)
    if not title:
        raise DraftValidationError("title is required")

    sections_in = data.get("sections")
    if not isinstance(sections_in, list) or not sections_in:
        raise DraftValidationError("sections must be a non-empty list")

    sections_out: list[dict[str, Any]] = []
    for section in sections_in:
        if not isinstance(section, dict):
            raise DraftValidationError("section must be an object")
        section_key = normalize_text(section.get("section_key"), max_len=128)
        section_title = normalize_text(section.get("title"), max_len=256)
        if not section_key or not section_title:
            raise DraftValidationError("section_key and title are required")
        blocks_in = section.get("blocks")
        if not isinstance(blocks_in, list):
            raise DraftValidationError("blocks must be a list")
        blocks_out: list[dict[str, Any]] = []
        for block in blocks_in:
            blocks_out.append(
                _validate_block(
                    block,
                    whitelist,
                    allow_manual_unreferenced=allow_manual_unreferenced,
                )
            )
        sections_out.append(
            {
                "section_key": section_key,
                "title": section_title,
                "blocks": blocks_out,
            }
        )

    matrix_out = _validate_matrix(data.get("compliance_matrix") or [], whitelist)
    warnings_out = _validate_warnings(data.get("warnings") or [], whitelist)

    normalized: dict[str, Any] = {
        "title": title,
        "sections": sections_out,
        "compliance_matrix": matrix_out,
        "warnings": warnings_out,
        "disclaimer": (
            "本文件为基于已审核材料生成的响应准备草稿，须经人工复核、补充、签署和法务或业务确认后方可使用，"
            "不构成投标结论或投标提交文件。"
        ),
    }
    if content_has_unevidenced_manual(normalized):
        normalized["has_unevidenced_manual_content"] = True
    return normalized


def _validate_block(
    block: Any,
    whitelist: WhitelistContext,
    *,
    allow_manual_unreferenced: bool,
) -> dict[str, Any]:
    if not isinstance(block, dict):
        raise DraftValidationError("block must be an object")
    kind = normalize_text(block.get("block_kind"), max_len=64)
    if kind not in ALLOWED_BLOCK_KINDS:
        raise DraftValidationError(f"illegal block_kind: {kind}")
    if kind == "manual_unreferenced" and not allow_manual_unreferenced:
        raise DraftValidationError("manual_unreferenced not allowed in generated drafts")

    content = normalize_text(block.get("content"))
    if not content:
        raise DraftValidationError("block content is required")
    if _FORBIDDEN_CLAIM_RE.search(content):
        raise DraftValidationError("block content contains forbidden bid/commitment language")

    req_ids = [
        _parse_uuid(x, field_name="requirement_id") for x in (block.get("requirement_ids") or [])
    ]
    if not req_ids:
        raise DraftValidationError("block.requirement_ids required")
    for rid in req_ids:
        if rid not in whitelist.requirement_ids:
            raise DraftValidationError(f"requirement_id not in whitelist: {rid}")

    citation_ids = [
        _parse_uuid(x, field_name="citation_id") for x in (block.get("citation_ids") or [])
    ]
    quote_ids = [normalize_text(str(x), max_len=64) for x in (block.get("source_quote_ids") or [])]
    human_action = normalize_text(block.get("human_action"), max_len=1000) or None

    for rid in req_ids:
        status = whitelist.requirement_match_status.get(rid)
        if kind == "manual_unreferenced":
            continue
        expected = STATUS_TO_BLOCK.get(status) if status else None
        if expected is None:
            raise DraftValidationError(
                f"requirement {rid} has no eligible confirmed match for block_kind {kind}"
            )
        if kind != expected:
            raise DraftValidationError(
                f"block_kind {kind} incompatible with match status {status} for {rid}"
            )

    if kind in FACTUAL_BLOCK_KINDS:
        if not citation_ids or not quote_ids:
            raise DraftValidationError(f"{kind} requires citation_ids and source_quote_ids")
        _assert_citations_and_quotes(citation_ids, quote_ids, req_ids, whitelist)
        gap_hint_missing = (
            kind == "partial_response"
            and not human_action
            and "缺口" not in content
            and "补充" not in content
        )
        if gap_hint_missing:
            raise DraftValidationError("partial_response must describe remaining gaps")

    if kind in {"risk_item", "scope_item"}:
        if not citation_ids:
            raise DraftValidationError(f"{kind} requires dual-side citation_ids")
        if len(citation_ids) < 2:
            raise DraftValidationError(f"{kind} requires at least two citation_ids")
        _assert_citations_and_quotes(
            citation_ids,
            quote_ids,
            req_ids,
            whitelist,
            require_quotes=bool(quote_ids),
        )
        # Still require quotes when provided; require quotes for conflict/scope
        if not quote_ids or len(quote_ids) < 2:
            raise DraftValidationError(f"{kind} requires at least two source_quote_ids")

    if kind == "material_gap":
        # Citations optional; must not claim satisfaction
        if re.search(r"(已具备|完全满足|充分支撑)", content):
            raise DraftValidationError("material_gap must not claim satisfaction")
        for cid in citation_ids:
            if cid not in whitelist.citation_ids:
                raise DraftValidationError(f"unknown citation_id: {cid}")

    if kind == "manual_unreferenced":
        if citation_ids:
            _assert_citations_and_quotes(
                citation_ids,
                quote_ids,
                req_ids,
                whitelist,
                require_quotes=False,
            )
        elif UNEVIDENCED_MARKER not in content:
            content = f"{content}（{UNEVIDENCED_MARKER}）"

    out: dict[str, Any] = {
        "block_kind": kind,
        "requirement_ids": [str(x) for x in req_ids],
        "content": content,
        "citation_ids": [str(x) for x in citation_ids],
        "source_quote_ids": quote_ids,
        "human_action": human_action,
    }
    if kind == "manual_unreferenced" and not citation_ids:
        out["flags"] = ["unevidenced_manual"]
    # Enrich locations from whitelist only (LLM cannot invent)
    locations = []
    for cid in citation_ids:
        meta = whitelist.citations.get(cid)
        if meta:
            locations.append({"citation_id": str(cid), **meta.location})
    if locations:
        out["locations"] = locations
    return out


def _assert_citations_and_quotes(
    citation_ids: list[UUID],
    quote_ids: list[str],
    req_ids: list[UUID],
    whitelist: WhitelistContext,
    *,
    require_quotes: bool = True,
) -> None:
    for cid in citation_ids:
        if cid not in whitelist.citation_ids:
            raise DraftValidationError(f"unknown citation_id: {cid}")
        meta = whitelist.citations[cid]
        if meta.requirement_id not in req_ids:
            raise DraftValidationError(f"citation {cid} does not belong to block requirements")
    if require_quotes:
        for qid in quote_ids:
            if qid not in whitelist.quote_ids:
                raise DraftValidationError(f"unknown or invented source_quote_id: {qid}")
            qmeta = whitelist.quotes[qid]
            if qmeta.citation_id not in citation_ids:
                raise DraftValidationError(f"quote {qid} does not match citation_ids")


def _validate_matrix(matrix: Any, whitelist: WhitelistContext) -> list[dict[str, Any]]:
    if not isinstance(matrix, list):
        raise DraftValidationError("compliance_matrix must be a list")
    out: list[dict[str, Any]] = []
    allowed_disp = {
        "responded",
        "partially_responded",
        "material_gap",
        "risk_review",
        "scope_review",
        "excluded",
    }
    for row in matrix:
        if not isinstance(row, dict):
            raise DraftValidationError("matrix row must be object")
        rid = _parse_uuid(row.get("requirement_id"), field_name="requirement_id")
        if rid not in whitelist.requirement_ids:
            raise DraftValidationError(f"matrix requirement not in whitelist: {rid}")
        disposition = normalize_text(row.get("disposition"), max_len=64)
        if disposition not in allowed_disp:
            raise DraftValidationError(f"illegal disposition: {disposition}")
        status = whitelist.requirement_match_status.get(rid)
        _assert_disposition_matches(status, disposition, rid, whitelist)
        citation_ids = [
            _parse_uuid(x, field_name="citation_id") for x in (row.get("citation_ids") or [])
        ]
        for cid in citation_ids:
            if cid not in whitelist.citation_ids:
                raise DraftValidationError(f"unknown matrix citation: {cid}")
        out.append(
            {
                "requirement_id": str(rid),
                "disposition": disposition,
                "citation_ids": [str(x) for x in citation_ids],
                "required_human_action": normalize_text(
                    row.get("required_human_action"), max_len=1000
                )
                or None,
            }
        )
    return out


def _assert_disposition_matches(
    status: EvidenceMatchStatus | None,
    disposition: str,
    rid: UUID,
    whitelist: WhitelistContext,
) -> None:
    if rid in whitelist.excluded_requirement_ids:
        if disposition != "excluded":
            raise DraftValidationError(f"excluded requirement {rid} must use disposition=excluded")
        return
    expected = {
        EvidenceMatchStatus.supported: "responded",
        EvidenceMatchStatus.partially_supported: "partially_responded",
        EvidenceMatchStatus.insufficient_evidence: "material_gap",
        EvidenceMatchStatus.conflicting_evidence: "risk_review",
        EvidenceMatchStatus.not_applicable: "scope_review",
    }.get(status)  # type: ignore[arg-type]
    if expected and disposition != expected:
        raise DraftValidationError(
            f"disposition {disposition} incompatible with status {status} for {rid}"
        )


def _validate_warnings(warnings: Any, whitelist: WhitelistContext) -> list[dict[str, Any]]:
    if not isinstance(warnings, list):
        raise DraftValidationError("warnings must be a list")
    allowed = {
        "material_gap",
        "conflicting_evidence",
        "scope_exclusion",
        "pending_review",
        "rejected_match",
        "needs_more_material",
    }
    out: list[dict[str, Any]] = []
    for row in warnings:
        if not isinstance(row, dict):
            raise DraftValidationError("warning must be object")
        rid = _parse_uuid(row.get("requirement_id"), field_name="requirement_id")
        if rid not in whitelist.requirement_ids:
            raise DraftValidationError(f"warning requirement not in whitelist: {rid}")
        wtype = normalize_text(row.get("warning_type"), max_len=64)
        if wtype not in allowed:
            raise DraftValidationError(f"illegal warning_type: {wtype}")
        content = normalize_text(row.get("content"))
        if not content:
            raise DraftValidationError("warning content required")
        citation_ids = [
            _parse_uuid(x, field_name="citation_id") for x in (row.get("citation_ids") or [])
        ]
        for cid in citation_ids:
            if cid not in whitelist.citation_ids:
                raise DraftValidationError(f"unknown warning citation: {cid}")
        out.append(
            {
                "requirement_id": str(rid),
                "warning_type": wtype,
                "content": content,
                "citation_ids": [str(x) for x in citation_ids],
            }
        )
    return out


def render_markdown(content: dict[str, Any]) -> str:
    """Server-side markdown render from structured content (no LLM)."""
    from app.schemas.proposal_draft import DISCLAIMER

    lines: list[str] = [
        f"# {normalize_text(content.get('title'), max_len=_MAX_TITLE_LEN)}",
        "",
        f"> {DISCLAIMER}",
        "",
    ]
    for section in content.get("sections") or []:
        lines.append(f"## {normalize_text(section.get('title'), max_len=256)}")
        lines.append("")
        for block in section.get("blocks") or []:
            kind = block.get("block_kind")
            lines.append(f"### [{kind}]")
            lines.append(normalize_text(block.get("content")))
            cites = block.get("citation_ids") or []
            if cites:
                lines.append(f"- 引用: {', '.join(str(c) for c in cites)}")
            locs = block.get("locations") or []
            for loc in locs:
                parts = []
                if loc.get("document_file_name"):
                    parts.append(str(loc["document_file_name"]))
                if loc.get("page_start") is not None:
                    parts.append(f"p.{loc['page_start']}")
                if loc.get("section"):
                    parts.append(str(loc["section"]))
                if loc.get("clause_id"):
                    parts.append(str(loc["clause_id"]))
                if parts:
                    lines.append(f"- 定位: {' / '.join(parts)}")
            if block.get("human_action"):
                lines.append(f"- 人工动作: {normalize_text(block.get('human_action'))}")
            lines.append("")

    matrix = content.get("compliance_matrix") or []
    if matrix:
        lines.append("## 合规准备矩阵")
        lines.append("")
        lines.append("| Requirement | Disposition | Citations |")
        lines.append("|---|---|---|")
        for row in matrix:
            lines.append(
                f"| {row.get('requirement_id')} | {row.get('disposition')} | "
                f"{', '.join(row.get('citation_ids') or [])} |"
            )
        lines.append("")

    warnings = content.get("warnings") or []
    if warnings:
        lines.append("## 风险与待核验")
        lines.append("")
        for w in warnings:
            lines.append(
                f"- [{w.get('warning_type')}] {w.get('requirement_id')}: "
                f"{normalize_text(w.get('content'))}"
            )
        lines.append("")

    lines.append("---")
    lines.append(DISCLAIMER)
    lines.append("")
    return "\n".join(lines)
