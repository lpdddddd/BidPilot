import pytest
from app.models import BidProject, Document, DocumentChunk, DocumentVersion, Organization
from app.models.enums import DocumentType, ParseStatus
from sqlalchemy.exc import IntegrityError


def test_document_version_unique_constraint(db):
    org = Organization(name="Constraint Org")
    db.add(org)
    db.flush()
    project = BidProject(
        organization_id=org.id,
        project_code="C-001",
        project_name="Constraint Project",
    )
    db.add(project)
    db.flush()
    document = Document(
        project_id=project.id,
        organization_id=org.id,
        document_type=DocumentType.tender,
        file_name="a.pdf",
        storage_bucket="bidpilot-documents",
        storage_key="a.pdf",
        parse_status=ParseStatus.pending,
        is_scanned=False,
    )
    db.add(document)
    db.flush()

    db.add(
        DocumentVersion(
            document_id=document.id,
            version_number=1,
            storage_key="a-v1.pdf",
        )
    )
    db.flush()
    db.add(
        DocumentVersion(
            document_id=document.id,
            version_number=1,
            storage_key="a-v1-dup.pdf",
        )
    )
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_document_chunk_unique_constraint(db):
    org = Organization(name="Chunk Org")
    db.add(org)
    db.flush()
    project = BidProject(
        organization_id=org.id,
        project_code="C-002",
        project_name="Chunk Project",
    )
    db.add(project)
    db.flush()
    document = Document(
        project_id=project.id,
        organization_id=org.id,
        document_type=DocumentType.tender,
        file_name="b.pdf",
        storage_bucket="bidpilot-documents",
        storage_key="b.pdf",
        parse_status=ParseStatus.pending,
        is_scanned=False,
    )
    db.add(document)
    db.flush()
    db.add(
        DocumentChunk(
            document_id=document.id,
            project_id=project.id,
            chunk_index=0,
            content="hello",
        )
    )
    db.flush()
    db.add(
        DocumentChunk(
            document_id=document.id,
            project_id=project.id,
            chunk_index=0,
            content="dup",
        )
    )
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()
