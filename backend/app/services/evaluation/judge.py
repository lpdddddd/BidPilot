"""Optional AI Judge — unavailable by default; never returns fake scores."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class JudgeCapability:
    available: bool
    reason: str | None


@dataclass
class JudgeResult:
    ok: bool
    score: float | None = None
    rationale: str | None = None
    error_summary: str | None = None


class AiJudge:
    """Opt-in judge. Default implementation is unavailable (no fake scores)."""

    def capability(self) -> JudgeCapability:
        return JudgeCapability(
            available=False,
            reason="AI Judge is opt-in and requires a configured structured-output provider",
        )

    def judge(self, *, case_input: dict[str, Any], prediction: dict[str, Any]) -> JudgeResult:
        cap = self.capability()
        if not cap.available:
            return JudgeResult(ok=False, error_summary=cap.reason)
        return JudgeResult(ok=False, error_summary="judge not configured")
