from __future__ import annotations

from typing import Any

from bidpilot_data.logging import get_logger, log_stats
from bidpilot_data.schemas import (
    ChunkRecord,
    Difficulty,
    QualityLevel,
    QuestionType,
    RAGQuestion,
    ReviewStatus,
)
from bidpilot_data.settings import get_settings
from bidpilot_data.utils import ensure_dir, read_jsonl, stable_uuid, write_jsonl

log = get_logger(__name__)


def build_rag_eval(*, dry_run: bool = False, limit: int | None = 40) -> dict[str, Any]:
    settings = get_settings()
    chunks = [ChunkRecord.model_validate(r) for r in read_jsonl(settings.datasets_root / "interim" / "chunks" / "chunks.jsonl")]
    reqs = read_jsonl(settings.datasets_root / "silver" / "requirements.jsonl")
    docs = {d["document_id"]: d for d in read_jsonl(settings.datasets_root / "manifests" / "documents.jsonl")}
    questions: list[RAGQuestion] = []

    type_cycle = [
        QuestionType.project_basic,
        QuestionType.qualification,
        QuestionType.scoring,
        QuestionType.commercial,
        QuestionType.technical,
        QuestionType.rejection,
        QuestionType.time_location,
        QuestionType.evidence,
        QuestionType.multi_section,
    ]

    for i, chunk in enumerate(chunks):
        if limit is not None and len(questions) >= limit:
            break
        qtype = type_cycle[i % len(type_cycle)]
        quote = chunk.text.strip().splitlines()[0][:120] if chunk.text.strip() else chunk.text[:120]
        question = f"根据招标文件，关于“{quote[:40]}”的要求是什么？"
        answer = quote
        src_url = (docs.get(chunk.document_id) or {}).get("source_url")
        questions.append(
            RAGQuestion(
                question_id=str(stable_uuid(f"ragq:{chunk.chunk_id}:{qtype.value}")),
                project_id=chunk.project_id,
                question=question,
                answer=answer,
                answerable=True,
                gold_chunk_ids=[chunk.chunk_id],
                gold_document_ids=[chunk.document_id],
                source_document_ids=[chunk.document_id],
                source_urls=[src_url] if src_url else [],
                source_pages=[chunk.page_start],
                source_quotes=[quote],
                question_type=qtype,
                difficulty=Difficulty.medium,
                quality_level=QualityLevel.silver,
                review_status=ReviewStatus.pending,
            )
        )

    # 10-15% unanswerable
    n_unans = max(1, int(len(questions) * 0.12))
    for i in range(n_unans):
        pid = chunks[i % len(chunks)].project_id if chunks else "unknown"
        questions.append(
            RAGQuestion(
                question_id=str(stable_uuid(f"ragq:unans:{i}:{pid}")),
                project_id=pid,
                question="该招标文件中是否规定了月球基地验收标准？",
                answer=None,
                answerable=False,
                gold_chunk_ids=[],
                gold_document_ids=[],
                source_pages=[],
                source_quotes=[],
                question_type=QuestionType.unanswerable,
                difficulty=Difficulty.easy,
                quality_level=QualityLevel.silver,
                review_status=ReviewStatus.pending,
            )
        )

    # Prefer requirements for qualification questions when available
    for req in reqs[: min(10, len(reqs))]:
        if limit is not None and len([q for q in questions if q.answerable]) >= limit:
            break
        chunk_id = req.get("chunk_id")
        if not chunk_id:
            continue
        questions.append(
            RAGQuestion(
                question_id=str(stable_uuid(f"ragq:req:{req['requirement_id']}")),
                project_id=req["project_id"],
                question=f"资格/条款要求“{req.get('title', '')}”的具体内容是什么？",
                answer=req.get("normalized_requirement"),
                answerable=True,
                gold_chunk_ids=[chunk_id],
                gold_document_ids=[req.get("document_id")] if req.get("document_id") else [],
                source_document_ids=[req.get("document_id")] if req.get("document_id") else [],
                source_urls=[req.get("source_url")] if req.get("source_url") else [],
                source_pages=[req["source_page"]] if req.get("source_page") else [],
                source_quotes=[req.get("original_text", "")[:160]],
                question_type=QuestionType.qualification,
                quality_level=QualityLevel.silver,
                review_status=ReviewStatus.pending,
            )
        )

    stats = {
        "questions": len(questions),
        "answerable": sum(1 for q in questions if q.answerable),
        "unanswerable": sum(1 for q in questions if not q.answerable),
        "dry_run": dry_run,
    }
    if not dry_run:
        write_jsonl(ensure_dir(settings.datasets_root / "eval" / "rag") / "questions.jsonl", questions)
    log_stats(log, "rag_eval", stats)
    return stats
