"""Shared draft helpers for coverage / draft_safety / consistency rules."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from app.schemas.compliance import ComplianceContext


def current_draft_versions(ctx: ComplianceContext) -> list[Any]:
    versions = []
    for draft in ctx.drafts:
        current_id = getattr(draft, "current_version_id", None)
        for ver in ctx.draft_versions:
            if ver.draft_id != draft.id:
                continue
            if current_id and ver.id == current_id:
                versions.append(ver)
                break
            if not current_id and getattr(ver, "is_current", False):
                versions.append(ver)
                break
    return versions


def iter_block_texts(content: dict[str, Any]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for section in content.get("sections") or []:
        if not isinstance(section, dict):
            continue
        for block in section.get("blocks") or []:
            if not isinstance(block, dict):
                continue
            kind = str(block.get("block_kind") or "")
            text = str(block.get("content") or "")
            out.append((kind, text))
    return out


def draft_blob(content: dict[str, Any], markdown: str | None = None) -> str:
    texts = [t for _, t in iter_block_texts(content)]
    if markdown:
        texts.append(markdown)
    return "\n".join(texts)


def draft_covered_requirement_ids(ctx: ComplianceContext, versions: list[Any]) -> set[UUID]:
    """Requirement IDs referenced by current draft sources / blocks / matrix."""
    covered: set[UUID] = set()
    version_ids = {v.id for v in versions}
    for src in ctx.draft_sources:
        if src.draft_version_id in version_ids and src.requirement_id:
            covered.add(src.requirement_id)
    for ver in versions:
        content = ver.content_json if isinstance(ver.content_json, dict) else {}
        for section in content.get("sections") or []:
            if not isinstance(section, dict):
                continue
            for block in section.get("blocks") or []:
                if not isinstance(block, dict):
                    continue
                for raw in block.get("requirement_ids") or []:
                    try:
                        covered.add(UUID(str(raw)))
                    except (TypeError, ValueError):
                        continue
        for row in content.get("compliance_matrix") or []:
            if not isinstance(row, dict):
                continue
            raw = row.get("requirement_id")
            if raw is None:
                continue
            try:
                covered.add(UUID(str(raw)))
            except (TypeError, ValueError):
                continue
    return covered


def parse_uuid_safe(value: Any) -> UUID | None:
    if value is None:
        return None
    try:
        return UUID(str(value))
    except (TypeError, ValueError):
        return None
