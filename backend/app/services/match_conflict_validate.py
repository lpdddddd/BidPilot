"""Deterministic validators for conflicting_evidence and dual-scope not_applicable."""

from __future__ import annotations

import re
from typing import Literal
from uuid import UUID

from app.services.evidence_validate import (
    normalize_whitespace,
    soft_normalize_for_grounding,
)

ConflictDimension = Literal[
    "qualification_level",
    "certificate_validity",
    "effective_period",
    "quantity",
    "coverage_scope",
    "technical_parameter",
    "affirmative_negation",
]

_GRADE_PATTERNS: list[tuple[str, int]] = [
    ("特级", 50),
    ("一级", 40),
    ("二级", 30),
    ("三级", 20),
    ("四级", 10),
    ("甲级", 40),
    ("乙级", 30),
    ("丙级", 20),
]

_SCOPE_LIMIT_RE = re.compile(
    r"(仅适用|仅限|仅针对|本标段|本包件|适用范围|服务范围|适用区域|其他区域不适用|"
    r"其他标段不适用|不适用于|范围外)"
)

_REGION_RE = re.compile(
    r"([\u4e00-\u9fff]{1,8}?(?:省|市|区|县|旗|州|盟|镇|乡)|"
    r"[\u4e00-\u9fff]{2,12}标段|[\u4e00-\u9fff]{1,8}包件)"
)

_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")

_AFFIRM_NEG_PAIRS: list[tuple[str, str]] = [
    ("具备", "不具备"),
    ("有", "无"),
    ("是", "否"),
    ("有效", "无效"),
    ("有效", "过期"),
    ("通过", "未通过"),
    ("符合", "不符合"),
]


def _subject_in_text(subject: str, text: str) -> bool:
    needle = soft_normalize_for_grounding(subject)
    hay = soft_normalize_for_grounding(text)
    return bool(needle) and needle in hay


def _claim_in_quote(claim: str, quote: str) -> bool:
    c = normalize_whitespace(claim)
    q = normalize_whitespace(quote)
    return bool(c) and c in q


def _extract_grades(text: str) -> list[tuple[str, int]]:
    found: list[tuple[str, int]] = []
    for label, rank in _GRADE_PATTERNS:
        if label in (text or ""):
            found.append((label, rank))
    return found


def _mutex_grades(primary: str, conflicting: str) -> bool:
    a = _extract_grades(primary)
    b = _extract_grades(conflicting)
    if not a or not b:
        return False
    return max(r for _, r in a) != max(r for _, r in b)


def _mutex_quantities(primary: str, conflicting: str) -> bool:
    nums_a = set(_NUMBER_RE.findall(primary or ""))
    nums_b = set(_NUMBER_RE.findall(conflicting or ""))
    if not nums_a or not nums_b:
        return False
    return nums_a.isdisjoint(nums_b) or nums_a != nums_b


def _extract_regions(text: str) -> set[str]:
    return {m.group(0) for m in _REGION_RE.finditer(text or "")}


def _has_exclusive_scope_language(text: str) -> bool:
    return bool(_SCOPE_LIMIT_RE.search(text or ""))


def validate_scope_exclusion(req_quote: str, current_quote: str) -> bool:
    """True when requirement and current-scope quotes prove mutually exclusive scopes."""
    req_q = normalize_whitespace(req_quote)
    cur_q = normalize_whitespace(current_quote)
    if not req_q or not cur_q:
        return False
    if not _has_exclusive_scope_language(req_q):
        return False
    # Current side should also locate the object/project into a concrete scope.
    if not (
        _has_exclusive_scope_language(cur_q)
        or any(tok in cur_q for tok in ("服务范围", "位于", "注册于", "所在地", "覆盖"))
    ):
        return False
    req_regions = _extract_regions(req_q)
    cur_regions = _extract_regions(cur_q)
    if not req_regions or not cur_regions:
        return False
    # Mutually exclusive: no shared region token, and both sides name a region.
    return req_regions.isdisjoint(cur_regions)


def _mutex_affirmative_negation(primary: str, conflicting: str) -> bool:
    soft_a = soft_normalize_for_grounding(primary)
    soft_b = soft_normalize_for_grounding(conflicting)
    for pos, neg in _AFFIRM_NEG_PAIRS:
        pos_n = soft_normalize_for_grounding(pos)
        neg_n = soft_normalize_for_grounding(neg)
        if (pos_n in soft_a and neg_n in soft_b) or (neg_n in soft_a and pos_n in soft_b):
            return True
    return False


def _mutex_discrete_values(primary: str, conflicting: str) -> bool:
    """Conflicting discrete claim strings after whitespace normalize."""
    a = normalize_whitespace(primary)
    b = normalize_whitespace(conflicting)
    if not a or not b:
        return False
    # Soft-equal after grounding still counts as same claim.
    return soft_normalize_for_grounding(a) != soft_normalize_for_grounding(b)


def _dimension_mutex(
    dimension: ConflictDimension,
    primary_claim: str,
    conflicting_claim: str,
    primary_quote: str,
    conflict_quote: str,
) -> bool:
    if dimension == "qualification_level":
        return _mutex_grades(primary_claim, conflicting_claim) or _mutex_grades(
            primary_quote, conflict_quote
        )
    if dimension == "quantity":
        return _mutex_quantities(primary_claim, conflicting_claim)
    if dimension == "coverage_scope":
        return validate_scope_exclusion(primary_quote, conflict_quote) or (
            _extract_regions(primary_claim).isdisjoint(_extract_regions(conflicting_claim))
            and bool(_extract_regions(primary_claim))
            and bool(_extract_regions(conflicting_claim))
        )
    if dimension == "affirmative_negation":
        return _mutex_affirmative_negation(primary_claim, conflicting_claim) or (
            _mutex_affirmative_negation(primary_quote, conflict_quote)
        )
    if dimension in (
        "certificate_validity",
        "effective_period",
        "technical_parameter",
    ):
        return _mutex_discrete_values(primary_claim, conflicting_claim)
    return False


def validate_direct_company_conflict(
    *,
    primary_chunk_id: UUID,
    primary_quote: str,
    conflict_chunk_id: UUID,
    conflict_quote: str,
    primary_project_id: UUID,
    conflict_project_id: UUID,
    primary_doc_type_excluded: bool,
    conflict_doc_type_excluded: bool,
    allowed_chunk_ids: set[UUID],
    conflict_dimension: ConflictDimension | None,
    conflict_subject: str | None,
    primary_claim_value: str | None,
    conflicting_claim_value: str | None,
    primary_chunk_content: str,
    conflict_chunk_content: str,
) -> tuple[bool, str | None]:
    """Return (ok, reason_code). reason_code is set when ok is False."""
    if primary_doc_type_excluded or conflict_doc_type_excluded:
        return False, "out_of_scope_chunk"
    if primary_project_id != conflict_project_id:
        return False, "cross_project_chunk"
    if primary_chunk_id not in allowed_chunk_ids or conflict_chunk_id not in allowed_chunk_ids:
        return False, "out_of_scope_chunk"

    p_quote = normalize_whitespace(primary_quote)
    c_quote = normalize_whitespace(conflict_quote)
    if not p_quote or not c_quote:
        return False, "quote_not_found"
    if p_quote not in normalize_whitespace(primary_chunk_content or ""):
        return False, "quote_not_found"
    if c_quote not in normalize_whitespace(conflict_chunk_content or ""):
        return False, "quote_not_found"

    # Forbid same chunk + same quote (identical text position).
    if primary_chunk_id == conflict_chunk_id and soft_normalize_for_grounding(
        p_quote
    ) == soft_normalize_for_grounding(c_quote):
        return False, "invalid_conflict"

    if not conflict_dimension or not conflict_subject:
        return False, "invalid_conflict"
    if not primary_claim_value or not conflicting_claim_value:
        return False, "invalid_conflict"

    primary_hay = f"{p_quote}\n{primary_chunk_content or ''}"
    conflict_hay = f"{c_quote}\n{conflict_chunk_content or ''}"
    if not _subject_in_text(conflict_subject, primary_hay):
        return False, "invalid_conflict"
    if not _subject_in_text(conflict_subject, conflict_hay):
        return False, "invalid_conflict"

    if not _claim_in_quote(primary_claim_value, p_quote):
        return False, "invalid_conflict"
    if not _claim_in_quote(conflicting_claim_value, c_quote):
        return False, "invalid_conflict"

    if not _dimension_mutex(
        conflict_dimension,
        primary_claim_value,
        conflicting_claim_value,
        p_quote,
        c_quote,
    ):
        return False, "invalid_conflict"

    return True, None
