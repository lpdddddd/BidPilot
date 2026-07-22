"""Tests for Step 9: auditable RequirementMatch human review workflow."""

from __future__ import annotations

import json
from uuid import uuid4

import pytest
from app.models import (
    BidProject,
    Document,
    DocumentChunk,
    Organization,
    Requirement,
)
from app.models.enums import (
    ActorAuthn,
    DocumentType,
    EvidenceMatchStatus,
    ExtractionRunStatus,
    MatchReviewAction,
    MatchReviewReasonCode,
    MatchReviewStatus,
    ParseStatus,
    QualityLevel,
    RequirementCategory,
    ReviewStatus,
    RiskLevel,
)
from app.models.match_run import (
    RequirementEvidenceMatch,
    RequirementMatchReview,
    RequirementMatchRun,
)
from app.schemas.match import MatchStartRequest
from app.schemas.match_review import MatchReopenRequest, MatchReviewRequest
from app.services.llm_client import ChatResult
from app.services.requirement_match_review_service import RequirementMatchReviewService
from app.services.requirement_match_service import (
    AUTO_SOURCE,
    RequirementMatchService,
    _is_protected_match,
)
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session


class FakeLlm:
    def __init__(self, responder=None, *, enabled: bool = True):
        self.enabled = enabled
        self.model = "fake-qwen"
        self.chat_calls: list = []
        self._responder = responder or (lambda messages: {"items": []})

    def chat(self, messages, **kwargs):
        self.chat_calls.append({"messages": messages, **kwargs})
        payload = self._responder(messages)
        content = (
            payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
        )
        return ChatResult(
            content=content,
            model=self.model,
            latency_ms=1.0,
            finish_reason="stop",
            request_id=kwargs.get("request_id") or "rid",
        )


def _org_project(db: Session, code: str = "REV-001") -> BidProject:
    org = Organization(name=f"Org-{code}-{uuid4().hex[:6]}")
    db.add(org)
    db.flush()
    project = BidProject(
        organization_id=org.id,
        project_code=code,
        project_name=f"Project {code}",
    )
    db.add(project)
    db.flush()
    return project


def _doc(
    db: Session,
    project: BidProject,
    *,
    document_type: DocumentType,
    file_name: str,
) -> Document:
    doc = Document(
        project_id=project.id,
        organization_id=project.organization_id,
        document_type=document_type,
        file_name=file_name,
        storage_bucket="bidpilot-documents",
        storage_key=f"{project.id}/{file_name}",
        parse_status=ParseStatus.success,
        is_scanned=False,
    )
    db.add(doc)
    db.flush()
    return doc


def _chunk(
    db: Session,
    project: BidProject,
    document: Document,
    *,
    index: int,
    content: str,
) -> DocumentChunk:
    chunk = DocumentChunk(
        document_id=document.id,
        project_id=project.id,
        chunk_index=index,
        content=content,
        section="企业资质",
        clause_id="Q.1",
        page_start=2,
        page_end=2,
    )
    db.add(chunk)
    db.flush()
    return chunk


def _requirement(
    db: Session,
    project: BidProject,
    *,
    title: str = "投标人资质要求",
    normalized: str = "投标人须具备一级资质。",
) -> Requirement:
    req = Requirement(
        project_id=project.id,
        requirement_code=f"REQ-{uuid4().hex[:8]}",
        category=RequirementCategory.qualification,
        title=title,
        normalized_requirement=normalized,
        mandatory=True,
        risk_level=RiskLevel.medium,
        quality_level=QualityLevel.pending,
        review_status=ReviewStatus.unreviewed,
        metadata_json={"source": "auto_extraction"},
    )
    db.add(req)
    db.flush()
    return req


def _auto_match(
    db: Session,
    project: BidProject,
    req: Requirement,
    *,
    status: EvidenceMatchStatus = EvidenceMatchStatus.supported,
    summary: str = "材料含可定位引文，需人工确认",
    quote: str | None = "本公司具备一级资质证书。",
) -> RequirementEvidenceMatch:
    match = RequirementEvidenceMatch(
        project_id=project.id,
        requirement_id=req.id,
        status=status,
        summary=summary,
        needs_review=True,
        risk_level=RiskLevel.medium,
        primary_company_quote=quote,
        metadata_json={"source": AUTO_SOURCE},
        review_status=MatchReviewStatus.pending,
        is_review_protected=False,
        review_lock_version=0,
        lifecycle_status="active",
    )
    db.add(match)
    db.flush()
    return match


def _match_payload(messages) -> dict:
    user = messages[-1]["content"]
    raw = user.split("<<<MATCH_INPUT>>>\n", 1)[-1]
    return json.loads(raw)


def _supported_item(req: Requirement, chunk: DocumentChunk, **overrides) -> dict:
    quote = (chunk.content or "")[:40]
    base = {
        "requirement_id": str(req.id),
        "status": "supported",
        "summary": "材料含一级资质引文，需人工确认",
        "primary_company_chunk_id": str(chunk.id),
        "company_evidence_quote": quote,
        "additional_company_chunk_ids": [],
        "needs_review": True,
        "conflict_note": None,
    }
    base.update(overrides)
    return base


def _run_matching(db: Session, svc: RequirementMatchService, project_id, request):
    run_resp = svc.start_matching(project_id, request)
    svc.execute_run(run_resp.id)
    run = db.get(RequirementMatchRun, run_resp.id)
    assert run is not None
    return run


def test_review_state_machine_confirm_reject_needs_material_reopen(db):
    project = _org_project(db, "SM1")
    req = _requirement(db, project)
    match = _auto_match(db, project, req)
    db.commit()

    svc = RequirementMatchReviewService(db)

    confirmed = svc.apply_review(
        project.id,
        match.id,
        MatchReviewRequest(
            action=MatchReviewAction.confirm,
            actor_label="alice",
            review_lock_version=0,
        ),
    )
    assert confirmed.review_status == MatchReviewStatus.confirmed
    assert confirmed.is_review_protected is True
    assert confirmed.needs_review is False
    assert confirmed.reviewed_by == "alice"
    assert confirmed.status == EvidenceMatchStatus.supported  # unchanged

    with pytest.raises(HTTPException) as exc:
        svc.apply_review(
            project.id,
            match.id,
            MatchReviewRequest(
                action=MatchReviewAction.reject,
                actor_label="alice",
                comment="should fail",
                review_lock_version=confirmed.review_lock_version,
            ),
        )
    assert exc.value.status_code == 409

    reopened = svc.reopen(
        project.id,
        match.id,
        MatchReopenRequest(
            actor_label="alice",
            comment="需要重新核对材料",
            review_lock_version=confirmed.review_lock_version,
        ),
    )
    assert reopened.review_status == MatchReviewStatus.pending
    assert reopened.is_review_protected is False
    assert reopened.needs_review is True
    assert reopened.status == EvidenceMatchStatus.supported
    assert reopened.summary == match.summary

    rejected = svc.apply_review(
        project.id,
        match.id,
        MatchReviewRequest(
            action=MatchReviewAction.reject,
            actor_label="bob",
            comment="证据不充分",
            reason_code=MatchReviewReasonCode.evidence_insufficient,
            review_lock_version=reopened.review_lock_version,
        ),
    )
    assert rejected.review_status == MatchReviewStatus.rejected

    again = svc.reopen(
        project.id,
        match.id,
        MatchReopenRequest(
            actor_label="bob",
            comment="补充材料后重审",
            review_lock_version=rejected.review_lock_version,
        ),
    )
    needs = svc.apply_review(
        project.id,
        match.id,
        MatchReviewRequest(
            action=MatchReviewAction.needs_more_material,
            actor_label="bob",
            comment="请上传最新资质证书",
            reason_code=MatchReviewReasonCode.needs_updated_material,
            review_lock_version=again.review_lock_version,
        ),
    )
    assert needs.review_status == MatchReviewStatus.needs_more_material
    assert needs.is_review_protected is True

    reviews = svc.list_reviews(project.id, match.id)
    assert reviews.total == 5
    actions = [r.action for r in reviews.items]
    assert MatchReviewAction.confirm in actions
    assert MatchReviewAction.reopen in actions
    assert all(r.actor_authn == ActorAuthn.unverified_local_operator for r in reviews.items)


def test_reject_and_reopen_require_comment(db):
    project = _org_project(db, "CM1")
    req = _requirement(db, project)
    match = _auto_match(db, project, req)
    db.commit()
    svc = RequirementMatchReviewService(db)

    with pytest.raises(HTTPException) as exc:
        svc.apply_review(
            project.id,
            match.id,
            MatchReviewRequest(
                action=MatchReviewAction.reject,
                actor_label="alice",
                comment="   ",
                review_lock_version=0,
            ),
        )
    assert exc.value.status_code == 422

    confirmed = svc.apply_review(
        project.id,
        match.id,
        MatchReviewRequest(
            action=MatchReviewAction.confirm,
            actor_label="alice",
            review_lock_version=0,
        ),
    )
    with pytest.raises(ValidationError):
        MatchReopenRequest(
            actor_label="alice",
            comment="   ",
            review_lock_version=confirmed.review_lock_version,
        )


def test_lock_version_conflict(db):
    project = _org_project(db, "LV1")
    req = _requirement(db, project)
    match = _auto_match(db, project, req)
    db.commit()
    svc = RequirementMatchReviewService(db)

    with pytest.raises(HTTPException) as exc:
        svc.apply_review(
            project.id,
            match.id,
            MatchReviewRequest(
                action=MatchReviewAction.confirm,
                actor_label="alice",
                review_lock_version=99,
            ),
        )
    assert exc.value.status_code == 409


def test_idempotency_same_key_same_body(db):
    project = _org_project(db, "ID1")
    req = _requirement(db, project)
    match = _auto_match(db, project, req)
    db.commit()
    svc = RequirementMatchReviewService(db)
    payload = MatchReviewRequest(
        action=MatchReviewAction.confirm,
        actor_label="alice",
        review_lock_version=0,
    )
    r1 = svc.apply_review(project.id, match.id, payload, idempotency_key="k1")
    r2 = svc.apply_review(project.id, match.id, payload, idempotency_key="k1")
    assert r1.review_lock_version == r2.review_lock_version
    assert r1.review_status == MatchReviewStatus.confirmed
    reviews = svc.list_reviews(project.id, match.id)
    assert reviews.total == 1


def test_idempotency_same_key_different_body(db):
    project = _org_project(db, "ID2")
    req = _requirement(db, project)
    match = _auto_match(db, project, req)
    db.commit()
    svc = RequirementMatchReviewService(db)
    svc.apply_review(
        project.id,
        match.id,
        MatchReviewRequest(
            action=MatchReviewAction.confirm,
            actor_label="alice",
            review_lock_version=0,
        ),
        idempotency_key="k2",
    )
    # reopen then try conflicting body under same key against confirm history
    match = db.get(RequirementEvidenceMatch, match.id)
    assert match is not None
    with pytest.raises(HTTPException) as exc:
        svc.apply_review(
            project.id,
            match.id,
            MatchReviewRequest(
                action=MatchReviewAction.reject,
                actor_label="alice",
                comment="不同内容",
                review_lock_version=0,
            ),
            idempotency_key="k2",
        )
    assert exc.value.status_code == 409


def test_review_never_mutates_match_status_or_summary(db):
    project = _org_project(db, "IM1")
    req = _requirement(db, project)
    match = _auto_match(
        db,
        project,
        req,
        status=EvidenceMatchStatus.insufficient_evidence,
        summary="原始摘要不可改",
    )
    original_status = match.status
    original_summary = match.summary
    db.commit()
    svc = RequirementMatchReviewService(db)
    detail = svc.apply_review(
        project.id,
        match.id,
        MatchReviewRequest(
            action=MatchReviewAction.confirm,
            actor_label="alice",
            review_lock_version=0,
        ),
    )
    assert detail.status == original_status
    assert detail.summary == original_summary


def test_is_protected_match_rules(db):
    project = _org_project(db, "PR1")
    req = _requirement(db, project)
    pending = _auto_match(db, project, req)
    assert _is_protected_match(pending) is False

    pending.is_review_protected = True
    assert _is_protected_match(pending) is True
    pending.is_review_protected = False

    pending.review_status = MatchReviewStatus.confirmed
    assert _is_protected_match(pending) is True
    pending.review_status = MatchReviewStatus.pending

    pending.lifecycle_status = "superseded"
    assert _is_protected_match(pending) is True
    pending.lifecycle_status = "active"

    pending.metadata_json = {"source": "manual"}
    assert _is_protected_match(pending) is True
    pending.metadata_json = {"source": AUTO_SOURCE, "review_status": "reviewed"}
    assert _is_protected_match(pending) is True


def test_force_keeps_protected_and_skips_from_llm(db):
    project = _org_project(db, "FP1")
    company = _doc(db, project, document_type=DocumentType.qualification, file_name="q.pdf")
    chunk = _chunk(db, project, company, index=0, content="本公司具备一级资质证书。")
    req_protected = _requirement(db, project, title="已确认资质")
    req_open = _requirement(db, project, title="待匹配资质", normalized="投标人须具备一级资质。")
    protected = _auto_match(
        db, project, req_protected, summary="人工确认保留"
    )
    protected.review_status = MatchReviewStatus.confirmed
    protected.is_review_protected = True
    protected.needs_review = False
    protected.reviewed_by = "alice"
    db.commit()

    def responder(messages):
        payload = _match_payload(messages)
        req_ids = {r["requirement_id"] for r in payload["requirements"]}
        assert str(req_protected.id) not in req_ids
        assert str(req_open.id) in req_ids
        return {
            "items": [
                _supported_item(req_open, chunk)
            ]
        }

    llm = FakeLlm(responder)
    svc = RequirementMatchService(db, llm=llm)
    run = _run_matching(db, svc, project.id, MatchStartRequest(force=True))
    assert run.status == ExtractionRunStatus.succeeded
    assert run.protected_requirement_count == 1
    assert run.skipped_reviewed_requirement_count == 1

    db.refresh(protected)
    assert protected.summary == "人工确认保留"
    assert protected.lifecycle_status == "active"
    assert protected.is_review_protected is True

    open_matches = list(
        db.scalars(
            select(RequirementEvidenceMatch).where(
                RequirementEvidenceMatch.requirement_id == req_open.id,
                RequirementEvidenceMatch.lifecycle_status == "active",
            )
        )
    )
    assert len(open_matches) == 1


def test_all_protected_succeeds_empty(db):
    project = _org_project(db, "AP1")
    company = _doc(db, project, document_type=DocumentType.qualification, file_name="q.pdf")
    _chunk(db, project, company, index=0, content="本公司具备一级资质证书。")
    req = _requirement(db, project)
    match = _auto_match(db, project, req)
    match.review_status = MatchReviewStatus.confirmed
    match.is_review_protected = True
    db.commit()

    llm = FakeLlm(lambda _m: {"items": []})
    svc = RequirementMatchService(db, llm=llm)
    run = _run_matching(db, svc, project.id, MatchStartRequest(force=True))
    assert run.status == ExtractionRunStatus.succeeded
    assert run.total_requirements == 0
    assert run.protected_requirement_count == 1
    assert llm.chat_calls == []
    assert (run.config_json or {}).get("result_kind") == "all_requirements_protected"


def test_reopen_then_force_supersedes_with_history(db):
    project = _org_project(db, "SU1")
    company = _doc(db, project, document_type=DocumentType.qualification, file_name="q.pdf")
    chunk = _chunk(db, project, company, index=0, content="本公司具备一级资质证书。")
    req = _requirement(db, project)
    old = _auto_match(db, project, req, summary="旧匹配摘要")
    db.commit()

    review_svc = RequirementMatchReviewService(db)
    confirmed = review_svc.apply_review(
        project.id,
        old.id,
        MatchReviewRequest(
            action=MatchReviewAction.confirm,
            actor_label="alice",
            review_lock_version=0,
        ),
    )
    reopened = review_svc.reopen(
        project.id,
        old.id,
        MatchReopenRequest(
            actor_label="alice",
            comment="材料更新后重跑",
            review_lock_version=confirmed.review_lock_version,
        ),
    )
    assert reopened.is_review_protected is False
    assert reopened.review_status == MatchReviewStatus.pending

    def responder(messages):
        return {
            "items": [
                _supported_item(req, chunk)
            ]
        }

    llm = FakeLlm(responder)
    match_svc = RequirementMatchService(db, llm=llm)
    run = _run_matching(
        db,
        match_svc,
        project.id,
        MatchStartRequest(requirement_ids=[req.id], force=True),
    )
    assert run.status == ExtractionRunStatus.succeeded

    db.refresh(old)
    assert old.lifecycle_status == "superseded"
    assert old.superseded_by_match_id is not None
    assert old.summary == "旧匹配摘要"
    # Review history retained on old row.
    history = list(
        db.scalars(
            select(RequirementMatchReview).where(
                RequirementMatchReview.match_id == old.id
            )
        )
    )
    assert len(history) >= 2

    new_match = db.get(RequirementEvidenceMatch, old.superseded_by_match_id)
    assert new_match is not None
    assert new_match.lifecycle_status == "active"
    assert new_match.supersedes_match_id == old.id
    assert new_match.review_status == MatchReviewStatus.pending


def test_force_deletes_pending_auto_without_reviews(db):
    project = _org_project(db, "DL1")
    company = _doc(db, project, document_type=DocumentType.qualification, file_name="q.pdf")
    chunk = _chunk(db, project, company, index=0, content="本公司具备一级资质证书。")
    req = _requirement(db, project)
    old = _auto_match(db, project, req, summary="可删除旧匹配")
    old_id = old.id
    db.commit()

    def responder(messages):
        return {
            "items": [
                _supported_item(req, chunk)
            ]
        }

    llm = FakeLlm(responder)
    svc = RequirementMatchService(db, llm=llm)
    run = _run_matching(db, svc, project.id, MatchStartRequest(force=True))
    assert run.status == ExtractionRunStatus.succeeded
    assert db.get(RequirementEvidenceMatch, old_id) is None


def test_project_isolation_review_apis(db, client: TestClient):
    p1 = _org_project(db, "PI1")
    p2 = _org_project(db, "PI2")
    req1 = _requirement(db, p1)
    req2 = _requirement(db, p2)
    m1 = _auto_match(db, p1, req1)
    m2 = _auto_match(db, p2, req2)
    db.commit()

    # Cross-project get
    resp = client.get(f"/api/v1/projects/{p1.id}/requirement-matches/{m2.id}")
    assert resp.status_code == 404

    resp = client.post(
        f"/api/v1/projects/{p1.id}/requirement-matches/{m2.id}/review",
        json={
            "action": "confirm",
            "actor_label": "alice",
            "review_lock_version": 0,
        },
    )
    assert resp.status_code == 404

    resp = client.get(f"/api/v1/projects/{p1.id}/requirement-matches/review-queue")
    assert resp.status_code == 200
    body = resp.json()
    assert body["counts"]["pending"] >= 1
    ids = {item["id"] for item in body["items"]}
    assert str(m1.id) in ids
    assert str(m2.id) not in ids

    resp = client.post(
        f"/api/v1/projects/{p1.id}/requirement-matches/{m1.id}/review",
        json={
            "action": "confirm",
            "actor_label": "alice",
            "review_lock_version": 0,
        },
        headers={"Idempotency-Key": "iso-1"},
    )
    assert resp.status_code == 200
    assert resp.json()["review_status"] == "confirmed"
    assert resp.json()["status"] == "supported"


def test_review_queue_filters(db, client: TestClient):
    project = _org_project(db, "QF1")
    req_a = _requirement(db, project, title="A")
    req_b = _requirement(db, project, title="B")
    m_pending = _auto_match(db, project, req_a)
    m_conf = _auto_match(
        db, project, req_b, status=EvidenceMatchStatus.insufficient_evidence
    )
    m_conf.review_status = MatchReviewStatus.confirmed
    m_conf.is_review_protected = True
    m_conf.needs_review = False
    db.commit()

    resp = client.get(
        f"/api/v1/projects/{project.id}/requirement-matches/review-queue",
        params={"review_status": "pending"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["id"] == str(m_pending.id)
    assert body["counts"]["pending"] == 1
    assert body["counts"]["confirmed"] == 1


def test_integration_statuses_and_protection(db):
    """Create supported/insufficient/conflict/NA, review them, force keeps protected."""
    project = _org_project(db, "IG1")
    company = _doc(db, project, document_type=DocumentType.qualification, file_name="q.pdf")
    chunk = _chunk(db, project, company, index=0, content="本公司具备一级资质证书。")

    reqs = {
        "supported": _requirement(db, project, title="支持"),
        "insufficient": _requirement(db, project, title="不足"),
        "conflict": _requirement(db, project, title="冲突"),
        "na": _requirement(db, project, title="不适用"),
    }
    matches = {
        "supported": _auto_match(
            db, project, reqs["supported"], status=EvidenceMatchStatus.supported
        ),
        "insufficient": _auto_match(
            db,
            project,
            reqs["insufficient"],
            status=EvidenceMatchStatus.insufficient_evidence,
        ),
        "conflict": _auto_match(
            db,
            project,
            reqs["conflict"],
            status=EvidenceMatchStatus.conflicting_evidence,
        ),
        "na": _auto_match(
            db, project, reqs["na"], status=EvidenceMatchStatus.not_applicable
        ),
    }
    db.commit()

    review_svc = RequirementMatchReviewService(db)
    review_svc.apply_review(
        project.id,
        matches["supported"].id,
        MatchReviewRequest(
            action=MatchReviewAction.confirm,
            actor_label="reviewer",
            review_lock_version=0,
        ),
    )
    review_svc.apply_review(
        project.id,
        matches["insufficient"].id,
        MatchReviewRequest(
            action=MatchReviewAction.reject,
            actor_label="reviewer",
            comment="证据不够",
            review_lock_version=0,
        ),
    )
    review_svc.apply_review(
        project.id,
        matches["conflict"].id,
        MatchReviewRequest(
            action=MatchReviewAction.needs_more_material,
            actor_label="reviewer",
            comment="需补充冲突说明材料",
            review_lock_version=0,
        ),
    )
    # leave NA pending

    llm = FakeLlm(
        lambda messages: {
            "items": [
                _supported_item(
                    reqs["na"],
                    chunk,
                    summary="材料含一级资质引文，需人工确认",
                )
            ]
        }
    )
    match_svc = RequirementMatchService(db, llm=llm)
    run = _run_matching(db, match_svc, project.id, MatchStartRequest(force=True))
    assert run.status == ExtractionRunStatus.succeeded
    assert run.protected_requirement_count == 3
    assert run.skipped_reviewed_requirement_count == 3

    for key in ("supported", "insufficient", "conflict"):
        db.refresh(matches[key])
        assert matches[key].lifecycle_status == "active"
        assert matches[key].is_review_protected is True


def test_needs_new_auto_version_rules(db):
    from app.services.requirement_match_service import _needs_new_auto_version

    project = _org_project(db, "NV1")
    req = _requirement(db, project)
    assert _needs_new_auto_version(None, force=False) is True
    assert _needs_new_auto_version(None, force=True) is True

    pending = _auto_match(db, project, req)
    db.flush()
    assert _needs_new_auto_version(pending, force=False) is False
    assert _needs_new_auto_version(pending, force=True) is True

    pending.review_status = MatchReviewStatus.confirmed
    pending.is_review_protected = True
    assert _needs_new_auto_version(pending, force=True) is False
    pending.review_status = MatchReviewStatus.pending
    pending.is_review_protected = False

    pending.lifecycle_status = "superseded"
    assert _needs_new_auto_version(pending, force=True) is False
    pending.lifecycle_status = "active"

    # Simulate reopen: pending + review history → successor for both force modes.
    db.add(
        RequirementMatchReview(
            project_id=project.id,
            match_id=pending.id,
            action=MatchReviewAction.confirm,
            from_review_status=MatchReviewStatus.pending,
            to_review_status=MatchReviewStatus.confirmed,
            actor_label="alice",
            actor_authn=ActorAuthn.unverified_local_operator,
        )
    )
    db.flush()
    db.refresh(pending)
    assert _needs_new_auto_version(pending, force=False) is True
    assert _needs_new_auto_version(pending, force=True) is True


def test_reopen_then_force_false_creates_successor(db):
    """After reopen, force=false must create a new auto Match version (A1 bugfix)."""
    project = _org_project(db, "RF0")
    company = _doc(db, project, document_type=DocumentType.qualification, file_name="q.pdf")
    chunk = _chunk(db, project, company, index=0, content="本公司具备一级资质证书。")
    req = _requirement(db, project)
    old = _auto_match(db, project, req, summary="旧匹配摘要-force0")
    db.commit()

    review_svc = RequirementMatchReviewService(db)
    confirmed = review_svc.apply_review(
        project.id,
        old.id,
        MatchReviewRequest(
            action=MatchReviewAction.confirm,
            actor_label="alice",
            review_lock_version=0,
        ),
    )
    reopened = review_svc.reopen(
        project.id,
        old.id,
        MatchReopenRequest(
            actor_label="alice",
            comment="材料更新后重跑(force=false)",
            review_lock_version=confirmed.review_lock_version,
        ),
    )
    assert reopened.is_review_protected is False
    assert reopened.review_status == MatchReviewStatus.pending

    llm = FakeLlm(lambda _m: {"items": [_supported_item(req, chunk)]})
    match_svc = RequirementMatchService(db, llm=llm)
    run = _run_matching(
        db,
        match_svc,
        project.id,
        MatchStartRequest(requirement_ids=[req.id], force=False),
    )
    assert run.status == ExtractionRunStatus.succeeded
    assert len(llm.chat_calls) == 1

    db.refresh(old)
    assert old.lifecycle_status == "superseded"
    assert old.superseded_by_match_id is not None
    assert old.summary == "旧匹配摘要-force0"
    history = list(
        db.scalars(
            select(RequirementMatchReview).where(
                RequirementMatchReview.match_id == old.id
            )
        )
    )
    assert len(history) >= 2

    new_match = db.get(RequirementEvidenceMatch, old.superseded_by_match_id)
    assert new_match is not None
    assert new_match.lifecycle_status == "active"
    assert new_match.supersedes_match_id == old.id
    assert new_match.review_status == MatchReviewStatus.pending

    active = list(
        db.scalars(
            select(RequirementEvidenceMatch).where(
                RequirementEvidenceMatch.requirement_id == req.id,
                RequirementEvidenceMatch.lifecycle_status == "active",
            )
        )
    )
    assert len(active) == 1
    assert active[0].id == new_match.id


def test_force_false_skips_pending_without_reviews_no_llm(db):
    project = _org_project(db, "ID0")
    company = _doc(db, project, document_type=DocumentType.qualification, file_name="q.pdf")
    _chunk(db, project, company, index=0, content="本公司具备一级资质证书。")
    req = _requirement(db, project)
    old = _auto_match(db, project, req, summary="幂等保留")
    db.commit()

    llm = FakeLlm(lambda _m: {"items": []})
    svc = RequirementMatchService(db, llm=llm)
    run = _run_matching(
        db, svc, project.id, MatchStartRequest(requirement_ids=[req.id], force=False)
    )
    assert run.status == ExtractionRunStatus.succeeded
    assert llm.chat_calls == []
    db.refresh(old)
    assert old.lifecycle_status == "active"
    assert old.summary == "幂等保留"


def test_invalid_result_keeps_reopened_match_active(db):
    project = _org_project(db, "KE1")
    company = _doc(db, project, document_type=DocumentType.qualification, file_name="q.pdf")
    chunk = _chunk(db, project, company, index=0, content="本公司具备一级资质证书。")
    req = _requirement(db, project)
    old = _auto_match(db, project, req, summary="保留旧版")
    db.commit()

    review_svc = RequirementMatchReviewService(db)
    confirmed = review_svc.apply_review(
        project.id,
        old.id,
        MatchReviewRequest(
            action=MatchReviewAction.confirm,
            actor_label="alice",
            review_lock_version=0,
        ),
    )
    review_svc.reopen(
        project.id,
        old.id,
        MatchReopenRequest(
            actor_label="alice",
            comment="准备重跑但结果无效",
            review_lock_version=confirmed.review_lock_version,
        ),
    )

    # Invalid quote → validation fail → no supersede.
    bad = _supported_item(req, chunk, company_evidence_quote="这段引文不在原文中")
    llm = FakeLlm(lambda _m: {"items": [bad]})
    match_svc = RequirementMatchService(db, llm=llm)
    run = _run_matching(
        db,
        match_svc,
        project.id,
        MatchStartRequest(requirement_ids=[req.id], force=False),
    )
    assert run.status == ExtractionRunStatus.failed
    db.refresh(old)
    assert old.lifecycle_status == "active"
    assert old.superseded_by_match_id is None


def test_concurrent_reopened_match_successor_at_most_one(engine):
    """Two sessions racing persist for one reopened Requirement → one active successor."""
    import threading
    from sqlalchemy.orm import sessionmaker

    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    setup = SessionLocal()
    try:
        project = _org_project(setup, "CR1")
        company = _doc(
            setup, project, document_type=DocumentType.qualification, file_name="q.pdf"
        )
        chunk = _chunk(
            setup, project, company, index=0, content="本公司具备一级资质证书。"
        )
        req = _requirement(setup, project)
        old = _auto_match(setup, project, req, summary="并发旧版")
        setup.commit()

        review_svc = RequirementMatchReviewService(setup)
        confirmed = review_svc.apply_review(
            project.id,
            old.id,
            MatchReviewRequest(
                action=MatchReviewAction.confirm,
                actor_label="alice",
                review_lock_version=0,
            ),
        )
        review_svc.reopen(
            project.id,
            old.id,
            MatchReopenRequest(
                actor_label="alice",
                comment="并发重跑",
                review_lock_version=confirmed.review_lock_version,
            ),
        )
        project_id = project.id
        req_id = req.id
        old_id = old.id
        chunk_id = chunk.id
        # Snapshot requirement for responder
        req_row = setup.get(Requirement, req_id)
        chunk_row = setup.get(DocumentChunk, chunk_id)
        assert req_row is not None and chunk_row is not None
        item = _supported_item(req_row, chunk_row)
    finally:
        setup.close()

    barrier = threading.Barrier(2)
    results: list[str] = []
    lock = threading.Lock()

    def worker(force: bool):
        session = SessionLocal()
        try:
            llm = FakeLlm(lambda _m: {"items": [item]})
            svc = RequirementMatchService(session, llm=llm)
            run_resp = svc.start_matching(
                project_id,
                MatchStartRequest(requirement_ids=[req_id], force=force),
            )
            barrier.wait(timeout=10)
            svc.execute_run(run_resp.id)
            run = session.get(RequirementMatchRun, run_resp.id)
            with lock:
                results.append(run.status.value if run else "missing")
        except Exception as exc:  # noqa: BLE001
            with lock:
                results.append(f"err:{type(exc).__name__}")
        finally:
            session.close()

    t1 = threading.Thread(target=worker, args=(False,))
    t2 = threading.Thread(target=worker, args=(False,))
    t1.start()
    t2.start()
    t1.join(timeout=30)
    t2.join(timeout=30)

    verify = SessionLocal()
    try:
        active = list(
            verify.scalars(
                select(RequirementEvidenceMatch).where(
                    RequirementEvidenceMatch.requirement_id == req_id,
                    RequirementEvidenceMatch.lifecycle_status == "active",
                )
            )
        )
        assert len(active) == 1
        old_row = verify.get(RequirementEvidenceMatch, old_id)
        assert old_row is not None
        assert old_row.lifecycle_status == "superseded"
        assert old_row.superseded_by_match_id == active[0].id
        assert active[0].supersedes_match_id == old_id
    finally:
        verify.close()


def test_concurrent_confirm_one_wins(engine):
    """Real concurrent confirm with FOR UPDATE: one 200-equivalent, one 409."""
    import threading
    from sqlalchemy.orm import sessionmaker

    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    setup = SessionLocal()
    try:
        project = _org_project(setup, "CC1")
        req = _requirement(setup, project)
        match = _auto_match(
            setup,
            project,
            req,
            status=EvidenceMatchStatus.supported,
            summary="并发确认摘要不可改",
            quote="本公司具备一级资质证书。",
        )
        setup.commit()
        project_id = project.id
        match_id = match.id
        original_status = match.status
        original_summary = match.summary
        original_quote = match.primary_company_quote
    finally:
        setup.close()

    barrier = threading.Barrier(2)
    outcomes: list[tuple[str, int | None]] = []
    lock = threading.Lock()

    def worker(label: str):
        session = SessionLocal()
        try:
            svc = RequirementMatchReviewService(session)
            barrier.wait(timeout=10)
            try:
                detail = svc.apply_review(
                    project_id,
                    match_id,
                    MatchReviewRequest(
                        action=MatchReviewAction.confirm,
                        actor_label=label,
                        review_lock_version=0,
                    ),
                )
                with lock:
                    outcomes.append(("ok", detail.review_lock_version))
            except HTTPException as exc:
                with lock:
                    outcomes.append(("http", exc.status_code))
        finally:
            session.close()

    t1 = threading.Thread(target=worker, args=("alice",))
    t2 = threading.Thread(target=worker, args=("bob",))
    t1.start()
    t2.start()
    t1.join(timeout=20)
    t2.join(timeout=20)

    assert len(outcomes) == 2
    oks = [o for o in outcomes if o[0] == "ok"]
    conflicts = [o for o in outcomes if o[0] == "http" and o[1] == 409]
    assert len(oks) == 1
    assert len(conflicts) == 1
    assert oks[0][1] == 1

    verify = SessionLocal()
    try:
        row = verify.get(RequirementEvidenceMatch, match_id)
        assert row is not None
        assert row.review_status == MatchReviewStatus.confirmed
        assert row.review_lock_version == 1
        assert row.status == original_status
        assert row.summary == original_summary
        assert row.primary_company_quote == original_quote
        reviews = list(
            verify.scalars(
                select(RequirementMatchReview).where(
                    RequirementMatchReview.match_id == match_id
                )
            )
        )
        assert len(reviews) == 1
        assert reviews[0].action == MatchReviewAction.confirm
    finally:
        verify.close()


def test_concurrent_confirm_vs_reopen(engine):
    """Race confirm vs reopen on pending→terminal path vs invalid reopen."""
    import threading
    from sqlalchemy.orm import sessionmaker

    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    setup = SessionLocal()
    try:
        project = _org_project(setup, "CCR")
        req = _requirement(setup, project)
        match = _auto_match(setup, project, req, summary="确认与重开竞态")
        setup.commit()
        # First confirm so reopen is valid from confirmed; then reopen to pending,
        # then race confirm vs a second confirm (reopen only valid from terminal).
        # Instead: start pending, race confirm vs confirm is above.
        # Here: confirm first in setup to terminal, then race second confirm vs reopen.
        svc = RequirementMatchReviewService(setup)
        confirmed = svc.apply_review(
            project.id,
            match.id,
            MatchReviewRequest(
                action=MatchReviewAction.confirm,
                actor_label="seed",
                review_lock_version=0,
            ),
        )
        lock_version = confirmed.review_lock_version
        project_id = project.id
        match_id = match.id
        original_summary = match.summary
    finally:
        setup.close()

    barrier = threading.Barrier(2)
    outcomes: list[str] = []
    lock = threading.Lock()

    def do_confirm():
        session = SessionLocal()
        try:
            svc = RequirementMatchReviewService(session)
            barrier.wait(timeout=10)
            try:
                svc.apply_review(
                    project_id,
                    match_id,
                    MatchReviewRequest(
                        action=MatchReviewAction.confirm,
                        actor_label="alice",
                        review_lock_version=lock_version,
                    ),
                )
                with lock:
                    outcomes.append("confirm_ok")
            except HTTPException as exc:
                with lock:
                    outcomes.append(f"confirm_{exc.status_code}")
        finally:
            session.close()

    def do_reopen():
        session = SessionLocal()
        try:
            svc = RequirementMatchReviewService(session)
            barrier.wait(timeout=10)
            try:
                svc.reopen(
                    project_id,
                    match_id,
                    MatchReopenRequest(
                        actor_label="bob",
                        comment="竞态重开",
                        review_lock_version=lock_version,
                    ),
                )
                with lock:
                    outcomes.append("reopen_ok")
            except HTTPException as exc:
                with lock:
                    outcomes.append(f"reopen_{exc.status_code}")
        finally:
            session.close()

    t1 = threading.Thread(target=do_confirm)
    t2 = threading.Thread(target=do_reopen)
    t1.start()
    t2.start()
    t1.join(timeout=20)
    t2.join(timeout=20)

    assert len(outcomes) == 2
    # Exactly one should succeed; the other gets 409 (lock or invalid transition).
    oks = [o for o in outcomes if o.endswith("_ok")]
    conflicts = [o for o in outcomes if o.endswith("_409")]
    assert len(oks) == 1
    assert len(conflicts) == 1

    verify = SessionLocal()
    try:
        row = verify.get(RequirementEvidenceMatch, match_id)
        assert row is not None
        assert row.summary == original_summary
        assert row.review_lock_version == lock_version + 1
        reviews = list(
            verify.scalars(
                select(RequirementMatchReview).where(
                    RequirementMatchReview.match_id == match_id
                )
            )
        )
        # seed confirm + exactly one of racing actions
        assert len(reviews) == 2
    finally:
        verify.close()


def test_review_queue_defaults_and_include_superseded(db, client: TestClient):
    project = _org_project(db, "QD1")
    req = _requirement(db, project, title="队列需求")
    active = _auto_match(db, project, req)
    active.metadata_json = {
        "source": AUTO_SOURCE,
        "run_id": str(uuid4()),
        "not_applicable_basis": None,
    }
    superseded = _auto_match(
        db, project, req, status=EvidenceMatchStatus.insufficient_evidence
    )
    superseded.lifecycle_status = "superseded"
    superseded.superseded_by_match_id = active.id
    active.supersedes_match_id = superseded.id
    db.commit()

    # Default: active + pending only
    resp = client.get(
        f"/api/v1/projects/{project.id}/requirement-matches/review-queue"
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["include_superseded"] is False
    assert body["total"] == 1
    assert body["items"][0]["id"] == str(active.id)
    assert body["items"][0]["requirement_title"] == "队列需求"
    assert "has_conflict" in body["items"][0]
    assert "has_scope_exclusion" in body["items"][0]
    assert "by_match_status" in body["counts"]
    assert "by_risk_level" in body["counts"]

    resp = client.get(
        f"/api/v1/projects/{project.id}/requirement-matches/review-queue",
        params={"include_superseded": True, "review_status": "all"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["include_superseded"] is True
    ids = {item["id"] for item in body["items"]}
    assert str(active.id) in ids
    assert str(superseded.id) in ids


def test_review_queue_category_and_conflict_filters(db, client: TestClient):
    project = _org_project(db, "QF2")
    req_q = _requirement(db, project, title="资质")
    req_t = Requirement(
        project_id=project.id,
        requirement_code=f"REQ-{uuid4().hex[:8]}",
        category=RequirementCategory.technical,
        title="技术",
        normalized_requirement="技术参数",
        mandatory=False,
        risk_level=RiskLevel.low,
        quality_level=QualityLevel.pending,
        review_status=ReviewStatus.unreviewed,
        metadata_json={"potential_conflict": True},
    )
    db.add(req_t)
    db.flush()
    m_q = _auto_match(db, project, req_q)
    m_c = _auto_match(
        db, project, req_t, status=EvidenceMatchStatus.conflicting_evidence
    )
    db.commit()

    resp = client.get(
        f"/api/v1/projects/{project.id}/requirement-matches/review-queue",
        params={"requirement_category": "technical", "review_status": "all"},
    )
    assert resp.status_code == 200
    assert resp.json()["total"] == 1
    assert resp.json()["items"][0]["id"] == str(m_c.id)

    resp = client.get(
        f"/api/v1/projects/{project.id}/requirement-matches/review-queue",
        params={"has_conflict": True, "review_status": "all"},
    )
    assert resp.status_code == 200
    ids = {i["id"] for i in resp.json()["items"]}
    assert str(m_c.id) in ids
    assert str(m_q.id) not in ids
