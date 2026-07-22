"""Generate reference sample candidates per task type (deterministic by default)."""

from __future__ import annotations

import random
import re
from datetime import datetime, timezone
from typing import Any

from bidpilot_data.rag_eval.build import question_leaks_quote
from bidpilot_data.reference_dataset.schema import (
    GENERATOR_VERSION,
    CitationMetadata,
    DataProvenance,
    EvidenceItem,
    QualityChecks,
    ReferenceSample,
)
from bidpilot_data.reference_dataset.select import CorpusIndexes, SelectedProject, map_category
from bidpilot_data.reference_dataset.validate import quote_contiguous_in_text
from bidpilot_data.utils import stable_uuid

_UNANSWERABLE_TEMPLATES = [
    "采购文件是否规定质保期内每月巡检次数？",
    "是否要求投标人提供指定品牌服务器？",
    "是否约定提前交付奖励金额？",
    "是否要求驻场人员夜间值班？",
    "是否公开了所有评审专家的评分明细？",
    "是否要求提供英文版操作手册？",
    "是否约定故障恢复时间目标（RTO）的具体数值？",
    "是否要求项目团队核心成员不少于五名本地户籍人员？",
    "是否规定必须采用微服务架构交付？",
    "是否要求提供省级以上科技进步奖证明？",
]

_COMPLIANCE_PATTERNS = [
    ("mandatory", re.compile(r"(必须|应当|须|不得|禁止)"), "mandatory_clause"),
    ("deadline", re.compile(r"(截止|之前递交|开标时间|投标文件递交|响应文件提交)"), "deadline_check"),
    ("invalid_bid", re.compile(r"(无效投标|废标|否决|资格审查不合格)"), "invalid_bid_rule"),
]

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _sid(*parts: Any) -> str:
    return str(stable_uuid("ref:" + ":".join(str(p) for p in parts)))


def _trim_quote(text: str, max_len: int = 220) -> str:
    t = re.sub(r"\s+", " ", (text or "").strip())
    return t[:max_len]


def _find_char_span(quote: str, text: str) -> tuple[int | None, int | None]:
    if not quote or not text:
        return None, None
    idx = text.find(quote)
    if idx >= 0:
        return idx, idx + len(quote)
    # whitespace-insensitive locate approximate start of first 12 chars
    qn = "".join(quote.split())
    tn = "".join(text.split())
    if qn and qn in tn:
        # Best-effort: return None ranges rather than wrong offsets
        return None, None
    return None, None


def _chunk_evidence(
    chunk: dict[str, Any],
    quote: str,
    *,
    source_url: str | None = None,
    evidence_id: str | None = None,
) -> EvidenceItem:
    text = chunk.get("text") or ""
    q = _trim_quote(quote) if quote else _trim_quote(text)
    # Ensure quote is grounded; fall back to a sentence from chunk
    if q and not quote_contiguous_in_text(q, text):
        # pick a contiguous substring from chunk
        q = _trim_quote(text[0:180] if len(text) > 40 else text)
    cs, ce = _find_char_span(q, text)
    return EvidenceItem(
        evidence_id=evidence_id,
        chunk_id=chunk.get("chunk_id"),
        document_id=chunk.get("document_id"),
        page_number=chunk.get("page_start"),
        char_start=cs,
        char_end=ce,
        quote=q,
        source_url=source_url,
    )


def _base_sample(
    *,
    task_type: str,
    project_id: str,
    document_id: str,
    input_obj: dict[str, Any],
    output_obj: dict[str, Any],
    evidence: list[EvidenceItem],
    citation: CitationMetadata,
    confidence: float,
    provenance: DataProvenance,
    generation_model: str = "deterministic",
    label_source: str = "auto_reference",
    key: str,
    created_at: datetime | None = None,
) -> ReferenceSample:
    return ReferenceSample(
        sample_id=_sid(task_type, project_id, key),
        task_type=task_type,  # type: ignore[arg-type]
        project_id=project_id,
        document_id=document_id,
        input=input_obj,
        reference_output=output_obj,
        evidence=evidence,
        citation_metadata=citation,
        quality_checks=QualityChecks(),
        confidence=confidence,
        generation_model=generation_model,
        generator_version=GENERATOR_VERSION,
        data_provenance=provenance,
        label_source=label_source,  # type: ignore[arg-type]
        created_at=created_at or _now(),
    )


def _long_chunks(chunks: list[dict[str, Any]], min_len: int = 80) -> list[dict[str, Any]]:
    return [c for c in chunks if len((c.get("text") or "").strip()) >= min_len]


def generate_rag_samples(
    corpus: CorpusIndexes,
    selected: list[SelectedProject],
    *,
    rng: random.Random,
    target: int,
    created_at: datetime | None = None,
) -> list[ReferenceSample]:
    out: list[ReferenceSample] = []
    # Prefer reuse+normalize existing RAG questions where quote validates
    existing: list[dict[str, Any]] = []
    for sp in selected:
        existing.extend(corpus.rag_by_project.get(sp.project_id) or [])
    rng.shuffle(existing)

    for q in existing:
        if len(out) >= target:
            break
        if not q.get("answerable"):
            continue
        quotes = list(q.get("source_quotes") or [])
        chunk_ids = list(q.get("gold_chunk_ids") or [])
        if not quotes or not chunk_ids:
            continue
        quote = quotes[0]
        chunk = corpus.chunks.get(chunk_ids[0])
        if not chunk:
            continue
        if not quote_contiguous_in_text(quote, chunk.get("text") or ""):
            continue
        if question_leaks_quote(q.get("question") or "", quote):
            continue
        doc_id = (q.get("gold_document_ids") or [chunk.get("document_id")])[0]
        ev = _chunk_evidence(
            chunk,
            quote,
            source_url=(q.get("source_urls") or [None])[0],
            evidence_id=(q.get("evidence_ids") or [None])[0],
        )
        sample = _base_sample(
            task_type="rag",
            project_id=q["project_id"],
            document_id=doc_id or chunk.get("document_id") or "",
            input_obj={
                "question": q.get("question"),
                "question_type": q.get("question_type"),
                "context_chunk_ids": chunk_ids[:3],
            },
            output_obj={
                "answer": q.get("answer"),
                "answerable": True,
                "citations": chunk_ids[:3],
            },
            evidence=[ev],
            citation=CitationMetadata(
                chunk_ids=chunk_ids[:3],
                document_ids=list(q.get("gold_document_ids") or [doc_id])[:3],
                page_numbers=list(q.get("source_pages") or ([chunk.get("page_start")] if chunk.get("page_start") else [])),
                source_urls=list(q.get("source_urls") or []),
                quotes=[quote],
                category=map_category(q.get("question_type")),
                notes="reused_eval_rag",
            ),
            confidence=0.82,
            provenance=DataProvenance(
                source_paths=["eval/rag/questions.jsonl", "interim/chunks/chunks.jsonl"],
                source_record_ids=[q.get("question_id") or ""],
                method="reuse_normalize_rag",
                reuse_existing_rag=True,
            ),
            key=f"rag-reuse-{q.get('question_id')}",
            created_at=created_at,
        )
        out.append(sample)

    # Template generation from requirement-like chunks
    if len(out) < target:
        for sp in selected:
            if len(out) >= target:
                break
            chunks = _long_chunks(corpus.chunks_by_project.get(sp.project_id) or [])
            reqs = corpus.requirements_by_project.get(sp.project_id) or []
            rng.shuffle(reqs)
            for req in reqs:
                if len(out) >= target:
                    break
                chunk = corpus.chunks.get(req.get("chunk_id") or "")
                if not chunk:
                    continue
                quote = _trim_quote(req.get("original_text") or "")
                if len(quote) < 20 or not quote_contiguous_in_text(quote, chunk.get("text") or ""):
                    continue
                title = (req.get("title") or "")[:24]
                cat = map_category(req.get("category"))
                question = f"关于「{title or cat}」的条款要求是什么？"
                if question_leaks_quote(question, quote):
                    question = f"该项目{cat}类要求中有哪些关键约束？"
                answer = quote[:180]
                doc_id = req.get("document_id") or chunk.get("document_id") or ""
                ev = _chunk_evidence(chunk, quote, source_url=req.get("source_url"))
                sample = _base_sample(
                    task_type="rag",
                    project_id=sp.project_id,
                    document_id=doc_id,
                    input_obj={"question": question, "question_type": cat, "context_chunk_ids": [chunk["chunk_id"]]},
                    output_obj={"answer": answer, "answerable": True, "citations": [chunk["chunk_id"]]},
                    evidence=[ev],
                    citation=CitationMetadata(
                        chunk_ids=[chunk["chunk_id"]],
                        document_ids=[doc_id] if doc_id else [],
                        page_numbers=[chunk.get("page_start")] if chunk.get("page_start") else [],
                        source_urls=[req.get("source_url")] if req.get("source_url") else [],
                        quotes=[quote],
                        category=cat,
                        notes="template_from_requirement",
                    ),
                    confidence=0.72,
                    provenance=DataProvenance(
                        source_paths=["silver/requirements.jsonl", "interim/chunks/chunks.jsonl"],
                        source_record_ids=[req.get("requirement_id") or req.get("annotation_id") or ""],
                        method="template_rag",
                    ),
                    key=f"rag-tpl-{req.get('requirement_id')}",
                created_at=created_at,
                )
                out.append(sample)
                break  # diversify across projects
    return out[:target]


def generate_extraction_samples(
    corpus: CorpusIndexes,
    selected: list[SelectedProject],
    *,
    rng: random.Random,
    target: int,
    created_at: datetime | None = None,
) -> list[ReferenceSample]:
    out: list[ReferenceSample] = []
    # Diversify categories
    by_cat: dict[str, list[tuple[SelectedProject, dict[str, Any]]]] = {}
    for sp in selected:
        for req in corpus.requirements_by_project.get(sp.project_id) or []:
            cat = map_category(req.get("category"))
            by_cat.setdefault(cat, []).append((sp, req))
    for cat in by_cat:
        rng.shuffle(by_cat[cat])

    cats = list(by_cat.keys()) or ["risk"]
    i = 0
    while len(out) < target and any(by_cat.values()):
        cat = cats[i % len(cats)]
        i += 1
        bucket = by_cat.get(cat) or []
        if not bucket:
            continue
        sp, req = bucket.pop(0)
        chunk = corpus.chunks.get(req.get("chunk_id") or "")
        if not chunk:
            continue
        quote = _trim_quote(req.get("original_text") or "")
        if len(quote) < 16 or not quote_contiguous_in_text(quote, chunk.get("text") or ""):
            continue
        doc_id = req.get("document_id") or chunk.get("document_id") or ""
        ev = _chunk_evidence(chunk, quote, source_url=req.get("source_url"))
        sample = _base_sample(
            task_type="extraction",
            project_id=sp.project_id,
            document_id=doc_id,
            input_obj={
                "text": (chunk.get("text") or "")[:1200],
                "instruction": "从文本中抽取招投标需求条款",
                "category_hint": req.get("category"),
            },
            output_obj={
                "title": req.get("title"),
                "category": req.get("category"),
                "normalized_requirement": req.get("normalized_requirement") or quote,
                "mandatory": bool(req.get("mandatory")),
                "risk_level": req.get("risk_level") or "medium",
            },
            evidence=[ev],
            citation=CitationMetadata(
                chunk_ids=[chunk["chunk_id"]],
                document_ids=[doc_id] if doc_id else [],
                page_numbers=[req.get("source_page") or chunk.get("page_start")],
                source_urls=[req.get("source_url")] if req.get("source_url") else [],
                quotes=[quote],
                category=cat,
            ),
            confidence=float(req.get("confidence") or 0.7),
            provenance=DataProvenance(
                source_paths=["silver/requirements.jsonl", "interim/chunks/chunks.jsonl"],
                source_record_ids=[req.get("requirement_id") or ""],
                method="silver_requirement_extraction",
            ),
            label_source="auto_reference",
            key=f"ext-{req.get('requirement_id')}",
        created_at=created_at,
        )
        out.append(sample)
    return out[:target]


_MISSING_COMPANY_MATERIAL = "缺少企业侧证据：当前材料未找到充分证据以支撑该需求条款的企业侧响应判定。"


def _chunks_for_documents(corpus: CorpusIndexes, document_ids: list[str]) -> list[dict[str, Any]]:
    wanted = {d for d in document_ids if d}
    if not wanted:
        return []
    return [c for c in corpus.chunks.values() if c.get("document_id") in wanted]


def _find_supplier_chunk(
    corpus: CorpusIndexes,
    supplier: dict[str, Any],
) -> tuple[dict[str, Any], str] | None:
    """Locate a real chunk in supplier source documents that contains the supplier name."""
    name = (supplier.get("name") or "").strip()
    if not name:
        return None
    docs = list(supplier.get("source_document_ids") or [])
    for chunk in _chunks_for_documents(corpus, docs):
        text = chunk.get("text") or ""
        if name not in text:
            continue
        # Prefer a contiguous window around the name mention
        idx = text.find(name)
        start = max(0, idx - 40)
        end = min(len(text), idx + len(name) + 80)
        quote = _trim_quote(text[start:end])
        if len(quote) < 8 or not quote_contiguous_in_text(quote, text):
            quote = _trim_quote(name)
            if not quote_contiguous_in_text(quote, text):
                continue
        return chunk, quote
    return None


def _requirement_tender_evidence(
    corpus: CorpusIndexes,
    req: dict[str, Any],
) -> tuple[dict[str, Any], str] | None:
    chunk = corpus.chunks.get(req.get("chunk_id") or "")
    if not chunk:
        return None
    quote = _trim_quote(req.get("original_text") or "")
    if len(quote) < 16 or not quote_contiguous_in_text(quote, chunk.get("text") or ""):
        return None
    return chunk, quote


def generate_matching_samples(
    corpus: CorpusIndexes,
    selected: list[SelectedProject],
    *,
    rng: random.Random,
    target: int,
    created_at: datetime | None = None,
) -> list[ReferenceSample]:
    """Matching samples use ONLY real company-side evidence (or explicit insufficient_evidence)."""
    out: list[ReferenceSample] = []
    used_req_ids: set[str] = set()

    # 1) Prefer disclosed matches when present
    req_by_id = {r.get("requirement_id"): r for r in corpus.requirements if r.get("requirement_id")}
    for m in corpus.matches:
        if len(out) >= target:
            break
        req = req_by_id.get(m.get("requirement_id"))
        if not req:
            continue
        chunk = corpus.chunks.get(m.get("evidence_chunk_id") or "")
        if not chunk:
            continue
        quote = _trim_quote(m.get("source_quote") or "")
        if not quote or not quote_contiguous_in_text(quote, chunk.get("text") or ""):
            continue
        status_map = {
            "satisfied": "supported",
            "partially_satisfied": "partially_supported",
            "missing": "insufficient_evidence",
            "uncertain": "insufficient_evidence",
        }
        status = status_map.get(str(m.get("status")), "insufficient_evidence")
        doc_id = m.get("evidence_document_id") or chunk.get("document_id") or ""
        ev = _chunk_evidence(chunk, quote, source_url=m.get("source_url"), evidence_id=(m.get("evidence_ids") or [None])[0])
        rid = req.get("requirement_id") or ""
        sample = _base_sample(
            task_type="matching",
            project_id=req["project_id"],
            document_id=doc_id,
            input_obj={
                "requirement": req.get("normalized_requirement") or req.get("original_text"),
                "company_material": quote,
                "supplier_id": m.get("supplier_id"),
            },
            output_obj={"status": status, "reason": m.get("reason") or status, "evidence_chunk_ids": [chunk["chunk_id"]]},
            evidence=[ev],
            citation=CitationMetadata(
                chunk_ids=[chunk["chunk_id"]],
                document_ids=[doc_id] if doc_id else [],
                quotes=[quote],
                source_urls=[m.get("source_url")] if m.get("source_url") else [],
                category=map_category(req.get("category")),
                notes="disclosed_match",
            ),
            confidence=max(0.88, float(m.get("confidence") or 0.88)),
            provenance=DataProvenance(
                source_paths=["silver/requirement_matches.jsonl"],
                source_record_ids=[m.get("match_id") or ""],
                method="disclosed_match",
                notes="real_bilateral_evidence",
            ),
            key=f"match-disclosed-{m.get('match_id')}",
            created_at=created_at,
        )
        out.append(sample)
        if rid:
            used_req_ids.add(rid)

    # 2) Disclosed suppliers: real chunks containing supplier name + tender requirement evidence
    # Cap bilateral pairs so insufficient_evidence padding can still reach ≥10 when possible.
    BILATERAL_SUPPLIER_CAP = 20
    selected_ids = {sp.project_id for sp in selected}
    supplier_rows = [s for s in corpus.suppliers if s.get("project_id") in selected_ids]
    rng.shuffle(supplier_rows)
    bilateral_added = 0
    for supplier in supplier_rows:
        if len(out) >= target or bilateral_added >= BILATERAL_SUPPLIER_CAP:
            break
        found = _find_supplier_chunk(corpus, supplier)
        if not found:
            continue
        company_chunk, company_quote = found
        pid = supplier.get("project_id") or ""
        reqs = list(corpus.requirements_by_project.get(pid) or [])
        rng.shuffle(reqs)
        taken_for_supplier = 0
        for req in reqs:
            if (
                len(out) >= target
                or taken_for_supplier >= 5
                or bilateral_added >= BILATERAL_SUPPLIER_CAP
            ):
                break
            rid = req.get("requirement_id") or ""
            if rid in used_req_ids:
                continue
            tender = _requirement_tender_evidence(corpus, req)
            if not tender:
                continue
            tender_chunk, tender_quote = tender
            company_doc = company_chunk.get("document_id") or ""
            tender_doc = tender_chunk.get("document_id") or req.get("document_id") or ""
            company_ev = _chunk_evidence(company_chunk, company_quote, source_url=(supplier.get("source_urls") or [None])[0])
            tender_ev = _chunk_evidence(tender_chunk, tender_quote, source_url=req.get("source_url"))
            # Name attestation only — clause-level satisfaction not fully proven
            status = "partially_supported"
            sample = _base_sample(
                task_type="matching",
                project_id=pid,
                document_id=tender_doc or company_doc,
                input_obj={
                    "requirement": req.get("normalized_requirement") or tender_quote,
                    "company_material": company_quote,
                    "supplier_id": supplier.get("supplier_id"),
                    "supplier_name": supplier.get("name"),
                },
                output_obj={
                    "status": status,
                    "reason": (
                        f"公开材料出现供应商「{supplier.get('name')}」相关表述，"
                        "可作为企业侧证据；条款完全满足情况仍需人工核验。"
                    ),
                    "evidence_chunk_ids": [tender_chunk["chunk_id"], company_chunk["chunk_id"]],
                },
                evidence=[tender_ev, company_ev],
                citation=CitationMetadata(
                    chunk_ids=[tender_chunk["chunk_id"], company_chunk["chunk_id"]],
                    document_ids=[d for d in [tender_doc, company_doc] if d],
                    page_numbers=[
                        p
                        for p in [tender_chunk.get("page_start"), company_chunk.get("page_start")]
                        if p is not None
                    ],
                    source_urls=[u for u in [req.get("source_url"), *((supplier.get("source_urls") or []))] if u],
                    quotes=[tender_quote, company_quote],
                    category=map_category(req.get("category")),
                    notes="disclosed_supplier_bilateral",
                ),
                confidence=0.86,
                provenance=DataProvenance(
                    source_paths=[
                        "silver/disclosed_suppliers.jsonl",
                        "silver/requirements.jsonl",
                        "interim/chunks/chunks.jsonl",
                    ],
                    source_record_ids=[supplier.get("supplier_id") or "", rid],
                    method="disclosed_supplier_bilateral",
                    notes="real_bilateral_evidence",
                ),
                key=f"match-bilateral-{rid}-{supplier.get('supplier_id')}",
                created_at=created_at,
            )
            out.append(sample)
            taken_for_supplier += 1
            bilateral_added += 1
            if rid:
                used_req_ids.add(rid)

    # 3) Pad with insufficient_evidence when no real company-side evidence for a requirement
    for sp in selected:
        if len(out) >= target:
            break
        reqs = list(corpus.requirements_by_project.get(sp.project_id) or [])
        rng.shuffle(reqs)
        for req in reqs:
            if len(out) >= target:
                break
            rid = req.get("requirement_id") or ""
            if rid in used_req_ids:
                continue
            tender = _requirement_tender_evidence(corpus, req)
            if not tender:
                continue
            tender_chunk, tender_quote = tender
            doc_id = req.get("document_id") or tender_chunk.get("document_id") or ""
            tender_ev = _chunk_evidence(tender_chunk, tender_quote, source_url=req.get("source_url"))
            sample = _base_sample(
                task_type="matching",
                project_id=sp.project_id,
                document_id=doc_id,
                input_obj={
                    "requirement": req.get("normalized_requirement") or tender_quote,
                    "company_material": _MISSING_COMPANY_MATERIAL,
                },
                output_obj={
                    "status": "insufficient_evidence",
                    "reason": "缺少企业侧证据，当前材料未找到充分证据，无法判定企业是否满足该需求。",
                    "evidence_chunk_ids": [tender_chunk["chunk_id"]],
                },
                evidence=[tender_ev],
                citation=CitationMetadata(
                    chunk_ids=[tender_chunk["chunk_id"]],
                    document_ids=[doc_id] if doc_id else [],
                    page_numbers=[tender_chunk.get("page_start")] if tender_chunk.get("page_start") else [],
                    source_urls=[req.get("source_url")] if req.get("source_url") else [],
                    quotes=[tender_quote],
                    category=map_category(req.get("category")),
                    notes="missing_company_evidence",
                ),
                confidence=0.64,
                provenance=DataProvenance(
                    source_paths=["silver/requirements.jsonl", "interim/chunks/chunks.jsonl"],
                    source_record_ids=[rid],
                    method="insufficient_company_evidence",
                    notes="matching_missing_company_evidence",
                ),
                key=f"match-insuff-{rid}",
                created_at=created_at,
            )
            out.append(sample)
            if rid:
                used_req_ids.add(rid)
    return out[:target]


def generate_compliance_samples(
    corpus: CorpusIndexes,
    selected: list[SelectedProject],
    *,
    rng: random.Random,
    target: int,
    created_at: datetime | None = None,
) -> list[ReferenceSample]:
    out: list[ReferenceSample] = []
    for sp in selected:
        if len(out) >= target:
            break
        chunks = _long_chunks(corpus.chunks_by_project.get(sp.project_id) or [])
        rng.shuffle(chunks)
        for chunk in chunks:
            if len(out) >= target:
                break
            text = chunk.get("text") or ""
            for rule_name, pattern, check_id in _COMPLIANCE_PATTERNS:
                m = pattern.search(text)
                if not m:
                    continue
                # Surrounding sentence as quote
                start = max(0, m.start() - 40)
                end = min(len(text), m.end() + 80)
                quote = _trim_quote(text[start:end])
                if len(quote) < 12 or not quote_contiguous_in_text(quote, text):
                    continue
                doc_id = chunk.get("document_id") or ""
                ev = _chunk_evidence(chunk, quote)
                verdict = "fail" if rule_name == "invalid_bid" and "不得" not in quote else "pass"
                if rule_name == "deadline":
                    verdict = "attention_required"
                sample = _base_sample(
                    task_type="compliance",
                    project_id=sp.project_id,
                    document_id=doc_id,
                    input_obj={
                        "rule_type": rule_name,
                        "check_id": check_id,
                        "text": text[:1000],
                        "instruction": f"检查文本是否包含{rule_name}类合规要点",
                    },
                    output_obj={
                        "verdict": verdict,
                        "rule_type": rule_name,
                        "finding": quote[:160],
                        "severity": True if rule_name == "mandatory" and ("必须" in quote or "应当" in quote) else False,
                    },
                    evidence=[ev],
                    citation=CitationMetadata(
                        chunk_ids=[chunk["chunk_id"]],
                        document_ids=[doc_id] if doc_id else [],
                        page_numbers=[chunk.get("page_start")] if chunk.get("page_start") else [],
                        quotes=[quote],
                        category="risk" if rule_name == "invalid_bid" else "commercial",
                    ),
                    confidence=0.7,
                    provenance=DataProvenance(
                        source_paths=["interim/chunks/chunks.jsonl"],
                        source_record_ids=[chunk.get("chunk_id") or ""],
                        method="rule_pattern_compliance",
                    ),
                    key=f"comp-{chunk.get('chunk_id')}-{rule_name}",
                created_at=created_at,
                )
                out.append(sample)
                break
    return out[:target]


def generate_drafting_samples(
    corpus: CorpusIndexes,
    selected: list[SelectedProject],
    *,
    rng: random.Random,
    target: int,
    created_at: datetime | None = None,
) -> list[ReferenceSample]:
    """Draft outlines ONLY from confirmed-like evidence (silver supported pairs / evidence quotes)."""
    out: list[ReferenceSample] = []
    for sp in selected:
        if len(out) >= target:
            break
        evidence_rows = list(corpus.evidence_by_project.get(sp.project_id) or [])
        reqs = list(corpus.requirements_by_project.get(sp.project_id) or [])
        rng.shuffle(evidence_rows)
        rng.shuffle(reqs)

        pair: tuple[dict[str, Any], dict[str, Any]] | None = None
        # Prefer evidence linked to a chunk that also backs a requirement
        for ev in evidence_rows:
            chunk = corpus.chunks.get(ev.get("chunk_id") or "")
            if not chunk:
                continue
            quote = _trim_quote(ev.get("quote") or "")
            if len(quote) < 20 or not quote_contiguous_in_text(quote, chunk.get("text") or ""):
                continue
            # Find a requirement on same chunk or document
            related = None
            for req in reqs:
                if req.get("chunk_id") == chunk.get("chunk_id") or req.get("document_id") == chunk.get("document_id"):
                    related = req
                    break
            if related is None and reqs:
                related = reqs[0]
            if related is None:
                continue
            pair = (ev, related)
            break

        # Fallback: mandatory silver requirement as "supported-like" evidence
        if pair is None:
            for req in reqs:
                if not req.get("mandatory"):
                    continue
                chunk = corpus.chunks.get(req.get("chunk_id") or "")
                if not chunk:
                    continue
                quote = _trim_quote(req.get("original_text") or "")
                if len(quote) < 20 or not quote_contiguous_in_text(quote, chunk.get("text") or ""):
                    continue
                pair = (
                    {
                        "evidence_id": None,
                        "chunk_id": chunk["chunk_id"],
                        "document_id": chunk.get("document_id"),
                        "quote": quote,
                        "source_url": req.get("source_url"),
                        "page_number": req.get("source_page") or chunk.get("page_start"),
                    },
                    req,
                )
                break

        if pair is None:
            continue
        ev_row, req = pair
        chunk = corpus.chunks.get(ev_row.get("chunk_id") or "")
        if not chunk:
            continue
        quote = _trim_quote(ev_row.get("quote") or "")
        doc_id = ev_row.get("document_id") or chunk.get("document_id") or ""
        ev = _chunk_evidence(chunk, quote, source_url=ev_row.get("source_url"), evidence_id=ev_row.get("evidence_id"))
        outline = [
            "一、需求理解：概述采购方对条款的核心要求",
            f"二、响应要点：针对「{(req.get('title') or '')[:40]}」给出对应能力说明",
            "三、证明材料：列出资质/业绩/人员等证据清单",
            "四、风险说明：标注仍需人工核验的不确定项",
        ]
        sample = _base_sample(
            task_type="drafting",
            project_id=sp.project_id,
            document_id=doc_id,
            input_obj={
                "requirement": req.get("normalized_requirement") or req.get("original_text"),
                "instruction": "基于已确认证据起草响应提纲（非终稿）",
            },
            output_obj={
                "outline": outline,
                "summary": f"依据公开证据起草响应提纲：{quote[:80]}",
                "disclaimer": True,
                "disclaimer_flag": True,
                "disclaimer_text": "自动生成提纲，仅供课程演示/自动评测，不构成正式投标承诺。",
            },
            evidence=[ev],
            citation=CitationMetadata(
                chunk_ids=[chunk["chunk_id"]],
                document_ids=[doc_id] if doc_id else [],
                page_numbers=[ev_row.get("page_number") or chunk.get("page_start")],
                source_urls=[ev_row.get("source_url")] if ev_row.get("source_url") else [],
                quotes=[quote],
                category=map_category(req.get("category")),
            ),
            confidence=0.68,
            provenance=DataProvenance(
                source_paths=["silver/evidence.jsonl", "silver/requirements.jsonl"],
                source_record_ids=[ev_row.get("evidence_id") or req.get("requirement_id") or ""],
                method="supported_pair_draft_outline",
            ),
            key=f"draft-{chunk.get('chunk_id')}-{req.get('requirement_id')}",
        created_at=created_at,
        )
        out.append(sample)
    return out[:target]


def generate_unanswerable_samples(
    corpus: CorpusIndexes,
    selected: list[SelectedProject],
    *,
    rng: random.Random,
    target: int,
    created_at: datetime | None = None,
) -> list[ReferenceSample]:
    out: list[ReferenceSample] = []
    # Reuse existing unanswerable RAG
    for sp in selected:
        for q in corpus.rag_by_project.get(sp.project_id) or []:
            if len(out) >= target:
                break
            if q.get("answerable"):
                continue
            chunks = _long_chunks(corpus.chunks_by_project.get(sp.project_id) or [])
            if not chunks:
                continue
            # Irrelevant evidence: pick a chunk unlikely related
            chunk = rng.choice(chunks)
            doc_id = chunk.get("document_id") or (q.get("gold_document_ids") or [""])[0] or ""
            # Intentionally weak/empty evidence
            sample = _base_sample(
                task_type="unanswerable",
                project_id=sp.project_id,
                document_id=doc_id or chunk.get("document_id") or "",
                input_obj={
                    "question": q.get("question"),
                    "context_chunk_ids": [chunk["chunk_id"]],
                },
                output_obj={
                    "answer": "依据所给材料无法确定；公开文本未提及该信息，应作证据不足处理。",
                    "answerable": False,
                    "abstain": True,
                    "status": "insufficient_evidence",
                },
                evidence=[],  # empty evidence for abstain
                citation=CitationMetadata(
                    chunk_ids=[],
                    document_ids=[doc_id] if doc_id else [],
                    quotes=[],
                    notes="unanswerable_reuse_rag",
                ),
                confidence=0.8,
                provenance=DataProvenance(
                    source_paths=["eval/rag/questions.jsonl"],
                    source_record_ids=[q.get("question_id") or ""],
                    method="reuse_unanswerable_rag",
                    reuse_existing_rag=True,
                ),
                key=f"una-reuse-{q.get('question_id')}",
            created_at=created_at,
            )
            out.append(sample)

    # Template unanswerable with irrelevant chunk evidence (quote not used as support for claim)
    ti = 0
    for sp in selected:
        if len(out) >= target:
            break
        chunks = _long_chunks(corpus.chunks_by_project.get(sp.project_id) or [])
        if not chunks:
            continue
        chunk = rng.choice(chunks)
        question = _UNANSWERABLE_TEMPLATES[ti % len(_UNANSWERABLE_TEMPLATES)]
        ti += 1
        # Provide irrelevant quote but answer must abstain
        irr = _trim_quote(chunk.get("text") or "", 80)
        doc_id = chunk.get("document_id") or ""
        sample = _base_sample(
            task_type="unanswerable",
            project_id=sp.project_id,
            document_id=doc_id,
            input_obj={"question": question, "context_chunk_ids": [chunk["chunk_id"]], "irrelevant_context": True},
            output_obj={
                "answer": "材料中没有足够信息回答该问题，应 abstain / 标记为证据不足。",
                "answerable": False,
                "abstain": True,
                "status": "insufficient_evidence",
            },
            evidence=[
                EvidenceItem(
                    chunk_id=chunk["chunk_id"],
                    document_id=doc_id,
                    page_number=chunk.get("page_start"),
                    quote=irr,
                    source_url=None,
                )
            ]
            if irr
            else [],
            citation=CitationMetadata(
                chunk_ids=[chunk["chunk_id"]],
                document_ids=[doc_id] if doc_id else [],
                quotes=[irr] if irr else [],
                notes="irrelevant_evidence_for_unanswerable",
            ),
            confidence=0.75,
            provenance=DataProvenance(
                source_paths=["interim/chunks/chunks.jsonl"],
                source_record_ids=[chunk.get("chunk_id") or ""],
                method="template_unanswerable",
            ),
            key=f"una-tpl-{sp.project_id}-{ti}",
        created_at=created_at,
        )
        out.append(sample)
    return out[:target]


def generate_all_candidates(
    corpus: CorpusIndexes,
    selected: list[SelectedProject],
    *,
    seed: int,
    targets: dict[str, int],
    created_at: datetime | None = None,
) -> list[ReferenceSample]:
    rng = random.Random(seed)
    samples: list[ReferenceSample] = []
    samples.extend(
        generate_rag_samples(corpus, selected, rng=rng, target=targets.get("rag", 30), created_at=created_at)
    )
    samples.extend(
        generate_extraction_samples(
            corpus, selected, rng=rng, target=targets.get("extraction", 30), created_at=created_at
        )
    )
    samples.extend(
        generate_matching_samples(
            corpus, selected, rng=rng, target=targets.get("matching", 30), created_at=created_at
        )
    )
    samples.extend(
        generate_compliance_samples(
            corpus, selected, rng=rng, target=targets.get("compliance", 20), created_at=created_at
        )
    )
    samples.extend(
        generate_drafting_samples(
            corpus, selected, rng=rng, target=targets.get("drafting", 20), created_at=created_at
        )
    )
    samples.extend(
        generate_unanswerable_samples(
            corpus, selected, rng=rng, target=targets.get("unanswerable", 10), created_at=created_at
        )
    )
    return samples


def overgenerate_for_retry(
    corpus: CorpusIndexes,
    selected: list[SelectedProject],
    *,
    seed: int,
    targets: dict[str, int],
    multiplier: float = 2.5,
    created_at: datetime | None = None,
) -> list[ReferenceSample]:
    inflated = {k: max(int(v * multiplier), v + 5) for k, v in targets.items()}
    return generate_all_candidates(corpus, selected, seed=seed, targets=inflated, created_at=created_at)
