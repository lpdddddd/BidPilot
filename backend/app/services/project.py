from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.repositories.organization import OrganizationRepository
from app.repositories.project import ProjectRepository
from app.schemas.project import ProjectCreate, ProjectListResponse, ProjectRead


class ProjectService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.projects = ProjectRepository(db)
        self.organizations = OrganizationRepository(db)

    def create_project(self, data: ProjectCreate) -> ProjectRead:
        if data.organization_id is not None:
            org = self.organizations.get_by_id(data.organization_id)
            if org is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Organization not found",
                )
        else:
            name = data.organization_name or "Default Organization"
            org = self.organizations.get_or_create(
                name=name,
                description="Auto-created organization for BidPilot scaffold",
            )

        project = self.projects.create(organization_id=org.id, data=data)
        self.db.commit()
        self.db.refresh(project)
        return ProjectRead.model_validate(project)

    def list_projects(
        self,
        *,
        organization_id: UUID | None = None,
        skip: int = 0,
        limit: int = 50,
    ) -> ProjectListResponse:
        items, total = self.projects.list_projects(
            organization_id=organization_id,
            skip=skip,
            limit=limit,
        )
        return ProjectListResponse(
            items=[ProjectRead.model_validate(item) for item in items],
            total=total,
        )

    def get_project(self, project_id: UUID) -> ProjectRead:
        project = self.projects.get_by_id(project_id)
        if project is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Project not found",
            )
        return ProjectRead.model_validate(project)
