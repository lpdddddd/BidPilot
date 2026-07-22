from app.db.base import Base
from app.models import BidProject, Document, DocumentChunk, Organization, Requirement
from app.models.enums import RequirementCategory, RiskLevel
from sqlalchemy import inspect


def test_all_expected_tables_registered():
    expected = {
        "organizations",
        "users",
        "organization_members",
        "bid_projects",
        "documents",
        "document_versions",
        "document_chunks",
        "requirements",
        "evidence_links",
        "company_profiles",
        "requirement_matches",
        "requirement_match_evidence",
        "conversations",
        "messages",
        "agent_runs",
        "agent_steps",
        "tool_calls",
    }
    assert expected.issubset(set(Base.metadata.tables))
    assert "agent_checkpoints" in Base.metadata.tables


def test_document_chunk_unique_and_qdrant_point_reserved(engine):
    insp = inspect(engine)
    assert "document_chunks" in insp.get_table_names()
    cols = {c["name"] for c in insp.get_columns("document_chunks")}
    assert "qdrant_point_id" in cols
    uq = {uq["name"] for uq in insp.get_unique_constraints("document_chunks")}
    assert "uq_document_chunks_document_id_chunk_index" in uq


def test_create_org_project_requirement(db):
    org = Organization(name="Model Test Org")
    db.add(org)
    db.flush()
    project = BidProject(
        organization_id=org.id,
        project_code="MT-001",
        project_name="Model Test",
    )
    db.add(project)
    db.flush()
    req = Requirement(
        project_id=project.id,
        category=RequirementCategory.qualification,
        title="需要营业执照",
        mandatory=True,
        risk_level=RiskLevel.high,
    )
    db.add(req)
    db.commit()
    assert req.id is not None
    assert Document.__tablename__ == "documents"
    assert DocumentChunk.__tablename__ == "document_chunks"
