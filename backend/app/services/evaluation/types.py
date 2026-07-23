"""Structural isolation: target input vs private reference vs execution context."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field, fields, is_dataclass
from typing import Any
from uuid import UUID

FORBIDDEN_TARGET_ATTRS = frozenset(
    {
        "reference_output",
        "expected_output",
        "expected",
        "gold_answer",
        "gold",
        "human_gold",
        "rule_verdict",
        "verdict_expected",
        "citation_metadata",
        "citation_chunk_ids",
        "citation_page",
        "citation_document_ids",
        "has_evidence",
        "scorer",
        "scorer_fields",
        "reference_kind",
        "label_source",
        "reference",
        "references",
        "evidence",
        "private_reference",
        "citation_targets",
    }
)


@dataclass(frozen=True)
class TargetCaseInput:
    """Public payload for generation targets — no private reference fields."""

    case_key: str
    task_family: str
    split: str
    task_input: dict[str, Any]
    # Provenance only — never used for authorization.
    source_project_id: str | None = None
    source_document_id: str | None = None


@dataclass
class TargetExecutionContext:
    """Trusted execution scope injected by the server for an authorized run."""

    project_id: UUID
    target_config: dict[str, Any] = field(default_factory=dict)
    seed: int = 42
    evaluation_run_id: UUID | None = None


@dataclass(frozen=True)
class PrivateReferenceBundle:
    """Evaluator-only reference — never passed to targets."""

    reference_kind: str
    label_source: str
    reference_output: dict[str, Any] | None
    evidence: list[Any] = field(default_factory=list)
    citation_metadata: dict[str, Any] | None = None
    expected_verdict: Any | None = None
    scorer_fields: dict[str, Any] = field(default_factory=dict)


def _walk_forbidden(obj: Any, *, path: str = "", seen: set[int] | None = None) -> str | None:
    if seen is None:
        seen = set()
    oid = id(obj)
    if oid in seen:
        return None
    if isinstance(obj, (str, bytes, int, float, bool, type(None), UUID)):
        return None
    seen.add(oid)

    if is_dataclass(obj) and not isinstance(obj, type):
        for f in fields(obj):
            name = f.name
            if name.lower() in FORBIDDEN_TARGET_ATTRS or any(
                tok in name.lower()
                for tok in (
                    "reference_output",
                    "expected_output",
                    "gold_answer",
                    "human_gold",
                    "rule_verdict",
                    "citation_metadata",
                    "citation_chunk",
                    "label_source",
                    "has_evidence",
                )
            ):
                # PrivateReferenceBundle itself is allowed to *own* these fields;
                # only fail when they appear on target-facing objects.
                cls_name = type(obj).__name__
                if cls_name != "PrivateReferenceBundle":
                    return f"{path}.{name}" if path else name
            found = _walk_forbidden(
                getattr(obj, name),
                path=f"{path}.{name}" if path else name,
                seen=seen,
            )
            if found:
                return found
        return None

    if isinstance(obj, dict):
        for key, value in obj.items():
            key_l = str(key).lower()
            if key_l in FORBIDDEN_TARGET_ATTRS or any(
                tok in key_l
                for tok in (
                    "reference_output",
                    "expected_output",
                    "gold_answer",
                    "human_gold",
                    "rule_verdict",
                    "citation_metadata",
                    "citation_chunk",
                    "label_source",
                    "has_evidence",
                )
            ):
                return f"{path}.{key}" if path else str(key)
            found = _walk_forbidden(value, path=f"{path}.{key}" if path else str(key), seen=seen)
            if found:
                return found
    elif isinstance(obj, (list, tuple, set)):
        for i, item in enumerate(obj):
            found = _walk_forbidden(item, path=f"{path}[{i}]", seen=seen)
            if found:
                return found
    elif hasattr(obj, "__dict__"):
        for key, value in vars(obj).items():
            if key.startswith("_"):
                continue
            key_l = str(key).lower()
            if key_l in FORBIDDEN_TARGET_ATTRS:
                return f"{path}.{key}" if path else key
            found = _walk_forbidden(value, path=f"{path}.{key}" if path else key, seen=seen)
            if found:
                return found
    return None


def assert_no_private_reference(*objects: Any) -> None:
    """Raise if private reference content appears in target-facing objects."""
    for obj in objects:
        # PrivateReferenceBundle is evaluator-only — never assert against it here.
        if type(obj).__name__ == "PrivateReferenceBundle":
            raise ValueError("PrivateReferenceBundle must not be passed to target")
        found = _walk_forbidden(obj)
        if found:
            raise ValueError(f"private reference field leaked to target at '{found}'")


def split_case_for_evaluation(case: Any) -> tuple[TargetCaseInput, PrivateReferenceBundle]:
    """Build public target input and private reference from a normalized case."""
    task_input = deepcopy(getattr(case, "input_data", None) or {})
    # Strip any leaked private keys from task_input defensively.
    for key in list(task_input.keys()):
        if str(key).lower() in FORBIDDEN_TARGET_ATTRS:
            task_input.pop(key, None)
    target_input = TargetCaseInput(
        case_key=str(case.case_key),
        task_family=str(case.task_family),
        split=str(case.split),
        task_input=task_input,
        source_project_id=getattr(case, "project_id", None),
        source_document_id=getattr(case, "document_id", None),
    )
    ref_out = getattr(case, "reference_output", None)
    expected_verdict = None
    if isinstance(ref_out, dict):
        expected_verdict = ref_out.get("verdict") or ref_out.get("expected_verdict")
    private = PrivateReferenceBundle(
        reference_kind=str(getattr(case, "reference_kind", "no_direct_reference")),
        label_source=str(getattr(case, "label_source", "")),
        reference_output=deepcopy(ref_out) if isinstance(ref_out, dict) else None,
        evidence=list(getattr(case, "evidence", None) or []),
        citation_metadata=deepcopy(getattr(case, "citation_metadata", None))
        if isinstance(getattr(case, "citation_metadata", None), dict)
        else None,
        expected_verdict=expected_verdict,
        scorer_fields={},
    )
    assert_no_private_reference(target_input)
    return target_input, private
