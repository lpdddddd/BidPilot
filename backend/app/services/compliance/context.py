"""Load ComplianceContext snapshots from the database."""

from __future__ import annotations

from collections import defaultdict
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.document import Document, DocumentChunk
from app.models.match_run import (
    RequirementEvidenceMatch,
    RequirementEvidenceMatchLink,
)
from app.models.project import BidProject
from app.models.proposal_draft import (
    ProposalDraft,
    ProposalDraftSource,
    ProposalDraftVersion,
)
from app.models.requirement import EvidenceLink, Requirement
from app.schemas.compliance import ComplianceContext


def load_compliance_context(
    db: Session,
    project_id: UUID,
    *,
    draft_id: UUID | None = None,
    requirement_ids: list[UUID] | None = None,
    match_ids: list[UUID] | None = None,
    draft_ids: list[UUID] | None = None,
    document_ids: list[UUID] | None = None,
    chunk_ids: list[UUID] | None = None,
    evidence_link_ids: list[UUID] | None = None,
) -> ComplianceContext:
    """Load project-scoped compliance snapshot.

    Optional explicit IDs load those objects BY ID (not only project filter) so that
    E005 can observe cross-project ownership mismatches. Default path remains
    project-scoped; when draft sources / match links point to foreign IDs they are
    also pulled in.
    """
    return load_compliance_context_for_check(
        db,
        project_id,
        draft_id=draft_id,
        requirement_ids=requirement_ids,
        match_ids=match_ids,
        draft_ids=draft_ids,
        document_ids=document_ids,
        chunk_ids=chunk_ids,
        evidence_link_ids=evidence_link_ids,
    )


def load_compliance_context_for_check(
    db: Session,
    project_id: UUID,
    *,
    draft_id: UUID | None = None,
    requirement_ids: list[UUID] | None = None,
    match_ids: list[UUID] | None = None,
    draft_ids: list[UUID] | None = None,
    document_ids: list[UUID] | None = None,
    chunk_ids: list[UUID] | None = None,
    evidence_link_ids: list[UUID] | None = None,
) -> ComplianceContext:
    project = db.get(BidProject, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")

    # --- requirements (project-scoped + explicit IDs) ---
    req_stmt = select(Requirement).where(Requirement.project_id == project_id)
    requirements = list(db.scalars(req_stmt.order_by(Requirement.created_at.asc())).all())
    requirements_by_id = {r.id: r for r in requirements}
    if requirement_ids:
        missing = [rid for rid in requirement_ids if rid not in requirements_by_id]
        if missing:
            extra = list(
                db.scalars(select(Requirement).where(Requirement.id.in_(missing))).all()
            )
            for r in extra:
                requirements_by_id[r.id] = r
                requirements.append(r)

    # --- matches ---
    matches = list(
        db.scalars(
            select(RequirementEvidenceMatch)
            .where(
                RequirementEvidenceMatch.project_id == project_id,
                RequirementEvidenceMatch.lifecycle_status == "active",
            )
            .options(selectinload(RequirementEvidenceMatch.company_links))
            .order_by(RequirementEvidenceMatch.created_at.asc())
        ).all()
    )
    matches_by_id = {m.id: m for m in matches}
    if match_ids:
        missing_m = [mid for mid in match_ids if mid not in matches_by_id]
        if missing_m:
            extra_m = list(
                db.scalars(
                    select(RequirementEvidenceMatch)
                    .where(RequirementEvidenceMatch.id.in_(missing_m))
                    .options(selectinload(RequirementEvidenceMatch.company_links))
                ).all()
            )
            for m in extra_m:
                matches_by_id[m.id] = m
                matches.append(m)

    matches_by_requirement: dict[UUID, list] = defaultdict(list)
    for match in matches:
        matches_by_requirement[match.requirement_id].append(match)

    tender_links = list(
        db.scalars(
            select(EvidenceLink)
            .join(Requirement, EvidenceLink.requirement_id == Requirement.id)
            .where(Requirement.project_id == project_id)
            .order_by(EvidenceLink.created_at.asc())
        ).all()
    )
    if evidence_link_ids:
        have = {lnk.id for lnk in tender_links}
        missing_l = [lid for lid in evidence_link_ids if lid not in have]
        if missing_l:
            tender_links.extend(
                list(
                    db.scalars(
                        select(EvidenceLink).where(EvidenceLink.id.in_(missing_l))
                    ).all()
                )
            )

    company_links: list[RequirementEvidenceMatchLink] = []
    for match in matches:
        company_links.extend(list(match.company_links or []))

    # --- drafts ---
    draft_query = select(ProposalDraft).where(ProposalDraft.project_id == project_id)
    if draft_id is not None:
        draft_query = draft_query.where(ProposalDraft.id == draft_id)
    drafts = list(db.scalars(draft_query.order_by(ProposalDraft.created_at.asc())).all())
    drafts_by_id = {d.id: d for d in drafts}
    if draft_id is not None and draft_id not in drafts_by_id:
        # May be foreign — still load by id for E005
        foreign = db.get(ProposalDraft, draft_id)
        if foreign is None:
            raise HTTPException(status_code=404, detail="proposal draft not found")
        drafts.append(foreign)
        drafts_by_id[foreign.id] = foreign
    if draft_ids:
        missing_d = [did for did in draft_ids if did not in drafts_by_id]
        if missing_d:
            for d in db.scalars(
                select(ProposalDraft).where(ProposalDraft.id.in_(missing_d))
            ).all():
                drafts.append(d)
                drafts_by_id[d.id] = d

    draft_id_list = [d.id for d in drafts]
    versions: list[ProposalDraftVersion] = []
    sources: list[ProposalDraftSource] = []
    if draft_id_list:
        versions = list(
            db.scalars(
                select(ProposalDraftVersion)
                .where(ProposalDraftVersion.draft_id.in_(draft_id_list))
                .order_by(
                    ProposalDraftVersion.draft_id.asc(),
                    ProposalDraftVersion.version_number.asc(),
                )
            ).all()
        )
        version_ids = [v.id for v in versions]
        if version_ids:
            sources = list(
                db.scalars(
                    select(ProposalDraftSource)
                    .where(ProposalDraftSource.draft_version_id.in_(version_ids))
                    .order_by(ProposalDraftSource.created_at.asc())
                ).all()
            )

    # Collect foreign IDs referenced by sources / company links so E005 can see them
    extra_doc_ids: set[UUID] = set(document_ids or [])
    extra_chunk_ids: set[UUID] = set(chunk_ids or [])
    extra_req_ids: set[UUID] = set()
    extra_match_ids: set[UUID] = set()
    for src in sources:
        if src.requirement_id and src.requirement_id not in requirements_by_id:
            extra_req_ids.add(src.requirement_id)
        if src.match_id and src.match_id not in matches_by_id:
            extra_match_ids.add(src.match_id)
        loc = src.location_json if isinstance(src.location_json, dict) else {}
        for key in ("document_id", "doc_id", "chunk_id"):
            raw = loc.get(key)
            if not raw:
                continue
            try:
                uid = UUID(str(raw))
            except (TypeError, ValueError):
                continue
            if key == "chunk_id":
                extra_chunk_ids.add(uid)
            else:
                extra_doc_ids.add(uid)
    for link in company_links:
        if getattr(link, "document_id", None):
            extra_doc_ids.add(link.document_id)
        if getattr(link, "chunk_id", None):
            extra_chunk_ids.add(link.chunk_id)

    if extra_req_ids:
        for r in db.scalars(
            select(Requirement).where(Requirement.id.in_(list(extra_req_ids)))
        ).all():
            if r.id not in requirements_by_id:
                requirements.append(r)
                requirements_by_id[r.id] = r
    if extra_match_ids:
        for m in db.scalars(
            select(RequirementEvidenceMatch)
            .where(RequirementEvidenceMatch.id.in_(list(extra_match_ids)))
            .options(selectinload(RequirementEvidenceMatch.company_links))
        ).all():
            if m.id in matches_by_id:
                continue
            matches_by_id[m.id] = m
            # Active same-project matches participate in coverage / draft rules.
            # Non-active or foreign matches stay in matches_by_id for D003/E005 only.
            if (
                getattr(m, "lifecycle_status", "active") == "active"
                and m.project_id == project_id
            ):
                matches.append(m)
                matches_by_requirement[m.requirement_id].append(m)
                company_links.extend(list(m.company_links or []))
            else:
                company_links.extend(list(m.company_links or []))
                # Include in evidence_matches so E005 can flag foreign project_id
                if m.project_id != project_id:
                    matches.append(m)

    documents = list(
        db.scalars(select(Document).where(Document.project_id == project_id)).all()
    )
    documents_by_id = {d.id: d for d in documents}
    if extra_doc_ids:
        missing_docs = [i for i in extra_doc_ids if i not in documents_by_id]
        if missing_docs:
            for d in db.scalars(
                select(Document).where(Document.id.in_(missing_docs))
            ).all():
                documents_by_id[d.id] = d

    chunks = list(
        db.scalars(
            select(DocumentChunk).where(DocumentChunk.project_id == project_id)
        ).all()
    )
    chunks_by_id = {c.id: c for c in chunks}
    if extra_chunk_ids:
        missing_chunks = [i for i in extra_chunk_ids if i not in chunks_by_id]
        if missing_chunks:
            for c in db.scalars(
                select(DocumentChunk).where(DocumentChunk.id.in_(missing_chunks))
            ).all():
                chunks_by_id[c.id] = c

    return ComplianceContext(
        project_id=project_id,
        draft_id=draft_id,
        project=project,
        requirements=requirements,
        evidence_matches=matches,
        tender_evidence_links=tender_links,
        company_match_links=company_links,
        drafts=drafts,
        draft_versions=versions,
        draft_sources=sources,
        documents_by_id=documents_by_id,
        chunks_by_id=chunks_by_id,
        requirements_by_id=requirements_by_id,
        matches_by_id=matches_by_id,
        matches_by_requirement_id=dict(matches_by_requirement),
        metadata={
            "requirement_count": len(requirements),
            "active_match_count": len(matches),
            "draft_count": len(drafts),
            "foreign_document_ids": [
                str(i) for i in extra_doc_ids if i not in {d.id for d in documents}
            ],
        },
    )
