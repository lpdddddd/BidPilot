"""Safe evaluation report serializers."""

from __future__ import annotations

import csv
import io
import json
import re
from typing import Any

_SECRET_RE = re.compile(
    r"(api[_-]?key\s*[:=]\s*\S+|authorization\s*[:=]\s*\S+|bearer\s+[A-Za-z0-9._\-]+|postgres(?:ql)?://\S+|mongodb(?:\+srv)?://\S+)",
    re.I,
)


def scrub(text: str) -> str:
    return _SECRET_RE.sub("[REDACTED]", text)


def serialize_json(report: dict[str, Any]) -> str:
    return scrub(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2, default=str))


def serialize_markdown(report: dict[str, Any]) -> str:
    model = report.get("model") or {}
    lines = [
        "# Evaluation Report",
        "",
        f"- run_id: `{report.get('run_id')}`",
        f"- status: {report.get('status')}",
        f"- dataset_hash: `{report.get('dataset_hash')}`",
        f"- evaluator_version: {report.get('evaluator_version')}",
        f"- overall_score: {report.get('overall_score')}",
        f"- pass_rate: {report.get('pass_rate')}",
        f"- error_rate: {report.get('error_rate')}",
        f"- reference_coverage: {report.get('reference_coverage')}",
        f"- model_id: `{model.get('model_id')}`",
        f"- model_display_name: {model.get('model_display_name')}",
        f"- model_type: {model.get('model_type')}",
        f"- adapter_version: {model.get('adapter_version')}",
        f"- served_model_name: `{model.get('served_model_name')}`",
        f"- git_commit: `{model.get('git_commit')}`",
        "",
        "## Task family scores",
    ]
    for k, v in (report.get("task_family_scores") or {}).items():
        lines.append(f"- {k}: {v}")
    lines.append("")
    lines.append("## Reference kind counts")
    for k, v in (report.get("reference_kind_counts") or {}).items():
        lines.append(f"- {k}: {v}")
    lines.append("")
    lines.append("## Cases")
    for c in report.get("cases") or []:
        lines.append(
            f"- `{c.get('case_key')}` [{c.get('task_family')}/{c.get('split')}] "
            f"status={c.get('status')} score={c.get('score')} gates={c.get('hard_gate_failures')}"
        )
    return scrub("\n".join(lines))


def serialize_csv(report: dict[str, Any]) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(
        buf,
        fieldnames=[
            "case_key",
            "task_family",
            "split",
            "status",
            "score",
            "passed",
            "reference_kind",
            "hard_gate_failures",
        ],
    )
    writer.writeheader()
    for c in report.get("cases") or []:
        writer.writerow(
            {
                "case_key": c.get("case_key"),
                "task_family": c.get("task_family"),
                "split": c.get("split"),
                "status": c.get("status"),
                "score": c.get("score"),
                "passed": c.get("passed"),
                "reference_kind": c.get("reference_kind"),
                "hard_gate_failures": ";".join(c.get("hard_gate_failures") or []),
            }
        )
    return scrub(buf.getvalue())


def build_report_dict(
    run, case_results, *, suite_stats: dict[str, Any] | None = None
) -> dict[str, Any]:
    cases = []
    for cr in case_results:
        cases.append(
            {
                "case_key": cr.case_key,
                "task_family": cr.task_family,
                "split": cr.split,
                "status": cr.status.value if hasattr(cr.status, "value") else str(cr.status),
                "score": cr.score,
                "passed": cr.passed,
                "reference_kind": cr.reference_kind.value
                if hasattr(cr.reference_kind, "value")
                else str(cr.reference_kind),
                "hard_gate_failures": list(cr.hard_gate_failures or []),
                "duration_ms": cr.duration_ms,
            }
        )
    summary = dict(run.summary_json or {})
    cfg = dict(run.target_config_snapshot or {})
    model_meta = {
        "model_id": cfg.get("model_id"),
        "model_display_name": cfg.get("model_display_name"),
        "model_type": cfg.get("model_type"),
        "adapter_version": cfg.get("adapter_version"),
        "dataset_version": cfg.get("dataset_version") or run.dataset_hash,
        "git_commit": run.source_commit_sha,
        "served_model_name": cfg.get("served_model_name"),
    }
    return {
        "run_id": str(run.id),
        "status": run.status.value if hasattr(run.status, "value") else str(run.status),
        "dataset_hash": run.dataset_hash,
        "evaluator_version": run.evaluator_version,
        "target_type": run.target_type.value
        if hasattr(run.target_type, "value")
        else str(run.target_type),
        "target_config_snapshot": run.target_config_snapshot,
        "model": model_meta,
        "seed": run.seed,
        "source_commit_sha": run.source_commit_sha,
        "overall_score": run.overall_score,
        "pass_rate": summary.get("pass_rate"),
        "error_rate": summary.get("error_rate"),
        "reference_coverage": summary.get("reference_coverage"),
        "task_family_scores": summary.get("task_family_scores") or {},
        "reference_kind_counts": (suite_stats or {}).get("reference_kind_counts")
        or summary.get("reference_kind_counts"),
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "duration_ms": run.duration_ms,
        "cases": cases,
    }
