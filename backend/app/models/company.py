from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import Boolean, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.organization import Organization
    from app.models.requirement import RequirementMatch


class CompanyProfile(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "company_profiles"
    __table_args__ = (
        Index("ix_company_profiles_organization_id", "organization_id"),
        Index("ix_company_profiles_credit_code", "credit_code"),
    )

    organization_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(512), nullable=False)
    credit_code: Mapped[str | None] = mapped_column(String(64))
    industry: Mapped[str | None] = mapped_column(String(128))
    synthetic: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    organization: Mapped[Organization] = relationship(back_populates="company_profiles")
    matches: Mapped[list[RequirementMatch]] = relationship(back_populates="company_profile")
