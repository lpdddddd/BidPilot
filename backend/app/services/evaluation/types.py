"""Structural isolation: target input vs private reference vs execution context."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from typing import Any
from uuid import UUID

# Strict whitelist of task_input keys a target may see (never gold/reference ids).
PUBLIC_TASK_INPUT_KEYS = frozenset(
    {
        "question",
        "question_type",
        "query",
        "user_input",
        "text",
        "instruction",
        "category_hint",
        "category",
        "clause",
        "title",
        "requirement",
        "company_material",
        "supplier_id",
        "supplier_name",
        "rule_id",
        "rule_type",
        "check_id",
    }
)

# Keys that must never appear on target-facing objects (any nesting).
FORBIDDEN_TARGET_ATTRS = frozenset(
    {
        "reference_output",
        "expected_output",
        "expected",
        "expected_verdict",
        "gold_answer",
        "gold",
        "human_gold",
        "rule_verdict",
        "verdict_expected",
        "verdict",
        "citation_metadata",
        "citation_chunk_ids",
        "citation_page",
        "citation_document_ids",
        "citations",
        "context_chunk_ids",
        "source_document_id",
        "source_chunk_id",
        "document_id",
        "chunk_id",
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
        "retrieved_chunk_ids",
        "evidence_chunk_ids",
        "page_numbers",
        "document_ids",
    }
)


@dataclass(frozen=True)
class TargetCaseInput:
    """Public payload for generation targets — whitelist-only, no private reference."""

    case_key: str
    task_family: str
    split: str
    task_input: dict[str, Any]
    public_metadata: dict[str, Any] = field(default_factory=dict)


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
    # Gold / provenance identifiers that must not reach targets.
    context_chunk_ids: list[str] = field(default_factory=list)
    source_project_id: str | None = None
    source_document_id: str | None = None


@dataclass(frozen=True)
class EvaluatorCaseView:
    """Identity + private reference for metrics/hard-gates (never for targets)."""

    case_key: str
    task_family: str
    split: str
    content_hash: str
    private: PrivateReferenceBundle

    @property
    def reference_kind(self) -> str:
        return self.private.reference_kind

    @property
    def label_source(self) -> str:
        return self.private.label_source

    @property
    def reference_output(self) -> dict[str, Any] | None:
        return self.private.reference_output

    @property
    def evidence(self) -> list[Any]:
        return self.private.evidence

    @property
    def citation_metadata(self) -> dict[str, Any] | None:
        return self.private.citation_metadata

    @property
    def expected_verdict(self) -> Any:
        return self.private.expected_verdict

    def reference_summary(self, *, include_output: bool = False) -> dict[str, Any]:
        summary: dict[str, Any] = {
            "reference_kind": self.reference_kind,
            "label_source": self.label_source,
            "has_reference_output": self.reference_output is not None,
            "has_reference": self.reference_output is not None,
            "source_description": (
                "auto_reference from Step 1 builder — not human_gold"
                if self.reference_kind == "auto_reference"
                else f"reference_kind={self.reference_kind}"
            ),
        }
        if include_output and self.split != "test" and self.reference_output is not None:
            summary["reference_output"] = deepcopy(self.reference_output)
        return summary


def whitelist_task_input(raw: dict[str, Any] | None) -> dict[str, Any]:
    """Construct public task_input by strict key whitelist (not blacklist stripping)."""
    src = raw or {}
    out: dict[str, Any] = {}
    for key, value in src.items():
        key_s = str(key)
        if key_s not in PUBLIC_TASK_INPUT_KEYS:
            continue
        if key_s.lower() in FORBIDDEN_TARGET_ATTRS:
            continue
        out[key_s] = deepcopy(value)
    return out


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
        cls_name = type(obj).__name__
        if cls_name in {"PrivateReferenceBundle", "EvaluatorCaseView"}:
            return None
        for f in fields(obj):
            name = f.name
            if name.lower() in FORBIDDEN_TARGET_ATTRS or any(
                tok in name.lower()
                for tok in (
                    "reference_output",
                    "expected_output",
                    "expected_verdict",
                    "gold_answer",
                    "human_gold",
                    "rule_verdict",
                    "citation_metadata",
                    "citation_chunk",
                    "context_chunk",
                    "label_source",
                    "has_evidence",
                    "source_document",
                    "source_chunk",
                )
            ):
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
                    "expected_verdict",
                    "gold_answer",
                    "human_gold",
                    "rule_verdict",
                    "citation_metadata",
                    "citation_chunk",
                    "context_chunk",
                    "label_source",
                    "has_evidence",
                    "source_document",
                    "source_chunk",
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
        name = type(obj).__name__
        if name in {"PrivateReferenceBundle", "EvaluatorCaseView"}:
            raise ValueError(f"{name} must not be passed to target")
        found = _walk_forbidden(obj)
        if found:
            raise ValueError(f"private reference field leaked to target at '{found}'")
        if isinstance(obj, TargetCaseInput):
            for key in obj.task_input:
                if key not in PUBLIC_TASK_INPUT_KEYS:
                    raise ValueError(f"non-whitelisted task_input key leaked to target: {key}")
            for key in obj.public_metadata:
                if str(key).lower() in FORBIDDEN_TARGET_ATTRS:
                    raise ValueError(f"forbidden public_metadata key: {key}")


def split_case_for_evaluation(case: Any) -> tuple[TargetCaseInput, PrivateReferenceBundle]:
    """Build whitelist TargetCaseInput and PrivateReferenceBundle from a case."""
    raw_input = getattr(case, "input_data", None) or {}
    task_input = whitelist_task_input(raw_input if isinstance(raw_input, dict) else {})
    target_input = TargetCaseInput(
        case_key=str(case.case_key),
        task_family=str(case.task_family),
        split=str(case.split),
        task_input=task_input,
        public_metadata={},
    )
    ref_out = getattr(case, "reference_output", None)
    expected_verdict = None
    if isinstance(ref_out, dict):
        expected_verdict = ref_out.get("verdict") or ref_out.get("expected_verdict")
    context_chunk_ids = [
        str(x) for x in (raw_input.get("context_chunk_ids") or []) if x is not None
    ]
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
        context_chunk_ids=context_chunk_ids,
        source_project_id=getattr(case, "project_id", None),
        source_document_id=getattr(case, "document_id", None),
    )
    assert_no_private_reference(target_input)
    return target_input, private


def build_evaluator_view(
    *,
    case_key: str,
    task_family: str,
    split: str,
    content_hash: str,
    private: PrivateReferenceBundle,
) -> EvaluatorCaseView:
    return EvaluatorCaseView(
        case_key=case_key,
        task_family=task_family,
        split=split,
        content_hash=content_hash,
        private=private,
    )


def target_input_as_dict(target_input: TargetCaseInput) -> dict[str, Any]:
    """Safe dict for persistence / logging — whitelist fields only."""
    return {
        "case_key": target_input.case_key,
        "task_family": target_input.task_family,
        "split": target_input.split,
        "input": dict(target_input.task_input),
        "public_metadata": dict(target_input.public_metadata or {}),
    }


def dataclass_public_repr(obj: Any) -> dict[str, Any]:
    if is_dataclass(obj) and not isinstance(obj, type):
        return asdict(obj)
    if isinstance(obj, dict):
        return dict(obj)
    return {"repr": repr(obj)}
