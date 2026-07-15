from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import ProjectStatus


class ProjectCreate(BaseModel):
    organization_id: UUID | None = None
    organization_name: str | None = Field(default=None, max_length=255)
    project_code: str = Field(min_length=1, max_length=128)
    project_name: str = Field(min_length=1, max_length=512)
    purchaser: str | None = None
    procurement_agency: str | None = None
    procurement_method: str | None = None
    industry: str | None = None
    region: str | None = None
    budget_cny: Decimal | None = None
    price_ceiling_cny: Decimal | None = None
    bid_deadline: datetime | None = None
    status: ProjectStatus = ProjectStatus.draft
    metadata_json: dict[str, Any] | None = None


class ProjectRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    organization_id: UUID
    project_code: str
    project_name: str
    purchaser: str | None
    procurement_agency: str | None
    procurement_method: str | None
    industry: str | None
    region: str | None
    budget_cny: Decimal | None
    price_ceiling_cny: Decimal | None
    bid_deadline: datetime | None
    status: ProjectStatus
    metadata_json: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime


class ProjectListResponse(BaseModel):
    items: list[ProjectRead]
    total: int
