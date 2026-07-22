"""Evaluation batch runner."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.enums import EvaluationCaseStatus, EvaluationReferenceKind, EvaluationRunStatus
from app.models.evaluation import EvaluationCaseResult, EvaluationMetricResult, EvaluationRun
from app.services.evaluation import EVALUATOR_VERSION
from app.services.evaluation.aggregator import aggregate_case_score, aggregate_run
from app.services.evaluation.case_loader import EvaluationCase, filter_cases
from app.services.evaluation.metrics import evaluate_case_metrics
from app.services.evaluation.profiles import evaluate_hard_gates, get_profile
from app.services.evaluation.suite_loader import load_reference_suite
from app.services.evaluation.targets import get_target
from app.services.evaluation.targets.base import run_target_safely


def _now():
    return datetime.now(UTC)


def _kind(value: str) -> EvaluationReferenceKind:
    try:
        return EvaluationReferenceKind(value)
    except ValueError:
        return EvaluationReferenceKind.auto_reference


def _persist_metrics(db: Session, case_result: EvaluationCaseResult, metrics) -> None:
    for m in metrics:
        db.add(
            EvaluationMetricResult(
                case_result_id=case_result.id,
                metric_name=m.name,
                metric_version=m.version,
                value=m.value,
                applicable=m.applicable,
                weight=m.weight,
                threshold=m.threshold,
                passed=m.passed,
                evidence_summary=m.evidence_summary,
                reference_kind=_kind(m.reference_kind),
            )
        )


def execute_evaluation_run(
    db: Session,
    run_id: UUID,
    *,
    samples: list[dict[str, Any]] | None = None,
    max_workers: int = 4,
) -> EvaluationRun:
    run = db.get(EvaluationRun, run_id)
    assert run is not None
    if run.cancel_requested:
        run.status = EvaluationRunStatus.cancelled
        run.finished_at = _now()
        db.commit()
        return run

    bundle_samples = samples
    if bundle_samples is None:
        # Prefer fixture path from filter_json
        filt = dict(run.filter_json or {})
        fixture = filt.get("fixture_path")
        if fixture:
            from pathlib import Path

            from app.services.evaluation.suite_loader import load_jsonl

            bundle_samples = load_jsonl(Path(fixture))
        else:
            bundle = load_reference_suite()
            bundle_samples = bundle.samples

    filt = dict(run.filter_json or {})
    cases = filter_cases(
        bundle_samples,
        split=filt.get("split"),
        splits=filt.get("splits"),
        task_family=filt.get("task_family"),
        task_families=filt.get("task_families"),
        limit=filt.get("limit"),
        case_keys=filt.get("case_keys"),
    )

    # Skip already completed case keys (resume)
    done_keys = {
        r.case_key
        for r in run.case_results
        if r.status
        in {
            EvaluationCaseStatus.passed,
            EvaluationCaseStatus.failed,
            EvaluationCaseStatus.error,
            EvaluationCaseStatus.skipped,
        }
    }
    pending = [c for c in cases if c.case_key not in done_keys]
    run.total_cases = len(cases)
    run.started_at = run.started_at or _now()
    run.status = EvaluationRunStatus.running
    run.evaluator_version = run.evaluator_version or EVALUATOR_VERSION
    db.commit()

    target = get_target(
        run.target_type.value if hasattr(run.target_type, "value") else str(run.target_type),
        config=dict(run.target_config_snapshot or {}),
        db=db,
    )
    cap = target.capability()
    if not cap.available:
        run.status = EvaluationRunStatus.failed
        run.safe_error_summary = cap.reason or "target unavailable"
        run.finished_at = _now()
        db.commit()
        return run

    def _one(case: EvaluationCase) -> dict[str, Any]:
        result = run_target_safely(target, case)
        return {"case": case, "result": result}

    # Controlled concurrency for target execution; DB writes stay on main session.
    results: list[dict[str, Any]] = []
    if max_workers <= 1 or len(pending) <= 1:
        for pending_case in pending:
            db.refresh(run)
            if run.cancel_requested:
                break
            results.append(_one(pending_case))
    else:
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futs = {pool.submit(_one, c): c.case_key for c in pending}
            for fut in as_completed(futs):
                db.refresh(run)
                if run.cancel_requested:
                    break
                results.append(fut.result())

    for item in results:
        case = item["case"]
        tres = item["result"]
        started = _now()
        profile = get_profile(case.task_family)
        if not tres.ok:
            status = EvaluationCaseStatus.error
            metrics = []
            score = None
            passed = False
            gates: list[str] = []
            snap = {"error": tres.error_summary, "unavailable": tres.unavailable}
        else:
            metrics = evaluate_case_metrics(
                case, tres.output, profile=profile, duration_ms=tres.duration_ms
            )
            gates = evaluate_hard_gates(case, tres.output, metrics)
            score = aggregate_case_score(metrics)
            passed = score is not None and score >= 0.5 and not gates
            # metric hard fails
            if any(
                m.passed is False
                and m.name
                in {
                    "critical_false_negative_count",
                    "forbidden_fabrication_count",
                    "sensitive_data_leakage_count",
                }
                for m in metrics
            ):
                passed = False
            if gates:
                passed = False
            status = EvaluationCaseStatus.passed if passed else EvaluationCaseStatus.failed
            snap = {"output": tres.output}
        finished = _now()
        row = EvaluationCaseResult(
            evaluation_run_id=run.id,
            case_key=case.case_key,
            case_content_hash=case.content_hash,
            task_family=case.task_family,
            split=case.split,
            status=status,
            response_snapshot=snap,
            reference_kind=_kind(case.reference_kind),
            score=score,
            passed=passed if status != EvaluationCaseStatus.error else False,
            hard_gate_failures=gates,
            safe_error_summary=tres.error_summary if not tres.ok else None,
            started_at=started,
            finished_at=finished,
            duration_ms=tres.duration_ms
            or max(0, int((finished - started).total_seconds() * 1000)),
            input_snapshot=case.target_input(),
            reference_summary=case.reference_summary(include_output=False),
        )
        db.add(row)
        db.flush()
        _persist_metrics(db, row, metrics)
        db.commit()

    db.refresh(run)
    # Aggregate
    rows = list(run.case_results)
    agg_input = [
        {
            "score": r.score,
            "passed": r.passed,
            "status": r.status.value,
            "task_family": r.task_family,
            "reference_kind": r.reference_kind.value,
            "hard_gate_failures": r.hard_gate_failures or [],
        }
        for r in rows
    ]
    summary = aggregate_run(agg_input)
    run.completed_cases = len(rows)
    run.passed_cases = summary["passed_cases"]
    run.failed_cases = summary["failed_cases"]
    run.error_cases = summary["error_cases"]
    run.overall_score = summary["overall_score"]
    run.summary_json = summary
    run.finished_at = _now()
    if run.started_at:
        run.duration_ms = int((run.finished_at - run.started_at).total_seconds() * 1000)
    if run.cancel_requested:
        run.status = EvaluationRunStatus.cancelled
    elif (
        summary["error_cases"]
        and summary["error_cases"] + summary["failed_cases"] + summary["passed_cases"]
        < run.total_cases
    ):
        run.status = EvaluationRunStatus.partial
    elif summary["error_cases"] and summary["passed_cases"] == 0 and summary["failed_cases"] == 0:
        run.status = EvaluationRunStatus.failed
    elif summary["error_cases"]:
        run.status = EvaluationRunStatus.partial
    else:
        run.status = EvaluationRunStatus.completed
    run.execution_claim_token = None
    db.commit()
    db.refresh(run)
    return run
