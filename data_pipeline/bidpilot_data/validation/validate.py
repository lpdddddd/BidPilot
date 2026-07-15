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
from bidpilot_data.rag_eval.build import LEAK_MARKERS, question_leaks_quote
from bidpilot_data.schemas import RAGQuestion, RequirementAnnotation, RequirementMatchAnnotation, SFTRecord
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


def validate_rag(*, write_report: bool = True) -> dict[str, Any]:
    """Validate RAG quality: no quote leak, evidence support, unanswerable band, citation IDs."""
    settings = get_settings()
    datasets = settings.datasets_root
    errors: list[str] = []
    warnings: list[str] = []
    ragqs = read_jsonl(datasets / "eval" / "rag" / "questions.jsonl")
    chunks = {c.get("chunk_id"): c for c in read_jsonl(datasets / "interim" / "chunks" / "chunks.jsonl")}
    docs = {d.get("document_id"): d for d in read_jsonl(datasets / "manifests" / "documents.jsonl")}
    projects = {p.get("project_id"): p for p in read_jsonl(datasets / "manifests" / "projects.jsonl")}

    seen_q: set[str] = set()
    unans = 0
    for i, q in enumerate(ragqs):
        try:
            RAGQuestion.model_validate(q)
        except ValidationError as exc:
            errors.append(f"rag schema#{i}: {exc}")
        question = str(q.get("question") or "")
        if any(m in question for m in LEAK_MARKERS) or "原文：" in question:
            errors.append(f"rag question contains leak marker {q.get('question_id')}")
        quotes = q.get("source_quotes") or []
        for quote in quotes:
            if question_leaks_quote(question, quote):
                errors.append(f"rag question copies source_quote {q.get('question_id')}")
        key = re.sub(r"\s+", "", question)
        if key in seen_q:
            errors.append(f"duplicate rag question {q.get('question_id')}")
        seen_q.add(key)

        pid = q.get("project_id")
        proj = projects.get(pid) or {}
        if proj.get("project_code") == "PORTAL_SNAPSHOT":
            errors.append(f"PORTAL_SNAPSHOT in rag {q.get('question_id')}")
        if proj.get("bundle_level") == "incomplete":
            errors.append(f"incomplete project in rag {q.get('question_id')}")

        if q.get("answerable"):
            if not (q.get("answer") or "").strip():
                errors.append(f"answerable empty answer {q.get('question_id')}")
            if not q.get("gold_chunk_ids"):
                errors.append(f"missing gold_chunk_ids {q.get('question_id')}")
            if not q.get("source_document_ids") and not q.get("gold_document_ids"):
                errors.append(f"missing document ids {q.get('question_id')}")
            if not q.get("source_urls"):
                errors.append(f"missing source_urls {q.get('question_id')}")
            if not q.get("source_pages"):
                errors.append(f"missing source_pages {q.get('question_id')}")
            for cid in q.get("gold_chunk_ids") or []:
                if cid not in chunks:
                    errors.append(f"citation chunk missing {cid}")
            for quote in quotes:
                if not quote:
                    continue
                supported = False
                for cid in q.get("gold_chunk_ids") or []:
                    ch = chunks.get(cid) or {}
                    text = ch.get("text") or ""
                    if quote_supported_by_chunk(quote, text) or fuzz.partial_ratio(quote[:120], text[:2000]) >= 70:
                        supported = True
                        break
                if not supported:
                    errors.append(f"source_quote not in chunk {q.get('question_id')}")
            answer = str(q.get("answer") or "")
            if quotes and answer:
                joined = " ".join(quotes)
                if fuzz.partial_ratio(answer[:180], joined[:500]) < 60 and answer[:40] not in joined:
                    errors.append(f"answer not supported by quote {q.get('question_id')}")
        else:
            unans += 1
            if q.get("answer") is not None:
                errors.append(f"unanswerable must have answer=null {q.get('question_id')}")
            if quotes or q.get("gold_chunk_ids"):
                errors.append(f"unanswerable must not bind fake quote {q.get('question_id')}")
            # Corpus absence check
            corpus = "".join(
                (c.get("text") or "") for c in chunks.values() if c.get("project_id") == pid
            )
            from bidpilot_data.rag_eval.build import _unanswerable_keywords, _corpus_has_any

            kws = _unanswerable_keywords(question)
            if kws and _corpus_has_any(corpus, kws):
                errors.append(f"unanswerable keywords present in corpus {q.get('question_id')}")

    ratio = (unans / len(ragqs)) if ragqs else 0.0
    if ragqs and not (0.10 <= ratio <= 0.15):
        errors.append(f"unanswerable_ratio out of band: {ratio:.4f} (need 0.10-0.15)")
    from collections import Counter as _Counter

    if ragqs:
        share = max(_Counter(q.get("project_id") for q in ragqs).values(), default=0) / len(ragqs)
        if share > 0.10 + 1e-9:
            errors.append(f"max_project_share={share:.4f} exceeds 0.10")
        # multi_section integrity
        for q in ragqs:
            if q.get("question_type") != "multi_section":
                continue
            cids = q.get("gold_chunk_ids") or []
            quotes = q.get("source_quotes") or []
            if len(set(cids)) < 2:
                errors.append(f"multi_section needs 2 chunks {q.get('question_id')}")
            if len(quotes) < 2:
                errors.append(f"multi_section needs 2 quotes {q.get('question_id')}")
            ans = str(q.get("answer") or "")
            if quotes and ans:
                ok_both = all(
                    fuzz.partial_ratio(qq[:80], ans[:500]) >= 40 or (qq[:20] in ans)
                    for qq in quotes[:2]
                )
                if not ok_both:
                    errors.append(f"multi_section answer missing dual evidence {q.get('question_id')}")

    report = {
        "ok": not errors,
        "error_count": len(errors),
        "warning_count": len(warnings),
        "errors": errors[:300],
        "warnings": warnings[:100],
        "questions": len(ragqs),
        "unanswerable": unans,
        "unanswerable_ratio": ratio,
    }
    if write_report:
        write_json(datasets / "reports" / "rag_validation_report.json", report)
    log_stats(log, "validate_rag", {"ok": report["ok"], "errors": report["error_count"], "ratio": ratio})
    return report


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

    # Evidence quote presence in chunks (fuzzy OK for OCR/whitespace noise)
    for e in evidence:
        cid = e.get("chunk_id")
        if cid and cid in chunk_by_id:
            quote = str(e.get("quote") or "")
            text = chunk_by_id[cid].get("text") or ""
            if not quote_supported_by_chunk(quote, text):
                if fuzz.partial_ratio(quote[:180], text[:2500]) < 75:
                    errors.append(f"evidence quote not in chunk {e.get('evidence_id')}")
                else:
                    warnings.append(f"evidence quote weak match {e.get('evidence_id')}")

    unknown_matches = 0
    for m in matches:
        if m.get("status") == "unknown":
            unknown_matches += 1
            errors.append(f"unknown match forbidden {m.get('match_id')}")
        if not m.get("supplier_id"):
            errors.append(f"match missing supplier_id {m.get('match_id')}")
        try:
            RequirementMatchAnnotation.model_validate(m)
        except ValidationError as exc:
            errors.append(f"match schema {m.get('match_id')}: {exc}")
        if m.get("requirement_id") not in req_ids and req_ids:
            errors.append(f"match missing requirement {m.get('match_id')}")
        eids = m.get("evidence_ids") or []
        if not eids and not m.get("evidence_document_id") and not m.get("evidence_chunk_id"):
            errors.append(f"match without evidence {m.get('match_id')}")
        for eid in eids:
            if eid not in evidence_ids and evidence_ids:
                errors.append(f"match evidence_id missing {eid}")
        # tender docs must never ground matches
        evid_doc = doc_by_id.get(m.get("evidence_document_id") or "")
        if evid_doc and evid_doc.get("document_type") in {"tender_document", "tender_notice", "tender", "announcement"}:
            errors.append(f"match grounded on tender doc forbidden {m.get('match_id')}")
        if m.get("synthetic") is True:
            errors.append(f"synthetic match forbidden {m.get('match_id')}")

    # Detect cartesian-like explosion: matches >> suppliers * 5 with all same status historically unknown
    suppliers_n = len(read_jsonl(datasets / "silver" / "disclosed_suppliers.jsonl"))
    if matches and suppliers_n and len(matches) > max(500, suppliers_n * 20):
        warnings.append(f"match count unusually high vs suppliers ({len(matches)} vs {suppliers_n})")

    rag_report = validate_rag(write_report=True)
    if not rag_report.get("ok"):
        errors.extend([f"rag:{e}" for e in rag_report.get("errors", [])[:100]])

    sft_val = read_jsonl(datasets / "sft" / "validation" / "records.jsonl")
    for rows, split in [(sft_train, "train"), (sft_val, "validation"), (sft_test, "test")]:
        for i, row in enumerate(rows):
            try:
                rec = SFTRecord.model_validate(row)
            except ValidationError as exc:
                errors.append(f"sft {split}#{i}: {exc}")
                continue
            if (projects and (next((p for p in projects if p.get("project_id") == rec.project_id), {}) or {}).get("project_code")
                    == "PORTAL_SNAPSHOT"):
                errors.append(f"PORTAL_SNAPSHOT in sft {rec.record_id}")
            assistants = [m.content for m in rec.messages if m.role == "assistant"]
            if not assistants:
                errors.append(f"sft missing assistant {rec.record_id}")
                continue
            try:
                json.loads(assistants[-1])
            except json.JSONDecodeError:
                errors.append(f"sft assistant json invalid {rec.record_id}")
            # Tool call pairing
            if rec.task_type.value == "tool_call":
                roles = [m.role for m in rec.messages]
                for j, role in enumerate(roles):
                    if role == "tool" and (j == 0 or roles[j - 1] != "assistant"):
                        errors.append(f"tool result not paired {rec.record_id}")
                final = json.loads(assistants[-1]) if assistants else {}
                if not (final.get("citations") or final.get("clarify")):
                    errors.append(f"tool_call final missing citations {rec.record_id}")

    train_projects = {r.get("project_id") for r in sft_train}
    val_projects = {r.get("project_id") for r in sft_val}
    test_projects = {r.get("project_id") for r in sft_test}
    if train_projects & test_projects:
        errors.append(f"train/test project leakage: {sorted(train_projects & test_projects)[:10]}")
    if train_projects & val_projects:
        errors.append(f"train/validation project leakage: {sorted(train_projects & val_projects)[:10]}")
    if val_projects & test_projects:
        errors.append(f"validation/test project leakage: {sorted(val_projects & test_projects)[:10]}")
    if len(val_projects) < 5:
        errors.append(f"validation projects < 5: {len(val_projects)}")

    # PORTAL_SNAPSHOT must not enter training / eval artifacts
    portal_pids = {p.get("project_id") for p in projects if p.get("project_code") == "PORTAL_SNAPSHOT"}
    for label, rows in [
        ("requirements", silver + gold),
        ("rag", ragqs),
        ("sft_train", sft_train),
        ("sft_validation", sft_val),
        ("sft_test", sft_test),
        ("matches", matches),
    ]:
        for row in rows:
            if row.get("project_id") in portal_pids:
                errors.append(f"PORTAL_SNAPSHOT leaked into {label}")
                break
    agents = read_jsonl(datasets / "eval" / "agent" / "tasks.jsonl")
    for row in agents:
        if row.get("project_id") in portal_pids:
            errors.append("PORTAL_SNAPSHOT leaked into agent tasks")
            break

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
    from bidpilot_data.sft.cross_split import analyze_cross_split_similarity

    xsim = analyze_cross_split_similarity()
    if not xsim.get("ok"):
        errors.append(
            f"severe train/test near-duplicates={xsim.get('severe_train_test_near_duplicates')} "
            "(see cross_split_similarity_report.json)"
        )
    if xsim.get("template_overlap"):
        warnings.append(f"template_overlap train/test={xsim.get('template_overlap')}")
    if near_doc and xsim.get("severe_train_test_near_duplicates", 0) == 0:
        warnings.append(f"approx chunk near-duplicates scanned={near_doc} (classified in cross_split report)")

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
            "unknown_matches": unknown_matches,
            "sft_validation": len(sft_val),
            "validation_projects": len(val_projects),
        },
    }
    write_json(datasets / "reports" / "validation_report.json", report)
    log_stats(log, "validate_all", {"ok": report["ok"], "errors": report["error_count"], "warnings": report["warning_count"]})
    return report
