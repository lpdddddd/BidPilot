from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.enums import RequirementCategory, ReviewStatus, RiskLevel
from app.schemas.extraction import ExtractionRunResponse, ExtractionStartRequest
from app.schemas.requirement import RequirementDetail, RequirementListResponse
from app.schemas.structured_clause import StructuredClauseRequest, StructuredClauseResponse
from app.services import requirement_extraction_tasks
from app.services.requirement_extraction_service import RequirementExtractionService
from app.services.structured_clause import StructuredClauseService

router = APIRouter()


@router.post(
    "/{project_id}/requirements/structured-analyses",
    response_model=StructuredClauseResponse,
)
def analyze_structured_clause(
    project_id: UUID,
    payload: StructuredClauseRequest,
    db: Session = Depends(get_db),
) -> StructuredClauseResponse:
    """SFT-protocol clause analysis (Base or Course LoRA). Project id scopes auth."""
    # Ensure project exists / accessible via extraction service helper.
    RequirementExtractionService(db)._require_project(project_id)  # noqa: SLF001
    result = StructuredClauseService().analyze(
        clause_text=payload.clause_text,
        task_type=payload.task_type,
        model_id=payload.model_id,
        allow_base_fallback=payload.allow_base_fallback,
        temperature=payload.temperature,
        max_tokens=payload.max_tokens,
    )
    return StructuredClauseResponse(**result.public_dict())


@router.post(
    "/{project_id}/requirements/extractions",
    response_model=ExtractionRunResponse,
    status_code=201,
)
def start_requirement_extraction(
    project_id: UUID,
    payload: ExtractionStartRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> ExtractionRunResponse:
    run = RequirementExtractionService(db).start_extraction(project_id, payload)
    background_tasks.add_task(
        requirement_extraction_tasks.run_requirement_extraction,
        run.id,
    )
    return run


@router.get(
    "/{project_id}/requirements/extractions/{run_id}",
    response_model=ExtractionRunResponse,
)
def get_requirement_extraction_run(
    project_id: UUID,
    run_id: UUID,
    db: Session = Depends(get_db),
) -> ExtractionRunResponse:
    return RequirementExtractionService(db).get_run(project_id, run_id)


@router.get(
    "/{project_id}/requirements",
    response_model=RequirementListResponse,
)
def list_requirements(
    project_id: UUID,
    category: RequirementCategory | None = Query(default=None),
    mandatory: bool | None = Query(default=None),
    risk_level: RiskLevel | None = Query(default=None),
    review_status: ReviewStatus | None = Query(default=None),
    source_document_id: UUID | None = Query(default=None),
    has_conflict: bool | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int | None = Query(default=None, ge=0),
    db: Session = Depends(get_db),
) -> RequirementListResponse:
    return RequirementExtractionService(db).list_requirements(
        project_id,
        category=category,
        mandatory=mandatory,
        risk_level=risk_level,
        review_status=review_status,
        source_document_id=source_document_id,
        has_conflict=has_conflict,
        page=page,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{project_id}/requirements/{requirement_id}",
    response_model=RequirementDetail,
)
def get_requirement(
    project_id: UUID,
    requirement_id: UUID,
    db: Session = Depends(get_db),
) -> RequirementDetail:
    return RequirementExtractionService(db).get_requirement(project_id, requirement_id)
