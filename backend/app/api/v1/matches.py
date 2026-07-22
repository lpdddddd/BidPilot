from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.enums import EvidenceMatchStatus, RequirementCategory, RiskLevel
from app.schemas.match import (
    MatchDetail,
    MatchListResponse,
    MatchRunResponse,
    MatchStartRequest,
)
from app.services import requirement_match_tasks
from app.services.requirement_match_service import RequirementMatchService

router = APIRouter()


@router.post(
    "/{project_id}/requirement-matches/runs",
    response_model=MatchRunResponse,
    status_code=201,
)
def start_requirement_matching(
    project_id: UUID,
    payload: MatchStartRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> MatchRunResponse:
    run = RequirementMatchService(db).start_matching(project_id, payload)
    background_tasks.add_task(
        requirement_match_tasks.run_requirement_matching,
        run.id,
    )
    return run


@router.get(
    "/{project_id}/requirement-matches/runs/{run_id}",
    response_model=MatchRunResponse,
)
def get_requirement_match_run(
    project_id: UUID,
    run_id: UUID,
    db: Session = Depends(get_db),
) -> MatchRunResponse:
    return RequirementMatchService(db).get_run(project_id, run_id)


@router.get(
    "/{project_id}/requirement-matches",
    response_model=MatchListResponse,
)
def list_requirement_matches(
    project_id: UUID,
    requirement_id: UUID | None = Query(default=None),
    status: EvidenceMatchStatus | None = Query(default=None),
    risk_level: RiskLevel | None = Query(default=None),
    category: RequirementCategory | None = Query(default=None),
    mandatory: bool | None = Query(default=None),
    needs_review: bool | None = Query(default=None),
    source_document_id: UUID | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int | None = Query(default=None, ge=0),
    db: Session = Depends(get_db),
) -> MatchListResponse:
    return RequirementMatchService(db).list_matches(
        project_id,
        requirement_id=requirement_id,
        match_status=status,
        risk_level=risk_level,
        category=category,
        mandatory=mandatory,
        needs_review=needs_review,
        source_document_id=source_document_id,
        page=page,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{project_id}/requirement-matches/{match_id}",
    response_model=MatchDetail,
)
def get_requirement_match(
    project_id: UUID,
    match_id: UUID,
    db: Session = Depends(get_db),
) -> MatchDetail:
    return RequirementMatchService(db).get_match(project_id, match_id)
