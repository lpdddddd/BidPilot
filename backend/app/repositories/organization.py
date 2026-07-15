from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Organization


class OrganizationRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_by_id(self, organization_id: UUID) -> Organization | None:
        return self.db.get(Organization, organization_id)

    def get_by_name(self, name: str) -> Organization | None:
        stmt = select(Organization).where(Organization.name == name)
        return self.db.scalar(stmt)

    def create(self, *, name: str, description: str | None = None) -> Organization:
        org = Organization(name=name, description=description)
        self.db.add(org)
        self.db.flush()
        return org

    def get_or_create(self, *, name: str, description: str | None = None) -> Organization:
        existing = self.get_by_name(name)
        if existing:
            return existing
        return self.create(name=name, description=description)
