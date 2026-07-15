from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import Boolean, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import MemberRole
from app.models.types import EnumType

if TYPE_CHECKING:
    from app.models.agent import AgentRun
    from app.models.company import CompanyProfile
    from app.models.conversation import Conversation
    from app.models.document import Document
    from app.models.project import BidProject


class Organization(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text)

    members: Mapped[list[OrganizationMember]] = relationship(back_populates="organization")
    projects: Mapped[list[BidProject]] = relationship(back_populates="organization")
    documents: Mapped[list[Document]] = relationship(back_populates="organization")
    company_profiles: Mapped[list[CompanyProfile]] = relationship(back_populates="organization")
    conversations: Mapped[list[Conversation]] = relationship(back_populates="organization")
    agent_runs: Mapped[list[AgentRun]] = relationship(back_populates="organization")


class User(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    memberships: Mapped[list[OrganizationMember]] = relationship(back_populates="user")
    conversations: Mapped[list[Conversation]] = relationship(back_populates="user")


class OrganizationMember(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "organization_members"
    __table_args__ = (
        UniqueConstraint("organization_id", "user_id", name="uq_organization_members_org_user"),
    )

    organization_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[MemberRole] = mapped_column(
        EnumType(MemberRole, name="member_role"),
        nullable=False,
        default=MemberRole.member,
    )

    organization: Mapped[Organization] = relationship(back_populates="members")
    user: Mapped[User] = relationship(back_populates="memberships")
