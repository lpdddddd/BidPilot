"""Auto reference dataset builder (BidPilot Step 1).

Produces silver / auto_reference eval samples — never human_gold.
"""

from __future__ import annotations

from bidpilot_data.reference_dataset.build import build_reference_dataset
from bidpilot_data.reference_dataset.schema import GENERATOR_VERSION, ReferenceSample

__all__ = [
    "GENERATOR_VERSION",
    "ReferenceSample",
    "build_reference_dataset",
]
