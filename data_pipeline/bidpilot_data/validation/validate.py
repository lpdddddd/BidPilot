from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from pydantic import ValidationError
from rapidfuzz import fuzz

from bidpilot_data.collectors.source_registry import load_source_registry
from bidpilot_data.labeling.evidence import quote_supported_by_chunk
from bidpilot_data.logging import get_logger, log_stats
from bidpilot_data.schemas import RAGQuestion, RequirementAnnotation, SFTRecord
from bidpilot_data.settings import get_settings
from bidpilot_data.utils import read_json, read_jsonl, write_json

log = get_logger(__name__)

FICTION_MARKERS = (
    "虚构",
    "虚拟企业",
    "synthetic",
    "FICTION",
    "V91310000FICTION",
    "青梧科创虚拟",
    "霜序环保虚构",
)


def _walk_jsonl_for_synthetic(path: Path, errors: list[str], *, limit: int = 50) -> None:
    if not path.exists():
        return
    for i, row in enumerate(read_jsonl(path)):
        if row.get("synthetic") is True:
            errors.append(f"synthetic=true forbidden in {path.name}#{i}")
            if len([e for e in errors if "synthetic=true" in e]) >= limit:
                return
        # Only flag enterprise identity fields — real tenders may say “不得虚构业绩”.
        name = str(row.get("name") or "")
        credit = str(row.get("credit_code") or "")
        company_title = str(row.get("company_name") or "")
        for marker in ("虚构", "虚拟企业", "FICTION", "V91310000FICTION"):
            if marker in name or marker in credit or marker in company_title:
                errors.append(f"fictional marker '{marker}' in {path.name}#{i}")
                break


def validate_all(*, allow_demo_fixture: bool = False) -> dict[str, Any]:
    settings = get_settings()
    registry = load_source_registry()
    errors: list[str] = []
    warnings: list[str] = []

    datasets = settings.datasets_root
    if not allow_demo_fixture and "fixtures/demo" in str(datasets):
        warnings.append("validate running under demo fixture root")

    docs = read_jsonl(datasets / "manifests" / "documents.jsonl")
    sources = read_jsonl(datasets / "manifests" / "sources.jsonl")
    projects = read_jsonl(datasets / "manifests" / "projects.jsonl")
    chunks = read_jsonl(datasets / "interim" / "chunks" / "chunks.jsonl")
    silver = read_jsonl(datasets / "silver" / "requirements.jsonl")
    gold = read_jsonl(datasets / "gold" / "requirements.jsonl")
    matches = read_jsonl(datasets / "silver" / "requirement_matches.jsonl")
    evidence = read_jsonl(datasets / "silver" / "evidence.jsonl")
    ragqs = read_jsonl(datasets / "eval" / "rag" / "questions.jsonl")
    sft_train = read_jsonl(datasets / "sft" / "train" / "records.jsonl")
    sft_test = read_jsonl(datasets / "sft" / "test" / "records.jsonl")
    review_log = read_jsonl(datasets / "review" / "imported" / "review_log.jsonl")

    chunk_by_id = {c.get("chunk_id"): c for c in chunks}
    chunk_ids = set(chunk_by_id)
    doc_by_id = {d.get("document_id"): d for d in docs}
    doc_ids = set(doc_by_id)
    req_ids = {r.get("requirement_id") for r in silver + gold}
    evidence_ids = {e.get("evidence_id") for e in evidence}
    page_counts = {d.get("document_id"): d.get("page_count") or 10**9 for d in docs}

    # Hard fail: any synthetic in formal silver/gold/eval/sft
    for rel in [
        "silver/requirements.jsonl",
        "silver/requirement_matches.jsonl",
        "silver/company_profiles.jsonl",
        "silver/company_materials.jsonl",
        "silver/disclosed_suppliers.jsonl",
        "gold/requirements.jsonl",
        "eval/rag/questions.jsonl",
        "sft/train/records.jsonl",
        "sft/validation/records.jsonl",
        "sft/test/records.jsonl",
        "manifests/projects.jsonl",
        "manifests/documents.jsonl",
        "manifests/sources.jsonl",
    ]:
        _walk_jsonl_for_synthetic(datasets / rel, errors)

    # Official domain / source_url traceability
    if not allow_demo_fixture:
        for i, row in enumerate(sources + docs + projects):
            url = row.get("source_url") or row.get("official_project_url")
            if not url:
                errors.append(f"missing source_url/official_project_url row#{i}")
                continue
            if url.startswith("file://"):
                errors.append(f"file:// not allowed in formal dataset: {url}")
                continue
            host = urlparse(str(url)).netloc.lower().split(":")[0]
            if not registry.is_official_domain(str(url)):
                errors.append(f"non_official_domain: {host} url={url}")

        for p in projects:
            if not p.get("project_code") or p.get("project_code") in {"", "UNKNOWN"}:
                warnings.append(f"project_code missing/unknown project_id={p.get('project_id')}")
            if not p.get("project_name"):
                errors.append(f"project_name missing {p.get('project_id')}")

    # SHA256 presence for downloaded docs
    for d in docs:
        if not d.get("sha256"):
            errors.append(f"document missing sha256 {d.get('document_id')}")
        else:
            storage = d.get("storage_path")
            if storage:
                path = datasets / storage
                if path.exists():
                    from bidpilot_data.utils import sha256_file

                    digest = sha256_file(path)
                    if digest != d["sha256"]:
                        errors.append(f"sha256 mismatch {d.get('document_id')}")

    # schema checks for requirements
    for i, row in enumerate(silver + gold):
        try:
            RequirementAnnotation.model_validate(row)
        except ValidationError as exc:
            errors.append(f"requirement schema#{i}: {exc}")
        if not row.get("source_url") and not allow_demo_fixture:
            errors.append(f"missing source_url for annotation {row.get('annotation_id')}")
        if row.get("source_page") and row.get("document_id"):
            max_page = page_counts.get(row["document_id"], 10**9)
            if row["source_page"] > max_page:
                errors.append(f"source_page out of range annotation={row.get('annotation_id')}")
        if row.get("chunk_id") and row["chunk_id"] not in chunk_ids:
            errors.append(f"missing chunk ref {row.get('chunk_id')}")
        if row.get("quality_level") == "gold":
            if not row.get("reviewer"):
                errors.append(f"gold without reviewer {row.get('annotation_id')}")
            if not row.get("source_page"):
                errors.append(f"gold without source_page {row.get('annotation_id')}")
            reviewed_ids = {x.get("annotation_id") for x in review_log if x.get("decision") in {"accept", "corrected"}}
            if row.get("annotation_id") not in reviewed_ids and review_log:
                errors.append(f"gold not from review import {row.get('annotation_id')}")

    for name, rows, key in [
        ("document", docs, "document_id"),
        ("chunk", chunks, "chunk_id"),
        ("annotation", silver + gold, "annotation_id"),
    ]:
        c = Counter(r.get(key) for r in rows)
        dups = [k for k, v in c.items() if k and v > 1]
        if dups:
            errors.append(f"duplicate {name} ids: {dups[:5]}")

    # Evidence quote presence in chunks
    for e in evidence:
        cid = e.get("chunk_id")
        if cid and cid in chunk_by_id:
            if not quote_supported_by_chunk(str(e.get("quote") or ""), chunk_by_id[cid].get("text") or ""):
                errors.append(f"evidence quote not in chunk {e.get('evidence_id')}")

    for m in matches:
        if m.get("requirement_id") not in req_ids and req_ids:
            errors.append(f"match missing requirement {m.get('match_id')}")
        if m.get("status") == "satisfied":
            eids = m.get("evidence_ids") or []
            if not eids and not m.get("evidence_document_id") and not m.get("evidence_chunk_id"):
                errors.append(f"satisfied match without evidence {m.get('match_id')}")
            for eid in eids:
                if eid not in evidence_ids and evidence_ids:
                    errors.append(f"match evidence_id missing {eid}")
        if m.get("synthetic") is True:
            errors.append(f"synthetic match forbidden {m.get('match_id')}")

    for q in ragqs:
        try:
            RAGQuestion.model_validate(q)
        except ValidationError as exc:
            errors.append(f"rag schema: {exc}")
        if q.get("answerable"):
            if not q.get("gold_chunk_ids"):
                errors.append(f"rag answerable without evidence {q.get('question_id')}")
            elif any(cid not in chunk_ids for cid in q.get("gold_chunk_ids", [])):
                errors.append(f"rag invalid chunk evidence {q.get('question_id')}")
            # Answer must be supported by quotes
            quotes = q.get("source_quotes") or []
            answer = str(q.get("answer") or "")
            if quotes and answer:
                joined = " ".join(quotes)
                if fuzz.partial_ratio(answer[:180], joined[:500]) < 60 and answer[:40] not in joined:
                    errors.append(f"rag answer not supported by quotes {q.get('question_id')}")
            for cid in q.get("gold_chunk_ids") or []:
                chunk = chunk_by_id.get(cid)
                if not chunk:
                    continue
                for quote in quotes:
                    if quote and not quote_supported_by_chunk(quote, chunk.get("text") or ""):
                        # quote may span sentence; warn instead of hard fail if partial
                        if fuzz.partial_ratio(quote[:120], (chunk.get("text") or "")[:2000]) < 70:
                            errors.append(f"rag quote missing from chunk {q.get('question_id')}")

    for rows, split in [(sft_train, "train"), (sft_test, "test")]:
        for i, row in enumerate(rows):
            try:
                rec = SFTRecord.model_validate(row)
            except ValidationError as exc:
                errors.append(f"sft {split}#{i}: {exc}")
                continue
            assistant = next(m.content for m in rec.messages if m.role == "assistant")
            try:
                json.loads(assistant)
            except json.JSONDecodeError:
                errors.append(f"sft assistant json invalid {rec.record_id}")

    train_projects = {r.get("project_id") for r in sft_train}
    test_projects = {r.get("project_id") for r in sft_test}
    leak = train_projects & test_projects
    if leak:
        errors.append(f"train/test project leakage: {sorted(leak)[:10]}")

    # Approximate document/chunk leakage across train/test via near-duplicate text
    train_chunk_texts = [
        chunk_by_id[r.get("source_chunk_ids", [None])[0]]["text"]
        for r in sft_train
        if r.get("source_chunk_ids") and r.get("source_chunk_ids")[0] in chunk_by_id
    ][:200]
    test_chunk_texts = [
        chunk_by_id[r.get("source_chunk_ids", [None])[0]]["text"]
        for r in sft_test
        if r.get("source_chunk_ids") and r.get("source_chunk_ids")[0] in chunk_by_id
    ][:200]
    near_doc = 0
    for tu in test_chunk_texts:
        if any(fuzz.token_set_ratio(tu[:500], tr[:500]) >= 98 for tr in train_chunk_texts):
            near_doc += 1
    if near_doc:
        if len({r.get("project_id") for r in sft_train + sft_test}) < 5:
            warnings.append(f"approx chunk near-duplicates train/test={near_doc} (small project set)")
        else:
            errors.append(f"approx chunk near-duplicates train/test={near_doc}")

    train_users = [next(m["content"] for m in r["messages"] if m["role"] == "user") for r in sft_train[:200]]
    test_users = [next(m["content"] for m in r["messages"] if m["role"] == "user") for r in sft_test[:200]]
    near = 0
    for tu in test_users:
        if any(fuzz.token_set_ratio(tu, tr) >= 98 for tr in train_users):
            near += 1
    if near:
        warnings.append(f"approx question near-duplicates train/test={near}")

    for c in chunks:
        if not (c.get("text") or "").strip():
            errors.append(f"empty chunk {c.get('chunk_id')}")

    cat_counts = Counter(r.get("category") for r in silver + gold)
    if silver + gold and cat_counts.most_common(1)[0][1] / max(len(silver + gold), 1) > 0.95:
        warnings.append("category distribution highly skewed (>95% one class)")

    info_path = settings.repo_root / "training" / "llamafactory" / "data" / "dataset_info.json"
    if info_path.exists():
        info = json.loads(info_path.read_text(encoding="utf-8"))
        for name in ("bidpilot_sft_train", "bidpilot_sft_validation", "bidpilot_sft_test", "bidpilot_sft_train_qwen3"):
            if name not in info:
                errors.append(f"dataset_info missing {name}")
    else:
        errors.append("dataset_info.json missing")

    # Demo leakage into formal root
    if not allow_demo_fixture:
        for d in docs + sources:
            if d.get("fixture") or "demo_fixture" in str(d.get("source_site") or "") or "DEMO-" in str(d.get("project_code") or ""):
                errors.append(f"demo fixture leaked into formal dataset: {d.get('project_code') or d.get('document_id')}")
            url = str(d.get("source_url") or "")
            if url.startswith("file://") and "/demo_data/" in url:
                errors.append(f"demo_data file URL in formal dataset: {url}")

    report = {
        "ok": not errors,
        "error_count": len(errors),
        "warning_count": len(warnings),
        "errors": errors[:300],
        "warnings": warnings[:200],
        "counts": {
            "projects": len(projects),
            "documents": len(docs),
            "chunks": len(chunks),
            "silver_requirements": len(silver),
            "gold_requirements": len(gold),
            "matches": len(matches),
            "evidence": len(evidence),
            "rag_questions": len(ragqs),
            "sft_train": len(sft_train),
            "sft_test": len(sft_test),
            "unknown_matches": sum(1 for m in matches if m.get("status") == "unknown"),
        },
    }
    write_json(datasets / "reports" / "validation_report.json", report)
    log_stats(log, "validate_all", {"ok": report["ok"], "errors": report["error_count"], "warnings": report["warning_count"]})
    return report
