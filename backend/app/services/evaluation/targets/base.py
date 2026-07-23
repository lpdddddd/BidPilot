"""Target adapter protocol — targets never receive EvaluationCase or private reference."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from app.services.evaluation.types import (
    TargetCaseInput,
    TargetExecutionContext,
    assert_no_private_reference,
)


@dataclass
class TargetResult:
    ok: bool
    output: dict[str, Any] = field(default_factory=dict)
    citations: list[dict[str, Any]] = field(default_factory=list)
    retrieved_chunk_ids: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    error_summary: str | None = None
    unavailable: bool = False
    duration_ms: int | None = None

    def to_response_snapshot(self) -> dict[str, Any]:
        """Canonical persistence shape shared by runner, citation extractor, and API."""
        if not self.ok:
            return {
                "error": self.error_summary,
                "unavailable": self.unavailable,
                "output": self.output or {},
                "citations": [],
                "retrieved_chunk_ids": [],
                "metadata": dict(self.metadata or {}),
            }
        citations = list(self.citations or [])
        retrieved = list(self.retrieved_chunk_ids or [])
        output = dict(self.output or {})
        # Keep nested output for metrics; also flatten citation fields at top level.
        if not citations and isinstance(output.get("citations"), list):
            citations = [
                (c if isinstance(c, dict) else {"chunk_id": str(c)}) for c in output["citations"]
            ]
        if not retrieved and isinstance(output.get("retrieved_chunk_ids"), list):
            retrieved = [str(x) for x in output["retrieved_chunk_ids"]]
        return {
            "output": output,
            "citations": citations,
            "retrieved_chunk_ids": retrieved,
            "metadata": dict(self.metadata or {}),
            "error": None,
            "unavailable": False,
        }


@dataclass
class TargetCapability:
    target_type: str
    available: bool
    reason: str | None = None
    reason_code: str | None = None


class EvaluationTarget(Protocol):
    target_type: str

    def capability(self) -> TargetCapability: ...

    def run_case(
        self, target_input: TargetCaseInput, context: TargetExecutionContext
    ) -> TargetResult: ...


def run_target_safely(
    target: EvaluationTarget,
    target_input: TargetCaseInput,
    context: TargetExecutionContext,
) -> TargetResult:
    assert_no_private_reference(target_input, context)
    return target.run_case(target_input, context)
