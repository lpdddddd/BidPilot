"""Filter and normalize evaluation cases; strip references from target inputs."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from app.services.evaluation.suite_loader import map_label_source_to_reference_kind


@dataclass
class EvaluationCase:
    """Normalized evaluation case for runner / metrics."""

    case_key: str
    content_hash: str
    task_family: str
    split: str
    label_source: str
    reference_kind: str
    input_data: dict[str, Any]
    # Held only for evaluator metrics — never passed to targets.
    reference_output: dict[str, Any] | None
    evidence: list[Any] = field(default_factory=list)
    citation_metadata: dict[str, Any] | None = None
    sample_id: str | None = None
    project_id: str | None = None
    document_id: str | None = None
    raw_meta: dict[str, Any] = field(default_factory=dict)

    def target_input(self) -> dict[str, Any]:
        """Input payload for generation targets (reference stripped)."""
        return {
            "case_key": self.case_key,
            "task_family": self.task_family,
            "split": self.split,
            "input": deepcopy(self.input_data),
            # Document / chunk ids may appear in input; still no reference_output.
            "context_hints": {
                "project_id": self.project_id,
                "document_id": self.document_id,
                "has_evidence": bool(self.evidence),
                "citation_chunk_ids": list((self.citation_metadata or {}).get("chunk_ids") or []),
            },
        }

    def reference_summary(self, *, include_output: bool = False) -> dict[str, Any]:
        """Safe summary for API / persistence. Test split never includes full output."""
        summary: dict[str, Any] = {
            "reference_kind": self.reference_kind,
            "label_source": self.label_source,
            "has_reference_output": self.reference_output is not None,
            "source_description": (
                "auto_reference from Step 1 builder — not human_gold"
                if self.reference_kind == "auto_reference"
                else f"reference_kind={self.reference_kind}"
            ),
        }
        if include_output and self.split != "test" and self.reference_output is not None:
            summary["reference_output"] = deepcopy(self.reference_output)
        return summary


def stable_case_key(sample: dict[str, Any]) -> str:
    """Stable key preferring sample_id; falls back to content digest."""
    sid = sample.get("sample_id")
    if sid:
        return str(sid)
    payload = {
        "task_type": sample.get("task_type"),
        "split": sample.get("split"),
        "input": sample.get("input"),
        "project_id": sample.get("project_id"),
        "document_id": sample.get("document_id"),
    }
    digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return f"case_{digest[:24]}"


def case_content_hash(sample: dict[str, Any]) -> str:
    """Hash of input + task + split + label_source (excludes timestamps / reference)."""
    payload = {
        "task_type": sample.get("task_type") or sample.get("task_family"),
        "split": sample.get("split"),
        "label_source": sample.get("label_source"),
        "input": sample.get("input"),
        "project_id": sample.get("project_id"),
        "document_id": sample.get("document_id"),
        "sample_id": sample.get("sample_id"),
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def normalize_case(sample: dict[str, Any]) -> EvaluationCase:
    task = str(sample.get("task_type") or sample.get("task_family") or "unknown")
    label = str(sample.get("label_source") or "")
    kind = map_label_source_to_reference_kind(label)
    # Hard rule: never call auto_reference human_gold.
    if kind == "human_gold" and label != "human_gold":
        kind = "auto_reference"
    ref = sample.get("reference_output")
    if ref is None:
        kind = "no_direct_reference"
    return EvaluationCase(
        case_key=stable_case_key(sample),
        content_hash=case_content_hash(sample),
        task_family=task,
        split=str(sample.get("split") or "unknown"),
        label_source=label or "missing",
        reference_kind=kind,
        input_data=deepcopy(sample.get("input") or {}),
        reference_output=deepcopy(ref) if isinstance(ref, dict) else None,
        evidence=list(sample.get("evidence") or []),
        citation_metadata=(
            deepcopy(sample["citation_metadata"])
            if isinstance(sample.get("citation_metadata"), dict)
            else None
        ),
        sample_id=str(sample["sample_id"]) if sample.get("sample_id") else None,
        project_id=str(sample["project_id"]) if sample.get("project_id") else None,
        document_id=str(sample["document_id"]) if sample.get("document_id") else None,
        raw_meta={
            "generator_version": sample.get("generator_version"),
            "confidence": sample.get("confidence"),
            "data_provenance": sample.get("data_provenance"),
        },
    )


def filter_cases(
    samples: list[dict[str, Any]],
    *,
    split: str | None = None,
    splits: list[str] | None = None,
    task_family: str | None = None,
    task_families: list[str] | None = None,
    limit: int | None = None,
    case_keys: list[str] | None = None,
) -> list[EvaluationCase]:
    """Filter samples and normalize; order is stable by case_key."""
    allowed_splits = set(splits or ([] if split is None else [split]))
    allowed_tasks = set(task_families or ([] if task_family is None else [task_family]))
    allowed_keys = set(case_keys) if case_keys else None
    cases: list[EvaluationCase] = []
    for sample in samples:
        case = normalize_case(sample)
        if allowed_splits and case.split not in allowed_splits:
            continue
        if allowed_tasks and case.task_family not in allowed_tasks:
            continue
        if allowed_keys is not None and case.case_key not in allowed_keys:
            continue
        cases.append(case)
    cases.sort(key=lambda c: c.case_key)
    if limit is not None and limit >= 0:
        cases = cases[:limit]
    return cases


def assert_no_reference_in_target_input(payload: dict[str, Any]) -> None:
    """Raise if reference fields leaked into a target payload."""
    forbidden = (
        "reference_output",
        "expected_output",
        "gold_answer",
        "human_gold",
        "rule_verdict",
    )
    blob = json.dumps(payload, ensure_ascii=False)
    for key in forbidden:
        if key in payload:
            raise ValueError(f"reference field '{key}' must not be passed to target")
        # nested
        if f'"{key}"' in blob:
            raise ValueError(f"reference field '{key}' must not appear in target input JSON")
