"""Traceable tender requirement extraction from project document chunks.

Scans real DocumentChunks (not RAG top-k), validates LLM candidates against
chunk evidence, dedupes within a run, marks conflicts without auto-resolving,
and persists Requirement + EvidenceLink rows.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from collections import defaultdict
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
    ExtractionRunStatus,
    QualityLevel,
    RequirementCategory,
    ReviewStatus,
    RiskLevel,
)
from app.models.extraction_run import RequirementExtractionRun
from app.schemas.extraction import (
    DEFAULT_EXTRACTION_DOCUMENT_TYPES,
    EXCLUDED_EXTRACTION_DOCUMENT_TYPES,
    ExtractionBatchResult,
    ExtractionCandidateItem,
    ExtractionRunResponse,
    ExtractionStartRequest,
)
from app.schemas.requirement import (
    EvidenceLinkRead,
    RequirementDetail,
    RequirementListResponse,
    RequirementSummary,
)
from app.services.evidence_validate import normalize_whitespace, quote_in_content
from app.services.llm_client import LlmClient, LlmError, get_llm_client

logger = logging.getLogger("bidpilot.requirement_extraction")

BATCH_SIZE = 4
AUTO_SOURCE = "auto_extraction"

SYSTEM_PROMPT = """你是 BidPilot 的招标要求结构化抽取器。
只可依据本轮给出的原始 chunk 抽取明确陈述的要求。
禁止依赖外部知识、常识推断、经验补全或编造。
禁止生成原文不存在的资质、金额、日期、页码、章节、条款号、评分规则、废标条件或合同条款。
每条结果必须提供 source_chunk_ids 和 evidence_quote。
evidence_quote 必须是来源 chunk 中可逐字或经空白规范化后匹配的连续原文。
要求无法由原文明确支持时，不输出该条目。
不确定该内容是否为硬性要求时，可输出但 needs_review=true。
输出必须是符合给定 schema 的 JSON，不要输出思考过程。"""

_NUMBER_RE = re.compile(
    r"(?:"
    r"\d{4}[-/年]\d{1,2}[-/月]\d{1,2}日?"
    r"|\d+(?:\.\d+)?%?"
    r"|[一二三四五六七八九十百千万亿两]+"
    r")"
)
_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE | re.MULTILINE)


@dataclass
class _ChunkContext:
    chunk: DocumentChunk
    document: Document


@dataclass
class _ValidatedCandidate:
    item: ExtractionCandidateItem
    primary_chunk: DocumentChunk
    primary_document: Document
    chunk_ids: list[UUID]
    document_ids: list[UUID]
    evidence_quote: str
    risk_level: RiskLevel
    requirement_code: str
    potential_conflict: bool
    conflict_note: str | None
    needs_review: bool


@dataclass
class _RunAccumulator:
    candidates: list[_ValidatedCandidate] = field(default_factory=list)
    candidate_count: int = 0
    created_count: int = 0
    merged_count: int = 0
    conflict_count: int = 0
    failed_chunk_count: int = 0
    processed_chunks: int = 0
    errors: list[str] = field(default_factory=list)


def stable_requirement_code(category: RequirementCategory, normalized: str) -> str:
    digest = hashlib.sha1(normalize_whitespace(normalized).encode("utf-8")).hexdigest()[:12]
    return f"auto-{category.value}-{digest}"


def risk_for_category(
    category: RequirementCategory,
    *,
    potential_conflict: bool = False,
) -> RiskLevel:
    if category == RequirementCategory.invalid_bid:
        risk = RiskLevel.critical
    elif category in (RequirementCategory.mandatory, RequirementCategory.deadline):
        risk = RiskLevel.high
    elif category in (
        RequirementCategory.qualification,
        RequirementCategory.scoring,
        RequirementCategory.material,
        RequirementCategory.contract,
    ):
        risk = RiskLevel.medium
    else:
        risk = RiskLevel.low
    if potential_conflict and risk in (RiskLevel.low, RiskLevel.medium):
        risk = RiskLevel.high
    return risk


def document_center_path(project_id: UUID, document_id: UUID, chunk_id: UUID) -> str:
    return (
        f"/projects/{project_id}?tab=documents"
        f"&documentId={document_id}&chunkId={chunk_id}"
    )


def _parse_llm_json(raw: str) -> dict[str, Any]:
    text = raw.strip()
    text = _FENCE_RE.sub("", text).strip()
    # Prefer outermost object if model adds chatter (should not).
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        text = text[start : end + 1]
    loaded: Any = json.loads(text)
    if not isinstance(loaded, dict):
        raise ValueError("LLM JSON root must be an object")
    return loaded


def _locator_ok(item: ExtractionCandidateItem, chunk: DocumentChunk) -> bool:
    if item.source_section is not None and (
        chunk.section is None or item.source_section != chunk.section
    ):
        return False
    if item.source_clause_id is not None and (
        chunk.clause_id is None or item.source_clause_id != chunk.clause_id
    ):
        return False
    if item.source_page is not None:
        ps, pe = chunk.page_start, chunk.page_end
        if ps is None and pe is None:
            return False
        if pe is None:
            pe = ps
        if ps is None:
            ps = pe
        assert ps is not None and pe is not None
        if not (ps <= item.source_page <= pe):
            return False
    return True


def _extract_value_tokens(text: str) -> set[str]:
    return {m.group(0) for m in _NUMBER_RE.finditer(text or "")}


def _has_numeric_conflict(a: str, b: str) -> bool:
    ta, tb = _extract_value_tokens(a), _extract_value_tokens(b)
    if not ta or not tb:
        return False
    # Conflict when both mention numbers/dates but the sets differ.
    return ta != tb


class RequirementExtractionService:
    def __init__(self, db: Session, llm: LlmClient | None = None) -> None:
        self.db = db
        self.llm = llm if llm is not None else get_llm_client()

    # ---------------------------------------------------------------- API ops

    def start_extraction(
        self,
        project_id: UUID,
        request: ExtractionStartRequest,
    ) -> ExtractionRunResponse:
        project = self._require_project(project_id)
        doc_types = [
            t for t in request.document_types if t not in EXCLUDED_EXTRACTION_DOCUMENT_TYPES
        ] or list(DEFAULT_EXTRACTION_DOCUMENT_TYPES)

        run = RequirementExtractionRun(
            project_id=project.id,
            status=ExtractionRunStatus.queued,
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
        return ExtractionRunResponse.model_validate(run)

    def get_run(self, project_id: UUID, run_id: UUID) -> ExtractionRunResponse:
        self._require_project(project_id)
        run = self.db.get(RequirementExtractionRun, run_id)
        if run is None or run.project_id != project_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="抽取任务不存在",
            )
        return ExtractionRunResponse.model_validate(run)

    def list_requirements(
        self,
        project_id: UUID,
        *,
        category: RequirementCategory | None = None,
        mandatory: bool | None = None,
        risk_level: RiskLevel | None = None,
        review_status: ReviewStatus | None = None,
        source_document_id: UUID | None = None,
        has_conflict: bool | None = None,
        page: int = 1,
        limit: int = 50,
        offset: int | None = None,
    ) -> RequirementListResponse:
        self._require_project(project_id)
        if page < 1:
            page = 1
        if offset is None:
            offset = (page - 1) * limit

        stmt = select(Requirement).where(Requirement.project_id == project_id)
        count_stmt = select(func.count()).select_from(Requirement).where(
            Requirement.project_id == project_id
        )

        if category is not None:
            stmt = stmt.where(Requirement.category == category)
            count_stmt = count_stmt.where(Requirement.category == category)
        if mandatory is not None:
            stmt = stmt.where(Requirement.mandatory == mandatory)
            count_stmt = count_stmt.where(Requirement.mandatory == mandatory)
        if risk_level is not None:
            stmt = stmt.where(Requirement.risk_level == risk_level)
            count_stmt = count_stmt.where(Requirement.risk_level == risk_level)
        if review_status is not None:
            stmt = stmt.where(Requirement.review_status == review_status)
            count_stmt = count_stmt.where(Requirement.review_status == review_status)
        if source_document_id is not None:
            stmt = stmt.where(Requirement.source_document_id == source_document_id)
            count_stmt = count_stmt.where(Requirement.source_document_id == source_document_id)
        if has_conflict is True:
            conflict_filter = Requirement.metadata_json.contains({"potential_conflict": True})
            stmt = stmt.where(conflict_filter)
            count_stmt = count_stmt.where(conflict_filter)
        elif has_conflict is False:
            conflict_filter = ~Requirement.metadata_json.contains({"potential_conflict": True})
            stmt = stmt.where(conflict_filter)
            count_stmt = count_stmt.where(conflict_filter)

        total = int(self.db.scalar(count_stmt) or 0)
        rows = list(
            self.db.scalars(
                stmt.order_by(Requirement.created_at.desc()).offset(offset).limit(limit)
            )
        )
        items = [self._to_summary(r) for r in rows]
        return RequirementListResponse(
            items=items,
            total=total,
            page=page,
            limit=limit,
            offset=offset,
        )

    def get_requirement(self, project_id: UUID, requirement_id: UUID) -> RequirementDetail:
        self._require_project(project_id)
        req = self.db.scalar(
            select(Requirement)
            .where(Requirement.id == requirement_id, Requirement.project_id == project_id)
            .options(
                selectinload(Requirement.evidence_links).selectinload(EvidenceLink.document),
                selectinload(Requirement.evidence_links).selectinload(EvidenceLink.chunk),
                selectinload(Requirement.source_document),
            )
        )
        if req is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="需求不存在",
            )
        summary = self._to_summary(req)
        evidence: list[EvidenceLinkRead] = []
        for link in req.evidence_links:
            path = None
            if link.document_id and link.chunk_id:
                path = document_center_path(project_id, link.document_id, link.chunk_id)
            evidence.append(
                EvidenceLinkRead(
                    id=link.id,
                    requirement_id=link.requirement_id,
                    document_id=link.document_id,
                    chunk_id=link.chunk_id,
                    evidence_type=link.evidence_type,
                    confidence=link.confidence,
                    notes=link.notes,
                    created_at=link.created_at,
                    updated_at=link.updated_at,
                    document_file_name=link.document.file_name if link.document else None,
                    document_type=(
                        link.document.document_type.value if link.document else None
                    ),
                    chunk_index=link.chunk.chunk_index if link.chunk else None,
                    section=link.chunk.section if link.chunk else None,
                    clause_id=link.chunk.clause_id if link.chunk else None,
                    page_start=link.chunk.page_start if link.chunk else None,
                    page_end=link.chunk.page_end if link.chunk else None,
                    document_center_path=path,
                )
            )
        return RequirementDetail(
            **summary.model_dump(),
            evidence_required_json=req.evidence_required_json,
            evidence_links=evidence,
        )

    # ------------------------------------------------------------- background

    def execute_run(self, run_id: UUID) -> None:
        run = self.db.get(RequirementExtractionRun, run_id)
        if run is None:
            logger.warning("Extraction run %s missing", run_id)
            return
        if run.status == ExtractionRunStatus.cancelled:
            return

        run.status = ExtractionRunStatus.running
        run.started_at = datetime.now(UTC)
        run.error_summary = None
        self.db.commit()

        acc = _RunAccumulator()
        try:
            force = bool((run.config_json or {}).get("force"))
            if force:
                self._delete_auto_requirements(run.project_id)

            contexts = self._load_eligible_chunks(run)
            run.total_chunks = len(contexts)
            self.db.commit()

            if not contexts:
                run.status = ExtractionRunStatus.succeeded
                run.finished_at = datetime.now(UTC)
                run.processed_chunks = 0
                self.db.commit()
                return

            for batch in _batched(contexts, BATCH_SIZE):
                try:
                    validated = self._extract_batch(batch)
                    acc.candidate_count += len(validated)
                    acc.candidates.extend(validated)
                except Exception as exc:  # noqa: BLE001 - isolate batch failures
                    logger.warning(
                        "Extraction batch failed run=%s: %s",
                        run_id,
                        type(exc).__name__,
                    )
                    acc.failed_chunk_count += len(batch)
                    acc.errors.append(f"{type(exc).__name__}: {exc}")
                finally:
                    acc.processed_chunks += len(batch)
                    run.processed_chunks = acc.processed_chunks
                    run.candidate_count = acc.candidate_count
                    run.failed_chunk_count = acc.failed_chunk_count
                    self.db.commit()

            created, merged, conflicts = self._persist_candidates(
                run.project_id,
                run.id,
                acc.candidates,
                force=force,
            )
            acc.created_count = created
            acc.merged_count = merged
            acc.conflict_count = conflicts

            run.candidate_count = acc.candidate_count
            run.created_count = acc.created_count
            run.merged_count = acc.merged_count
            run.conflict_count = acc.conflict_count
            run.failed_chunk_count = acc.failed_chunk_count
            run.processed_chunks = acc.processed_chunks
            run.finished_at = datetime.now(UTC)

            if acc.errors and acc.created_count == 0 and acc.candidate_count == 0:
                run.status = ExtractionRunStatus.failed
                run.error_summary = "; ".join(acc.errors)[:2000]
            else:
                run.status = ExtractionRunStatus.succeeded
                if acc.errors:
                    run.error_summary = (
                        f"部分批次失败（{acc.failed_chunk_count} chunks）: "
                        + "; ".join(acc.errors)[:1800]
                    )
            self.db.commit()
        except Exception as exc:
            logger.exception("Extraction run %s crashed", run_id)
            self.db.rollback()
            run = self.db.get(RequirementExtractionRun, run_id)
            if run is not None:
                run.status = ExtractionRunStatus.failed
                run.finished_at = datetime.now(UTC)
                run.error_summary = f"{type(exc).__name__}: {exc}"[:2000]
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

    def _delete_auto_requirements(self, project_id: UUID) -> None:
        """Remove only auto-extracted requirements; never touch manual/imported."""
        rows = list(
            self.db.scalars(select(Requirement).where(Requirement.project_id == project_id))
        )
        for req in rows:
            meta = req.metadata_json or {}
            if meta.get("source") == AUTO_SOURCE:
                self.db.execute(delete(EvidenceLink).where(EvidenceLink.requirement_id == req.id))
                self.db.delete(req)
        self.db.flush()

    def _load_eligible_chunks(self, run: RequirementExtractionRun) -> list[_ChunkContext]:
        defaults = [t.value for t in DEFAULT_EXTRACTION_DOCUMENT_TYPES]
        type_values = run.document_types_json or defaults
        allowed_types = []
        for raw in type_values:
            try:
                dt = DocumentType(raw)
            except ValueError:
                continue
            if dt not in EXCLUDED_EXTRACTION_DOCUMENT_TYPES:
                allowed_types.append(dt)
        if not allowed_types:
            allowed_types = list(DEFAULT_EXTRACTION_DOCUMENT_TYPES)

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

    def _extract_batch(self, batch: list[_ChunkContext]) -> list[_ValidatedCandidate]:
        by_id = {ctx.chunk.id: ctx for ctx in batch}
        payload = {
            "chunks": [
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
                for ctx in batch
            ]
        }
        user_content = (
            "请从以下 chunks 抽取招标要求，仅输出 JSON，"
            '格式为 {"items":[...]}：\n'
            + json.dumps(payload, ensure_ascii=False)
        )
        result = self.llm.chat(
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            request_id=str(uuid.uuid4()),
        )
        try:
            data = _parse_llm_json(result.content)
            batch_result = ExtractionBatchResult.model_validate(data)
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            raise LlmError(
                "大模型返回的 JSON 无效",
                detail=f"{type(exc).__name__}: {exc}",
            ) from exc

        validated: list[_ValidatedCandidate] = []
        for item in batch_result.items:
            ok = self._validate_candidate(item, by_id)
            if ok is not None:
                validated.append(ok)
        return validated

    def _validate_candidate(
        self,
        item: ExtractionCandidateItem,
        by_id: dict[UUID, _ChunkContext],
    ) -> _ValidatedCandidate | None:
        # All chunk ids must belong to this batch.
        if any(cid not in by_id for cid in item.source_chunk_ids):
            return None

        primary_id = item.source_chunk_ids[0]
        primary_ctx = by_id[primary_id]
        # Quote must appear in at least one cited chunk; prefer primary.
        matched_ctx: _ChunkContext | None = None
        for cid in item.source_chunk_ids:
            ctx = by_id[cid]
            if quote_in_content(item.evidence_quote, ctx.chunk.content):
                matched_ctx = ctx
                break
        if matched_ctx is None:
            return None

        # Locator fields must match the primary (first) cited chunk metadata.
        if not _locator_ok(item, primary_ctx.chunk):
            return None

        # Also verify locators against the quote-matched chunk if different.
        if matched_ctx.chunk.id != primary_ctx.chunk.id and not _locator_ok(
            item, matched_ctx.chunk
        ):
            # Soft: require quote match only on matched; locators tied to primary.
            pass

        code = stable_requirement_code(item.category, item.normalized_requirement)
        risk = risk_for_category(item.category, potential_conflict=item.potential_conflict)
        return _ValidatedCandidate(
            item=item,
            primary_chunk=primary_ctx.chunk,
            primary_document=primary_ctx.document,
            chunk_ids=list(item.source_chunk_ids),
            document_ids=[by_id[c].document.id for c in item.source_chunk_ids],
            evidence_quote=item.evidence_quote,
            risk_level=risk,
            requirement_code=code,
            potential_conflict=bool(item.potential_conflict),
            conflict_note=item.conflict_note,
            needs_review=bool(item.needs_review),
        )

    def _persist_candidates(
        self,
        project_id: UUID,
        run_id: UUID,
        candidates: list[_ValidatedCandidate],
        *,
        force: bool,
    ) -> tuple[int, int, int]:
        # Within-run dedupe: category + exact normalized text.
        groups: dict[tuple[RequirementCategory, str], list[_ValidatedCandidate]] = defaultdict(
            list
        )
        for cand in candidates:
            key = (cand.item.category, normalize_whitespace(cand.item.normalized_requirement))
            groups[key].append(cand)

        existing_codes = {
            r.requirement_code: r
            for r in self.db.scalars(
                select(Requirement).where(
                    Requirement.project_id == project_id,
                    Requirement.requirement_code.is_not(None),
                )
            )
            if r.requirement_code
        }

        created = 0
        merged = 0
        persisted: list[Requirement] = []

        for (_cat, _norm), group in groups.items():
            primary = group[0]
            if len(group) > 1:
                merged += len(group) - 1

            existing = existing_codes.get(primary.requirement_code)
            if existing is not None and not force:
                # Idempotent skip (unless force already deleted autos).
                meta = existing.metadata_json or {}
                if meta.get("source") == AUTO_SOURCE:
                    merged += 1
                    continue
                # Manual/imported with same code: skip creating duplicate auto row.
                continue

            # Collect unique evidence (document_id, chunk_id).
            evidence_keys: set[tuple[UUID, UUID]] = set()
            evidence_rows: list[tuple[UUID, UUID, str]] = []
            for cand in group:
                for cid in cand.chunk_ids:
                    doc_id = cand.primary_document.id
                    if cid in cand.chunk_ids:
                        idx = cand.chunk_ids.index(cid)
                        doc_id = cand.document_ids[idx]
                    ev_key = (doc_id, cid)
                    if ev_key in evidence_keys:
                        continue
                    evidence_keys.add(ev_key)
                    evidence_rows.append((doc_id, cid, cand.evidence_quote))

            # Prefer first evidence quote / locator from primary.
            req_meta: dict[str, Any] = {
                "source": AUTO_SOURCE,
                "extraction_run_id": str(run_id),
                "evidence_quote": primary.evidence_quote,
                "needs_review": primary.needs_review or primary.potential_conflict,
                "potential_conflict": primary.potential_conflict,
                "conflict_note": primary.conflict_note,
            }
            req = Requirement(
                project_id=project_id,
                source_document_id=primary.primary_document.id,
                requirement_code=primary.requirement_code,
                category=primary.item.category,
                title=primary.item.title[:1024],
                normalized_requirement=normalize_whitespace(primary.item.normalized_requirement),
                mandatory=primary.item.mandatory,
                score=primary.item.score,
                risk_level=primary.risk_level,
                source_page=primary.item.source_page
                if primary.item.source_page is not None
                else primary.primary_chunk.page_start,
                source_section=primary.item.source_section
                if primary.item.source_section is not None
                else primary.primary_chunk.section,
                source_clause_id=primary.item.source_clause_id
                if primary.item.source_clause_id is not None
                else primary.primary_chunk.clause_id,
                quality_level=QualityLevel.pending,
                review_status=ReviewStatus.unreviewed,
                metadata_json=req_meta,
            )
            self.db.add(req)
            self.db.flush()
            for doc_id, cid, quote in evidence_rows:
                self.db.add(
                    EvidenceLink(
                        requirement_id=req.id,
                        document_id=doc_id,
                        chunk_id=cid,
                        evidence_type="quote",
                        notes=quote,
                    )
                )
            existing_codes[primary.requirement_code] = req
            persisted.append(req)
            created += 1

        self.db.flush()
        conflict_count = self._mark_conflicts(project_id, persisted)
        self.db.commit()
        return created, merged, conflict_count

    def _mark_conflicts(
        self,
        project_id: UUID,
        new_rows: list[Requirement],
    ) -> int:
        """Detect potential conflicts; never auto-resolve. Returns conflicted count."""
        if not new_rows:
            return 0

        all_reqs = list(
            self.db.scalars(select(Requirement).where(Requirement.project_id == project_id))
        )
        by_category: dict[RequirementCategory, list[Requirement]] = defaultdict(list)
        by_clause: dict[str, list[Requirement]] = defaultdict(list)
        for r in all_reqs:
            by_category[r.category].append(r)
            if r.source_clause_id:
                by_clause[r.source_clause_id].append(r)

        conflicted: set[UUID] = set()
        group_counter = 0

        def _flag(a: Requirement, b: Requirement, reason: str) -> None:
            nonlocal group_counter
            group_counter += 1
            gid = f"conflict-{group_counter}-{uuid.uuid4().hex[:8]}"
            for req, other in ((a, b), (b, a)):
                meta = dict(req.metadata_json or {})
                meta["potential_conflict"] = True
                meta["needs_review"] = True
                meta["conflict_group_id"] = gid
                peers = list(meta.get("conflict_with") or [])
                peer_id = str(other.id)
                if peer_id not in peers:
                    peers.append(peer_id)
                meta["conflict_with"] = peers
                meta["conflict_note"] = reason
                req.metadata_json = meta
                if req.risk_level in (RiskLevel.low, RiskLevel.medium):
                    req.risk_level = RiskLevel.high
                conflicted.add(req.id)

        # Same clause_id, different normalized text across documents.
        for _clause, rows in by_clause.items():
            for i, a in enumerate(rows):
                for b in rows[i + 1 :]:
                    if a.source_document_id == b.source_document_id:
                        continue
                    na = normalize_whitespace(a.normalized_requirement or "")
                    nb = normalize_whitespace(b.normalized_requirement or "")
                    if na and nb and na != nb:
                        _flag(a, b, "同一条款号在不同文档中文本不一致")

        # Same category with conflicting numeric/date-like values.
        for _cat, rows in by_category.items():
            for i, a in enumerate(rows):
                for b in rows[i + 1 :]:
                    if a.id == b.id:
                        continue
                    na = a.normalized_requirement or ""
                    nb = b.normalized_requirement or ""
                    if _has_numeric_conflict(na, nb):
                        # Avoid flagging identical texts.
                        if normalize_whitespace(na) == normalize_whitespace(nb):
                            continue
                        _flag(a, b, "同类要求存在不同数值或日期")

        # Amendment vs tender differences (same category, different text).
        for _cat, rows in by_category.items():
            tenders = []
            amendments = []
            for r in rows:
                doc = self.db.get(Document, r.source_document_id) if r.source_document_id else None
                if doc is None:
                    continue
                if doc.document_type == DocumentType.tender:
                    tenders.append(r)
                elif doc.document_type == DocumentType.amendment:
                    amendments.append(r)
            for a in amendments:
                for t in tenders:
                    na = normalize_whitespace(a.normalized_requirement or "")
                    nt = normalize_whitespace(t.normalized_requirement or "")
                    if not na or not nt or na == nt:
                        continue
                    # Heuristic: overlapping tokens but differing numbers, or LLM already marked.
                    if _has_numeric_conflict(na, nt) or (
                        len(set(na) & set(nt)) > 8 and na != nt
                    ):
                        _flag(a, t, "补遗/澄清与招标文件要求存在差异")

        self.db.flush()
        return len(conflicted)

    def _to_summary(self, req: Requirement) -> RequirementSummary:
        meta = req.metadata_json or {}
        evidence_count = 0
        if req.evidence_links:
            evidence_count = len(req.evidence_links)
        else:
            evidence_count = (
                self.db.scalar(
                    select(func.count())
                    .select_from(EvidenceLink)
                    .where(EvidenceLink.requirement_id == req.id)
                )
                or 0
            )
        file_name = None
        if req.source_document_id:
            doc = self.db.get(Document, req.source_document_id)
            if doc is not None:
                file_name = doc.file_name
        return RequirementSummary(
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
            evidence_count=int(evidence_count),
            has_conflict=bool(meta.get("potential_conflict")),
            source_document_file_name=file_name,
        )


def _batched(items: list[_ChunkContext], size: int) -> list[list[_ChunkContext]]:
    return [items[i : i + size] for i in range(0, len(items), size)]
