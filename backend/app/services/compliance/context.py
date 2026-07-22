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
) -> ComplianceContext:
    project = db.get(BidProject, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")

    requirements = list(
        db.scalars(
            select(Requirement)
            .where(Requirement.project_id == project_id)
            .order_by(Requirement.created_at.asc())
        ).all()
    )
    requirements_by_id = {r.id: r for r in requirements}

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

    company_links: list[RequirementEvidenceMatchLink] = []
    for match in matches:
        company_links.extend(list(match.company_links or []))

    draft_query = select(ProposalDraft).where(ProposalDraft.project_id == project_id)
    if draft_id is not None:
        draft_query = draft_query.where(ProposalDraft.id == draft_id)
    drafts = list(db.scalars(draft_query.order_by(ProposalDraft.created_at.asc())).all())
    if draft_id is not None and not drafts:
        raise HTTPException(status_code=404, detail="proposal draft not found")

    draft_ids = [d.id for d in drafts]
    versions: list[ProposalDraftVersion] = []
    sources: list[ProposalDraftSource] = []
    if draft_ids:
        versions = list(
            db.scalars(
                select(ProposalDraftVersion)
                .where(ProposalDraftVersion.draft_id.in_(draft_ids))
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

    documents = list(
        db.scalars(select(Document).where(Document.project_id == project_id)).all()
    )
    chunks = list(
        db.scalars(
            select(DocumentChunk).where(DocumentChunk.project_id == project_id)
        ).all()
    )

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
        documents_by_id={d.id: d for d in documents},
        chunks_by_id={c.id: c for c in chunks},
        requirements_by_id=requirements_by_id,
        matches_by_id=matches_by_id,
        matches_by_requirement_id=dict(matches_by_requirement),
        metadata={
            "requirement_count": len(requirements),
            "active_match_count": len(matches),
            "draft_count": len(drafts),
        },
    )
