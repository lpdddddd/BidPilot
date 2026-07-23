"""Evaluation batch runner — session-per-case, bounded cancel, isolated target I/O."""

from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.db.session import SessionLocal
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
from app.services.evaluation.types import (
    TargetExecutionContext,
    build_evaluator_view,
    split_case_for_evaluation,
    target_input_as_dict,
)


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


def _load_samples(run: EvaluationRun, samples: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    if samples is not None:
        return samples
    filt = dict(run.filter_json or {})
    fixture = filt.get("fixture_id")
    # Production never accepts arbitrary paths; only pre-registered fixture ids.
    if fixture == "mini_suite":
        from pathlib import Path

        from app.services.evaluation.suite_loader import load_jsonl

        path = (
            Path(__file__).resolve().parents[3]
            / "tests"
            / "fixtures"
            / "evaluation"
            / "mini_suite.jsonl"
        )
        return load_jsonl(path)
    # Internal test-only: filter_json.fixture_path set by service when ALLOW_FAKE
    legacy = filt.get("fixture_path")
    if legacy and __import__("os").getenv("EVALUATION_ALLOW_FAKE", "").lower() in {
        "1",
        "true",
        "yes",
    }:
        from pathlib import Path

        from app.services.evaluation.suite_loader import load_jsonl

        return load_jsonl(Path(legacy))
    bundle = load_reference_suite()
    return bundle.samples


def _execute_one_case(
    *,
    run_id: UUID,
    project_id: UUID,
    target_type: str,
    target_config: dict[str, Any],
    seed: int,
    case: EvaluationCase,
) -> dict[str, Any]:
    """Run a single case in an isolated Session + target instance."""
    session = SessionLocal()
    try:
        run = session.get(EvaluationRun, run_id)
        if run is None or run.cancel_requested or run.status == EvaluationRunStatus.cancelled:
            return {"case": case, "skipped": True, "reason": "cancelled"}
        target = get_target(target_type, config=target_config, db=session)
        target_input, private = split_case_for_evaluation(case)
        context = TargetExecutionContext(
            project_id=project_id,
            target_config=dict(target_config or {}),
            seed=seed,
            evaluation_run_id=run_id,
        )
        result = run_target_safely(target, target_input, context)
        return {
            "case": case,
            "result": result,
            "private": private,
            "target_input": target_input,
            "skipped": False,
        }
    finally:
        session.close()


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

    bundle_samples = _load_samples(run, samples)
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

    target_type = (
        run.target_type.value if hasattr(run.target_type, "value") else str(run.target_type)
    )
    target_config = dict(run.target_config_snapshot or {})
    # Capability gate (no shared session leak into workers).
    probe = get_target(target_type, config=target_config, db=db)
    cap = probe.capability()
    if not cap.available:
        run.status = EvaluationRunStatus.failed
        run.safe_error_summary = cap.reason or "target unavailable"
        run.finished_at = _now()
        run.execution_claim_token = None
        db.commit()
        return run

    workers = max(1, min(int(max_workers or 1), 4))
    results: list[dict[str, Any]] = []
    queue = list(pending)

    if workers == 1 or len(queue) <= 1:
        while queue:
            db.refresh(run)
            if run.cancel_requested:
                break
            case = queue.pop(0)
            results.append(
                _execute_one_case(
                    run_id=run.id,
                    project_id=run.project_id,
                    target_type=target_type,
                    target_config=target_config,
                    seed=int(run.seed or 42),
                    case=case,
                )
            )
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            in_flight: dict[Any, str] = {}

            def _submit_next() -> None:
                nonlocal queue
                db.refresh(run)
                if run.cancel_requested or not queue:
                    return
                if len(in_flight) >= workers:
                    return
                case = queue.pop(0)
                fut = pool.submit(
                    _execute_one_case,
                    run_id=run.id,
                    project_id=run.project_id,
                    target_type=target_type,
                    target_config=target_config,
                    seed=int(run.seed or 42),
                    case=case,
                )
                in_flight[fut] = case.case_key

            while queue and len(in_flight) < workers:
                _submit_next()

            while in_flight:
                done, _ = wait(set(in_flight.keys()), return_when=FIRST_COMPLETED)
                for fut in done:
                    in_flight.pop(fut, None)
                    item = fut.result()
                    if not item.get("skipped"):
                        results.append(item)
                db.refresh(run)
                if run.cancel_requested:
                    # Do not submit more; allow in-flight to finish.
                    queue.clear()
                    continue
                while queue and len(in_flight) < workers:
                    _submit_next()

    for item in results:
        if item.get("skipped"):
            continue
        eval_case: EvaluationCase = item["case"]
        tres = item["result"]
        private = item["private"]
        target_input = item["target_input"]
        started = _now()
        # Evaluator sees PrivateReferenceBundle via EvaluatorCaseView — never full case to metrics.
        evaluator_view = build_evaluator_view(
            case_key=eval_case.case_key,
            task_family=eval_case.task_family,
            split=eval_case.split,
            content_hash=eval_case.content_hash,
            private=private,
        )
        profile = get_profile(evaluator_view.task_family)
        if not tres.ok:
            status = EvaluationCaseStatus.error
            metrics = []
            score = None
            passed = False
            gates: list[str] = []
            snap = tres.to_response_snapshot()
        else:
            metrics = evaluate_case_metrics(
                evaluator_view, tres.output, profile=profile, duration_ms=tres.duration_ms
            )
            gates = evaluate_hard_gates(evaluator_view, tres.output, metrics)
            score = aggregate_case_score(metrics)
            passed = score is not None and score >= 0.5 and not gates
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
            snap = tres.to_response_snapshot()
        finished = _now()
        input_snap = target_input_as_dict(target_input)
        row = EvaluationCaseResult(
            evaluation_run_id=run.id,
            case_key=evaluator_view.case_key,
            case_content_hash=evaluator_view.content_hash,
            task_family=evaluator_view.task_family,
            split=evaluator_view.split,
            status=status,
            response_snapshot=snap,
            reference_kind=_kind(private.reference_kind),
            score=score,
            passed=passed if status != EvaluationCaseStatus.error else False,
            hard_gate_failures=gates,
            safe_error_summary=tres.error_summary if not tres.ok else None,
            started_at=started,
            finished_at=finished,
            duration_ms=tres.duration_ms
            or max(0, int((finished - started).total_seconds() * 1000)),
            input_snapshot=input_snap,
            reference_summary=evaluator_view.reference_summary(include_output=False),
        )
        db.add(row)
        db.flush()
        _persist_metrics(db, row, metrics)
        db.commit()

    db.refresh(run)
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
    elif run.completed_cases < run.total_cases and summary["error_cases"]:
        run.status = EvaluationRunStatus.partial
    elif summary["error_cases"] and summary["passed_cases"] == 0 and summary["failed_cases"] == 0:
        run.status = EvaluationRunStatus.failed
    elif summary["error_cases"] or run.completed_cases < run.total_cases:
        run.status = EvaluationRunStatus.partial
    else:
        run.status = EvaluationRunStatus.completed
    run.execution_claim_token = None
    db.commit()
    db.refresh(run)
    return run
