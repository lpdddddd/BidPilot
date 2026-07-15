from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from bidpilot_data.logging import get_logger, log_stats
from bidpilot_data.settings import get_settings, load_taxonomy
from bidpilot_data.utils import read_jsonl, write_json

log = get_logger(__name__)


def _backend_root() -> Path:
    settings = get_settings()
    candidate = settings.repo_root / "backend"
    if (candidate / "app").exists():
        return candidate
    # Fallback when tests override repo_root to a temp directory.
    return Path(__file__).resolve().parents[3] / "backend"


def _ensure_backend_path() -> None:
    backend = _backend_root()
    if str(backend) not in sys.path:
        sys.path.insert(0, str(backend))


def _backend_session() -> Session:
    settings = get_settings()
    _ensure_backend_path()
    engine = create_engine(settings.database_url, future=True)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)()


def _map_category(cat: str, taxonomy: dict[str, Any]) -> str:
    return taxonomy.get("db_category_map", {}).get(cat, "project_info")


def _map_parse_status(status: str) -> str:
    if status == "ocr_required":
        return "failed"
    return status if status in {"pending", "processing", "success", "partial", "failed"} else "failed"


def import_documents(*, dry_run: bool = False) -> dict[str, Any]:
    settings = get_settings()
    rows = read_jsonl(settings.datasets_root / "manifests" / "documents.jsonl")
    stats = {"seen": len(rows), "created": 0, "skipped": 0, "errors": []}
    if dry_run:
        return stats
    from app.models import BidProject, Document, Organization
    from app.models.enums import DocumentType, ParseStatus, ProjectStatus

    db = _backend_session()
    try:
        org = db.scalar(select(Organization).where(Organization.name == "BidPilot Data Pipeline Org"))
        if org is None:
            org = Organization(name="BidPilot Data Pipeline Org", description="Imported by data_pipeline")
            db.add(org)
            db.flush()
        for row in rows:
            project_id = UUID(row["project_id"])
            project = db.get(BidProject, project_id)
            if project is None:
                project = BidProject(
                    id=project_id,
                    organization_id=org.id,
                    project_code=row.get("project_code") or str(project_id)[:8],
                    project_name=row.get("project_name") or f"Project {project_id}",
                    status=ProjectStatus.draft,
                    metadata_json={"source_url": row.get("source_url")},
                )
                db.add(project)
                db.flush()
            doc_id = UUID(row["document_id"])
            if db.get(Document, doc_id) is not None:
                stats["skipped"] += 1
                continue
            db.add(
                Document(
                    id=doc_id,
                    project_id=project_id,
                    organization_id=org.id,
                    document_type=DocumentType(row.get("document_type", "other")),
                    file_name=row.get("original_filename") or "unknown",
                    mime_type=row.get("mime_type"),
                    storage_bucket="filesystem",
                    storage_key=row.get("storage_path") or "",
                    sha256=row.get("sha256"),
                    file_size=row.get("file_size"),
                    page_count=row.get("page_count"),
                    parse_status=ParseStatus(_map_parse_status(row.get("parse_status", "pending"))),
                    is_scanned=row.get("parse_status") == "ocr_required",
                    metadata_json={"source_url": row.get("source_url"), "source_id": row.get("source_id")},
                )
            )
            stats["created"] += 1
        db.commit()
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        stats["errors"].append(str(exc))
        write_json(settings.datasets_root / "reports" / "db_import_documents_error.json", stats)
        raise
    finally:
        db.close()
    log_stats(log, "db_import_documents", stats)
    return stats


def import_chunks(*, dry_run: bool = False) -> dict[str, Any]:
    settings = get_settings()
    rows = read_jsonl(settings.datasets_root / "interim" / "chunks" / "chunks.jsonl")
    stats = {"seen": len(rows), "created": 0, "skipped": 0}
    if dry_run:
        return stats
    from app.models import DocumentChunk

    db = _backend_session()
    try:
        for row in rows:
            cid = UUID(row["chunk_id"])
            if db.get(DocumentChunk, cid) is not None:
                stats["skipped"] += 1
                continue
            db.add(
                DocumentChunk(
                    id=cid,
                    document_id=UUID(row["document_id"]),
                    project_id=UUID(row["project_id"]),
                    chunk_index=row["chunk_index"],
                    section=row.get("section_path"),
                    clause_id=row.get("clause_number"),
                    page_start=row.get("page_start"),
                    page_end=row.get("page_end"),
                    content=row["text"],
                    content_hash=row.get("content_hash"),
                    token_count=row.get("token_count"),
                    metadata_json={},
                )
            )
            stats["created"] += 1
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    log_stats(log, "db_import_chunks", stats)
    return stats


def import_requirements(*, dry_run: bool = False) -> dict[str, Any]:
    settings = get_settings()
    taxonomy = load_taxonomy()
    rows = read_jsonl(settings.datasets_root / "gold" / "requirements.jsonl") + read_jsonl(
        settings.datasets_root / "silver" / "requirements.jsonl"
    )
    stats = {"seen": len(rows), "created": 0, "skipped": 0}
    if dry_run:
        return stats
    from app.models import Requirement
    from app.models.enums import QualityLevel, RequirementCategory, ReviewStatus, RiskLevel

    db = _backend_session()
    try:
        for row in rows:
            rid = UUID(row["requirement_id"])
            if db.get(Requirement, rid) is not None:
                stats["skipped"] += 1
                continue
            # Never write gold unless reviewer present
            ql = row.get("quality_level", "silver")
            if ql == "gold" and not row.get("reviewer"):
                ql = "silver"
            rs = row.get("review_status", "unreviewed")
            if rs == "pending":
                rs = "unreviewed"
            db.add(
                Requirement(
                    id=rid,
                    project_id=UUID(row["project_id"]),
                    source_document_id=UUID(row["document_id"]) if row.get("document_id") else None,
                    requirement_code=row.get("requirement_code"),
                    category=RequirementCategory(_map_category(row.get("category", "other"), taxonomy)),
                    title=row.get("title") or row.get("normalized_requirement", "")[:200],
                    normalized_requirement=row.get("normalized_requirement"),
                    mandatory=bool(row.get("mandatory")),
                    score=row.get("score"),
                    risk_level=RiskLevel(row.get("risk_level", "medium")),
                    source_page=row.get("source_page"),
                    source_section=row.get("source_section"),
                    evidence_required_json=row.get("evidence_required"),
                    quality_level=QualityLevel(ql),
                    review_status=ReviewStatus(rs if rs in {"reviewed", "auto_checked", "unreviewed"} else "unreviewed"),
                    metadata_json={
                        "annotation_id": row.get("annotation_id"),
                        "generator": row.get("generator"),
                        "source_url": row.get("source_url"),
                        "chunk_id": row.get("chunk_id"),
                    },
                )
            )
            stats["created"] += 1
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    log_stats(log, "db_import_requirements", stats)
    return stats


def import_company_profiles(*, dry_run: bool = False) -> dict[str, Any]:
    settings = get_settings()
    rows = read_jsonl(settings.datasets_root / "silver" / "company_profiles.jsonl")
    stats = {"seen": len(rows), "created": 0, "skipped": 0}
    if dry_run:
        return stats
    from app.models import CompanyProfile, Organization

    db = _backend_session()
    try:
        org = db.scalar(select(Organization).where(Organization.name == "BidPilot Data Pipeline Org"))
        if org is None:
            org = Organization(name="BidPilot Data Pipeline Org")
            db.add(org)
            db.flush()
        for row in rows:
            cid = UUID(row["company_profile_id"])
            if db.get(CompanyProfile, cid) is not None:
                stats["skipped"] += 1
                continue
            db.add(
                CompanyProfile(
                    id=cid,
                    organization_id=org.id,
                    name=row["name"],
                    credit_code=row.get("credit_code"),
                    industry=row.get("industry"),
                    synthetic=bool(row.get("synthetic", False)),
                    metadata_json=row.get("metadata") or {},
                )
            )
            stats["created"] += 1
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    log_stats(log, "db_import_company_profiles", stats)
    return stats


def import_matches(*, dry_run: bool = False) -> dict[str, Any]:
    settings = get_settings()
    rows = read_jsonl(settings.datasets_root / "silver" / "requirement_matches.jsonl")
    stats = {"seen": len(rows), "created": 0, "skipped": 0}
    if dry_run:
        return stats
    from app.models import RequirementMatch
    from app.models.enums import MatchStatus, QualityLevel, ReviewStatus, RiskLevel

    db = _backend_session()
    try:
        for row in rows:
            mid = UUID(row["match_id"])
            if db.get(RequirementMatch, mid) is not None:
                stats["skipped"] += 1
                continue
            db.add(
                RequirementMatch(
                    id=mid,
                    requirement_id=UUID(row["requirement_id"]),
                    company_profile_id=UUID(row["company_profile_id"]),
                    status=MatchStatus(row["status"]),
                    reason=row.get("reason"),
                    risk_level=RiskLevel(row["risk_level"]) if row.get("risk_level") else None,
                    recommended_action=row.get("recommended_action"),
                    confidence=row.get("confidence"),
                    quality_level=QualityLevel(row.get("quality_level", "silver")),
                    review_status=ReviewStatus(
                        "unreviewed" if row.get("review_status") == "pending" else row.get("review_status", "unreviewed")
                    ),
                )
            )
            stats["created"] += 1
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    log_stats(log, "db_import_matches", stats)
    return stats
