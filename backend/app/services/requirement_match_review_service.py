"""Auditable human review closed-loop for RequirementEvidenceMatch rows.

Never mutates EvidenceMatchStatus, summary, EvidenceLinks, or match-run records.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.models import BidProject
from app.models.enums import (
    ActorAuthn,
    EvidenceMatchStatus,
    MatchReviewAction,
    MatchReviewReasonCode,
    MatchReviewStatus,
    RequirementCategory,
    RiskLevel,
)
from app.models.match_run import RequirementEvidenceMatch, RequirementMatchReview
from app.models.requirement import Requirement
from app.schemas.match import MatchDetail
from app.schemas.match_review import (
    MatchReopenRequest,
    MatchReviewListResponse,
    MatchReviewRead,
    MatchReviewRequest,
    ReviewQueueCounts,
    ReviewQueueItem,
    ReviewQueueResponse,
)
from app.services.requirement_match_service import RequirementMatchService

_TRANSITIONS: dict[tuple[MatchReviewStatus, MatchReviewAction], MatchReviewStatus] = {
    (MatchReviewStatus.pending, MatchReviewAction.confirm): MatchReviewStatus.confirmed,
    (MatchReviewStatus.pending, MatchReviewAction.reject): MatchReviewStatus.rejected,
    (
        MatchReviewStatus.pending,
        MatchReviewAction.needs_more_material,
    ): MatchReviewStatus.needs_more_material,
    (
        MatchReviewStatus.confirmed,
        MatchReviewAction.reopen,
    ): MatchReviewStatus.pending,
    (
        MatchReviewStatus.rejected,
        MatchReviewAction.reopen,
    ): MatchReviewStatus.pending,
    (
        MatchReviewStatus.needs_more_material,
        MatchReviewAction.reopen,
    ): MatchReviewStatus.pending,
}

_TERMINAL = frozenset(
    {
        MatchReviewStatus.confirmed,
        MatchReviewStatus.rejected,
        MatchReviewStatus.needs_more_material,
    }
)

_COMMENT_REQUIRED = frozenset(
    {
        MatchReviewAction.reject,
        MatchReviewAction.needs_more_material,
        MatchReviewAction.reopen,
    }
)

_MAX_COMMENT = 2000

_SORT_COLUMNS = {
    "created_at": RequirementEvidenceMatch.created_at,
    "updated_at": RequirementEvidenceMatch.updated_at,
    "reviewed_at": RequirementEvidenceMatch.reviewed_at,
    "risk_level": RequirementEvidenceMatch.risk_level,
    "status": RequirementEvidenceMatch.status,
    "review_status": RequirementEvidenceMatch.review_status,
}


def _normalize_comment(raw: str | None, *, required: bool) -> str | None:
    if raw is None:
        if required:
            raise HTTPException(
                status_code=422,
                detail="comment is required for this action",
            )
        return None
    cleaned = " ".join(raw.split())
    if not cleaned:
        if required:
            raise HTTPException(
                status_code=422,
                detail="comment is required for this action",
            )
        return None
    if len(cleaned) > _MAX_COMMENT:
        raise HTTPException(
            status_code=422,
            detail=f"comment must be at most {_MAX_COMMENT} characters",
        )
    return cleaned


def _validate_actor_label(label: str) -> str:
    cleaned = " ".join(label.split())
    if not cleaned or len(cleaned) > 64:
        raise HTTPException(
            status_code=422,
            detail="actor_label must be 1-64 printable characters",
        )
    if any(ord(ch) < 32 for ch in cleaned):
        raise HTTPException(
            status_code=422,
            detail="actor_label must be printable",
        )
    return cleaned


def _request_fingerprint(
    *,
    action: MatchReviewAction,
    actor_label: str,
    comment: str | None,
    reason_code: MatchReviewReasonCode | None,
) -> tuple[str, str, str | None, str | None]:
    return (
        action.value,
        actor_label,
        comment,
        reason_code.value if reason_code else None,
    )


def _parse_source_run_id(match: RequirementEvidenceMatch) -> UUID | None:
    raw = (match.metadata_json or {}).get("run_id")
    if not raw:
        return None
    try:
        return UUID(str(raw))
    except ValueError:
        return None


def _has_conflict(match: RequirementEvidenceMatch) -> bool:
    if match.status == EvidenceMatchStatus.conflicting_evidence:
        return True
    meta = match.metadata_json or {}
    if meta.get("requirement_potential_conflict") or meta.get("conflict_dimension"):
        return True
    req = match.requirement
    if req is not None and bool((req.metadata_json or {}).get("potential_conflict")):
        return True
    return False


def _has_scope_exclusion(match: RequirementEvidenceMatch) -> bool:
    if match.status == EvidenceMatchStatus.not_applicable:
        return True
    meta = match.metadata_json or {}
    if meta.get("not_applicable_basis") or meta.get("requirement_scope_chunk_id"):
        return True
    return False


class RequirementMatchReviewService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self._match_svc = RequirementMatchService(db)

    def _require_project(self, project_id: UUID) -> BidProject:
        project = self.db.get(BidProject, project_id)
        if project is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="项目不存在",
            )
        return project

    def _lock_match(
        self, project_id: UUID, match_id: UUID
    ) -> RequirementEvidenceMatch:
        match = self.db.execute(
            select(RequirementEvidenceMatch)
            .where(
                RequirementEvidenceMatch.id == match_id,
                RequirementEvidenceMatch.project_id == project_id,
            )
            .with_for_update()
        ).scalar_one_or_none()
        if match is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="匹配结果不存在",
            )
        return match

    def review_queue(
        self,
        project_id: UUID,
        *,
        review_status: MatchReviewStatus | None = MatchReviewStatus.pending,
        match_status: EvidenceMatchStatus | None = None,
        risk_level: RiskLevel | None = None,
        requirement_category: RequirementCategory | None = None,
        has_conflict: bool | None = None,
        has_scope_exclusion: bool | None = None,
        include_superseded: bool = False,
        requirement_id: UUID | None = None,
        page: int = 1,
        limit: int = 50,
        offset: int | None = None,
        sort: str = "created_at_desc",
    ) -> ReviewQueueResponse:
        self._require_project(project_id)
        if page < 1:
            page = 1
        if offset is None:
            offset = (page - 1) * limit

        lifecycle_filter = (
            True
            if include_superseded
            else RequirementEvidenceMatch.lifecycle_status == "active"
        )

        def _apply_filters(stmt):
            stmt = stmt.where(
                RequirementEvidenceMatch.project_id == project_id,
                lifecycle_filter,
            )
            if review_status is not None:
                stmt = stmt.where(
                    RequirementEvidenceMatch.review_status == review_status
                )
            if match_status is not None:
                stmt = stmt.where(RequirementEvidenceMatch.status == match_status)
            if risk_level is not None:
                stmt = stmt.where(RequirementEvidenceMatch.risk_level == risk_level)
            if requirement_category is not None:
                stmt = stmt.where(Requirement.category == requirement_category)
            if requirement_id is not None:
                stmt = stmt.where(
                    RequirementEvidenceMatch.requirement_id == requirement_id
                )
            if has_conflict is True:
                stmt = stmt.where(
                    or_(
                        RequirementEvidenceMatch.status
                        == EvidenceMatchStatus.conflicting_evidence,
                        RequirementEvidenceMatch.metadata_json.contains(
                            {"requirement_potential_conflict": True}
                        ),
                        Requirement.metadata_json.contains(
                            {"potential_conflict": True}
                        ),
                    )
                )
            elif has_conflict is False:
                stmt = stmt.where(
                    and_(
                        RequirementEvidenceMatch.status
                        != EvidenceMatchStatus.conflicting_evidence,
                        ~RequirementEvidenceMatch.metadata_json.contains(
                            {"requirement_potential_conflict": True}
                        ),
                        ~Requirement.metadata_json.contains(
                            {"potential_conflict": True}
                        ),
                    )
                )
            if has_scope_exclusion is True:
                stmt = stmt.where(
                    or_(
                        RequirementEvidenceMatch.status
                        == EvidenceMatchStatus.not_applicable,
                        RequirementEvidenceMatch.metadata_json.has_key(
                            "not_applicable_basis"
                        ),
                    )
                )
            elif has_scope_exclusion is False:
                stmt = stmt.where(
                    and_(
                        RequirementEvidenceMatch.status
                        != EvidenceMatchStatus.not_applicable,
                        ~RequirementEvidenceMatch.metadata_json.has_key(
                            "not_applicable_basis"
                        ),
                    )
                )
            return stmt

        base = _apply_filters(
            select(RequirementEvidenceMatch).join(
                Requirement, Requirement.id == RequirementEvidenceMatch.requirement_id
            )
        )
        count_base = _apply_filters(
            select(func.count())
            .select_from(RequirementEvidenceMatch)
            .join(
                Requirement, Requirement.id == RequirementEvidenceMatch.requirement_id
            )
        )

        total = int(self.db.scalar(count_base) or 0)

        sort_key = (sort or "created_at_desc").strip().lower()
        descending = sort_key.endswith("_desc") or sort_key.startswith("-")
        col_name = (
            sort_key.removeprefix("-").removesuffix("_desc").removesuffix("_asc")
        )
        order_col = _SORT_COLUMNS.get(col_name, RequirementEvidenceMatch.created_at)
        order_by = order_col.desc() if descending else order_col.asc()

        rows = list(
            self.db.scalars(
                base.options(selectinload(RequirementEvidenceMatch.requirement))
                .order_by(order_by, RequirementEvidenceMatch.id.desc())
                .offset(offset)
                .limit(limit)
            )
        )

        counts = ReviewQueueCounts()
        aggregate_lifecycle = (
            True
            if include_superseded
            else RequirementEvidenceMatch.lifecycle_status == "active"
        )
        status_rows = self.db.execute(
            select(
                RequirementEvidenceMatch.review_status,
                func.count(),
            )
            .where(
                RequirementEvidenceMatch.project_id == project_id,
                aggregate_lifecycle,
            )
            .group_by(RequirementEvidenceMatch.review_status)
        ).all()
        for rs, n in status_rows:
            n_int = int(n)
            counts.total += n_int
            if rs == MatchReviewStatus.pending:
                counts.pending = n_int
            elif rs == MatchReviewStatus.confirmed:
                counts.confirmed = n_int
            elif rs == MatchReviewStatus.rejected:
                counts.rejected = n_int
            elif rs == MatchReviewStatus.needs_more_material:
                counts.needs_more_material = n_int

        match_status_rows = self.db.execute(
            select(
                RequirementEvidenceMatch.status,
                func.count(),
            )
            .where(
                RequirementEvidenceMatch.project_id == project_id,
                aggregate_lifecycle,
            )
            .group_by(RequirementEvidenceMatch.status)
        ).all()
        counts.by_match_status = {
            (st.value if hasattr(st, "value") else str(st)): int(n)
            for st, n in match_status_rows
        }

        risk_rows = self.db.execute(
            select(
                RequirementEvidenceMatch.risk_level,
                func.count(),
            )
            .where(
                RequirementEvidenceMatch.project_id == project_id,
                aggregate_lifecycle,
            )
            .group_by(RequirementEvidenceMatch.risk_level)
        ).all()
        counts.by_risk_level = {
            (rl.value if hasattr(rl, "value") else str(rl)): int(n)
            for rl, n in risk_rows
        }

        items: list[ReviewQueueItem] = []
        for m in rows:
            req = m.requirement
            items.append(
                ReviewQueueItem(
                    id=m.id,
                    project_id=m.project_id,
                    requirement_id=m.requirement_id,
                    status=m.status,
                    review_status=m.review_status,
                    risk_level=m.risk_level,
                    needs_review=m.needs_review,
                    is_review_protected=m.is_review_protected,
                    review_lock_version=m.review_lock_version,
                    lifecycle_status=m.lifecycle_status,
                    summary=m.summary,
                    reviewed_at=m.reviewed_at,
                    reviewed_by=m.reviewed_by,
                    requirement_title=req.title if req else None,
                    requirement_code=req.requirement_code if req else None,
                    requirement_category=req.category if req else None,
                    requirement_risk_level=req.risk_level if req else None,
                    has_conflict=_has_conflict(m),
                    has_scope_exclusion=_has_scope_exclusion(m),
                    source_run_id=_parse_source_run_id(m),
                    superseded_by_match_id=m.superseded_by_match_id,
                    supersedes_match_id=m.supersedes_match_id,
                    last_reviewer=m.reviewed_by,
                    last_reviewed_at=m.reviewed_at,
                    detail_id=m.id,
                    created_at=m.created_at,
                    updated_at=m.updated_at,
                )
            )
        return ReviewQueueResponse(
            counts=counts,
            items=items,
            total=total,
            page=page,
            limit=limit,
            offset=offset,
            include_superseded=include_superseded,
        )

    def list_reviews(
        self, project_id: UUID, match_id: UUID
    ) -> MatchReviewListResponse:
        self._require_project(project_id)
        match = self.db.scalar(
            select(RequirementEvidenceMatch).where(
                RequirementEvidenceMatch.id == match_id,
                RequirementEvidenceMatch.project_id == project_id,
            )
        )
        if match is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="匹配结果不存在",
            )
        rows = list(
            self.db.scalars(
                select(RequirementMatchReview)
                .where(
                    RequirementMatchReview.project_id == project_id,
                    RequirementMatchReview.match_id == match_id,
                )
                .order_by(RequirementMatchReview.created_at.desc())
            )
        )
        items = [MatchReviewRead.model_validate(r) for r in rows]
        return MatchReviewListResponse(items=items, total=len(items))

    def apply_review(
        self,
        project_id: UUID,
        match_id: UUID,
        request: MatchReviewRequest,
        *,
        idempotency_key: str | None = None,
    ) -> MatchDetail:
        return self._apply_action(
            project_id,
            match_id,
            action=request.action,
            actor_label=request.actor_label,
            comment=request.comment,
            reason_code=request.reason_code,
            review_lock_version=request.review_lock_version,
            idempotency_key=idempotency_key,
        )

    def reopen(
        self,
        project_id: UUID,
        match_id: UUID,
        request: MatchReopenRequest,
        *,
        idempotency_key: str | None = None,
    ) -> MatchDetail:
        return self._apply_action(
            project_id,
            match_id,
            action=MatchReviewAction.reopen,
            actor_label=request.actor_label,
            comment=request.comment,
            reason_code=None,
            review_lock_version=request.review_lock_version,
            idempotency_key=idempotency_key,
        )

    def _find_idempotent(
        self,
        project_id: UUID,
        match_id: UUID,
        idempotency_key: str,
        *,
        action: MatchReviewAction,
        actor_label: str,
        comment: str | None,
        reason_code: MatchReviewReasonCode | None,
    ) -> MatchDetail | None:
        existing = self.db.scalar(
            select(RequirementMatchReview).where(
                RequirementMatchReview.project_id == project_id,
                RequirementMatchReview.match_id == match_id,
                RequirementMatchReview.idempotency_key == idempotency_key,
            )
        )
        if existing is None:
            return None
        expected = _request_fingerprint(
            action=action,
            actor_label=actor_label,
            comment=comment,
            reason_code=reason_code,
        )
        actual = _request_fingerprint(
            action=existing.action,
            actor_label=existing.actor_label,
            comment=existing.comment,
            reason_code=existing.reason_code,
        )
        if expected != actual:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Idempotency-Key reused with a different request body",
            )
        return self._match_svc.get_match(project_id, match_id)

    def _apply_action(
        self,
        project_id: UUID,
        match_id: UUID,
        *,
        action: MatchReviewAction,
        actor_label: str,
        comment: str | None,
        reason_code: MatchReviewReasonCode | None,
        review_lock_version: int,
        idempotency_key: str | None,
    ) -> MatchDetail:
        self._require_project(project_id)
        actor_label = _validate_actor_label(actor_label)
        comment_required = action in _COMMENT_REQUIRED
        comment = _normalize_comment(comment, required=comment_required)

        if idempotency_key:
            replay = self._find_idempotent(
                project_id,
                match_id,
                idempotency_key,
                action=action,
                actor_label=actor_label,
                comment=comment,
                reason_code=reason_code,
            )
            if replay is not None:
                return replay

        match = self._lock_match(project_id, match_id)

        if idempotency_key:
            replay = self._find_idempotent(
                project_id,
                match_id,
                idempotency_key,
                action=action,
                actor_label=actor_label,
                comment=comment,
                reason_code=reason_code,
            )
            if replay is not None:
                return replay

        if match.review_lock_version != review_lock_version:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "review_lock_version mismatch; refresh the match and retry"
                ),
            )

        current = match.review_status
        if action != MatchReviewAction.reopen and current in _TERMINAL:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"match review is already terminal ({current.value})",
            )

        target = _TRANSITIONS.get((current, action))
        if target is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"invalid transition: {current.value} + {action.value}"
                ),
            )

        now = datetime.now(UTC)
        review = RequirementMatchReview(
            project_id=project_id,
            match_id=match_id,
            action=action,
            from_review_status=current,
            to_review_status=target,
            comment=comment,
            reason_code=reason_code,
            actor_id=None,
            actor_label=actor_label,
            actor_authn=ActorAuthn.unverified_local_operator,
            idempotency_key=idempotency_key,
        )
        self.db.add(review)

        match.review_status = target
        match.review_lock_version = match.review_lock_version + 1
        if action == MatchReviewAction.reopen:
            match.needs_review = True
            match.is_review_protected = False
        else:
            match.needs_review = False
            match.is_review_protected = True
            match.reviewed_at = now
            match.reviewed_by = actor_label

        self.db.commit()
        return self._match_svc.get_match(project_id, match_id)
