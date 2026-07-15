from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import BidProject
from app.schemas.project import ProjectCreate


class ProjectRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create(self, *, organization_id: UUID, data: ProjectCreate) -> BidProject:
        project = BidProject(
            organization_id=organization_id,
            project_code=data.project_code,
            project_name=data.project_name,
            purchaser=data.purchaser,
            procurement_agency=data.procurement_agency,
            procurement_method=data.procurement_method,
            industry=data.industry,
            region=data.region,
            budget_cny=data.budget_cny,
            price_ceiling_cny=data.price_ceiling_cny,
            bid_deadline=data.bid_deadline,
            status=data.status,
            metadata_json=data.metadata_json,
        )
        self.db.add(project)
        self.db.flush()
        return project

    def get_by_id(self, project_id: UUID) -> BidProject | None:
        return self.db.get(BidProject, project_id)

    def list_projects(
        self,
        *,
        organization_id: UUID | None = None,
        skip: int = 0,
        limit: int = 50,
    ) -> tuple[list[BidProject], int]:
        stmt = select(BidProject).order_by(BidProject.created_at.desc())
        count_stmt = select(func.count()).select_from(BidProject)
        if organization_id is not None:
            stmt = stmt.where(BidProject.organization_id == organization_id)
            count_stmt = count_stmt.where(BidProject.organization_id == organization_id)
        total = self.db.scalar(count_stmt) or 0
        items = list(self.db.scalars(stmt.offset(skip).limit(limit)))
        return items, total

    def get_by_code(self, *, organization_id: UUID, project_code: str) -> BidProject | None:
        stmt = select(BidProject).where(
            BidProject.organization_id == organization_id,
            BidProject.project_code == project_code,
        )
        return self.db.scalar(stmt)
