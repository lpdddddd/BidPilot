"""Helpers for building stable ComplianceFinding objects."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from app.models.enums import (
    ComplianceFindingStatus,
    ComplianceRuleCategory,
    ComplianceSeverity,
)
from app.schemas.compliance import ComplianceFinding


def make_finding(
    *,
    rule_id: str,
    rule_name: str,
    category: ComplianceRuleCategory,
    severity: ComplianceSeverity,
    status: ComplianceFindingStatus,
    message: str,
    finding_suffix: str,
    remediation: str | None = None,
    requirement_id: UUID | None = None,
    match_id: UUID | None = None,
    draft_id: UUID | None = None,
    evidence_json: dict[str, Any] | list[Any] | None = None,
    source_location_json: dict[str, Any] | None = None,
    metadata_json: dict[str, Any] | None = None,
) -> ComplianceFinding:
    finding_id = f"{rule_id}:{finding_suffix}"
    return ComplianceFinding(
        finding_id=finding_id[:256],
        rule_id=rule_id,
        rule_name=rule_name,
        category=category,
        severity=severity,
        status=status,
        message=message,
        remediation=remediation,
        requirement_id=requirement_id,
        match_id=match_id,
        draft_id=draft_id,
        evidence_json=evidence_json,
        source_location_json=source_location_json,
        metadata_json=metadata_json,
    )


def enum_value(value: Any) -> str:
    if value is None:
        return ""
    return str(getattr(value, "value", value))
