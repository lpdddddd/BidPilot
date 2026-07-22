"""Pydantic I/O tool wrappers for agent retrieval / context / extract / match / draft."""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.document import Document
from app.models.enums import EvidenceMatchStatus
from app.models.match_run import RequirementEvidenceMatch
from app.models.project import BidProject
from app.models.requirement import Requirement
from app.schemas.extraction import ExtractionStartRequest
from app.schemas.match import MatchStartRequest
from app.schemas.proposal_draft import ProposalDraftCreateRequest
from app.schemas.search import SearchRequest, SearchResponse
from app.services.proposal_draft_service import ProposalDraftService
from app.services.requirement_extraction_service import RequirementExtractionService
from app.services.requirement_match_service import RequirementMatchService
from app.services.retrieval import RetrievalService

# Optional injectable retrieval for tests (Fake retrieval).
RetrievalFn = Callable[[UUID, SearchRequest], SearchResponse]


class ToolResult(BaseModel):
    ok: bool = True
    summary: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    detail: str | None = None


class GetProjectContextInput(BaseModel):
    project_id: UUID
    selected_document_ids: list[UUID] = Field(default_factory=list)


class SearchEvidenceInput(BaseModel):
    project_id: UUID
    query: str = Field(min_length=1, max_length=512)
    top_k: int = Field(default=8, ge=1, le=20)
    document_ids: list[str] = Field(default_factory=list)
    document_types: list[str] = Field(default_factory=list)


class ExtractRequirementsInput(BaseModel):
    project_id: UUID
    document_ids: list[UUID] = Field(default_factory=list)
    force: bool = False
    use_existing: bool = True


class MatchCompanyEvidenceInput(BaseModel):
    project_id: UUID
    requirement_ids: list[UUID] = Field(default_factory=list)
    force: bool = False
    use_existing: bool = True


class GenerateProposalDraftInput(BaseModel):
    project_id: UUID
    requirement_ids: list[UUID]
    title: str = "Agent response draft"
    idempotency_key: str | None = None
    risk_only: bool = False
    created_by: str = "agent"


class GetProposalDraftInput(BaseModel):
    project_id: UUID
    draft_id: UUID


class ListProposalDraftsInput(BaseModel):
    project_id: UUID


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def get_project_context(db: Session, payload: GetProjectContextInput) -> ToolResult:
    project = db.get(BidProject, payload.project_id)
    if project is None:
        return ToolResult(ok=False, detail="project not found", summary="missing project")

    doc_stmt = select(Document).where(Document.project_id == payload.project_id)
    if payload.selected_document_ids:
        doc_stmt = doc_stmt.where(Document.id.in_(payload.selected_document_ids))
    documents = list(db.scalars(doc_stmt).all())
    req_count = db.scalar(
        select(func.count())
        .select_from(Requirement)
        .where(Requirement.project_id == payload.project_id)
    )
    return ToolResult(
        summary=f"docs={len(documents)} requirements={req_count or 0}",
        data={
            "project_id": str(project.id),
            "organization_id": str(project.organization_id),
            "project_name": project.project_name,
            "document_ids": [str(d.id) for d in documents],
            "document_count": len(documents),
            "requirement_count": int(req_count or 0),
        },
    )


def search_evidence(
    db: Session,
    payload: SearchEvidenceInput,
    *,
    retrieval_fn: RetrievalFn | None = None,
) -> ToolResult:
    request = SearchRequest(
        query=payload.query,
        top_k=payload.top_k,
        document_ids=payload.document_ids,
        document_types=payload.document_types,
    )
    started = time.perf_counter()
    try:
        if retrieval_fn is not None:
            response = retrieval_fn(payload.project_id, request)
        else:
            response = RetrievalService(db).search(payload.project_id, request)
    except Exception as exc:  # noqa: BLE001
        return ToolResult(
            ok=False,
            detail=f"{type(exc).__name__}: {exc}",
            summary="search failed",
        )
    duration_ms = int((time.perf_counter() - started) * 1000)
    chunks = [
        {
            "chunk_id": item.chunk_id,
            "score": item.rerank_score if item.rerank_score is not None else item.rrf_score,
            "page_start": item.page_start,
            "page_end": item.page_end,
            "section": item.section,
            "document_id": item.document_id,
            "document_title": item.file_name,
            "file_name": item.file_name,
            "summary": (item.content or "")[:160],
            "conclusion_summary": (item.content or "")[:160],
        }
        for item in response.results
    ]
    return ToolResult(
        summary=f"retrieved={len(chunks)} duration_ms={duration_ms}",
        data={"chunks": chunks, "query": response.query},
    )


def extract_requirements(
    db: Session,
    payload: ExtractRequirementsInput,
    *,
    llm: Any | None = None,
) -> ToolResult:
    if payload.use_existing and not payload.force:
        existing = list(
            db.scalars(
                select(Requirement).where(Requirement.project_id == payload.project_id)
            ).all()
        )
        if existing:
            items = [
                {
                    "id": str(r.id),
                    "title": r.title,
                    "category": r.category.value if r.category else None,
                    "mandatory": r.mandatory,
                }
                for r in existing
            ]
            return ToolResult(
                summary=f"existing_requirements={len(items)}",
                data={"requirements": items, "source": "existing"},
            )

    service = (
        RequirementExtractionService(db, llm=llm)
        if llm is not None
        else (RequirementExtractionService(db))
    )
    run = service.start_extraction(
        payload.project_id,
        ExtractionStartRequest(
            document_ids=payload.document_ids,
            force=payload.force,
        ),
    )
    service.execute_run(run.id)
    refreshed = service.get_run(payload.project_id, run.id)
    reqs = service.list_requirements(payload.project_id, limit=200)
    items = [
        {
            "id": str(r.id),
            "title": r.title,
            "category": r.category.value if hasattr(r.category, "value") else r.category,
            "mandatory": r.mandatory,
        }
        for r in reqs.items
    ]
    return ToolResult(
        summary=f"extraction_status={refreshed.status} requirements={len(items)}",
        data={
            "requirements": items,
            "extraction_run_id": str(run.id),
            "source": "extraction",
        },
    )


def match_company_evidence(
    db: Session,
    payload: MatchCompanyEvidenceInput,
    *,
    llm: Any | None = None,
) -> ToolResult:
    if payload.use_existing and not payload.force:
        stmt = select(RequirementEvidenceMatch).where(
            RequirementEvidenceMatch.project_id == payload.project_id,
            RequirementEvidenceMatch.lifecycle_status == "active",
        )
        if payload.requirement_ids:
            stmt = stmt.where(RequirementEvidenceMatch.requirement_id.in_(payload.requirement_ids))
        existing = list(db.scalars(stmt).all())
        if existing:
            items = [
                {
                    "id": str(m.id),
                    "requirement_id": str(m.requirement_id),
                    "status": m.status.value if hasattr(m.status, "value") else str(m.status),
                    "review_status": (
                        m.review_status.value
                        if getattr(m, "review_status", None) is not None
                        and hasattr(m.review_status, "value")
                        else getattr(m, "review_status", None)
                    ),
                }
                for m in existing
            ]
            insufficient = [
                i
                for i in items
                if i["status"]
                in {
                    EvidenceMatchStatus.insufficient_evidence.value,
                    EvidenceMatchStatus.conflicting_evidence.value,
                }
            ]
            return ToolResult(
                summary=(f"existing_matches={len(items)} insufficient={len(insufficient)}"),
                data={
                    "matches": items,
                    "insufficient_count": len(insufficient),
                    "source": "existing",
                },
            )

    service = (
        RequirementMatchService(db, llm=llm) if llm is not None else RequirementMatchService(db)
    )
    run = service.start_matching(
        payload.project_id,
        MatchStartRequest(
            requirement_ids=payload.requirement_ids,
            force=payload.force,
        ),
    )
    service.execute_run(run.id)
    refreshed = service.get_run(payload.project_id, run.id)
    # Re-query matches
    stmt = select(RequirementEvidenceMatch).where(
        RequirementEvidenceMatch.project_id == payload.project_id,
        RequirementEvidenceMatch.lifecycle_status == "active",
    )
    matches = list(db.scalars(stmt).all())
    items = [
        {
            "id": str(m.id),
            "requirement_id": str(m.requirement_id),
            "status": m.status.value if hasattr(m.status, "value") else str(m.status),
            "review_status": (
                m.review_status.value
                if getattr(m, "review_status", None) is not None
                and hasattr(m.review_status, "value")
                else None
            ),
        }
        for m in matches
    ]
    insufficient = [
        i
        for i in items
        if i["status"]
        in {
            EvidenceMatchStatus.insufficient_evidence.value,
            EvidenceMatchStatus.conflicting_evidence.value,
        }
    ]
    return ToolResult(
        summary=(
            f"match_status={refreshed.status} matches={len(items)} insufficient={len(insufficient)}"
        ),
        data={
            "matches": items,
            "insufficient_count": len(insufficient),
            "match_run_id": str(run.id),
            "source": "matching",
        },
    )


def generate_proposal_draft(
    db: Session,
    payload: GenerateProposalDraftInput,
    *,
    llm: Any | None = None,
) -> ToolResult:
    if payload.risk_only:
        # Deterministic risk-only draft record via service path when possible;
        # if no confirmed matches, return a synthetic risk summary without claims.
        return ToolResult(
            summary="risk_only_draft",
            data={
                "draft_ids": [],
                "risk_only": True,
                "content_preview": (
                    "风险提示：存在关键资格/强制条件问题。"
                    "本稿仅列风险与材料缺口，禁止作出满足性承诺，仅供人工复核。"
                ),
                "forbid_satisfaction_claims": True,
            },
        )

    service = ProposalDraftService(db, llm=llm) if llm is not None else ProposalDraftService(db)
    run = service.start_generation(
        payload.project_id,
        ProposalDraftCreateRequest(
            title=payload.title,
            requirement_ids=payload.requirement_ids,
            created_by=payload.created_by,
        ),
        idempotency_key=payload.idempotency_key,
    )
    # Prefer sync execute when available
    execute = getattr(service, "execute_run", None)
    if callable(execute):
        execute(run.id)
        run = service.get_run(payload.project_id, run.id)  # type: ignore[attr-defined]
    draft_id = getattr(run, "draft_id", None)
    return ToolResult(
        summary=f"draft_run={run.status} draft_id={draft_id}",
        data={
            "draft_ids": [str(draft_id)] if draft_id else [],
            "generation_run_id": str(run.id),
            "status": str(run.status),
        },
    )


def get_proposal_draft(db: Session, payload: GetProposalDraftInput) -> ToolResult:
    service = ProposalDraftService(db)
    draft = service.get_draft(payload.project_id, payload.draft_id)
    return ToolResult(
        summary=f"draft={draft.id}",
        data={"draft": draft.model_dump(mode="json")},
    )


def list_proposal_drafts(db: Session, payload: ListProposalDraftsInput) -> ToolResult:
    service = ProposalDraftService(db)
    listed = service.list_drafts(payload.project_id)
    return ToolResult(
        summary=f"drafts={len(listed.items)}",
        data={"items": [i.model_dump(mode="json") for i in listed.items]},
    )


__all__ = [
    "ExtractRequirementsInput",
    "GenerateProposalDraftInput",
    "GetProjectContextInput",
    "GetProposalDraftInput",
    "ListProposalDraftsInput",
    "MatchCompanyEvidenceInput",
    "RetrievalFn",
    "SearchEvidenceInput",
    "ToolResult",
    "extract_requirements",
    "generate_proposal_draft",
    "get_project_context",
    "get_proposal_draft",
    "list_proposal_drafts",
    "match_company_evidence",
    "search_evidence",
]
