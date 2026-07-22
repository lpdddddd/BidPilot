"""Project-scoped evaluation service."""

from __future__ import annotations

import os
import subprocess
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.enums import EvaluationRunStatus, EvaluationTargetType
from app.models.evaluation import EvaluationCaseResult, EvaluationRun, EvaluationSuite
from app.models.project import BidProject
from app.services.evaluation import BUILTIN_SUITE_NAME, BUILTIN_SUITE_VERSION, EVALUATOR_VERSION
from app.services.evaluation.claims import (
    EvalClaimOutcome,
    claim_evaluation_run,
    release_evaluation_claim,
)
from app.services.evaluation.profiles import EVALUATOR_PROFILE_VERSION, all_profiles
from app.services.evaluation.report import (
    build_report_dict,
    serialize_csv,
    serialize_json,
    serialize_markdown,
)
from app.services.evaluation.runner import execute_evaluation_run
from app.services.evaluation.suite_loader import build_manifest_snapshot, load_reference_suite
from app.services.evaluation.targets import list_capabilities


def _now():
    return datetime.now(UTC)


def _git_sha() -> str | None:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd="/root/autodl-tmp/bidpilot",
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        return out or None
    except Exception:
        return os.getenv("GITHUB_SHA") or os.getenv("SOURCE_COMMIT")


class EvaluationService:
    def __init__(self, db: Session):
        self.db = db

    def _project(self, project_id: UUID) -> BidProject:
        project = self.db.get(BidProject, project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="project not found")
        return project

    def ensure_builtin_suite(self, project_id: UUID | None = None) -> EvaluationSuite:
        bundle = load_reference_suite()
        existing = self.db.scalar(
            select(EvaluationSuite).where(
                EvaluationSuite.name == BUILTIN_SUITE_NAME,
                EvaluationSuite.version == BUILTIN_SUITE_VERSION,
                EvaluationSuite.dataset_hash == bundle.dataset_hash,
            )
        )
        if existing:
            return existing
        suite = EvaluationSuite(
            project_id=None,
            name=BUILTIN_SUITE_NAME,
            version=BUILTIN_SUITE_VERSION,
            description="Step 1 auto_reference multi-task suite (never human_gold)",
            manifest_snapshot=build_manifest_snapshot(bundle),
            dataset_hash=bundle.dataset_hash,
            evaluator_profile_version=EVALUATOR_PROFILE_VERSION,
            task_family_config=bundle.stats.get("task_family_counts"),
        )
        self.db.add(suite)
        self.db.commit()
        self.db.refresh(suite)
        return suite

    def capabilities(self, project_id: UUID) -> dict[str, Any]:
        self._project(project_id)
        caps = list_capabilities(allow_fake=True)
        bundle = load_reference_suite()
        return {
            "targets": [
                {"target_type": c.target_type, "available": c.available, "reason": c.reason}
                for c in caps
            ],
            "profiles": list(all_profiles().keys()),
            "evaluator_version": EVALUATOR_VERSION,
            "dataset": {
                "name": BUILTIN_SUITE_NAME,
                "version": BUILTIN_SUITE_VERSION,
                "hash": bundle.dataset_hash,
                "hash_short": bundle.dataset_hash[:12],
                "stats": bundle.stats,
            },
        }

    def list_suites(self, project_id: UUID) -> list[EvaluationSuite]:
        self._project(project_id)
        self.ensure_builtin_suite()
        return list(
            self.db.scalars(
                select(EvaluationSuite).where(
                    (EvaluationSuite.project_id.is_(None))
                    | (EvaluationSuite.project_id == project_id)
                )
            ).all()
        )

    def get_suite(self, project_id: UUID, suite_id: UUID) -> EvaluationSuite:
        self._project(project_id)
        suite = self.db.get(EvaluationSuite, suite_id)
        if suite is None or (suite.project_id is not None and suite.project_id != project_id):
            raise HTTPException(status_code=404, detail="suite not found")
        return suite

    def create_run(
        self,
        project_id: UUID,
        payload: dict[str, Any],
        *,
        idempotency_key: str | None = None,
        execute: bool = True,
    ) -> EvaluationRun:
        self._project(project_id)
        if idempotency_key:
            existing = self.db.scalar(
                select(EvaluationRun).where(
                    EvaluationRun.project_id == project_id,
                    EvaluationRun.idempotency_key == idempotency_key,
                )
            )
            if existing:
                return existing
        suite_id = payload.get("suite_id")
        suite = self.get_suite(project_id, suite_id) if suite_id else self.ensure_builtin_suite()
        target_type = payload.get("target_type") or EvaluationTargetType.deterministic_fake.value
        try:
            tt = EvaluationTargetType(target_type)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="unknown target_type") from exc
        # Gate unavailable targets
        caps = {c.target_type: c for c in list_capabilities(allow_fake=True)}
        cap = caps.get(tt.value)
        if cap and not cap.available and tt != EvaluationTargetType.deterministic_fake:
            raise HTTPException(status_code=422, detail=cap.reason or "target unavailable")
        safe_config = {
            k: v
            for k, v in dict(payload.get("target_config") or {}).items()
            if k.lower()
            not in {"api_key", "token", "authorization", "password", "database_url", "prompt"}
        }
        safe_config["seed"] = int(payload.get("seed") or 42)
        if payload.get("fail_case_keys"):
            safe_config["fail_case_keys"] = list(payload["fail_case_keys"])
        filt = {
            "split": payload.get("split"),
            "splits": payload.get("splits"),
            "task_family": payload.get("task_family"),
            "task_families": payload.get("task_families"),
            "limit": payload.get("limit"),
            "case_keys": payload.get("case_keys"),
            "fixture_path": payload.get("fixture_path"),
        }
        run = EvaluationRun(
            project_id=project_id,
            suite_id=suite.id,
            status=EvaluationRunStatus.queued,
            target_type=tt,
            target_config_snapshot=safe_config,
            dataset_hash=suite.dataset_hash,
            evaluator_version=EVALUATOR_VERSION,
            seed=int(payload.get("seed") or 42),
            idempotency_key=idempotency_key,
            source_commit_sha=_git_sha(),
            created_by=payload.get("created_by"),
            filter_json=filt,
        )
        self.db.add(run)
        self.db.commit()
        self.db.refresh(run)
        claim = claim_evaluation_run(self.db, run.id, action="start", project_id=project_id)
        if claim.outcome != EvalClaimOutcome.claimed:
            self.db.refresh(run)
            return run
        if execute:
            try:
                return execute_evaluation_run(self.db, run.id)
            finally:
                release_evaluation_claim(self.db, run.id, claim_token=claim.claim_token)
        return run

    def get_run(self, project_id: UUID, run_id: UUID) -> EvaluationRun:
        self._project(project_id)
        run = self.db.get(EvaluationRun, run_id)
        if run is None or run.project_id != project_id:
            raise HTTPException(status_code=404, detail="evaluation run not found")
        return run

    def list_runs(
        self, project_id: UUID, *, status: str | None = None, limit: int = 50, offset: int = 0
    ) -> list[EvaluationRun]:
        self._project(project_id)
        q = select(EvaluationRun).where(EvaluationRun.project_id == project_id)
        if status:
            q = q.where(EvaluationRun.status == status)
        q = q.order_by(EvaluationRun.created_at.desc()).offset(offset).limit(limit)
        return list(self.db.scalars(q).all())

    def get_results(self, project_id: UUID, run_id: UUID, **filters) -> list[EvaluationCaseResult]:
        run = self.get_run(project_id, run_id)
        q = select(EvaluationCaseResult).where(EvaluationCaseResult.evaluation_run_id == run.id)
        if filters.get("status"):
            q = q.where(EvaluationCaseResult.status == filters["status"])
        if filters.get("task_family"):
            q = q.where(EvaluationCaseResult.task_family == filters["task_family"])
        if filters.get("passed") is not None:
            q = q.where(EvaluationCaseResult.passed == filters["passed"])
        return list(self.db.scalars(q.order_by(EvaluationCaseResult.case_key.asc())).all())

    def get_result(self, project_id: UUID, run_id: UUID, result_id: UUID) -> EvaluationCaseResult:
        self.get_run(project_id, run_id)
        row = self.db.scalar(
            select(EvaluationCaseResult)
            .where(
                EvaluationCaseResult.id == result_id,
                EvaluationCaseResult.evaluation_run_id == run_id,
            )
            .options(selectinload(EvaluationCaseResult.metric_results))
        )
        if row is None:
            raise HTTPException(status_code=404, detail="result not found")
        return row

    def cancel(self, project_id: UUID, run_id: UUID) -> EvaluationRun:
        run = self.get_run(project_id, run_id)
        if run.status in {EvaluationRunStatus.completed, EvaluationRunStatus.cancelled}:
            return run
        run.cancel_requested = True
        if run.status == EvaluationRunStatus.queued and run.execution_claim_token is None:
            run.status = EvaluationRunStatus.cancelled
            run.finished_at = _now()
        self.db.commit()
        self.db.refresh(run)
        return run

    def resume(self, project_id: UUID, run_id: UUID, *, execute: bool = True) -> EvaluationRun:
        run = self.get_run(project_id, run_id)
        claim = claim_evaluation_run(self.db, run.id, action="resume", project_id=project_id)
        if claim.outcome == EvalClaimOutcome.already_running and claim.run:
            return claim.run
        if claim.outcome != EvalClaimOutcome.claimed:
            raise HTTPException(status_code=409, detail=claim.detail or "cannot resume")
        if execute:
            try:
                return execute_evaluation_run(self.db, run.id)
            finally:
                release_evaluation_claim(self.db, run.id, claim_token=claim.claim_token)
        return self.get_run(project_id, run_id)

    def compare(self, project_id: UUID, left_id: UUID, right_id: UUID) -> dict[str, Any]:
        left = self.get_run(project_id, left_id)
        right = self.get_run(project_id, right_id)
        left_cases = {r.case_key: r for r in self.get_results(project_id, left_id)}
        right_cases = {r.case_key: r for r in self.get_results(project_id, right_id)}
        common = sorted(set(left_cases) & set(right_cases))
        only_left = sorted(set(left_cases) - set(right_cases))
        only_right = sorted(set(right_cases) - set(left_cases))
        improved = []
        regressed = []
        unchanged = []
        for k in common:
            a, b = left_cases[k], right_cases[k]
            sa, sb = a.score, b.score
            if sa is None or sb is None:
                unchanged.append(k)
            elif sb > sa + 1e-9:
                improved.append(k)
            elif sb < sa - 1e-9:
                regressed.append(k)
            else:
                unchanged.append(k)
        warnings = []
        if left.dataset_hash != right.dataset_hash:
            warnings.append("dataset_hash_mismatch")
        if left.evaluator_version != right.evaluator_version:
            warnings.append("evaluator_version_mismatch")
        return {
            "left_run_id": str(left.id),
            "right_run_id": str(right.id),
            "warnings": warnings,
            "overall_score_delta": (
                None
                if left.overall_score is None or right.overall_score is None
                else right.overall_score - left.overall_score
            ),
            "common_cases": common,
            "only_left": only_left,
            "only_right": only_right,
            "improved": improved,
            "regressed": regressed,
            "unchanged": unchanged,
            "left": {
                "dataset_hash": left.dataset_hash,
                "evaluator_version": left.evaluator_version,
                "target_type": left.target_type.value,
                "overall_score": left.overall_score,
            },
            "right": {
                "dataset_hash": right.dataset_hash,
                "evaluator_version": right.evaluator_version,
                "target_type": right.target_type.value,
                "overall_score": right.overall_score,
            },
        }

    def export(self, project_id: UUID, run_id: UUID, fmt: str = "json") -> tuple[str, str]:
        run = self.get_run(project_id, run_id)
        results = self.get_results(project_id, run_id)
        suite = self.db.get(EvaluationSuite, run.suite_id)
        stats = (suite.manifest_snapshot or {}) if suite else {}
        report = build_report_dict(run, results, suite_stats=stats)
        if fmt == "csv":
            return serialize_csv(report), "text/csv"
        if fmt == "markdown":
            return serialize_markdown(report), "text/markdown"
        return serialize_json(report), "application/json"
