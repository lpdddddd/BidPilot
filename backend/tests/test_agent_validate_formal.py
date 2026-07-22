"""Formal draft validation via check_draft_compliance / ComplianceService."""

from __future__ import annotations

import sys
from uuid import uuid4

import pytest
from app.agent.nodes._helpers import AgentRuntime, reset_runtime, set_runtime
from app.agent.nodes.revise_draft import revise_draft
from app.agent.nodes.validate_draft import validate_draft
from app.agent.state import empty_state
from app.models import BidProject, Document, Organization, Requirement
from app.models.document import DocumentChunk
from app.models.enums import (
    DocumentType,
    EvidenceMatchStatus,
    MatchReviewStatus,
    ParseStatus,
    ProposalDraftStatus,
    ProposalDraftVersionKind,
    ProposalDraftSourceRole,
    QualityLevel,
    RequirementCategory,
    ReviewStatus,
    RiskLevel,
)
from app.models.match_run import RequirementEvidenceMatch, RequirementEvidenceMatchLink
from app.models.proposal_draft import ProposalDraft, ProposalDraftSource, ProposalDraftVersion
from app.schemas.proposal_draft import UNEVIDENCED_MARKER
from app.services.compliance.service import ComplianceService
from app.tools.compliance_tools import DraftComplianceInput, check_draft_compliance
from sqlalchemy.orm import Session


def _seed_project(db: Session):
    org = Organization(name=f"Org-{uuid4().hex[:6]}")
    db.add(org)
    db.flush()
    project = BidProject(
        organization_id=org.id,
        project_code=f"VAL-{uuid4().hex[:4]}",
        project_name="Validate Formal",
    )
    db.add(project)
    db.flush()
    doc = Document(
        project_id=project.id,
        organization_id=org.id,
        document_type=DocumentType.tender,
        file_name="t.pdf",
        storage_bucket="b",
        storage_key="k",
        parse_status=ParseStatus.success,
        is_scanned=False,
    )
    company = Document(
        project_id=project.id,
        organization_id=org.id,
        document_type=DocumentType.qualification,
        file_name="qual.pdf",
        storage_bucket="b",
        storage_key="k2",
        parse_status=ParseStatus.success,
        is_scanned=False,
    )
    db.add_all([doc, company])
    db.flush()
    chunk = DocumentChunk(
        document_id=doc.id,
        project_id=project.id,
        chunk_index=0,
        content="须具备建筑施工总承包一级资质",
        content_hash="h",
    )
    company_chunk = DocumentChunk(
        document_id=company.id,
        project_id=project.id,
        chunk_index=0,
        content="本公司持有建筑施工总承包一级资质证书",
        content_hash="h2",
    )
    db.add_all([chunk, company_chunk])
    req = Requirement(
        project_id=project.id,
        category=RequirementCategory.qualification,
        title="一级资质",
        mandatory=True,
        risk_level=RiskLevel.high,
        quality_level=QualityLevel.pending,
        review_status=ReviewStatus.unreviewed,
    )
    db.add(req)
    db.flush()
    match = RequirementEvidenceMatch(
        project_id=project.id,
        requirement_id=req.id,
        status=EvidenceMatchStatus.supported,
        review_status=MatchReviewStatus.confirmed,
        lifecycle_status="active",
    )
    db.add(match)
    db.flush()
    db.add(
        RequirementEvidenceMatchLink(
            match_id=match.id,
            document_id=company.id,
            chunk_id=company_chunk.id,
            quote="一级资质证书",
            role="company_support",
        )
    )
    db.commit()
    return project, req, match, org

def _make_draft(
    db: Session,
    project: BidProject,
    *,
    markdown: str,
    content_json: dict | None = None,
    title: str = "draft",
) -> ProposalDraft:
    draft = ProposalDraft(
        project_id=project.id,
        title=title,
        status=ProposalDraftStatus.draft_pending_review,
    )
    db.add(draft)
    db.flush()
    version = ProposalDraftVersion(
        project_id=project.id,
        draft_id=draft.id,
        version_number=1,
        version_kind=ProposalDraftVersionKind.manual_revision,
        content_json=content_json
        or {
            "sections": [
                {
                    "title": "响应",
                    "blocks": [
                        {
                            "block_kind": "partial_response",
                            "content": markdown,
                            "citation_ids": [],
                        }
                    ],
                }
            ]
        },
        content_markdown=markdown,
        is_current=True,
    )
    db.add(version)
    db.flush()
    draft.current_version_id = version.id
    db.commit()
    db.refresh(draft)
    return draft


@pytest.fixture
def runtime(db: Session):
    rt = AgentRuntime(db=db)
    token = set_runtime(rt)
    yield rt
    reset_runtime(token)


def test_formal_validate_fails_then_revise_passes(db: Session, runtime, monkeypatch):
    project, req, match, _ = _seed_project(db)
    bad_md = (
        f"{UNEVIDENCED_MARKER} 本公司保证中标，完全满足全部招标要求。"
        "额外说明文字用于超过最短长度限制。"
    )
    bad = _make_draft(db, project, markdown=bad_md, title="bad")

    start_calls: list = []
    real_start = ComplianceService.start_run

    def spy_start(self, *a, **k):
        start_calls.append((a, k))
        return real_start(self, *a, **k)

    monkeypatch.setattr(ComplianceService, "start_run", spy_start)

    state = empty_state(
        run_id=str(uuid4()),
        project_id=str(project.id),
        metadata={"forbid_satisfaction_claims": True},
    )
    state["draft_ids"] = [str(bad.id)]
    state["requirements"] = [{"id": str(req.id), "title": req.title}]
    state["critical_qualification"] = True

    state = validate_draft(state)
    assert start_calls, "validate_draft must call ComplianceService.start_run"
    assert state["draft_validation_ok"] is False
    rule_ids = {f["rule_id"] for f in (state.get("draft_findings") or [])}
    assert any(r.startswith("D") for r in rule_ids) or "AGENT_SUPPLEMENT_strong_claim" in rule_ids

    # Tool-level check matches node findings rule ids.
    tool = check_draft_compliance(
        db,
        DraftComplianceInput(project_id=project.id, draft_id=bad.id),
    )
    tool_rules = {f.rule_id for f in (tool.report.findings if tool.report else [])}
    assert tool_rules & rule_ids

    events = [
        e for e in (state.get("tool_events") or []) if e.get("name") == "check_draft_compliance"
    ]
    assert events
    assert "finding_count=" in (events[0].get("summary") or "")

    # Replace with a clean draft for revise → second formal check.
    good_md = (
        "根据现有材料，本公司可按招标文件要求提供相关资质证明与响应说明，"
        "供评审参考，本稿不含满足性承诺。"
    )
    good = _make_draft(db, project, markdown=good_md, title="good")

    def fake_generate(db_sess, payload, llm=None):
        from app.tools.agent_tools import ToolResult

        return ToolResult(
            ok=True,
            summary="revised_clean",
            data={
                "draft_ids": [str(good.id)],
                "risk_only": True,
                "content_preview": good_md,
            },
        )

    monkeypatch.setattr(
        sys.modules["app.agent.nodes.revise_draft"],
        "generate_proposal_draft",
        fake_generate,
    )

    before = len(start_calls)
    state = revise_draft(state)
    assert state["draft_revise_count"] == 1
    assert str(good.id) in (state.get("draft_ids") or [])
    assert state.get("draft_validation_ok") is None

    # Point validation at the clean draft only.
    state["draft_ids"] = [str(good.id)]
    state["metadata"] = {
        **dict(state.get("metadata") or {}),
        "forbid_satisfaction_claims": True,
        "risk_draft_preview": good_md,
    }
    state["critical_qualification"] = True
    state = validate_draft(state)
    assert len(start_calls) > before
    assert state["draft_validation_ok"] is True
    failing = [
        f
        for f in (state.get("draft_findings") or [])
        if f.get("status") == "fail" and f.get("severity") in {"error", "critical"}
    ]
    assert failing == []


def test_cross_project_source_finding(db: Session, runtime, monkeypatch):
    project, req, match, org = _seed_project(db)
    other = BidProject(
        organization_id=org.id,
        project_code=f"OTH-{uuid4().hex[:4]}",
        project_name="Other",
    )
    db.add(other)
    db.flush()
    foreign_doc = Document(
        project_id=other.id,
        organization_id=org.id,
        document_type=DocumentType.qualification,
        file_name="other.pdf",
        storage_bucket="b",
        storage_key="k2",
        parse_status=ParseStatus.success,
        is_scanned=False,
    )
    db.add(foreign_doc)
    db.flush()

    md = "本公司按材料响应招标要求，提供资质证明文件供评审参考使用。"
    draft = _make_draft(db, project, markdown=md, title="cross")
    version = db.get(ProposalDraftVersion, draft.current_version_id)
    assert version is not None
    db.add(
        ProposalDraftSource(
            project_id=other.id,
            draft_version_id=version.id,
            requirement_id=req.id,
            match_id=match.id,
            source_role=ProposalDraftSourceRole.company_support,
            source_quote="外项目材料",
            location_json={"document_id": str(foreign_doc.id)},
        )
    )
    db.commit()

    start_calls: list = []
    real_start = ComplianceService.start_run

    def spy_start(self, *a, **k):
        start_calls.append(True)
        return real_start(self, *a, **k)

    monkeypatch.setattr(ComplianceService, "start_run", spy_start)

    state = empty_state(run_id=str(uuid4()), project_id=str(project.id))
    state["draft_ids"] = [str(draft.id)]
    state = validate_draft(state)
    assert start_calls
    assert state["draft_validation_ok"] is False
    rule_ids = {f["rule_id"] for f in (state.get("draft_findings") or [])}
    assert "E005_project_ownership" in rule_ids or "D007_cross_project_source" in rule_ids
