"""Citation extraction and validation for evaluation case results."""

from __future__ import annotations

from uuid import uuid4

from app.models import BidProject, Document, DocumentChunk, Organization
from app.models.enums import DocumentType, ParseStatus
from app.services.evaluation.citations import (
    extract_raw_citations,
    validate_citation,
    validate_citations_for_result,
)
from app.services.evaluation.targets.base import TargetResult
from sqlalchemy.orm import Session


def _seed(
    db: Session,
) -> tuple[BidProject, BidProject, Document, DocumentChunk, Document, DocumentChunk]:
    org_a = Organization(name=f"OrgA-{uuid4().hex[:6]}")
    org_b = Organization(name=f"OrgB-{uuid4().hex[:6]}")
    db.add_all([org_a, org_b])
    db.flush()
    p_run = BidProject(organization_id=org_a.id, project_code="RUN", project_name="Run")
    p_other = BidProject(organization_id=org_b.id, project_code="OTH", project_name="Other")
    db.add_all([p_run, p_other])
    db.flush()

    doc = Document(
        project_id=p_run.id,
        organization_id=org_a.id,
        document_type=DocumentType.tender,
        file_name="tender.pdf",
        storage_bucket="bidpilot-documents",
        storage_key=f"{p_run.id}/tender.pdf",
        parse_status=ParseStatus.success,
    )
    db.add(doc)
    db.flush()
    chunk = DocumentChunk(
        document_id=doc.id,
        project_id=p_run.id,
        chunk_index=0,
        content="资质条款",
        content_hash="c1",
        page_start=2,
        page_end=3,
        section="第二章",
    )
    db.add(chunk)
    db.flush()

    other_doc = Document(
        project_id=p_other.id,
        organization_id=org_b.id,
        document_type=DocumentType.tender,
        file_name="other.pdf",
        storage_bucket="bidpilot-documents",
        storage_key=f"{p_other.id}/other.pdf",
        parse_status=ParseStatus.success,
    )
    db.add(other_doc)
    db.flush()
    other_chunk = DocumentChunk(
        document_id=other_doc.id,
        project_id=p_other.id,
        chunk_index=0,
        content="other",
        content_hash="c2",
        page_start=1,
        page_end=1,
    )
    db.add(other_chunk)
    db.flush()
    return p_run, p_other, doc, chunk, other_doc, other_chunk


def test_valid_citation(db: Session):
    project, _, doc, chunk, _, _ = _seed(db)
    db.commit()
    row = validate_citation(
        db,
        project_id=project.id,
        citation={"chunk_id": str(chunk.id), "page": 2},
    )
    assert row["valid"] is True
    assert row["document_id"] == str(doc.id)
    assert row["page"] == 2
    assert row["detail_url"] and str(chunk.id) in row["detail_url"]


def test_chunk_missing(db: Session):
    project, _, _, _, _, _ = _seed(db)
    db.commit()
    row = validate_citation(
        db,
        project_id=project.id,
        citation={"chunk_id": str(uuid4())},
    )
    assert row["valid"] is False
    assert row["invalid_reason"] == "chunk_not_found_or_forbidden"


def test_document_missing(db: Session):
    project, _, _, _, _, _ = _seed(db)
    db.commit()
    row = validate_citation(
        db,
        project_id=project.id,
        citation={"document_id": str(uuid4())},
    )
    assert row["valid"] is False
    assert row["invalid_reason"] == "document_not_found_or_forbidden"


def test_invalid_page_and_out_of_range(db: Session):
    project, _, _, chunk, _, _ = _seed(db)
    db.commit()
    bad_type = validate_citation(
        db,
        project_id=project.id,
        citation={"chunk_id": str(chunk.id), "page": "not-a-page"},
    )
    assert bad_type["valid"] is False
    assert bad_type["invalid_reason"] == "invalid_page"

    out_of_range = validate_citation(
        db,
        project_id=project.id,
        citation={"chunk_id": str(chunk.id), "page": 99},
    )
    assert out_of_range["valid"] is False
    assert out_of_range["invalid_reason"] == "page_out_of_range"


def test_chunk_document_mismatch(db: Session):
    project, _, doc, chunk, _, _ = _seed(db)
    db.commit()
    row = validate_citation(
        db,
        project_id=project.id,
        citation={"chunk_id": str(chunk.id), "document_id": str(uuid4())},
    )
    assert row["valid"] is False
    assert row["invalid_reason"] == "chunk_document_mismatch"


def test_cross_project_chunk_forbidden(db: Session):
    project, other_project, _, _, _, other_chunk = _seed(db)
    db.commit()
    row = validate_citation(
        db,
        project_id=project.id,
        citation={"chunk_id": str(other_chunk.id)},
    )
    assert row["valid"] is False
    assert row["invalid_reason"] == "chunk_not_found_or_forbidden"
    assert row["project_id"] == str(project.id)


def test_missing_document_or_chunk(db: Session):
    project, _, _, _, _, _ = _seed(db)
    db.commit()
    row = validate_citation(db, project_id=project.id, citation={})
    assert row["valid"] is False
    assert row["invalid_reason"] == "missing_document_or_chunk"


def test_empty_citations_snapshot(db: Session):
    project, _, _, _, _, _ = _seed(db)
    db.commit()
    assert validate_citations_for_result(db, project_id=project.id, response_snapshot=None) == []
    assert (
        validate_citations_for_result(
            db, project_id=project.id, response_snapshot={"citations": [], "output": {}}
        )
        == []
    )


def test_target_result_snapshot_extract_validate_e2e(db: Session):
    project, _, doc, chunk, _, other_chunk = _seed(db)
    db.commit()

    tres = TargetResult(
        ok=True,
        output={
            "answer": "answer text",
            "citations": [{"chunk_id": str(chunk.id)}],
        },
        citations=[{"chunk_id": str(chunk.id), "document_id": str(doc.id), "page": 2}],
        retrieved_chunk_ids=[str(chunk.id)],
    )
    snapshot = tres.to_response_snapshot()
    assert snapshot["citations"]
    assert snapshot["retrieved_chunk_ids"] == [str(chunk.id)]

    extracted = extract_raw_citations(snapshot)
    assert any(c.get("chunk_id") == str(chunk.id) for c in extracted)

    validated = validate_citations_for_result(db, project_id=project.id, response_snapshot=snapshot)
    assert len(validated) >= 1
    assert any(v["valid"] for v in validated if v["chunk_id"] == str(chunk.id))

    snapshot_with_bad = {
        **snapshot,
        "citations": snapshot["citations"]
        + [{"chunk_id": str(other_chunk.id), "document_id": str(uuid4())}],
    }
    mixed = validate_citations_for_result(
        db, project_id=project.id, response_snapshot=snapshot_with_bad
    )
    reasons = {row["chunk_id"]: row["invalid_reason"] for row in mixed if not row["valid"]}
    assert str(other_chunk.id) in reasons
    assert reasons[str(other_chunk.id)] == "chunk_not_found_or_forbidden"


def test_extract_raw_citations_nested_output_fallback():
    snapshot = {
        "output": {
            "citations": [{"chunk_id": "nested-1"}],
            "retrieved_chunk_ids": ["rid-1"],
        }
    }
    raw = extract_raw_citations(snapshot)
    chunk_ids = {c.get("chunk_id") for c in raw}
    assert "nested-1" in chunk_ids
    assert "rid-1" in chunk_ids
