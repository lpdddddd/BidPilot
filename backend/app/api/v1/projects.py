from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.project import ProjectCreate, ProjectListResponse, ProjectRead
from app.services.project import ProjectService

router = APIRouter()


@router.post("", response_model=ProjectRead, status_code=201)
def create_project(
    payload: ProjectCreate,
    db: Session = Depends(get_db),
) -> ProjectRead:
    return ProjectService(db).create_project(payload)


@router.get("", response_model=ProjectListResponse)
def list_projects(
    organization_id: UUID | None = None,
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> ProjectListResponse:
    return ProjectService(db).list_projects(
        organization_id=organization_id,
        skip=skip,
        limit=limit,
    )


@router.get("/{project_id}", response_model=ProjectRead)
def get_project(project_id: UUID, db: Session = Depends(get_db)) -> ProjectRead:
    return ProjectService(db).get_project(project_id)
