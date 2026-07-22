"""Traceable matching of project Requirements against company-side document chunks.

Uses lexical overlap to select candidate company chunks (does not alter RAG
retrieval), validates LLM JSON against evidence helpers, and persists
RequirementEvidenceMatch + RequirementEvidenceMatchLink rows.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, selectinload

from app.models import BidProject, Document, EvidenceLink, Requirement
from app.models.document import DocumentChunk
from app.models.enums import (
    DocumentType,
    EvidenceMatchStatus,
    ExtractionRunStatus,
    MatchReviewStatus,
    RequirementCategory,
    ReviewStatus,
    RiskLevel,
)
from app.models.match_run import (
    RequirementEvidenceMatch,
    RequirementEvidenceMatchLink,
    RequirementMatchRun,
)
from app.schemas.match import (
    DEFAULT_MATCH_DOCUMENT_TYPES,
    EXCLUDED_MATCH_DOCUMENT_TYPES,
    CompanyEvidenceLinkRead,
    MatchBatchResult,
    MatchCandidateItem,
    MatchDetail,
    MatchListResponse,
    MatchRunResponse,
    MatchStartRequest,
    MatchSummary,
)
from app.schemas.match_review import MatchReviewRead
from app.schemas.requirement import EvidenceLinkRead, RequirementSummary
from app.services.evidence_validate import (
    extract_critical_tokens,
    normalize_whitespace,
    quote_in_content,
    soft_normalize_for_grounding,
)
from app.services.llm_client import LlmClient, LlmError, get_llm_client
from app.services.match_conflict_validate import (
    validate_direct_company_conflict,
    validate_scope_exclusion,
)
from app.services.requirement_extraction_service import document_center_path

logger = logging.getLogger("bidpilot.requirement_match")

BATCH_SIZE = 4
TOP_CHUNKS_PER_REQUIREMENT = 6
AUTO_SOURCE = "auto_match"
_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE | re.MULTILINE)
_TOKEN_SPLIT_RE = re.compile(r"[\s,，。．.;；:：、\-—_/\\()（）\[\]【】\"'“”‘’]+")
_SCOPE_LIMIT_HINT_RE = re.compile(
    r"(仅适用|仅限|仅针对|本标段|本包件|适用范围|服务范围|适用区域|其他区域不适用|"
    r"其他标段不适用|不适用于|范围外)"
)

_GRADE_PATTERNS: list[tuple[str, int]] = [
    ("特级", 50),
    ("一级", 40),
    ("二级", 30),
    ("三级", 20),
    ("四级", 10),
    ("甲级", 40),
    ("乙级", 30),
    ("丙级", 20),
]

# Obligation/modality words appear in tender text but rarely in company materials.
_MODALITY_TOKENS = frozenset(
    {"不得", "必须", "应当", "应", "须", "禁止", "可以", "可", "宜", "不应", "不可"}
)

SYSTEM_PROMPT = """你是 BidPilot 的企业材料证据匹配器。
只可根据本轮提供的招标 Requirement、招标主证据 chunks 和企业侧原始 chunks 作出结论。
招标要求本身以输入 Requirement 为准，不得改写、补充或生成新的要求。
不得依赖外部知识、行业惯例、企业常识或经验推断。
只有存在直接、可定位且足以支持该具体 Requirement 的企业原文时，才可输出 supported。
缺少证据只可输出 insufficient_evidence，含义是当前材料范围内证据不足，不代表企业不具备。
企业材料彼此存在直接矛盾时才可输出 conflicting_evidence，且必须同时提供：
1) primary_company_chunk_id + company_evidence_quote（支持侧）
2) conflicting_company_chunk_id + conflicting_company_evidence_quote（冲突侧）
3) conflict_dimension、conflict_subject、primary_claim_value、conflicting_claim_value
   （证明同一主体上互斥的直接主张；两侧必须是不同文本位置）
两侧必须是不同文本位置（不同 chunk，或同一 chunk 的不同引文），禁止同一 chunk+同一引文。
not_applicable 仅当 Requirement 原文明确限定适用范围，
且企业侧可定位证据证明当前对象/范围在适用之外；
必须同时提供双范围证据：
- requirement_scope_chunk_id + requirement_scope_quote（该 Requirement 的招标主证据）
- current_scope_chunk_id + current_scope_quote（本轮企业侧候选）
- not_applicable_basis、not_applicable_note（可选说明）
禁止仅用单侧旧字段；模型主观判断、常识、缺少企业材料或材料为空 → 禁止 not_applicable，
应输出 insufficient_evidence。
每个 supported、partially_supported、conflicting_evidence 必须提供
primary_company_chunk_id 和可精确匹配的 company_evidence_quote。
summary / conflict_note 只能描述材料与该要求的证据关系，不得发明证据中不存在的关键事实 token，
不得断言必然中标、完全满足或一定不符合。
本轮每个 Requirement 必须恰好输出一条合法结果；不得省略。
输出 JSON，不输出 Markdown、解释或思考过程。"""


class MatchValidationError(Exception):
    """Any validation reject / incomplete batch → whole Match run must fail atomically."""

    def __init__(
        self,
        message: str,
        *,
        reasons: list[str] | None = None,
        reason_counts: dict[str, int] | None = None,
    ) -> None:
        super().__init__(message)
        self.reasons = list(reasons or [])
        self.reason_counts = dict(reason_counts or {})

_STATUS_REQUIRING_PRIMARY = frozenset(
    {
        EvidenceMatchStatus.supported,
        EvidenceMatchStatus.partially_supported,
        EvidenceMatchStatus.conflicting_evidence,
    }
)


@dataclass
class _ChunkContext:
    chunk: DocumentChunk
    document: Document


@dataclass
class _ValidatedMatch:
    item: MatchCandidateItem
    requirement: Requirement
    status: EvidenceMatchStatus
    summary: str
    risk_level: RiskLevel
    primary_chunk: DocumentChunk | None
    primary_document: Document | None
    primary_quote: str | None
    company_links: list[tuple[Document, DocumentChunk, str, str]]  # doc, chunk, quote, role
    needs_review: bool
    metadata_extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class _RunAccumulator:
    validated: list[_ValidatedMatch] = field(default_factory=list)
    raw_item_count: int = 0
    rejected_count: int = 0
    llm_validated_count: int = 0
    failed_requirement_count: int = 0
    processed_requirements: int = 0
    errors: list[str] = field(default_factory=list)
    reject_reason_counts: dict[str, int] = field(default_factory=dict)

    def add_reject_reasons(self, reason_counts: dict[str, int]) -> None:
        for key, count in reason_counts.items():
            self.reject_reason_counts[key] = self.reject_reason_counts.get(key, 0) + count


def auto_match_key(requirement_id: UUID) -> str:
    return f"auto-match-{requirement_id.hex}"


def _parse_llm_json(raw: str) -> dict[str, Any]:
    text = raw.strip()
    text = _FENCE_RE.sub("", text).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        text = text[start : end + 1]
    loaded: Any = json.loads(text)
    if not isinstance(loaded, dict):
        raise ValueError("LLM JSON root must be an object")
    return loaded


def _extract_grades(text: str) -> list[tuple[str, int]]:
    found: list[tuple[str, int]] = []
    for label, rank in _GRADE_PATTERNS:
        if label in (text or ""):
            found.append((label, rank))
    return found


def grade_mismatch(requirement_text: str, evidence_text: str) -> bool:
    """True when requirement asks for a higher grade than evidence provides."""
    req_grades = _extract_grades(requirement_text or "")
    ev_grades = _extract_grades(evidence_text or "")
    if not req_grades:
        return False
    req_max = max(r for _, r in req_grades)
    if not ev_grades:
        # Requirement mentions a grade; evidence has none → cannot fully support.
        return True
    ev_max = max(r for _, r in ev_grades)
    return ev_max < req_max


def risk_for_match(
    requirement: Requirement,
    match_status: EvidenceMatchStatus,
) -> RiskLevel:
    """Deterministic risk rules for evidence matches."""
    floor = requirement.risk_level or RiskLevel.medium
    order = {
        RiskLevel.low: 0,
        RiskLevel.medium: 1,
        RiskLevel.high: 2,
        RiskLevel.critical: 3,
    }

    def raise_to(current: RiskLevel, minimum: RiskLevel) -> RiskLevel:
        return minimum if order[current] < order[minimum] else current

    risk = floor

    if floor == RiskLevel.critical:
        risk = raise_to(risk, RiskLevel.high)

    if match_status == EvidenceMatchStatus.conflicting_evidence:
        risk = raise_to(risk, RiskLevel.high)
    elif match_status == EvidenceMatchStatus.partially_supported:
        risk = raise_to(risk, RiskLevel.medium)
    elif match_status == EvidenceMatchStatus.insufficient_evidence:
        risk = raise_to(risk, RiskLevel.medium)
        if requirement.mandatory or requirement.category in (
            RequirementCategory.deadline,
            RequirementCategory.qualification,
            RequirementCategory.invalid_bid,
            RequirementCategory.mandatory,
        ):
            risk = raise_to(risk, RiskLevel.high)
    elif match_status == EvidenceMatchStatus.supported:
        risk = raise_to(risk, floor)
    elif match_status == EvidenceMatchStatus.not_applicable:
        risk = raise_to(risk, RiskLevel.low)

    # Non-supported mandatory / high-stakes categories → at least high.
    if match_status != EvidenceMatchStatus.supported and (
        requirement.mandatory
        or requirement.category
        in (
            RequirementCategory.deadline,
            RequirementCategory.qualification,
            RequirementCategory.invalid_bid,
            RequirementCategory.mandatory,
        )
    ):
        risk = raise_to(risk, RiskLevel.high)

    return risk


def _lexical_overlap_score(query: str, content: str) -> float:
    normalized = soft_normalize_for_grounding(query)
    q_tokens = {t for t in _TOKEN_SPLIT_RE.split(normalized) if len(t) >= 2}
    if not q_tokens:
        return 0.0
    hay = soft_normalize_for_grounding(content)
    hits = sum(1 for t in q_tokens if t in hay)
    return hits / max(len(q_tokens), 1)


def requirement_constraints_supported(requirement_text: str, evidence_text: str) -> bool:
    """Factual critical tokens from the requirement must appear in company evidence.

    Obligation/modality words (须/应/…) are ignored — they belong to the tender
    clause, not company materials.
    """
    tokens = [
        t for t in extract_critical_tokens(requirement_text) if t not in _MODALITY_TOKENS
    ]
    if not tokens:
        return True
    hay = soft_normalize_for_grounding(evidence_text)
    for tok in tokens:
        needle = soft_normalize_for_grounding(tok)
        if needle and needle not in hay:
            return False
    return True


def _summary_tokens_ok(summary: str, requirement_text: str, evidence_text: str) -> bool:
    """Every critical token in summary must appear in requirement or company evidence."""
    tokens = extract_critical_tokens(summary)
    if not tokens:
        return True
    combined = f"{requirement_text or ''}\n{evidence_text or ''}"
    hay = soft_normalize_for_grounding(combined)
    for tok in tokens:
        needle = soft_normalize_for_grounding(tok)
        if needle and needle not in hay:
            return False
    return True


def _is_protected_match(match: RequirementEvidenceMatch) -> bool:
    """Manual / imported / human-reviewed / superseded matches must never be force-deleted."""
    if match.is_review_protected:
        return True
    if match.review_status != MatchReviewStatus.pending:
        return True
    if match.lifecycle_status == "superseded":
        return True
    meta = match.metadata_json or {}
    if meta.get("source") != AUTO_SOURCE:
        return True
    if meta.get("reviewed") is True:
        return True
    return meta.get("review_status") == "reviewed"  # legacy


def _is_active_protected_match(match: RequirementEvidenceMatch) -> bool:
    """Active match that must be skipped from subsequent LLM matching."""
    if match.lifecycle_status != "active":
        return False
    if match.is_review_protected:
        return True
    return match.review_status != MatchReviewStatus.pending


def _batched(items: list[Any], size: int) -> list[list[Any]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


class RequirementMatchService:
    def __init__(self, db: Session, llm: LlmClient | None = None) -> None:
        self.db = db
        self.llm = llm if llm is not None else get_llm_client()

    # ---------------------------------------------------------------- API ops

    def start_matching(
        self,
        project_id: UUID,
        request: MatchStartRequest,
    ) -> MatchRunResponse:
        project = self._require_project(project_id)
        doc_types = [
            t for t in request.document_types if t not in EXCLUDED_MATCH_DOCUMENT_TYPES
        ] or list(DEFAULT_MATCH_DOCUMENT_TYPES)

        run = RequirementMatchRun(
            project_id=project.id,
            status=ExtractionRunStatus.queued,
            requirement_ids_json=[str(i) for i in request.requirement_ids] or None,
            document_ids_json=[str(i) for i in request.document_ids] or None,
            document_types_json=[t.value for t in doc_types],
            config_json={
                "force": request.force,
                "batch_size": BATCH_SIZE,
                "model": getattr(self.llm, "model", None),
            },
        )
        self.db.add(run)
        self.db.commit()
        self.db.refresh(run)
        return MatchRunResponse.model_validate(run)

    def get_run(self, project_id: UUID, run_id: UUID) -> MatchRunResponse:
        self._require_project(project_id)
        run = self.db.get(RequirementMatchRun, run_id)
        if run is None or run.project_id != project_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="匹配任务不存在",
            )
        return MatchRunResponse.model_validate(run)

    def cancel_run(self, project_id: UUID, run_id: UUID) -> MatchRunResponse:
        self._require_project(project_id)
        run = self.db.execute(
            select(RequirementMatchRun)
            .where(
                RequirementMatchRun.id == run_id,
                RequirementMatchRun.project_id == project_id,
            )
            .with_for_update()
        ).scalar_one_or_none()
        if run is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="匹配任务不存在",
            )
        if run.status in (
            ExtractionRunStatus.succeeded,
            ExtractionRunStatus.failed,
            ExtractionRunStatus.cancelled,
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="任务已结束，无法取消",
            )
        if run.status not in (
            ExtractionRunStatus.queued,
            ExtractionRunStatus.running,
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="当前状态不可取消",
            )
        cfg = dict(run.config_json or {})
        cfg["cancel_requested"] = True
        run.config_json = cfg
        run.status = ExtractionRunStatus.cancelled
        run.finished_at = datetime.now(UTC)
        run.error_summary = "任务已取消，未写入匹配结果"
        self.db.commit()
        self.db.refresh(run)
        return MatchRunResponse.model_validate(run)

    def _is_cancel_requested(self, run_id: UUID) -> bool:
        run = self.db.get(RequirementMatchRun, run_id)
        if run is None:
            return True
        self.db.refresh(run)
        if run.status == ExtractionRunStatus.cancelled:
            return True
        return bool((run.config_json or {}).get("cancel_requested"))

    def _mark_cancelled(self, run: RequirementMatchRun, *, processed: int, failed: int) -> None:
        run.status = ExtractionRunStatus.cancelled
        run.finished_at = datetime.now(UTC)
        run.processed_requirements = processed
        run.failed_requirement_count = failed
        run.error_summary = "任务已取消，未写入匹配结果"
        cfg = dict(run.config_json or {})
        cfg["cancel_requested"] = True
        cfg["result_kind"] = "invalid_or_incomplete_result"
        run.config_json = cfg
        self.db.commit()

    def list_matches(
        self,
        project_id: UUID,
        *,
        requirement_id: UUID | None = None,
        match_status: EvidenceMatchStatus | None = None,
        risk_level: RiskLevel | None = None,
        category: RequirementCategory | None = None,
        mandatory: bool | None = None,
        needs_review: bool | None = None,
        review_status: MatchReviewStatus | None = None,
        source_document_id: UUID | None = None,
        page: int = 1,
        limit: int = 50,
        offset: int | None = None,
    ) -> MatchListResponse:
        self._require_project(project_id)
        if page < 1:
            page = 1
        if offset is None:
            offset = (page - 1) * limit

        stmt = (
            select(RequirementEvidenceMatch)
            .join(Requirement, Requirement.id == RequirementEvidenceMatch.requirement_id)
            .where(RequirementEvidenceMatch.project_id == project_id)
        )
        count_stmt = (
            select(func.count())
            .select_from(RequirementEvidenceMatch)
            .join(Requirement, Requirement.id == RequirementEvidenceMatch.requirement_id)
            .where(RequirementEvidenceMatch.project_id == project_id)
        )

        if requirement_id is not None:
            stmt = stmt.where(RequirementEvidenceMatch.requirement_id == requirement_id)
            count_stmt = count_stmt.where(
                RequirementEvidenceMatch.requirement_id == requirement_id
            )
        if match_status is not None:
            stmt = stmt.where(RequirementEvidenceMatch.status == match_status)
            count_stmt = count_stmt.where(RequirementEvidenceMatch.status == match_status)
        if risk_level is not None:
            stmt = stmt.where(RequirementEvidenceMatch.risk_level == risk_level)
            count_stmt = count_stmt.where(RequirementEvidenceMatch.risk_level == risk_level)
        if category is not None:
            stmt = stmt.where(Requirement.category == category)
            count_stmt = count_stmt.where(Requirement.category == category)
        if mandatory is not None:
            stmt = stmt.where(Requirement.mandatory == mandatory)
            count_stmt = count_stmt.where(Requirement.mandatory == mandatory)
        if needs_review is not None:
            stmt = stmt.where(RequirementEvidenceMatch.needs_review == needs_review)
            count_stmt = count_stmt.where(
                RequirementEvidenceMatch.needs_review == needs_review
            )
        if review_status is not None:
            stmt = stmt.where(RequirementEvidenceMatch.review_status == review_status)
            count_stmt = count_stmt.where(
                RequirementEvidenceMatch.review_status == review_status
            )
        if source_document_id is not None:
            stmt = stmt.where(Requirement.source_document_id == source_document_id)
            count_stmt = count_stmt.where(
                Requirement.source_document_id == source_document_id
            )

        total = int(self.db.scalar(count_stmt) or 0)
        rows = list(
            self.db.scalars(
                stmt.options(
                    selectinload(RequirementEvidenceMatch.requirement).selectinload(
                        Requirement.source_document
                    ),
                    selectinload(RequirementEvidenceMatch.primary_company_document),
                    selectinload(RequirementEvidenceMatch.reviews),
                )
                .order_by(RequirementEvidenceMatch.created_at.desc())
                .offset(offset)
                .limit(limit)
            )
        )
        items = [self._to_summary(m) for m in rows]
        return MatchListResponse(
            items=items,
            total=total,
            page=page,
            limit=limit,
            offset=offset,
        )

    def get_match(self, project_id: UUID, match_id: UUID) -> MatchDetail:
        self._require_project(project_id)
        match = self.db.scalar(
            select(RequirementEvidenceMatch)
            .where(
                RequirementEvidenceMatch.id == match_id,
                RequirementEvidenceMatch.project_id == project_id,
            )
            .options(
                selectinload(RequirementEvidenceMatch.requirement)
                .selectinload(Requirement.evidence_links)
                .selectinload(EvidenceLink.document),
                selectinload(RequirementEvidenceMatch.requirement)
                .selectinload(Requirement.evidence_links)
                .selectinload(EvidenceLink.chunk),
                selectinload(RequirementEvidenceMatch.requirement).selectinload(
                    Requirement.source_document
                ),
                selectinload(RequirementEvidenceMatch.company_links).selectinload(
                    RequirementEvidenceMatchLink.document
                ),
                selectinload(RequirementEvidenceMatch.company_links).selectinload(
                    RequirementEvidenceMatchLink.chunk
                ),
                selectinload(RequirementEvidenceMatch.primary_company_document),
                selectinload(RequirementEvidenceMatch.primary_company_chunk),
                selectinload(RequirementEvidenceMatch.reviews),
            )
        )
        if match is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="匹配结果不存在",
            )
        summary = self._to_summary(match)
        tender_evidence: list[EvidenceLinkRead] = []
        req = match.requirement
        for elink in req.evidence_links if req else []:
            path = None
            if elink.document_id and elink.chunk_id:
                path = document_center_path(project_id, elink.document_id, elink.chunk_id)
            tender_evidence.append(
                EvidenceLinkRead(
                    id=elink.id,
                    requirement_id=elink.requirement_id,
                    document_id=elink.document_id,
                    chunk_id=elink.chunk_id,
                    evidence_type=elink.evidence_type,
                    confidence=elink.confidence,
                    notes=elink.notes,
                    created_at=elink.created_at,
                    updated_at=elink.updated_at,
                    document_file_name=elink.document.file_name if elink.document else None,
                    document_type=(
                        elink.document.document_type.value if elink.document else None
                    ),
                    chunk_index=elink.chunk.chunk_index if elink.chunk else None,
                    section=elink.chunk.section if elink.chunk else None,
                    clause_id=elink.chunk.clause_id if elink.chunk else None,
                    page_start=elink.chunk.page_start if elink.chunk else None,
                    page_end=elink.chunk.page_end if elink.chunk else None,
                    document_center_path=path,
                )
            )

        company_links: list[CompanyEvidenceLinkRead] = []
        for clink in match.company_links:
            path = None
            if clink.document_id and clink.chunk_id:
                path = document_center_path(project_id, clink.document_id, clink.chunk_id)
            company_links.append(
                CompanyEvidenceLinkRead(
                    id=clink.id,
                    match_id=clink.match_id,
                    document_id=clink.document_id,
                    chunk_id=clink.chunk_id,
                    quote=clink.quote,
                    notes=clink.notes,
                    role=clink.role,
                    created_at=clink.created_at,
                    updated_at=clink.updated_at,
                    document_file_name=clink.document.file_name if clink.document else None,
                    document_type=(
                        clink.document.document_type.value if clink.document else None
                    ),
                    chunk_index=clink.chunk.chunk_index if clink.chunk else None,
                    section=clink.chunk.section if clink.chunk else None,
                    clause_id=clink.chunk.clause_id if clink.chunk else None,
                    page_start=clink.chunk.page_start if clink.chunk else None,
                    page_end=clink.chunk.page_end if clink.chunk else None,
                    document_center_path=path,
                )
            )

        return MatchDetail(
            **summary.model_dump(),
            tender_evidence_links=tender_evidence,
            company_links=company_links,
            requirement_category=req.category if req else None,
            requirement_mandatory=req.mandatory if req else None,
        )

    # ------------------------------------------------------------- background

    def execute_run(self, run_id: UUID) -> None:
        run = self.db.get(RequirementMatchRun, run_id)
        if run is None:
            logger.warning("Match run %s missing", run_id)
            return
        if run.status == ExtractionRunStatus.cancelled:
            return
        if self._is_cancel_requested(run_id):
            self._mark_cancelled(run, processed=0, failed=0)
            return

        run.status = ExtractionRunStatus.running
        run.started_at = datetime.now(UTC)
        run.error_summary = None
        self.db.commit()

        acc = _RunAccumulator()
        try:
            if self._is_cancel_requested(run_id):
                self._mark_cancelled(run, processed=0, failed=0)
                return

            force = bool((run.config_json or {}).get("force"))
            all_requirements = self._load_requirements(run)
            protected_req_ids, skipped_reviewed_count = (
                self._protected_requirement_ids(run.project_id, all_requirements)
            )
            requirements = [
                r for r in all_requirements if r.id not in protected_req_ids
            ]
            company_chunks = self._load_company_chunks(run)
            scoped_requirement_ids = {r.id for r in requirements}

            run.total_requirements = len(requirements)
            run.protected_requirement_count = len(protected_req_ids)
            run.skipped_reviewed_requirement_count = skipped_reviewed_count
            run.config_json = {
                **(run.config_json or {}),
                "scoped_requirement_ids": [
                    str(i) for i in sorted(scoped_requirement_ids, key=str)
                ],
                "protected_requirement_ids": [
                    str(i) for i in sorted(protected_req_ids, key=str)
                ],
                "protected_requirement_count": len(protected_req_ids),
                "skipped_reviewed_requirement_count": skipped_reviewed_count,
                "company_chunk_count": len(company_chunks),
            }
            self.db.commit()

            if self._is_cancel_requested(run_id):
                self._mark_cancelled(run, processed=0, failed=0)
                return

            if not requirements:
                # All in-scope requirements are protected/skipped — legal empty success.
                run.status = ExtractionRunStatus.succeeded
                run.finished_at = datetime.now(UTC)
                run.processed_requirements = 0
                run.error_summary = None
                run.config_json = {
                    **(run.config_json or {}),
                    "result_kind": (
                        "all_requirements_protected"
                        if protected_req_ids
                        else "empty_requirements"
                    ),
                }
                self.db.commit()
                return

            if not company_chunks:
                # Empty company materials: do NOT call LLM; fail run; keep old matches.
                # Never produce not_applicable from empty materials.
                run.status = ExtractionRunStatus.failed
                run.finished_at = datetime.now(UTC)
                run.processed_requirements = 0
                run.error_summary = (
                    "企业材料为空：当前范围内无可用企业侧文档 chunks，"
                    "未调用 LLM，已保留旧匹配结果。"
                )
                run.config_json = {
                    **(run.config_json or {}),
                    "result_kind": "empty_company_materials",
                }
                self.db.commit()
                return

            batch_fatal = False
            cancelled = False
            for batch in _batched(requirements, BATCH_SIZE):
                if self._is_cancel_requested(run_id):
                    cancelled = True
                    acc.validated.clear()
                    break
                try:
                    batch_result = self._match_batch(
                        batch, company_chunks, run.project_id, run_id=run_id
                    )
                    if batch_result is None:
                        cancelled = True
                        acc.validated.clear()
                        break
                    llm_validated, raw_n, rejected_n, reason_counts = batch_result
                    # Cancel may arrive during/after LLM; discard before persisting.
                    if self._is_cancel_requested(run_id):
                        cancelled = True
                        acc.validated.clear()
                        break
                    acc.raw_item_count += raw_n
                    acc.rejected_count += rejected_n
                    acc.add_reject_reasons(reason_counts)
                    acc.llm_validated_count += len(llm_validated)
                    acc.validated.extend(llm_validated)
                except MatchValidationError as exc:
                    batch_fatal = True
                    logger.warning(
                        "Match batch validation failed run=%s: %s",
                        run_id,
                        exc,
                    )
                    acc.failed_requirement_count += len(batch)
                    acc.errors.append(str(exc))
                    acc.add_reject_reasons(exc.reason_counts)
                    acc.rejected_count += sum(exc.reason_counts.values()) or len(batch)
                    acc.validated.clear()
                    break
                except Exception as exc:  # noqa: BLE001
                    batch_fatal = True
                    logger.warning(
                        "Match batch failed run=%s: %s",
                        run_id,
                        type(exc).__name__,
                    )
                    acc.failed_requirement_count += len(batch)
                    acc.errors.append(f"{type(exc).__name__}: {exc}")
                    acc.validated.clear()
                    # Fatal batch → remaining requirements are incomplete; stop early.
                    break
                finally:
                    if not cancelled:
                        acc.processed_requirements += len(batch)
                        run.processed_requirements = acc.processed_requirements
                        run.failed_requirement_count = acc.failed_requirement_count
                        self.db.commit()

            if cancelled or self._is_cancel_requested(run_id):
                acc.validated.clear()
                self._mark_cancelled(
                    run,
                    processed=acc.processed_requirements,
                    failed=acc.failed_requirement_count,
                )
                return

            # Completeness: every selected Requirement must have exactly one legal result.
            covered = {v.requirement.id for v in acc.validated}
            missing = [r for r in requirements if r.id not in covered]
            if missing and not batch_fatal:
                batch_fatal = True
                miss_n = len(missing)
                acc.failed_requirement_count += miss_n
                acc.add_reject_reasons({"missing_requirement_result": miss_n})
                acc.errors.append(
                    f"missing_requirement_result: {miss_n} requirement(s) lack legal results"
                )
                acc.validated.clear()

            all_rejected = (
                not batch_fatal
                and acc.raw_item_count > 0
                and acc.llm_validated_count == 0
            )
            # ANY batch fatal OR all candidates rejected → invalid for both force modes.
            invalid_or_incomplete = batch_fatal or all_rejected

            if invalid_or_incomplete:
                reasons = list(acc.errors)
                if all_rejected:
                    reasons.append(
                        f"模型输出候选 {acc.raw_item_count} 条，"
                        f"全部未通过证据校验（拒绝 {acc.rejected_count}）"
                    )
                if acc.reject_reason_counts:
                    counts_txt = ", ".join(
                        f"{k}={v}" for k, v in sorted(acc.reject_reason_counts.items())
                    )
                    reasons.append(f"reject_reasons: {counts_txt}")
                if batch_fatal:
                    prefix = (
                        "force 重跑中止：结果无效或不完整，已保留旧自动匹配结果。 "
                        if force
                        else "匹配中止：结果无效或不完整，未写入匹配结果。 "
                    )
                else:
                    prefix = (
                        "force 重跑中止：结果无效或不完整，已保留旧自动匹配结果。 "
                        if force
                        else "匹配中止：候选全部未通过证据校验，未写入匹配结果。 "
                    )
                run.status = ExtractionRunStatus.failed
                run.finished_at = datetime.now(UTC)
                run.error_summary = (prefix + "; ".join(reasons))[:2000]
                run.failed_requirement_count = acc.failed_requirement_count
                run.processed_requirements = acc.processed_requirements
                run.config_json = {
                    **(run.config_json or {}),
                    "result_kind": "invalid_or_incomplete_result",
                    "raw_item_count": acc.raw_item_count,
                    "rejected_count": acc.rejected_count,
                    "reject_reason_counts": acc.reject_reason_counts,
                }
                # Discard in-memory results — zero writes / deletes.
                acc.validated.clear()
                self.db.commit()
                return

            if self._is_cancel_requested(run_id):
                acc.validated.clear()
                self._mark_cancelled(
                    run,
                    processed=acc.processed_requirements,
                    failed=acc.failed_requirement_count,
                )
                return

            stats = self._persist_matches(
                run.project_id,
                run.id,
                acc.validated,
                force=force,
                scoped_requirement_ids=scoped_requirement_ids,
                processed_requirements=acc.processed_requirements,
                failed_requirement_count=acc.failed_requirement_count,
                raw_item_count=acc.raw_item_count,
                rejected_count=acc.rejected_count,
                reject_reason_counts=acc.reject_reason_counts,
            )

            if stats.get("aborted_cancelled"):
                # Persist lost the race to cancel — keep cancelled, zero writes.
                return

            # Succeeded status + counts already committed inside _persist_matches.
            return
        except Exception as exc:
            logger.exception("Match run %s crashed", run_id)
            self.db.rollback()
            run = self.db.get(RequirementMatchRun, run_id)
            if run is not None and run.status != ExtractionRunStatus.cancelled:
                # Never overwrite cancelled → failed.
                if bool((run.config_json or {}).get("cancel_requested")):
                    run.status = ExtractionRunStatus.cancelled
                    run.finished_at = datetime.now(UTC)
                    run.error_summary = "任务已取消，未写入匹配结果"
                else:
                    run.status = ExtractionRunStatus.failed
                    run.finished_at = datetime.now(UTC)
                    run.error_summary = f"{type(exc).__name__}: {exc}"[:2000]
                    run.config_json = {
                        **(run.config_json or {}),
                        "result_kind": "invalid_or_incomplete_result",
                    }
                self.db.commit()
            raise

    # --------------------------------------------------------------- internals

    def _require_project(self, project_id: UUID) -> BidProject:
        project = self.db.get(BidProject, project_id)
        if project is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="项目不存在",
            )
        return project

    def _load_requirements(self, run: RequirementMatchRun) -> list[Requirement]:
        stmt = select(Requirement).where(Requirement.project_id == run.project_id)
        if run.requirement_ids_json:
            ids: list[UUID] = []
            for raw in run.requirement_ids_json:
                try:
                    ids.append(UUID(str(raw)))
                except ValueError:
                    continue
            if ids:
                stmt = stmt.where(Requirement.id.in_(ids))
        # Only match requirements with a known review_status (all enum values are valid).
        stmt = stmt.where(Requirement.review_status.in_(list(ReviewStatus)))
        rows = list(
            self.db.scalars(
                stmt.options(
                    selectinload(Requirement.evidence_links).selectinload(
                        EvidenceLink.chunk
                    ),
                    selectinload(Requirement.evidence_links).selectinload(
                        EvidenceLink.document
                    ),
                ).order_by(Requirement.created_at.asc())
            )
        )
        return rows

    def _protected_requirement_ids(
        self,
        project_id: UUID,
        requirements: list[Requirement],
    ) -> tuple[set[UUID], int]:
        """Return requirement IDs that have an active protected match, plus skip count."""
        if not requirements:
            return set(), 0
        req_ids = {r.id for r in requirements}
        rows = list(
            self.db.scalars(
                select(RequirementEvidenceMatch).where(
                    RequirementEvidenceMatch.project_id == project_id,
                    RequirementEvidenceMatch.requirement_id.in_(req_ids),
                    RequirementEvidenceMatch.lifecycle_status == "active",
                )
            )
        )
        protected: set[UUID] = set()
        skipped_reviewed = 0
        for match in rows:
            if not _is_active_protected_match(match):
                continue
            protected.add(match.requirement_id)
            skipped_reviewed += 1
        return protected, skipped_reviewed

    def _load_company_chunks(self, run: RequirementMatchRun) -> list[_ChunkContext]:
        defaults = [t.value for t in DEFAULT_MATCH_DOCUMENT_TYPES]
        type_values = run.document_types_json or defaults
        allowed_types: list[DocumentType] = []
        for raw in type_values:
            try:
                dt = DocumentType(raw)
            except ValueError:
                continue
            if dt not in EXCLUDED_MATCH_DOCUMENT_TYPES:
                allowed_types.append(dt)
        if not allowed_types:
            allowed_types = list(DEFAULT_MATCH_DOCUMENT_TYPES)

        doc_ids: list[UUID] | None = None
        if run.document_ids_json:
            doc_ids = []
            for raw in run.document_ids_json:
                try:
                    doc_ids.append(UUID(str(raw)))
                except ValueError:
                    continue

        stmt = (
            select(DocumentChunk, Document)
            .join(Document, Document.id == DocumentChunk.document_id)
            .where(
                DocumentChunk.project_id == run.project_id,
                Document.project_id == run.project_id,
                Document.document_type.in_(allowed_types),
            )
            .order_by(Document.created_at.asc(), DocumentChunk.chunk_index.asc())
        )
        if doc_ids is not None:
            stmt = stmt.where(Document.id.in_(doc_ids))

        rows = list(self.db.execute(stmt).all())
        return [_ChunkContext(chunk=c, document=d) for c, d in rows]

    def _select_related_chunks(
        self,
        requirement: Requirement,
        company_chunks: list[_ChunkContext],
        *,
        top_k: int = TOP_CHUNKS_PER_REQUIREMENT,
    ) -> list[_ChunkContext]:
        query = " ".join(
            filter(
                None,
                [
                    requirement.title,
                    requirement.normalized_requirement,
                    requirement.source_section,
                ],
            )
        )
        scored = [
            (ctx, _lexical_overlap_score(query, ctx.chunk.content or ""))
            for ctx in company_chunks
        ]
        scored.sort(key=lambda x: (-x[1], x[0].chunk.chunk_index))
        # Prefer positive overlap; if all zero, still pass a small sample so LLM
        # can legitimately return insufficient_evidence.
        positive = [ctx for ctx, s in scored if s > 0]
        if positive:
            return positive[:top_k]
        return [ctx for ctx, _ in scored[: min(3, len(scored))]]

    def _tender_primary_contexts(
        self, requirement: Requirement
    ) -> list[_ChunkContext]:
        """Load locatable tender primary evidence chunks for a Requirement."""
        out: list[_ChunkContext] = []
        seen: set[UUID] = set()
        for elink in requirement.evidence_links or []:
            if elink.chunk_id is None or elink.chunk_id in seen:
                continue
            chunk = elink.chunk
            doc = elink.document
            if chunk is None:
                chunk = self.db.get(DocumentChunk, elink.chunk_id)
            if doc is None and elink.document_id is not None:
                doc = self.db.get(Document, elink.document_id)
            if chunk is None or doc is None:
                continue
            if chunk.project_id != requirement.project_id:
                continue
            seen.add(chunk.id)
            out.append(_ChunkContext(chunk=chunk, document=doc))
        return out

    def _match_batch(
        self,
        requirements: list[Requirement],
        company_chunks: list[_ChunkContext],
        project_id: UUID,
        *,
        run_id: UUID | None = None,
    ) -> tuple[list[_ValidatedMatch], int, int, dict[str, int]] | None:
        """Returns (validated, raw_count, rejected, reason_counts) or None if cancelled.

        Any validation reject / missing / duplicate raises MatchValidationError.
        """
        per_req_chunks: dict[UUID, list[_ChunkContext]] = {}
        per_req_tender: dict[UUID, list[_ChunkContext]] = {}
        allowed_chunk_ids: set[UUID] = set()
        tender_by_id: dict[UUID, _ChunkContext] = {}
        for req in requirements:
            selected = self._select_related_chunks(req, company_chunks)
            per_req_chunks[req.id] = selected
            for ctx in selected:
                allowed_chunk_ids.add(ctx.chunk.id)
            tender_ctxs = self._tender_primary_contexts(req)
            per_req_tender[req.id] = tender_ctxs
            for ctx in tender_ctxs:
                tender_by_id[ctx.chunk.id] = ctx

        by_chunk_id = {
            ctx.chunk.id: ctx
            for ctx in company_chunks
            if ctx.chunk.id in allowed_chunk_ids
        }

        payload = {
            "requirements": [
                {
                    "requirement_id": str(req.id),
                    "category": req.category.value,
                    "title": req.title,
                    "normalized_requirement": req.normalized_requirement,
                    "mandatory": req.mandatory,
                    "risk_level": req.risk_level.value if req.risk_level else None,
                    "potential_conflict": bool(
                        (req.metadata_json or {}).get("potential_conflict")
                    ),
                    "candidate_chunk_ids": [
                        str(c.chunk.id) for c in per_req_chunks.get(req.id, [])
                    ],
                    "tender_primary_evidence_chunks": [
                        {
                            "chunk_id": str(ctx.chunk.id),
                            "document_id": str(ctx.document.id),
                            "document_type": ctx.document.document_type.value,
                            "file_name": ctx.document.file_name,
                            "section": ctx.chunk.section,
                            "clause_id": ctx.chunk.clause_id,
                            "page_start": ctx.chunk.page_start,
                            "page_end": ctx.chunk.page_end,
                            "content": ctx.chunk.content,
                        }
                        for ctx in per_req_tender.get(req.id, [])
                    ],
                }
                for req in requirements
            ],
            "company_chunks": [
                {
                    "chunk_id": str(ctx.chunk.id),
                    "document_id": str(ctx.document.id),
                    "document_type": ctx.document.document_type.value,
                    "file_name": ctx.document.file_name,
                    "section": ctx.chunk.section,
                    "clause_id": ctx.chunk.clause_id,
                    "page_start": ctx.chunk.page_start,
                    "page_end": ctx.chunk.page_end,
                    "content": ctx.chunk.content,
                }
                for ctx in by_chunk_id.values()
            ],
        }
        user_content = (
            "请对企业材料与招标 Requirement 做证据匹配。只输出一个 JSON 对象。\n"
            "schema：\n"
            '{"items":[{"requirement_id":"<UUID>","status":"supported|partially_supported|'
            'insufficient_evidence|conflicting_evidence|not_applicable",'
            '"summary":"仅描述证据关系","primary_company_chunk_id":"<chunk UUID 或 null>",'
            '"company_evidence_quote":"原文连续短引文或 null",'
            '"conflicting_company_chunk_id":"<冲突侧 chunk UUID 或 null>",'
            '"conflicting_company_evidence_quote":"冲突侧原文引文或 null",'
            '"conflict_dimension":"qualification_level|certificate_validity|effective_period|'
            'quantity|coverage_scope|technical_parameter|affirmative_negation|null",'
            '"conflict_subject":"冲突主体或 null",'
            '"primary_claim_value":"支持侧主张值或 null",'
            '"conflicting_claim_value":"冲突侧主张值或 null",'
            '"conflict_note":"冲突说明或 null",'
            '"not_applicable_basis":'
            '"requirement_scope_exclusion|project_scope_exclusion|null",'
            '"requirement_scope_chunk_id":"<招标主证据 chunk UUID 或 null>",'
            '"requirement_scope_quote":"招标范围限定引文或 null",'
            '"current_scope_chunk_id":"<企业侧当前范围 chunk UUID 或 null>",'
            '"current_scope_quote":"当前对象范围引文或 null",'
            '"not_applicable_note":"不适用说明或 null",'
            '"needs_review":true}]}\n'
            "<<<MATCH_INPUT>>>\n"
            + json.dumps(payload, ensure_ascii=False)
        )
        result = self.llm.chat(
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            max_tokens=max(getattr(self.llm, "max_tokens", 1024) or 1024, 2048),
            request_id=str(uuid.uuid4()),
        )
        if run_id is not None and self._is_cancel_requested(run_id):
            return None
        try:
            data = _parse_llm_json(result.content)
            batch_result = MatchBatchResult.model_validate(data)
        except Exception as exc:  # noqa: BLE001
            raise LlmError(
                "大模型返回的 JSON 无效",
                detail=f"{type(exc).__name__}: {exc}",
            ) from exc

        req_by_id = {r.id: r for r in requirements}
        validated: list[_ValidatedMatch] = []
        rejected = 0
        reason_counts: dict[str, int] = {}
        seen_req: set[UUID] = set()
        reject_details: list[str] = []

        def _bump(reason: str) -> None:
            nonlocal rejected
            rejected += 1
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
            reject_details.append(reason)

        for item in batch_result.items:
            if item.requirement_id in seen_req:
                _bump("duplicate_requirement")
                continue
            seen_req.add(item.requirement_id)
            ok, reason = self._validate_candidate(
                item,
                req_by_id=req_by_id,
                by_chunk_id=by_chunk_id,
                per_req_chunks=per_req_chunks,
                per_req_tender=per_req_tender,
                tender_by_id=tender_by_id,
                project_id=project_id,
            )
            if ok is not None:
                validated.append(ok)
            else:
                _bump(reason or "validation_rejected")
                logger.info(
                    "Rejected match candidate requirement_id=%s reason=%s",
                    item.requirement_id,
                    reason,
                )

        # Every batch requirement must appear exactly once in validated results.
        validated_ids = {v.requirement.id for v in validated}
        for req in requirements:
            if req.id not in validated_ids and req.id not in seen_req:
                _bump("missing_requirement_result")

        if rejected > 0 or len(validated) != len(requirements):
            # Mixed valid/invalid or incomplete → fail whole run (no partial keep).
            raise MatchValidationError(
                "batch validation failed: "
                + ", ".join(f"{k}={v}" for k, v in sorted(reason_counts.items())),
                reasons=reject_details,
                reason_counts=reason_counts,
            )
        return validated, len(batch_result.items), rejected, reason_counts

    def _location_meta_from_ctx(
        self, ctx: _ChunkContext, project_id: UUID
    ) -> dict[str, Any]:
        return {
            "document_id": str(ctx.document.id),
            "chunk_id": str(ctx.chunk.id),
            "file_name": ctx.document.file_name,
            "document_type": ctx.document.document_type.value,
            "section": ctx.chunk.section,
            "clause_id": ctx.chunk.clause_id,
            "page_start": ctx.chunk.page_start,
            "page_end": ctx.chunk.page_end,
            "chunk_index": ctx.chunk.chunk_index,
            "document_center_path": document_center_path(
                project_id, ctx.document.id, ctx.chunk.id
            ),
        }

    def _validate_candidate(
        self,
        item: MatchCandidateItem,
        *,
        req_by_id: dict[UUID, Requirement],
        by_chunk_id: dict[UUID, _ChunkContext],
        per_req_chunks: dict[UUID, list[_ChunkContext]],
        per_req_tender: dict[UUID, list[_ChunkContext]],
        tender_by_id: dict[UUID, _ChunkContext],
        project_id: UUID,
    ) -> tuple[_ValidatedMatch | None, str | None]:
        requirement = req_by_id.get(item.requirement_id)
        if requirement is None or requirement.project_id != project_id:
            return None, "unknown_requirement"

        status_val = item.status
        primary_ctx: _ChunkContext | None = None
        quote: str | None = None

        allowed_for_req = {c.chunk.id for c in per_req_chunks.get(requirement.id, [])}
        tender_ids = {c.chunk.id for c in per_req_tender.get(requirement.id, [])}

        if status_val == EvidenceMatchStatus.not_applicable:
            return self._validate_not_applicable(
                item,
                requirement=requirement,
                by_chunk_id=by_chunk_id,
                allowed_for_req=allowed_for_req,
                tender_ids=tender_ids,
                tender_by_id=tender_by_id,
                project_id=project_id,
            )

        if status_val in _STATUS_REQUIRING_PRIMARY:
            if item.primary_company_chunk_id is None or not item.company_evidence_quote:
                return None, "missing_required_evidence"
            if item.primary_company_chunk_id not in by_chunk_id:
                return None, "unknown_chunk"
            if item.primary_company_chunk_id not in allowed_for_req:
                return None, "out_of_scope_chunk"
            primary_ctx = by_chunk_id[item.primary_company_chunk_id]
            if primary_ctx.document.document_type in EXCLUDED_MATCH_DOCUMENT_TYPES:
                return None, "out_of_scope_chunk"
            if primary_ctx.document.project_id != project_id:
                return None, "cross_project_chunk"
            if not quote_in_content(
                item.company_evidence_quote, primary_ctx.chunk.content or ""
            ):
                return None, "quote_not_found"
            quote = normalize_whitespace(item.company_evidence_quote)
        else:
            if item.primary_company_chunk_id is not None:
                if item.primary_company_chunk_id not in by_chunk_id:
                    return None, "unknown_chunk"
                if item.primary_company_chunk_id not in allowed_for_req:
                    return None, "out_of_scope_chunk"
                primary_ctx = by_chunk_id[item.primary_company_chunk_id]
                if item.company_evidence_quote:
                    if not quote_in_content(
                        item.company_evidence_quote, primary_ctx.chunk.content or ""
                    ):
                        return None, "quote_not_found"
                    quote = normalize_whitespace(item.company_evidence_quote)

        conflict_ctx: _ChunkContext | None = None
        conflict_quote: str | None = None
        if status_val == EvidenceMatchStatus.conflicting_evidence:
            dual, reason = self._validate_conflict_dual(
                item,
                primary_ctx=primary_ctx,
                primary_quote=quote,
                by_chunk_id=by_chunk_id,
                allowed_for_req=allowed_for_req,
                project_id=project_id,
            )
            if dual is None:
                # Illegal conflict → whole-run validation fail (no downgrade).
                return None, reason or "invalid_conflict"
            conflict_ctx, conflict_quote = dual

        evidence_text = ""
        if primary_ctx is not None:
            evidence_text = primary_ctx.chunk.content or ""
            if quote:
                evidence_text = f"{quote}\n{evidence_text}"
        if conflict_ctx is not None:
            conflict_body = conflict_ctx.chunk.content or ""
            if conflict_quote:
                conflict_body = f"{conflict_quote}\n{conflict_body}"
            evidence_text = f"{evidence_text}\n{conflict_body}"

        req_text = requirement.normalized_requirement or requirement.title or ""

        meta_extra: dict[str, Any] = {}
        if status_val == EvidenceMatchStatus.supported and grade_mismatch(
            req_text, evidence_text
        ):
            status_val = EvidenceMatchStatus.partially_supported
            meta_extra["grade_downgrade"] = True
            meta_extra["downgrade_reason"] = "requirement_grade_exceeds_evidence"

        if status_val == EvidenceMatchStatus.supported:
            if primary_ctx is None or not quote:
                return None, "missing_required_evidence"
            if not requirement_constraints_supported(req_text, evidence_text):
                status_val = EvidenceMatchStatus.partially_supported
                meta_extra["critical_token_downgrade"] = True

        summary = normalize_whitespace(item.summary)
        if not summary:
            return None, "invalid_summary"
        if not _summary_tokens_ok(summary, req_text, evidence_text):
            return None, "fabricated_summary"

        banned = ("必然中标", "完全满足", "一定不符合", "必然不符合", "企业不符合")
        if any(b in summary for b in banned):
            return None, "banned_absolute_summary"

        if item.conflict_note:
            note = normalize_whitespace(item.conflict_note)
            if note and not _summary_tokens_ok(note, req_text, evidence_text):
                return None, "fabricated_summary"

        company_links: list[tuple[Document, DocumentChunk, str, str]] = []
        if status_val == EvidenceMatchStatus.conflicting_evidence:
            if primary_ctx is None or not quote or conflict_ctx is None or not conflict_quote:
                return None, "invalid_conflict"
            company_links.append(
                (primary_ctx.document, primary_ctx.chunk, quote, "company_support")
            )
            company_links.append(
                (
                    conflict_ctx.document,
                    conflict_ctx.chunk,
                    conflict_quote,
                    "company_conflict",
                )
            )
            meta_extra["conflict_dimension"] = item.conflict_dimension
            meta_extra["conflict_subject"] = item.conflict_subject
            meta_extra["primary_claim_value"] = item.primary_claim_value
            meta_extra["conflicting_claim_value"] = item.conflicting_claim_value
            if item.conflict_note:
                meta_extra["conflict_note"] = normalize_whitespace(item.conflict_note)
        elif primary_ctx is not None and quote:
            company_links.append(
                (primary_ctx.document, primary_ctx.chunk, quote, "company_support")
            )

        for cid in item.additional_company_chunk_ids:
            if cid == (primary_ctx.chunk.id if primary_ctx else None):
                continue
            if conflict_ctx is not None and cid == conflict_ctx.chunk.id:
                continue
            if cid not in by_chunk_id or cid not in allowed_for_req:
                return None, "out_of_scope_chunk"
            ctx = by_chunk_id[cid]
            if quote and not quote_in_content(quote, ctx.chunk.content or ""):
                continue
            company_links.append(
                (ctx.document, ctx.chunk, quote or "", "company_support")
            )

        needs_review = True
        req_meta = requirement.metadata_json or {}
        if req_meta.get("potential_conflict"):
            needs_review = True
            meta_extra["requirement_potential_conflict"] = True
            meta_extra["conflict_inheritance_note"] = (
                item.conflict_note or "招标要求本身存在待确认冲突"
            )

        return (
            _ValidatedMatch(
                item=item,
                requirement=requirement,
                status=status_val,
                summary=summary,
                risk_level=risk_for_match(requirement, status_val),
                primary_chunk=primary_ctx.chunk if primary_ctx else None,
                primary_document=primary_ctx.document if primary_ctx else None,
                primary_quote=quote,
                company_links=company_links,
                needs_review=needs_review,
                metadata_extra=meta_extra,
            ),
            None,
        )

    def _validate_conflict_dual(
        self,
        item: MatchCandidateItem,
        *,
        primary_ctx: _ChunkContext | None,
        primary_quote: str | None,
        by_chunk_id: dict[UUID, _ChunkContext],
        allowed_for_req: set[UUID],
        project_id: UUID,
    ) -> tuple[tuple[_ChunkContext, str] | None, str | None]:
        if primary_ctx is None or not primary_quote:
            return None, "missing_required_evidence"
        if (
            item.conflicting_company_chunk_id is None
            or not item.conflicting_company_evidence_quote
        ):
            return None, "invalid_conflict"
        cid = item.conflicting_company_chunk_id
        if cid not in by_chunk_id:
            return None, "unknown_chunk"
        if cid not in allowed_for_req:
            return None, "out_of_scope_chunk"
        conflict_ctx = by_chunk_id[cid]
        ok, reason = validate_direct_company_conflict(
            primary_chunk_id=primary_ctx.chunk.id,
            primary_quote=primary_quote,
            conflict_chunk_id=conflict_ctx.chunk.id,
            conflict_quote=item.conflicting_company_evidence_quote,
            primary_project_id=primary_ctx.document.project_id,
            conflict_project_id=conflict_ctx.document.project_id,
            primary_doc_type_excluded=(
                primary_ctx.document.document_type in EXCLUDED_MATCH_DOCUMENT_TYPES
            ),
            conflict_doc_type_excluded=(
                conflict_ctx.document.document_type in EXCLUDED_MATCH_DOCUMENT_TYPES
            ),
            allowed_chunk_ids=allowed_for_req,
            conflict_dimension=item.conflict_dimension,
            conflict_subject=item.conflict_subject,
            primary_claim_value=item.primary_claim_value,
            conflicting_claim_value=item.conflicting_claim_value,
            primary_chunk_content=primary_ctx.chunk.content or "",
            conflict_chunk_content=conflict_ctx.chunk.content or "",
        )
        if not ok:
            return None, reason or "invalid_conflict"
        if primary_ctx.document.project_id != project_id:
            return None, "cross_project_chunk"
        conflict_quote = normalize_whitespace(item.conflicting_company_evidence_quote)
        if item.conflict_note:
            support_hay = f"{primary_quote}\n{primary_ctx.chunk.content or ''}"
            conflict_hay = f"{conflict_quote}\n{conflict_ctx.chunk.content or ''}"
            combined = f"{support_hay}\n{conflict_hay}"
            hay = soft_normalize_for_grounding(combined)
            for tok in extract_critical_tokens(item.conflict_note):
                needle = soft_normalize_for_grounding(tok)
                if needle and needle not in hay:
                    return None, "fabricated_summary"
        return (conflict_ctx, conflict_quote), None

    def _validate_not_applicable(
        self,
        item: MatchCandidateItem,
        *,
        requirement: Requirement,
        by_chunk_id: dict[UUID, _ChunkContext],
        allowed_for_req: set[UUID],
        tender_ids: set[UUID],
        tender_by_id: dict[UUID, _ChunkContext],
        project_id: UUID,
    ) -> tuple[_ValidatedMatch | None, str | None]:
        """Dual-scope not_applicable; legacy single-evidence fields alone → reject."""
        # Legacy single-evidence path is no longer accepted.
        if (
            item.not_applicable_evidence_chunk_id is not None
            or item.not_applicable_evidence_quote
        ) and (
            item.requirement_scope_chunk_id is None
            or item.current_scope_chunk_id is None
        ):
            return None, "invalid_not_applicable"

        basis = item.not_applicable_basis
        req_chunk_id = item.requirement_scope_chunk_id
        req_quote_raw = item.requirement_scope_quote
        cur_chunk_id = item.current_scope_chunk_id
        cur_quote_raw = item.current_scope_quote
        if (
            basis is None
            or req_chunk_id is None
            or not req_quote_raw
            or cur_chunk_id is None
            or not cur_quote_raw
        ):
            return None, "invalid_not_applicable"

        if req_chunk_id not in tender_ids or req_chunk_id not in tender_by_id:
            return None, "invalid_not_applicable"
        req_ctx = tender_by_id[req_chunk_id]
        if req_ctx.chunk.project_id != project_id or req_ctx.document.project_id != project_id:
            return None, "cross_project_chunk"
        if not quote_in_content(req_quote_raw, req_ctx.chunk.content or ""):
            return None, "quote_not_found"
        req_quote = normalize_whitespace(req_quote_raw)
        if not _SCOPE_LIMIT_HINT_RE.search(req_quote):
            return None, "invalid_not_applicable"

        if cur_chunk_id not in by_chunk_id or cur_chunk_id not in allowed_for_req:
            return None, "out_of_scope_chunk"
        cur_ctx = by_chunk_id[cur_chunk_id]
        if cur_ctx.document.document_type in EXCLUDED_MATCH_DOCUMENT_TYPES:
            return None, "out_of_scope_chunk"
        if cur_ctx.document.project_id != project_id:
            return None, "cross_project_chunk"
        if not quote_in_content(cur_quote_raw, cur_ctx.chunk.content or ""):
            return None, "quote_not_found"
        cur_quote = normalize_whitespace(cur_quote_raw)

        if not validate_scope_exclusion(req_quote, cur_quote):
            return None, "invalid_not_applicable"

        req_text = requirement.normalized_requirement or requirement.title or ""
        evidence_text = (
            f"{req_quote}\n{req_ctx.chunk.content or ''}\n"
            f"{cur_quote}\n{cur_ctx.chunk.content or ''}"
        )

        summary = normalize_whitespace(item.summary)
        if not summary:
            summary = "Requirement 明确限定适用范围，当前对象不在适用范围内，待人工审核"
        if not _summary_tokens_ok(summary, req_text, evidence_text):
            return None, "fabricated_summary"
        if item.not_applicable_note:
            note = normalize_whitespace(item.not_applicable_note)
            if note and not _summary_tokens_ok(note, req_text, evidence_text):
                return None, "fabricated_summary"

        banned = ("必然中标", "完全满足", "一定不符合", "必然不符合", "企业不符合")
        if any(b in summary for b in banned):
            return None, "banned_absolute_summary"

        meta_extra: dict[str, Any] = {
            "not_applicable_basis": basis,
            "requirement_scope_chunk_id": str(req_chunk_id),
            "requirement_scope_quote": req_quote,
            "requirement_scope_location": self._location_meta_from_ctx(req_ctx, project_id),
            "current_scope_chunk_id": str(cur_chunk_id),
            "current_scope_quote": cur_quote,
            "current_scope_location": self._location_meta_from_ctx(cur_ctx, project_id),
        }
        if item.not_applicable_note:
            meta_extra["not_applicable_note"] = normalize_whitespace(
                item.not_applicable_note
            )

        req_meta = requirement.metadata_json or {}
        needs_review = True
        if req_meta.get("potential_conflict"):
            meta_extra["requirement_potential_conflict"] = True
            meta_extra["conflict_inheritance_note"] = (
                item.conflict_note or "招标要求本身存在待确认冲突"
            )

        company_links = [
            (
                cur_ctx.document,
                cur_ctx.chunk,
                cur_quote,
                "company_scope_exclusion",
            )
        ]

        return (
            _ValidatedMatch(
                item=item,
                requirement=requirement,
                status=EvidenceMatchStatus.not_applicable,
                summary=summary,
                risk_level=risk_for_match(
                    requirement, EvidenceMatchStatus.not_applicable
                ),
                primary_chunk=None,
                primary_document=None,
                primary_quote=None,
                company_links=company_links,
                needs_review=needs_review,
                metadata_extra=meta_extra,
            ),
            None,
        )

    def _delete_auto_matches(
        self,
        project_id: UUID,
        *,
        requirement_ids: set[UUID],
    ) -> dict[UUID, list[RequirementEvidenceMatch]]:
        """Delete unprotected pending auto matches without review history.

        Returns remaining (non-deleted) matches keyed by requirement_id — including
        protected rows and pending autos that have review history (for supersede).
        """
        remaining: dict[UUID, list[RequirementEvidenceMatch]] = {}
        if not requirement_ids:
            return remaining
        rows = list(
            self.db.scalars(
                select(RequirementEvidenceMatch)
                .where(
                    RequirementEvidenceMatch.project_id == project_id,
                    RequirementEvidenceMatch.requirement_id.in_(requirement_ids),
                )
                .options(selectinload(RequirementEvidenceMatch.reviews))
            )
        )
        for match in rows:
            has_reviews = bool(match.reviews)
            if (
                _is_protected_match(match)
                or has_reviews
                or match.review_status != MatchReviewStatus.pending
                or match.lifecycle_status == "superseded"
            ):
                remaining.setdefault(match.requirement_id, []).append(match)
                continue
            self.db.execute(
                delete(RequirementEvidenceMatchLink).where(
                    RequirementEvidenceMatchLink.match_id == match.id
                )
            )
            self.db.delete(match)
        self.db.flush()
        return remaining

    def _persist_matches(
        self,
        project_id: UUID,
        run_id: UUID,
        validated: list[_ValidatedMatch],
        *,
        force: bool,
        scoped_requirement_ids: set[UUID],
        processed_requirements: int = 0,
        failed_requirement_count: int = 0,
        raw_item_count: int = 0,
        rejected_count: int = 0,
        reject_reason_counts: dict[str, int] | None = None,
    ) -> dict[str, int | bool]:
        """Atomically persist matches or abort if cancel won the race.

        Locks the run row with FOR UPDATE; on cancel → zero writes, keep cancelled.
        On success → delete/write matches and mark run succeeded in the same transaction.
        """
        run = self.db.execute(
            select(RequirementMatchRun)
            .where(RequirementMatchRun.id == run_id)
            .with_for_update()
        ).scalar_one_or_none()
        if run is None:
            return {
                "aborted_cancelled": True,
                "created_count": 0,
                "skipped_existing": 0,
                "matched_count": 0,
                "partial_count": 0,
                "missing_evidence_count": 0,
                "conflict_count": 0,
            }

        cancel_requested = bool((run.config_json or {}).get("cancel_requested"))
        if run.status == ExtractionRunStatus.cancelled or cancel_requested:
            # Do not write matches; never overwrite cancelled → succeeded/failed.
            if run.status != ExtractionRunStatus.cancelled:
                run.status = ExtractionRunStatus.cancelled
                run.finished_at = datetime.now(UTC)
                run.error_summary = "任务已取消，未写入匹配结果"
            self.db.commit()
            return {
                "aborted_cancelled": True,
                "created_count": 0,
                "skipped_existing": 0,
                "matched_count": 0,
                "partial_count": 0,
                "missing_evidence_count": 0,
                "conflict_count": 0,
            }

        existing_by_req: dict[UUID, list[RequirementEvidenceMatch]] = {}
        if force:
            existing_by_req = self._delete_auto_matches(
                project_id, requirement_ids=scoped_requirement_ids
            )
        elif scoped_requirement_ids:
            for row in self.db.scalars(
                select(RequirementEvidenceMatch)
                .where(
                    RequirementEvidenceMatch.project_id == project_id,
                    RequirementEvidenceMatch.requirement_id.in_(scoped_requirement_ids),
                )
                .options(selectinload(RequirementEvidenceMatch.reviews))
            ):
                existing_by_req.setdefault(row.requirement_id, []).append(row)

        created = 0
        skipped = 0
        matched_count = 0
        partial_count = 0
        missing_count = 0
        conflict_count = 0

        for item in validated:
            req_id = item.requirement.id
            if not force:
                autos = [
                    m
                    for m in existing_by_req.get(req_id, [])
                    if (m.metadata_json or {}).get("source") == AUTO_SOURCE
                    and m.lifecycle_status == "active"
                ]
                if autos:
                    skipped += 1
                    st = autos[0].status
                    if st == EvidenceMatchStatus.supported:
                        matched_count += 1
                    elif st == EvidenceMatchStatus.partially_supported:
                        partial_count += 1
                    elif st == EvidenceMatchStatus.insufficient_evidence:
                        missing_count += 1
                    elif st == EvidenceMatchStatus.conflicting_evidence:
                        conflict_count += 1
                    continue
            else:
                # Skip creating a new match when an active protected match remains.
                active_protected = [
                    m
                    for m in existing_by_req.get(req_id, [])
                    if _is_active_protected_match(m)
                ]
                if active_protected:
                    skipped += 1
                    st = active_protected[0].status
                    if st == EvidenceMatchStatus.supported:
                        matched_count += 1
                    elif st == EvidenceMatchStatus.partially_supported:
                        partial_count += 1
                    elif st == EvidenceMatchStatus.insufficient_evidence:
                        missing_count += 1
                    elif st == EvidenceMatchStatus.conflicting_evidence:
                        conflict_count += 1
                    continue

            meta = {
                "source": AUTO_SOURCE,
                "run_id": str(run_id),
                "match_key": auto_match_key(req_id),
                **item.metadata_extra,
            }
            row = RequirementEvidenceMatch(
                project_id=project_id,
                requirement_id=req_id,
                status=item.status,
                confidence=None,
                summary=item.summary,
                needs_review=True,
                risk_level=item.risk_level,
                primary_company_document_id=(
                    item.primary_document.id if item.primary_document else None
                ),
                primary_company_chunk_id=(
                    item.primary_chunk.id if item.primary_chunk else None
                ),
                primary_company_quote=item.primary_quote,
                metadata_json=meta,
                review_status=MatchReviewStatus.pending,
                is_review_protected=False,
                review_lock_version=0,
                lifecycle_status="active",
            )
            self.db.add(row)
            self.db.flush()

            # Supersede prior active unprotected pending matches that retained history.
            if force:
                for old in existing_by_req.get(req_id, []):
                    if old.id == row.id:
                        continue
                    if old.lifecycle_status != "active":
                        continue
                    if _is_active_protected_match(old):
                        continue
                    old.lifecycle_status = "superseded"
                    old.superseded_by_match_id = row.id
                    row.supersedes_match_id = old.id

            for doc, chunk, quote, role in item.company_links:
                self.db.add(
                    RequirementEvidenceMatchLink(
                        match_id=row.id,
                        document_id=doc.id,
                        chunk_id=chunk.id,
                        quote=quote or None,
                        notes=None,
                        role=role,
                    )
                )

            created += 1
            if item.status == EvidenceMatchStatus.supported:
                matched_count += 1
            elif item.status == EvidenceMatchStatus.partially_supported:
                partial_count += 1
            elif item.status == EvidenceMatchStatus.insufficient_evidence:
                missing_count += 1
            elif item.status == EvidenceMatchStatus.conflicting_evidence:
                conflict_count += 1

        has_supported_or_partial = any(
            v.status
            in (
                EvidenceMatchStatus.supported,
                EvidenceMatchStatus.partially_supported,
                EvidenceMatchStatus.conflicting_evidence,
                EvidenceMatchStatus.not_applicable,
            )
            for v in validated
        )
        run.matched_count = matched_count
        run.partial_count = partial_count
        run.missing_evidence_count = missing_count
        run.conflict_count = conflict_count
        run.failed_requirement_count = failed_requirement_count
        run.processed_requirements = processed_requirements
        run.finished_at = datetime.now(UTC)
        run.config_json = {
            **(run.config_json or {}),
            "result_kind": (
                "valid_result"
                if has_supported_or_partial
                else "valid_empty_or_insufficient_result"
            ),
            "raw_item_count": raw_item_count,
            "rejected_count": rejected_count,
            "reject_reason_counts": reject_reason_counts or {},
            "created_count": created,
            "skipped_existing": skipped,
        }
        run.status = ExtractionRunStatus.succeeded
        run.error_summary = None
        self.db.commit()
        return {
            "aborted_cancelled": False,
            "created_count": created,
            "skipped_existing": skipped,
            "matched_count": matched_count,
            "partial_count": partial_count,
            "missing_evidence_count": missing_count,
            "conflict_count": conflict_count,
        }

    def _to_summary(self, match: RequirementEvidenceMatch) -> MatchSummary:
        req = match.requirement
        req_summary = None
        if req is not None:
            req_summary = RequirementSummary(
                id=req.id,
                project_id=req.project_id,
                source_document_id=req.source_document_id,
                requirement_code=req.requirement_code,
                category=req.category,
                title=req.title,
                normalized_requirement=req.normalized_requirement,
                mandatory=req.mandatory,
                score=req.score,
                risk_level=req.risk_level,
                source_page=req.source_page,
                source_section=req.source_section,
                source_clause_id=req.source_clause_id,
                quality_level=req.quality_level,
                review_status=req.review_status,
                metadata_json=req.metadata_json,
                created_at=req.created_at,
                updated_at=req.updated_at,
                evidence_count=len(req.evidence_links) if req.evidence_links else 0,
                has_conflict=bool((req.metadata_json or {}).get("potential_conflict")),
                source_document_file_name=(
                    req.source_document.file_name if req.source_document else None
                ),
            )
        path = None
        if match.primary_company_document_id and match.primary_company_chunk_id:
            path = document_center_path(
                match.project_id,
                match.primary_company_document_id,
                match.primary_company_chunk_id,
            )
        recent_reviews: list[MatchReviewRead] = []
        reviews = getattr(match, "reviews", None) or []
        for rev in list(reviews)[:10]:
            recent_reviews.append(MatchReviewRead.model_validate(rev))
        return MatchSummary(
            id=match.id,
            project_id=match.project_id,
            requirement_id=match.requirement_id,
            status=match.status,
            confidence=match.confidence,
            summary=match.summary,
            needs_review=match.needs_review,
            risk_level=match.risk_level,
            primary_company_document_id=match.primary_company_document_id,
            primary_company_chunk_id=match.primary_company_chunk_id,
            primary_company_quote=match.primary_company_quote,
            metadata_json=match.metadata_json,
            review_status=match.review_status or MatchReviewStatus.pending,
            reviewed_at=match.reviewed_at,
            reviewed_by=match.reviewed_by,
            is_review_protected=bool(match.is_review_protected),
            review_lock_version=int(match.review_lock_version or 0),
            lifecycle_status=match.lifecycle_status or "active",
            superseded_by_match_id=match.superseded_by_match_id,
            supersedes_match_id=match.supersedes_match_id,
            created_at=match.created_at,
            updated_at=match.updated_at,
            requirement=req_summary,
            primary_company_document_file_name=(
                match.primary_company_document.file_name
                if match.primary_company_document
                else None
            ),
            primary_company_document_type=(
                match.primary_company_document.document_type.value
                if match.primary_company_document
                else None
            ),
            document_center_path=path,
            recent_reviews=recent_reviews,
        )
