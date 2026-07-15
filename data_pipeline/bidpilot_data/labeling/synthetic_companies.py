"""Synthetic company generation has been permanently disabled.

BidPilot official datasets must only use supplier facts disclosed in real
public tender / award / contract filings. This module remains as a hard guard
so accidental imports fail loudly.
"""

from __future__ import annotations

from typing import Any


class SyntheticDataForbiddenError(RuntimeError):
    pass


def build_synthetic_companies_and_matches(*, dry_run: bool = False) -> dict[str, Any]:
    raise SyntheticDataForbiddenError(
        "synthetic company/material/match generation is forbidden; "
        "use disclosed suppliers from official public filings only"
    )
