from __future__ import annotations

import re
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

UNANSWERABLE_TEMPLATES = [
    "本项目采购文件是否要求投标人具备国际航天发射资质？",
    "招标文件是否规定必须提交月球探测任务履约业绩？",
    "本项目是否要求使用固态氦冷却服务器机柜？",
    "采购文件是否将“境外核动力船舶维保能力”列为资格条件？",
    "招标文件是否要求投标报价以比特币结算？",
]


def _sentence_spans(text: str) -> list[str]:
    parts = re.split(r"(?<=[。；\n])", text)
    return [p.strip() for p in parts if len(p.strip()) >= 20]


def _pick_requirement_like(chunk: ChunkRecord) -> str | None:
    for sent in _sentence_spans(chunk.text):
        if any(k in sent for k in ("应当", "必须", "不得", "投标人", "供应商", "评分", "资质", "预算", "截止", "废标", "无效")):
            return sent[:220]
    spans = _sentence_spans(chunk.text)
    return spans[0][:220] if spans else None


def _question_for(qtype: QuestionType, quote: str) -> str:
    mapping = {
        QuestionType.project_basic: f"根据采购文件，与下列内容直接相关的项目基本信息是什么？原文：{quote[:80]}",
        QuestionType.qualification: f"根据原文，投标人/供应商需要满足哪项资格或证明要求？原文：{quote[:80]}",
        QuestionType.scoring: f"根据评分相关原文，该项如何计分或评审？原文：{quote[:80]}",
        QuestionType.commercial: f"根据商务条款原文，具体商务要求是什么？原文：{quote[:80]}",
        QuestionType.technical: f"根据技术条款原文，关键技术要求是什么？原文：{quote[:80]}",
        QuestionType.rejection: f"根据否决/无效条款原文，何种情形将被否决或不予受理？原文：{quote[:80]}",
        QuestionType.time_location: f"根据原文，与时间或地点相关的要求是什么？原文：{quote[:80]}",
        QuestionType.evidence: f"完成该要求通常需要提供哪些证明材料？请只依据原文作答。原文：{quote[:80]}",
        QuestionType.multi_section: f"请概括原文中与投标义务直接相关的要求。原文：{quote[:80]}",
    }
    return mapping.get(qtype, f"根据采购文件原文回答：{quote[:80]}")


def _answer_from_quote(quote: str) -> str:
    # Do not use "first line of chunk" verbatim as the only answer shape —
    # normalize to a grounded paraphrase that still must be supported by quote.
    q = re.sub(r"\s+", " ", quote).strip()
    if len(q) <= 160:
        return q
    return q[:160].rstrip("，,；;、") + "…"


def _corpus_mentions(corpus: str, keywords: list[str]) -> bool:
    return any(k in corpus for k in keywords)


def build_rag_eval(*, dry_run: bool = False, limit: int | None = 40) -> dict[str, Any]:
    settings = get_settings()
    projects = {p["project_id"]: p for p in read_jsonl(settings.datasets_root / "manifests" / "projects.jsonl")}
    chunks_all = [ChunkRecord.model_validate(r) for r in read_jsonl(settings.datasets_root / "interim" / "chunks" / "chunks.jsonl")]
    # Prefer level_a / level_b for RAG; allow level_c only as fallback.
    preferred = [
        c
        for c in chunks_all
        if (projects.get(c.project_id) or {}).get("bundle_level") in {"level_a", "level_b"}
    ]
    chunks = preferred or [
        c for c in chunks_all if (projects.get(c.project_id) or {}).get("bundle_level") == "level_c"
    ] or chunks_all
    reqs = read_jsonl(settings.datasets_root / "silver" / "requirements.jsonl") + read_jsonl(
        settings.datasets_root / "gold" / "requirements.jsonl"
    )
    docs = {d["document_id"]: d for d in read_jsonl(settings.datasets_root / "manifests" / "documents.jsonl")}
    statements_by_project: dict[str, str] = {}
    for c in chunks_all:
        statements_by_project[c.project_id] = statements_by_project.get(c.project_id, "") + "\n" + c.text

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

    answerable_target = limit if limit is not None else 40
    for i, chunk in enumerate(chunks):
        if len([q for q in questions if q.answerable]) >= answerable_target:
            break
        quote = _pick_requirement_like(chunk)
        if not quote:
            continue
        qtype = type_cycle[i % len(type_cycle)]
        answer = _answer_from_quote(quote)
        # Guard: answer must not equal chunk first line only when that line is empty noise
        first_line = chunk.text.strip().splitlines()[0].strip() if chunk.text.strip() else ""
        if answer == first_line and len(answer) < 25:
            continue
        src_url = (docs.get(chunk.document_id) or {}).get("source_url")
        questions.append(
            RAGQuestion(
                question_id=str(stable_uuid(f"ragq:{chunk.chunk_id}:{qtype.value}:{content_key(quote)}")),
                project_id=chunk.project_id,
                question=_question_for(qtype, quote),
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

    # Requirement-grounded QA
    for req in reqs:
        if len([q for q in questions if q.answerable]) >= answerable_target:
            break
        level = (projects.get(req.get("project_id") or "") or {}).get("bundle_level")
        if level not in {"level_a", "level_b", "level_c"}:
            continue
        chunk_id = req.get("chunk_id")
        original = (req.get("original_text") or "").strip()
        if not chunk_id or len(original) < 16:
            continue
        questions.append(
            RAGQuestion(
                question_id=str(stable_uuid(f"ragq:req:{req['requirement_id']}")),
                project_id=req["project_id"],
                question=f"资格/条款“{req.get('title') or original[:24]}”的具体内容是什么？请依据采购文件原文回答。",
                answer=_answer_from_quote(req.get("normalized_requirement") or original),
                answerable=True,
                gold_chunk_ids=[chunk_id],
                gold_document_ids=[req["document_id"]] if req.get("document_id") else [],
                source_document_ids=[req["document_id"]] if req.get("document_id") else [],
                source_urls=[req["source_url"]] if req.get("source_url") else [],
                source_pages=[req["source_page"]] if req.get("source_page") else [],
                source_quotes=[original[:220]],
                question_type=QuestionType.qualification,
                quality_level=QualityLevel.silver,
                review_status=ReviewStatus.pending,
            )
        )

    # Unanswerable: procurement-domain and confirmed absent via full-project corpus scan
    n_unans = max(1, int(len([q for q in questions if q.answerable]) * 0.12))
    unans_added = 0
    project_ids = sorted({c.project_id for c in chunks})
    for i, pid in enumerate(project_ids):
        if unans_added >= n_unans:
            break
        tmpl = UNANSWERABLE_TEMPLATES[i % len(UNANSWERABLE_TEMPLATES)]
        corpus = statements_by_project.get(pid, "")
        # Confirm none of the exotic keywords exist in project corpus
        exotic = re.findall(r"[\u4e00-\u9fff]{2,12}", tmpl)
        # Use distinctive tokens from the template
        check_tokens = [t for t in ("航天发射", "月球探测", "固态氦", "核动力船舶", "比特币") if t in tmpl]
        if not check_tokens:
            check_tokens = exotic[-2:] if len(exotic) >= 2 else exotic
        if _corpus_mentions(corpus, check_tokens):
            continue
        questions.append(
            RAGQuestion(
                question_id=str(stable_uuid(f"ragq:unans:{pid}:{tmpl}")),
                project_id=pid,
                question=tmpl,
                answer=None,
                answerable=False,
                gold_chunk_ids=[],
                gold_document_ids=[],
                source_document_ids=[],
                source_urls=[],
                source_pages=[],
                source_quotes=[],
                question_type=QuestionType.unanswerable,
                difficulty=Difficulty.easy,
                quality_level=QualityLevel.silver,
                review_status=ReviewStatus.pending,
            )
        )
        unans_added += 1

    # Dedup questions by text
    seen_q: set[str] = set()
    deduped: list[RAGQuestion] = []
    for q in questions:
        key = re.sub(r"\s+", "", q.question)
        if key in seen_q:
            continue
        seen_q.add(key)
        deduped.append(q)
    questions = deduped

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


def content_key(text: str) -> str:
    from bidpilot_data.utils.hashing import content_fingerprint

    return content_fingerprint(text)
