"""Target adapter protocol."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from app.services.evaluation.case_loader import EvaluationCase, assert_no_reference_in_target_input


@dataclass
class TargetResult:
    ok: bool
    output: dict[str, Any] = field(default_factory=dict)
    error_summary: str | None = None
    unavailable: bool = False
    duration_ms: int | None = None


@dataclass
class TargetCapability:
    target_type: str
    available: bool
    reason: str | None = None


class EvaluationTarget(Protocol):
    target_type: str

    def capability(self) -> TargetCapability: ...

    def run_case(self, case: EvaluationCase) -> TargetResult: ...


def run_target_safely(target: EvaluationTarget, case: EvaluationCase) -> TargetResult:
    payload = case.target_input()
    assert_no_reference_in_target_input(payload)
    return target.run_case(case)
