from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import DocumentType, ParseStatus
from app.models.types import EnumType

if TYPE_CHECKING:
    from app.models.organization import Organization
    from app.models.project import BidProject
    from app.models.requirement import EvidenceLink, Requirement, RequirementMatchEvidence


class Document(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "documents"
    __table_args__ = (
        Index("ix_documents_project_id_document_type", "project_id", "document_type"),
        Index("ix_documents_organization_id", "organization_id"),
        Index("ix_documents_sha256", "sha256"),
    )

    project_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("bid_projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    organization_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    document_type: Mapped[DocumentType] = mapped_column(
        EnumType(DocumentType, name="document_type"),
        nullable=False,
        default=DocumentType.other,
    )
    file_name: Mapped[str] = mapped_column(String(512), nullable=False)
    mime_type: Mapped[str | None] = mapped_column(String(128))
    storage_bucket: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    sha256: Mapped[str | None] = mapped_column(String(64))
    file_size: Mapped[int | None] = mapped_column(Integer)
    page_count: Mapped[int | None] = mapped_column(Integer)
    parse_status: Mapped[ParseStatus] = mapped_column(
        EnumType(ParseStatus, name="parse_status"),
        nullable=False,
        default=ParseStatus.pending,
        index=True,
    )
    is_scanned: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    project: Mapped[BidProject] = relationship(back_populates="documents")
    organization: Mapped[Organization] = relationship(back_populates="documents")
    versions: Mapped[list[DocumentVersion]] = relationship(back_populates="document")
    chunks: Mapped[list[DocumentChunk]] = relationship(back_populates="document")
    sourced_requirements: Mapped[list[Requirement]] = relationship(back_populates="source_document")
    evidence_links: Mapped[list[EvidenceLink]] = relationship(back_populates="document")
    match_evidence: Mapped[list[RequirementMatchEvidence]] = relationship(back_populates="document")


class DocumentVersion(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "document_versions"
    __table_args__ = (
        UniqueConstraint(
            "document_id",
            "version_number",
            name="uq_document_versions_document_id_version_number",
        ),
    )

    document_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    storage_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    sha256: Mapped[str | None] = mapped_column(String(64))
    change_reason: Mapped[str | None] = mapped_column(Text)

    document: Mapped[Document] = relationship(back_populates="versions")


class DocumentChunk(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "document_chunks"
    __table_args__ = (
        UniqueConstraint(
            "document_id",
            "chunk_index",
            name="uq_document_chunks_document_id_chunk_index",
        ),
        Index("ix_document_chunks_project_id", "project_id"),
        Index("ix_document_chunks_content_hash", "content_hash"),
    )

    document_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    project_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("bid_projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    section: Mapped[str | None] = mapped_column(String(512))
    clause_id: Mapped[str | None] = mapped_column(String(128))
    page_start: Mapped[int | None] = mapped_column(Integer)
    page_end: Mapped[int | None] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str | None] = mapped_column(String(64))
    token_count: Mapped[int | None] = mapped_column(Integer)
    bbox_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # Reserved for Qdrant point linkage; embeddings are not stored in PostgreSQL.
    qdrant_point_id: Mapped[str | None] = mapped_column(String(64))

    document: Mapped[Document] = relationship(back_populates="chunks")
    project: Mapped[BidProject] = relationship(back_populates="chunks")
    evidence_links: Mapped[list[EvidenceLink]] = relationship(back_populates="chunk")
    match_evidence: Mapped[list[RequirementMatchEvidence]] = relationship(back_populates="chunk")
