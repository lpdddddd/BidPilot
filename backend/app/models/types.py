from enum import Enum
from typing import Any, TypeVar

from sqlalchemy import Enum as SAEnum

from app.models.enums import (
    ActorAuthn,
    EvidenceMatchStatus,
    MatchReviewAction,
    MatchReviewReasonCode,
    MatchReviewStatus,
    QualityLevel,
    ReviewStatus,
    RiskLevel,
)

E = TypeVar("E", bound=Enum)


def EnumType(enum_cls: type[E], *, name: str, create_type: bool = True) -> SAEnum:
    """PostgreSQL native enum with values stored as the enum member values."""
    return SAEnum(
        enum_cls,
        name=name,
        values_callable=lambda obj: [item.value for item in obj],
        validate_strings=True,
        create_constraint=True,
        create_type=create_type,
    )


# Shared enum types reused across multiple tables (single PG type each).
risk_level_enum = EnumType(RiskLevel, name="risk_level")
quality_level_enum = EnumType(QualityLevel, name="quality_level")
review_status_enum = EnumType(ReviewStatus, name="review_status")
evidence_match_status_enum = EnumType(EvidenceMatchStatus, name="evidence_match_status")
match_review_status_enum = EnumType(MatchReviewStatus, name="match_review_status")
match_review_action_enum = EnumType(MatchReviewAction, name="match_review_action")
match_review_reason_code_enum = EnumType(
    MatchReviewReasonCode, name="match_review_reason_code"
)
actor_authn_enum = EnumType(ActorAuthn, name="actor_authn")


def empty_json() -> dict[str, Any]:
    return {}
