"""Grounded RAG: hybrid retrieval → evidence-bound LLM answer → citation check.

Reuses RetrievalService.search; never invents chunks, citations, or answers.
"""

from __future__ import annotations

import logging
import re
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

import tiktoken
from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.schemas.ask import (
    AskRequest,
    AskResponse,
    CitationItem,
    GenerationTrace,
    RagRetrievalTrace,
)
from app.schemas.search import SearchRequest
from app.services.llm_client import (
    LlmClient,
    LlmDisabledError,
    LlmError,
    LlmTimeoutError,
    LlmUnavailableError,
    get_llm_client,
)
from app.services.model_serving import resolve_model_selection
from app.services.retrieval import RetrievalService

logger = logging.getLogger("bidpilot.rag")

CITATION_RE = re.compile(r"\[(S\d+)\]")
INSUFFICIENT_PHRASES = (
    "当前资料不足以确认",
    "当前项目中未检索到足以回答该问题的资料",
    "未检索到足以支持回答的资料",
)

SYSTEM_PROMPT = """你是 BidPilot 的投标资料证据助手。
只能依据本轮提供的证据回答。
不得使用外部知识、常识补全、猜测或编造。
不得编造招标条款、资格条件、日期、金额、页码、章节、文件名或来源编号。
每个可验证的关键结论必须紧跟一个或多个来源标记，如 [S1]、[S2]。
证据没有明确支持时，直接写“当前资料不足以确认”。
存在条件冲突时，明确说明冲突及对应来源。
回答使用简洁中文，优先列出结论、条件与例外。
不要输出思考过程，不要输出“根据上下文”，不要输出不存在的引用。
回答结束后不额外虚构参考文献。"""


class AnswerValidationError(Exception):
    def __init__(self, message: str, *, detail: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail or message


@dataclass
class EvidenceSource:
    source_id: str
    item: Any  # SearchResultItem
    citation: CitationItem


def _token_count(text: str) -> int:
    try:
        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except Exception:  # noqa: BLE001
        return max(1, len(text) // 2)


def _page_label(page_start: int | None, page_end: int | None) -> str:
    if page_start is None or page_end is None:
        return "无可靠页码"
    if page_start == page_end:
        return f"第 {page_start} 页"
    return f"第 {page_start}-{page_end} 页"


def _excerpt(content: str, limit: int = 800) -> str:
    text = content.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _document_url(project_id: UUID, document_id: str, chunk_id: str) -> str:
    return f"/projects/{project_id}?tab=documents&documentId={document_id}&chunkId={chunk_id}"


def build_citation(project_id: UUID, source_id: str, item: Any) -> CitationItem:
    return CitationItem(
        source_id=source_id,
        chunk_id=item.chunk_id,
        document_id=item.document_id,
        file_name=item.file_name,
        document_type=item.document_type,
        section=item.section,
        clause_id=item.clause_id,
        page_start=item.page_start,
        page_end=item.page_end,
        excerpt=_excerpt(item.content),
        content_hash=item.content_hash,
        rerank_score=item.rerank_score,
        rrf_score=item.rrf_score,
        dense_rank=item.dense_rank,
        dense_score=item.dense_score,
        bm25_rank=item.bm25_rank,
        bm25_score=item.bm25_score,
        chunk_index=item.chunk_index,
        document_url=_document_url(project_id, item.document_id, item.chunk_id),
    )


def format_evidence_block(source: EvidenceSource) -> str:
    item = source.item
    return "\n".join(
        [
            f"[{source.source_id}]",
            f"文件：{item.file_name or '未知文件'}",
            f"文档类型：{item.document_type or 'unknown'}",
            f"章节：{item.section or '未识别'}",
            f"条款：{item.clause_id or '无'}",
            f"页码：{_page_label(item.page_start, item.page_end)}",
            "正文：",
            item.content.strip(),
        ]
    )


def build_messages(question: str, sources: list[EvidenceSource]) -> list[dict[str, str]]:
    blocks = "\n\n".join(format_evidence_block(s) for s in sources)
    user = (
        f"问题：{question.strip()}\n\n"
        f"证据：\n{blocks}\n\n"
        "请仅依据上述证据作答，并在关键结论后标注 [S编号]。"
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


def select_context(
    project_id: UUID,
    results: list[Any],
    *,
    top_k: int,
    max_tokens: int,
    min_rerank_score: float,
) -> tuple[list[EvidenceSource], int, int]:
    """Filter by min score, assign S1..Sn, truncate by token budget.

    Returns (sources, filtered_count, context_token_count).
    """
    filtered_out = 0
    kept: list[Any] = []
    for item in results:
        if item.rerank_score is not None and item.rerank_score < min_rerank_score:
            filtered_out += 1
            continue
        kept.append(item)
        if len(kept) >= top_k:
            break

    sources: list[EvidenceSource] = []
    used_tokens = 0
    for idx, item in enumerate(kept, start=1):
        source_id = f"S{idx}"
        citation = build_citation(project_id, source_id, item)
        block = format_evidence_block(
            EvidenceSource(source_id=source_id, item=item, citation=citation)
        )
        block_tokens = _token_count(block)
        if sources and used_tokens + block_tokens > max_tokens:
            break
        citation = build_citation(project_id, source_id, item)
        sources.append(EvidenceSource(source_id=source_id, item=item, citation=citation))
        used_tokens += block_tokens
    return sources, filtered_out, used_tokens


def extract_citation_ids(answer: str) -> list[str]:
    return CITATION_RE.findall(answer)


def validate_answer(answer: str, allowed_ids: set[str]) -> list[str]:
    """Return ordered unique cited source ids that are allowed.

    Raises AnswerValidationError on unknown citations or ungrounded substance.
    """
    text = answer.strip()
    if not text:
        raise AnswerValidationError("回答校验失败", detail="模型返回空回答")

    found = extract_citation_ids(text)
    unknown = sorted({cid for cid in found if cid not in allowed_ids})
    if unknown:
        raise AnswerValidationError(
            "回答校验失败：存在未知来源引用",
            detail=f"未知引用 {unknown}；本轮允许 {sorted(allowed_ids)}",
        )

    insufficient = any(phrase in text for phrase in INSUFFICIENT_PHRASES)
    unique_cited = list(dict.fromkeys(found))

    if not unique_cited and not insufficient:
        # Substantive claim without citations is not allowed.
        raise AnswerValidationError(
            "回答校验失败：关键结论缺少来源引用",
            detail="请仅依据证据作答，并使用 [S1] 等形式标注来源；或明确写“当前资料不足以确认”",
        )

    return unique_cited


def citations_for_answer(
    cited_ids: list[str],
    sources: list[EvidenceSource],
) -> list[CitationItem]:
    by_id = {s.source_id: s.citation for s in sources}
    return [by_id[sid] for sid in cited_ids if sid in by_id]


class RagAnswerService:
    def __init__(
        self,
        db: Session,
        *,
        retrieval: RetrievalService | None = None,
        llm: LlmClient | None = None,
    ) -> None:
        self.db = db
        self.retrieval = retrieval or RetrievalService(db)
        self.llm = llm or get_llm_client()

    def _resolve_llm(self, request: AskRequest) -> tuple[Any, Any]:
        from app.services.llm_client import LlmClient
        from app.services.model_serving import BASE_MODEL_ID, ModelResolution

        # Unit tests inject FakeLlm (not LlmClient); keep injection and skip live probe.
        if self.llm is not None and not isinstance(self.llm, LlmClient):
            requested = (request.model_id or BASE_MODEL_ID).strip() or BASE_MODEL_ID
            return self.llm, ModelResolution(
                available=True,
                requested_model_id=requested,
                resolved_model_id=requested,
                served_model_name=str(getattr(self.llm, "model", None) or "test-llm"),
                model_type="base",
                adapter_version=None,
                train_track=None,
                fallback_used=False,
                reason_codes=[],
                display_name="test-llm",
            )

        resolution = resolve_model_selection(
            request.model_id,
            allow_fallback=bool(request.allow_base_fallback),
            probe=True,
        )
        if not resolution.available or not resolution.served_model_name:
            codes = ",".join(resolution.reason_codes) or "model_not_served"
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "message": "所选模型当前不可用",
                    "reason_codes": resolution.reason_codes,
                    "requested_model_id": resolution.requested_model_id,
                    "hint": (
                        "模型尚未启动在线服务"
                        if "model_not_served" in resolution.reason_codes
                        else codes
                    ),
                },
            )
        client = LlmClient(
            base_url=self.llm.base_url if isinstance(self.llm, LlmClient) else None,
            api_key=self.llm.api_key if isinstance(self.llm, LlmClient) else None,
            model=resolution.served_model_name,
            timeout_seconds=(self.llm.timeout_seconds if isinstance(self.llm, LlmClient) else None),
            enabled=self.llm.enabled if isinstance(self.llm, LlmClient) else None,
        )
        return client, resolution

    def _generation_trace(
        self,
        *,
        result: Any,
        sources: list[EvidenceSource],
        context_token_count: int,
        resolution: Any,
    ) -> GenerationTrace:
        return GenerationTrace(
            model=result.model,
            context_chunk_count=len(sources),
            context_token_count=context_token_count,
            latency_ms=result.latency_ms,
            finish_reason=result.finish_reason,
            request_id=result.request_id,
            requested_model_id=resolution.requested_model_id,
            resolved_model_id=resolution.resolved_model_id,
            served_model_name=resolution.served_model_name,
            model_type=resolution.model_type,
            adapter_version=resolution.adapter_version,
            fallback_used=bool(resolution.fallback_used),
        )

    def _prepare(
        self, project_id: UUID, request: AskRequest
    ) -> tuple[list[EvidenceSource], RagRetrievalTrace, str]:
        settings = get_settings()
        question = request.question.strip()
        if not question:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="问题不能为空")

        top_k = request.top_k if request.top_k is not None else settings.rag_context_top_k
        prepare_start = time.perf_counter()
        search = self.retrieval.search(
            project_id,
            SearchRequest(
                query=question,
                top_k=top_k,
                document_types=request.document_types,
                document_ids=request.document_ids,
            ),
        )
        sources, filtered_out, context_tokens = select_context(
            project_id,
            search.results,
            top_k=top_k,
            max_tokens=settings.rag_max_context_tokens,
            min_rerank_score=settings.rag_min_rerank_score,
        )
        prepare_ms = (time.perf_counter() - prepare_start) * 1000
        trace = RagRetrievalTrace(
            **search.trace.model_dump(),
            rag_prepare_ms=round(prepare_ms, 2),
            context_chunk_count=len(sources),
            context_token_count=context_tokens,
            filtered_by_min_score=filtered_out,
        )
        return sources, trace, question

    def _insufficient(
        self,
        question: str,
        sources: list[EvidenceSource],
        trace: RagRetrievalTrace,
        *,
        message: str,
    ) -> AskResponse:
        return AskResponse(
            question=question,
            answer=message,
            citations=[],
            sources=[s.citation for s in sources],
            retrieval_trace=trace,
            generation_trace=None,
            status="insufficient_evidence",
        )

    def answer(self, project_id: UUID, request: AskRequest) -> AskResponse:
        sources, trace, question = self._prepare(project_id, request)
        if not sources:
            logger.info(
                "RAG insufficient evidence project=%s chunks=0",
                project_id,
            )
            return self._insufficient(
                question,
                sources,
                trace,
                message="当前项目中未检索到足以回答该问题的资料。",
            )

        if not self.llm.enabled:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="大模型问答未启用。请设置 LLM_ENABLED=true 并启动 vLLM。",
            )

        llm, resolution = self._resolve_llm(request)
        messages = build_messages(question, sources)
        request_id = str(uuid.uuid4())
        logger.info(
            "RAG generate request_id=%s project=%s model=%s requested=%s chunks=%s tokens=%s",
            request_id,
            project_id,
            resolution.served_model_name,
            resolution.requested_model_id,
            len(sources),
            trace.context_token_count,
        )
        try:
            result = llm.chat(messages, request_id=request_id)
        except LlmDisabledError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=exc.detail
            ) from exc
        except LlmTimeoutError as exc:
            raise HTTPException(
                status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail=exc.detail
            ) from exc
        except LlmUnavailableError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=exc.detail
            ) from exc
        except LlmError as exc:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=exc.detail) from exc

        try:
            cited_ids = validate_answer(result.content, {s.source_id for s in sources})
        except AnswerValidationError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=exc.detail,
            ) from exc

        citations = citations_for_answer(cited_ids, sources)
        status_label: Literal["answered", "insufficient_evidence", "llm_disabled"] = (
            "insufficient_evidence"
            if any(p in result.content for p in INSUFFICIENT_PHRASES) and not citations
            else "answered"
        )
        return AskResponse(
            question=question,
            answer=result.content,
            citations=citations,
            sources=[s.citation for s in sources],
            retrieval_trace=trace,
            generation_trace=self._generation_trace(
                result=result,
                sources=sources,
                context_token_count=trace.context_token_count,
                resolution=resolution,
            ),
            status=status_label,
        )

    def answer_stream(self, project_id: UUID, request: AskRequest) -> Iterator[dict[str, Any]]:
        """Yield SSE events with evidence-first semantics (Scheme A).

        Event order on success: retrieval → generation_started → final.
        Unvalidated model text is never sent as delta/answer to the client.
        On citation validation failure: error only (no answer body).
        """
        try:
            sources, trace, question = self._prepare(project_id, request)
        except HTTPException as exc:
            yield {
                "event": "error",
                "data": {
                    "message": exc.detail if isinstance(exc.detail, str) else "请求失败",
                    "detail": exc.detail,
                },
            }
            return

        source_payload = [s.citation.model_dump() for s in sources]
        if not sources:
            result = self._insufficient(
                question,
                sources,
                trace,
                message="当前项目中未检索到足以回答该问题的资料。",
            )
            yield {
                "event": "retrieval",
                "data": {
                    "sources": [],
                    "retrieval_trace": trace.model_dump(),
                    "status": "insufficient_evidence",
                },
            }
            yield {"event": "final", "data": {"result": result.model_dump()}}
            return

        yield {
            "event": "retrieval",
            "data": {
                "sources": source_payload,
                "retrieval_trace": trace.model_dump(),
                "status": "ok",
            },
        }

        if not self.llm.enabled:
            yield {
                "event": "error",
                "data": {
                    "message": "大模型问答未启用",
                    "detail": (
                        "请设置 LLM_ENABLED=true 并启动 vLLM（见 scripts/serve_qwen3_vllm.sh）"
                    ),
                },
            }
            return

        try:
            llm, resolution = self._resolve_llm(request)
        except HTTPException as exc:
            yield {
                "event": "error",
                "data": {
                    "message": "所选模型当前不可用",
                    "detail": exc.detail,
                },
            }
            return

        messages = build_messages(question, sources)
        request_id = str(uuid.uuid4())
        logger.info(
            "RAG stream request_id=%s project=%s model=%s requested=%s chunks=%s",
            request_id,
            project_id,
            resolution.served_model_name,
            resolution.requested_model_id,
            len(sources),
        )
        yield {
            "event": "generation_started",
            "data": {
                "request_id": request_id,
                "model": resolution.served_model_name,
                "requested_model_id": resolution.requested_model_id,
                "resolved_model_id": resolution.resolved_model_id,
                "served_model_name": resolution.served_model_name,
                "model_type": resolution.model_type,
                "adapter_version": resolution.adapter_version,
                "fallback_used": resolution.fallback_used,
                "context_chunk_count": len(sources),
                "message": "正在生成并核验引用来源",
            },
        }

        started = time.perf_counter()
        parts: list[str] = []
        try:
            # Buffer tokens server-side; do not leak unverified text via delta.
            for delta in llm.chat_stream(messages, request_id=request_id):
                parts.append(delta)
        except LlmError as exc:
            yield {
                "event": "error",
                "data": {"message": exc.message, "detail": exc.detail},
            }
            return

        from app.services.llm_client import _strip_thinking

        answer = _strip_thinking("".join(parts).strip())
        latency_ms = (time.perf_counter() - started) * 1000
        try:
            cited_ids = validate_answer(answer, {s.source_id for s in sources})
        except AnswerValidationError as exc:
            yield {
                "event": "error",
                "data": {"message": exc.message, "detail": exc.detail},
            }
            return

        citations = citations_for_answer(cited_ids, sources)
        status_label: Literal["answered", "insufficient_evidence", "llm_disabled"] = (
            "insufficient_evidence"
            if any(p in answer for p in INSUFFICIENT_PHRASES) and not citations
            else "answered"
        )

        class _StreamResult:
            def __init__(self) -> None:
                self.model = resolution.served_model_name or llm.model
                self.latency_ms = round(latency_ms, 2)
                self.finish_reason = "stop"
                self.request_id = request_id

        result = AskResponse(
            question=question,
            answer=answer,
            citations=citations,
            sources=[s.citation for s in sources],
            retrieval_trace=trace,
            generation_trace=self._generation_trace(
                result=_StreamResult(),
                sources=sources,
                context_token_count=trace.context_token_count,
                resolution=resolution,
            ),
            status=status_label,
        )
        yield {"event": "final", "data": {"result": result.model_dump()}}
