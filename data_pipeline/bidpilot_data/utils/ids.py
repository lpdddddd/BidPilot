from __future__ import annotations

from uuid import NAMESPACE_URL, UUID, uuid5


def stable_uuid(key: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"bidpilot-data:{key}")


def stable_id(key: str) -> str:
    return str(stable_uuid(key))
