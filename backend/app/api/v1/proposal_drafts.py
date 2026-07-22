"""API routes for auditable proposal drafting workspace."""

from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Header, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.proposal_draft import (
    ProposalDraftCreateRequest,
    ProposalDraftDetail,
    ProposalDraftEligibilityResponse,
    ProposalDraftListResponse,
    ProposalDraftManualRevisionRequest,
    ProposalDraftReopenRequest,
    ProposalDraftReviewRequest,
    ProposalDraftRunResponse,
    ProposalDraftVersionDetail,
    ProposalDraftVersionListResponse,
)
from app.services import proposal_draft_tasks
from app.services.proposal_draft_service import ProposalDraftService

router = APIRouter()


@router.get(
    "/{project_id}/proposal-drafts/eligibility",
    response_model=ProposalDraftEligibilityResponse,
)
def get_proposal_draft_eligibility(
    project_id: UUID,
    requirement_id: list[UUID] | None = Query(default=None),
    db: Session = Depends(get_db),
) -> ProposalDraftEligibilityResponse:
    return ProposalDraftService(db).eligibility(project_id, requirement_id)


@router.post(
    "/{project_id}/proposal-drafts",
    response_model=ProposalDraftRunResponse,
    status_code=201,
)
def create_proposal_draft(
    project_id: UUID,
    payload: ProposalDraftCreateRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> ProposalDraftRunResponse:
    run = ProposalDraftService(db).start_generation(
        project_id, payload, idempotency_key=idempotency_key
    )
    if run.status.value == "queued":
        background_tasks.add_task(
            proposal_draft_tasks.run_proposal_draft_generation,
            run.id,
        )
    return run


@router.get(
    "/{project_id}/proposal-drafts",
    response_model=ProposalDraftListResponse,
)
def list_proposal_drafts(
    project_id: UUID,
    db: Session = Depends(get_db),
) -> ProposalDraftListResponse:
    return ProposalDraftService(db).list_drafts(project_id)


@router.get(
    "/{project_id}/proposal-drafts/{draft_id}",
    response_model=ProposalDraftDetail,
)
def get_proposal_draft(
    project_id: UUID,
    draft_id: UUID,
    db: Session = Depends(get_db),
) -> ProposalDraftDetail:
    return ProposalDraftService(db).get_draft(project_id, draft_id)


@router.get(
    "/{project_id}/proposal-drafts/{draft_id}/versions",
    response_model=ProposalDraftVersionListResponse,
)
def list_proposal_draft_versions(
    project_id: UUID,
    draft_id: UUID,
    db: Session = Depends(get_db),
) -> ProposalDraftVersionListResponse:
    return ProposalDraftService(db).list_versions(project_id, draft_id)


@router.get(
    "/{project_id}/proposal-drafts/{draft_id}/versions/{version_id}",
    response_model=ProposalDraftVersionDetail,
)
def get_proposal_draft_version(
    project_id: UUID,
    draft_id: UUID,
    version_id: UUID,
    db: Session = Depends(get_db),
) -> ProposalDraftVersionDetail:
    return ProposalDraftService(db).get_version(project_id, draft_id, version_id)


@router.post(
    "/{project_id}/proposal-drafts/{draft_id}/manual-revisions",
    response_model=ProposalDraftDetail,
)
def create_manual_revision(
    project_id: UUID,
    draft_id: UUID,
    payload: ProposalDraftManualRevisionRequest,
    db: Session = Depends(get_db),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> ProposalDraftDetail:
    return ProposalDraftService(db).create_manual_revision(
        project_id, draft_id, payload, idempotency_key=idempotency_key
    )


@router.post(
    "/{project_id}/proposal-drafts/{draft_id}/review",
    response_model=ProposalDraftDetail,
)
def review_proposal_draft(
    project_id: UUID,
    draft_id: UUID,
    payload: ProposalDraftReviewRequest,
    db: Session = Depends(get_db),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> ProposalDraftDetail:
    return ProposalDraftService(db).mark_reviewed(
        project_id, draft_id, payload, idempotency_key=idempotency_key
    )


@router.post(
    "/{project_id}/proposal-drafts/{draft_id}/reopen",
    response_model=ProposalDraftDetail,
)
def reopen_proposal_draft(
    project_id: UUID,
    draft_id: UUID,
    payload: ProposalDraftReopenRequest,
    db: Session = Depends(get_db),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> ProposalDraftDetail:
    return ProposalDraftService(db).reopen(
        project_id, draft_id, payload, idempotency_key=idempotency_key
    )


@router.get(
    "/{project_id}/proposal-drafts/{draft_id}/export",
)
def export_proposal_draft(
    project_id: UUID,
    draft_id: UUID,
    format: str = Query(default="markdown", pattern="^(markdown|docx)$"),
    db: Session = Depends(get_db),
) -> Response:
    body, media_type, filename = ProposalDraftService(db).export(
        project_id, draft_id, fmt=format
    )
    return Response(
        content=body,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get(
    "/{project_id}/proposal-draft-runs/{run_id}",
    response_model=ProposalDraftRunResponse,
)
def get_proposal_draft_run(
    project_id: UUID,
    run_id: UUID,
    db: Session = Depends(get_db),
) -> ProposalDraftRunResponse:
    return ProposalDraftService(db).get_run(project_id, run_id)


@router.post(
    "/{project_id}/proposal-draft-runs/{run_id}/cancel",
    response_model=ProposalDraftRunResponse,
)
def cancel_proposal_draft_run(
    project_id: UUID,
    run_id: UUID,
    db: Session = Depends(get_db),
) -> ProposalDraftRunResponse:
    return ProposalDraftService(db).cancel_run(project_id, run_id)
