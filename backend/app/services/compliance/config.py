"""Central thresholds and keyword lists for compliance rules."""

from __future__ import annotations

ENGINE_VERSION = "compliance-rules-1.1.0"

# Document types treated as tender-side (must not appear as company evidence).
TENDER_DOCUMENT_TYPES = frozenset(
    {
        "tender",
        "announcement",
        "amendment",
        "result",
        "contract",
    }
)

COMPANY_DOCUMENT_TYPES = frozenset(
    {
        "company_profile",
        "qualification",
        "case",
        "personnel",
        "product",
    }
)

# Match statuses that count as positive coverage for mandatory requirements.
POSITIVE_MATCH_STATUSES = frozenset(
    {
        "supported",
        "partially_supported",
    }
)

GAP_MATCH_STATUSES = frozenset(
    {
        "insufficient_evidence",
        "conflicting_evidence",
    }
)

# Active matches that still leave the requirement uncovered.
UNCOVERED_MATCH_STATUSES = frozenset(
    {
        "insufficient_evidence",
        "conflicting_evidence",
    }
)

# Definitive negative statuses (not mere "insufficient").
DEFINITIVE_NEGATIVE_STATUSES = frozenset(
    {
        "conflicting_evidence",
    }
)

# Categories that raise qualification / invalid-bid risk when unsupported.
QUALIFICATION_CATEGORIES = frozenset(
    {
        "qualification",
        "mandatory",
        "invalid_bid",
    }
)

HIGH_RISK_LEVELS = frozenset({"high", "critical"})

# Draft text patterns that must never appear as unverified claims.
FORBIDDEN_DRAFT_CLAIM_PATTERNS = (
    r"建议投标",
    r"不建议投标",
    r"保证中标",
    r"必然满足",
    r"已承诺",
    r"盖章承诺",
)

# Strong satisfaction claims that require a positive match backing.
STRONG_SATISFACTION_PATTERNS = (
    r"完全满足",
    r"已具备",
    r"保证满足",
    r"全面满足",
    r"完全符合",
    r"已完全响应",
    r"确保满足",
)

# Placeholder / unfinished draft markers.
PLACEHOLDER_PATTERNS = (
    r"TODO",
    r"FIXME",
    r"待补充",
    r"待完善",
    r"占位符",
    r"占位",
    r"\{\{",
    r"\}\}",
    r"\[TBD\]",
    r"xxx+",
)

# Structured field keys that may carry expiry / amount thresholds (never invent).
STRUCTURED_EXPIRY_KEYS = (
    "expiry",
    "expires_at",
    "expire_at",
    "expiry_date",
    "valid_until",
    "certificate_expiry",
)
STRUCTURED_AMOUNT_KEYS = (
    "amount",
    "min_amount",
    "amount_threshold",
    "threshold_amount",
    "registered_capital",
    "budget_cny",
)
STRUCTURED_CONFLICT_KEYS = (
    "conflict",
    "conflicts",
    "conflict_markers",
    "conflict_notes",
    "notes",
)

# Minimum quote length before grounding checks apply.
MIN_QUOTE_LENGTH = 8

# Draft considered "obviously short" below this (chars, whitespace-normalized).
MIN_DRAFT_CONTENT_CHARS = 40

# Offline reference adapter keyword maps (no invented company facts).
REFERENCE_RULE_TYPE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "mandatory": ("必须", "应当", "须", "不得擅自", "否则视为无效"),
    "deadline": ("截止", "投标截止", "递交截止", "开标时间"),
    "invalid_bid": ("无效投标", "投标无效", "废标", "否决投标"),
}

# Map lightweight REF_* findings → severity / category for offline aggregates.
REF_FINDING_META: dict[str, tuple[str, str]] = {
    # rule_id prefix or exact → (severity, category)
    "REF_insufficient_text": ("warning", "coverage"),
    "REF_mandatory_keyword": ("error", "coverage"),
    "REF_deadline_keyword": ("warning", "consistency"),
    "REF_invalid_bid_keyword": ("critical", "qualification_risk"),
    "REF_quote_grounding": ("error", "evidence"),
}
