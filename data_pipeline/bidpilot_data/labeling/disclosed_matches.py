from __future__ import annotations

import re
from collections import Counter
from typing import Any

from bidpilot_data.labeling.evidence import make_evidence, quote_supported_by_chunk
from bidpilot_data.labeling.supplier_names import extract_award_suppliers
from bidpilot_data.logging import get_logger, log_stats
from bidpilot_data.schemas import (
    MatchStatus,
    QualityLevel,
    RequirementMatchAnnotation,
    ReviewStatus,
    SupplierReviewOutcome,
)
from bidpilot_data.settings import get_settings
from bidpilot_data.utils import ensure_dir, read_jsonl, stable_uuid, write_json, write_jsonl

log = get_logger(__name__)

# Result-class documents only — never tender_document / tender_notice for Match.
RESULT_DOC_TYPES = {
    "award_notice",
    "result",
    "evaluation_result",
    "qualification_review_result",
    "compliance_review_result",
    "contract_notice",
}
AWARD_NAME_DOC_TYPES = {"award_notice", "result", "contract_notice", "contract"}

# Generic rule language that must NOT create matches (conditional future consequences).
GENERIC_RULE_MARKERS = (
    "按无效投标处理",
    "按废标处理",
    "否决其投标",
    "不得参与",
    "将被否决",
    "可能导致",
    "有权否决",
    "视为无效",
    "作废标处理",
)

# Fact patterns: supplier name must appear in the same evidence window.
# Use __NAME__ placeholder so regex quantifiers `{0,40}` are not eaten by str.format.
FACT_PATTERNS: list[tuple[str, MatchStatus, str]] = [
    (r"__NAME__[^\n。；]{0,40}资格审查\s*(合格|通过)", MatchStatus.satisfied, "qualification"),
    (r"__NAME__[^\n。；]{0,40}符合性审查\s*(合格|通过)", MatchStatus.satisfied, "compliance"),
    (r"__NAME__[^\n。；]{0,40}(通过|符合)\s*资格审查", MatchStatus.satisfied, "qualification"),
    (r"资格审查[^\n。；]{0,20}__NAME__[^\n。；]{0,20}(合格|通过)", MatchStatus.satisfied, "qualification"),
    (r"__NAME__[^\n。；]{0,40}资格审查\s*(不合格|不通过)", MatchStatus.missing, "qualification"),
    (r"__NAME__[^\n。；]{0,40}符合性审查\s*(不合格|不通过)", MatchStatus.missing, "compliance"),
    (r"__NAME__[^\n。；]{0,40}(未通过|不符合)\s*资格审查", MatchStatus.missing, "qualification"),
    (r"__NAME__[^\n。；]{0,40}未提供[^\n。；]{0,20}(营业执照|资质|证书|材料)", MatchStatus.missing, "qualification"),
    (r"__NAME__[^\n。；]{0,40}缺少[^\n。；]{0,20}(营业执照|资质|证书|材料|证明)", MatchStatus.missing, "qualification"),
    (r"__NAME__[^\n。；]{0,30}(部分通过|有条件通过)", MatchStatus.partially_satisfied, "qualification"),
]


def _escape_name(name: str) -> str:
    return re.escape(name)


def _is_generic_rule(quote: str) -> bool:
    q = quote or ""
    # Conditional / imperative rule voice
    if any(m in q for m in GENERIC_RULE_MARKERS) and not re.search(r"[\u4e00-\u9fff]{2,}(公司|大学|医院|中心)", q):
        return True
    if re.search(r"(应当|必须|不得|如果|若|未[^\n]{0,10}的[，,].*(无效|废标|否决))", q):
        # Allow only when a concrete org name is the grammatical subject of 审查结论
        if not re.search(r"(公司|大学|医院).{0,20}(资格审查|符合性审查).{0,10}(通过|合格|不通过|不合格)", q):
            return True
    return False


def _bind_requirement(reqs: list[dict[str, Any]], pid: str, quote: str, status: MatchStatus) -> dict[str, Any] | None:
    """Bind to a specific requirement only with substantive topical overlap — not generic keywords alone."""
    best = None
    best_score = 0
    # Extract specific phrases from the quote (documents, certificates, materials named)
    specifics = re.findall(
        r"(营业执照|资质证书|社保缴费|财务报表|业绩证明|安全生产许可证|ISO\d+|等保|软件著作权|人员社保|检测报告)",
        quote,
    )
    for d in specifics:
        for r in reqs:
            if r.get("project_id") != pid:
                continue
            text = (r.get("original_text") or "") + (r.get("normalized_requirement") or "") + (r.get("title") or "")
            if d in text:
                score = 3 + (1 if r.get("category") in {"qualification", "certification", "performance", "personnel"} else 0)
                if score > best_score:
                    best_score = score
                    best = r
    # For missing with named material, require specific hit
    if status == MatchStatus.missing and specifics:
        return best if best_score >= 3 else None
    # For satisfied overall without material specificity — do not bind to random requirement
    if status in {MatchStatus.satisfied, MatchStatus.partially_satisfied, MatchStatus.uncertain} and not specifics:
        return None
    return best if best_score >= 3 else None


def _doc_text(settings: Any, doc: dict[str, Any], chunks: dict[str, dict[str, Any]]) -> str:
    texts = [c["text"] for c in chunks.values() if c.get("document_id") == doc["document_id"]]
    blob = "\n".join(texts)
    if not blob and doc.get("storage_path"):
        path = settings.datasets_root / doc["storage_path"]
        if path.exists() and path.suffix.lower() in {".html", ".htm", ".txt"}:
            blob = path.read_text(encoding="utf-8", errors="ignore")
    return blob


def _find_chunk_for_quote(chunks: dict[str, dict[str, Any]], pid: str, doc_id: str, quote: str) -> dict[str, Any] | None:
    for c in chunks.values():
        if c.get("project_id") != pid or c.get("document_id") != doc_id:
            continue
        if quote_supported_by_chunk(quote, c.get("text") or ""):
            return c
    for c in chunks.values():
        if c.get("project_id") == pid and quote_supported_by_chunk(quote, c.get("text") or ""):
            return c
    return None


def build_disclosed_matches(*, dry_run: bool = False) -> dict[str, Any]:
    """Disclosed suppliers from awards; Match only from result docs with named supplier facts."""
    settings = get_settings()
    reqs = read_jsonl(settings.datasets_root / "silver" / "requirements.jsonl")
    chunks = {c["chunk_id"]: c for c in read_jsonl(settings.datasets_root / "interim" / "chunks" / "chunks.jsonl")}
    docs = {d["document_id"]: d for d in read_jsonl(settings.datasets_root / "manifests" / "documents.jsonl")}
    projects = [
        p
        for p in read_jsonl(settings.datasets_root / "manifests" / "projects.jsonl")
        if p.get("project_code") != "PORTAL_SNAPSHOT"
    ]

    award_docs: dict[str, list[dict[str, Any]]] = {}
    result_docs: dict[str, list[dict[str, Any]]] = {}
    for d in docs.values():
        if d.get("project_code") == "PORTAL_SNAPSHOT":
            continue
        dtype = d.get("document_type")
        # Explicitly exclude tender sources from match generation
        if dtype in {"tender_document", "tender_notice", "tender", "announcement", "clarification"}:
            continue
        if dtype in AWARD_NAME_DOC_TYPES:
            award_docs.setdefault(d["project_id"], []).append(d)
        if dtype in RESULT_DOC_TYPES:
            result_docs.setdefault(d["project_id"], []).append(d)

    cleaning_acc: dict[str, Any] = {
        "raw_candidates": [],
        "accepted_suppliers": [],
        "rejected_candidates": [],
        "rejection_reasons": Counter(),
        "duplicate_suppliers_removed": 0,
    }

    suppliers_out: list[dict[str, Any]] = []
    disclosed: dict[str, list[dict[str, Any]]] = {}
    for project in projects:
        pid = project["project_id"]
        seen_names: set[str] = set()
        for doc in award_docs.get(pid, []) + result_docs.get(pid, []):
            blob = _doc_text(settings, doc, chunks)
            html = None
            if doc.get("storage_path"):
                path = settings.datasets_root / doc["storage_path"]
                if path.exists() and path.suffix.lower() in {".html", ".htm"}:
                    html = path.read_text(encoding="utf-8", errors="ignore")
            extracted = extract_award_suppliers(blob, html=html)
            cleaning_acc["raw_candidates"].extend(extracted["raw_candidates"])
            cleaning_acc["rejected_candidates"].extend(extracted["rejected_candidates"])
            cleaning_acc["duplicate_suppliers_removed"] += extracted["duplicate_suppliers_removed"]
            for reason, cnt in (extracted.get("rejection_reasons") or {}).items():
                cleaning_acc["rejection_reasons"][reason] += cnt
            for name in extracted["accepted_suppliers"]:
                if name in seen_names:
                    cleaning_acc["duplicate_suppliers_removed"] += 1
                    continue
                seen_names.add(name)
                sid = str(stable_uuid(f"supplier:{pid}:{name}"))
                suppliers_out.append(
                    {
                        "supplier_id": sid,
                        "name": name,
                        "credit_code": None,
                        "industry": project.get("industry"),
                        "project_id": pid,
                        "source_document_ids": [doc["document_id"]],
                        "source_urls": [doc.get("source_url")] if doc.get("source_url") else [],
                        "synthetic": False,
                        "metadata": {"disclosed_in": doc.get("document_type")},
                    }
                )
                disclosed.setdefault(pid, []).append({"supplier_id": sid, "name": name, "document": doc})
                cleaning_acc["accepted_suppliers"].append({"project_id": pid, "name": name})

    matches_out: list[dict[str, Any]] = []
    outcomes_out: list[dict[str, Any]] = []
    evidence_out: list[dict[str, Any]] = []
    skipped = 0
    skipped_generic = 0
    skipped_no_name_window = 0
    seen_match_keys: set[str] = set()
    seen_outcome_keys: set[str] = set()

    # Isolate previous bad matches: archive then rewrite
    old_path = settings.datasets_root / "silver" / "requirement_matches.jsonl"
    archive_dir = ensure_dir(settings.datasets_root / "rejected")
    if old_path.exists() and not dry_run:
        old_rows = read_jsonl(old_path)
        if old_rows:
            write_jsonl(archive_dir / "requirement_matches_pre_strict.jsonl", old_rows)

    for project in projects:
        pid = project["project_id"]
        suppliers = disclosed.get(pid) or []
        if not suppliers:
            continue
        for doc in result_docs.get(pid, []):
            # Never use tender docs (already filtered)
            if doc.get("document_type") in {"tender_document", "tender_notice", "tender"}:
                continue
            blob = _doc_text(settings, doc, chunks)
            if not blob or not doc.get("source_url"):
                continue
            for s in suppliers:
                sid = s["supplier_id"]
                sname = s["name"]
                if not sid or not sname:
                    continue
                for pat_tmpl, status, review_type in FACT_PATTERNS:
                    pat = pat_tmpl.replace("__NAME__", _escape_name(sname))
                    for m in re.finditer(pat, blob):
                        start = max(0, m.start() - 30)
                        end = min(len(blob), m.end() + 40)
                        window = blob[start:end]
                        if sname not in window:
                            skipped_no_name_window += 1
                            skipped += 1
                            continue
                        quote = re.sub(r"\s+", " ", window).strip()[:220]
                        if _is_generic_rule(quote):
                            skipped_generic += 1
                            skipped += 1
                            continue
                        chunk_row = _find_chunk_for_quote(chunks, pid, doc["document_id"], quote)
                        if chunk_row is None:
                            skipped += 1
                            continue
                        req = _bind_requirement(reqs, pid, quote, status)
                        if req is None:
                            # Supplier-level outcome only
                            okey = f"{pid}|{sid}|{review_type}|{status.value}|{quote[:40]}"
                            if okey in seen_outcome_keys:
                                continue
                            seen_outcome_keys.add(okey)
                            ev = make_evidence(
                                project_id=pid,
                                document_id=doc["document_id"],
                                source_url=doc["source_url"],
                                quote=quote,
                                page_number=chunk_row.get("page_start") or 1,
                                chunk_id=chunk_row["chunk_id"],
                            )
                            evidence_out.append(ev.model_dump(mode="json"))
                            outcome = SupplierReviewOutcome(
                                outcome_id=str(stable_uuid(f"outcome:{okey}")),
                                project_id=pid,
                                supplier_id=sid,
                                review_type=review_type,
                                status=status,
                                reason=f"公开材料对供应商整体{review_type}审查表述为{status.value}，无法绑定具体条款",
                                source_document_id=doc["document_id"],
                                source_chunk_id=chunk_row["chunk_id"],
                                source_url=doc["source_url"],
                                source_quote=quote,
                                page_number=chunk_row.get("page_start") or 1,
                                quality_level=QualityLevel.silver,
                                review_status=ReviewStatus.pending,
                            )
                            outcomes_out.append(outcome.model_dump(mode="json"))
                            continue
                        key = f"{req['requirement_id']}|{sid}|{status.value}|{quote[:40]}"
                        if key in seen_match_keys:
                            continue
                        seen_match_keys.add(key)
                        ev = make_evidence(
                            project_id=pid,
                            document_id=doc["document_id"],
                            source_url=doc["source_url"],
                            quote=quote,
                            page_number=chunk_row.get("page_start") or 1,
                            chunk_id=chunk_row["chunk_id"],
                        )
                        evidence_out.append(ev.model_dump(mode="json"))
                        match = RequirementMatchAnnotation(
                            match_id=str(stable_uuid(f"match:{key}")),
                            requirement_id=req["requirement_id"],
                            company_profile_id=None,
                            supplier_id=sid,
                            status=status,
                            reason=f"依据公开结果文件中对「{sname}」的审查事实判定为{status.value}",
                            evidence_ids=[ev.evidence_id],
                            evidence_document_id=doc["document_id"],
                            evidence_chunk_id=chunk_row["chunk_id"],
                            source_url=doc["source_url"],
                            source_quote=quote,
                            confidence=0.75 if status != MatchStatus.uncertain else 0.5,
                            quality_level=QualityLevel.silver,
                            review_status=ReviewStatus.pending,
                        )
                        matches_out.append(match.model_dump(mode="json"))

    cleaning_report = {
        "raw_candidates": len(cleaning_acc["raw_candidates"]),
        "accepted_suppliers": len(suppliers_out),
        "rejected_candidates": len(cleaning_acc["rejected_candidates"]),
        "rejection_reasons": dict(cleaning_acc["rejection_reasons"]),
        "duplicate_suppliers_removed": cleaning_acc["duplicate_suppliers_removed"],
        "rejected_examples": cleaning_acc["rejected_candidates"][:30],
        "accepted_examples": [s["name"] for s in suppliers_out[:20]],
    }

    stats = {
        "disclosed_suppliers": len(suppliers_out),
        "evidence_supported_matches": len(matches_out),
        "supplier_review_outcomes": len(outcomes_out),
        "skipped_match_due_to_insufficient_evidence": skipped,
        "skipped_generic_rule": skipped_generic,
        "skipped_supplier_not_in_window": skipped_no_name_window,
        "matches": len(matches_out),
        "by_status": dict(Counter(m.get("status") for m in matches_out)),
        "supplier_cleaning": cleaning_report,
        "dry_run": dry_run,
        "note": "Matches only from result-class docs with supplier name in evidence window; tender docs never used.",
    }

    if not dry_run:
        write_jsonl(ensure_dir(settings.datasets_root / "silver") / "disclosed_suppliers.jsonl", suppliers_out)
        write_jsonl(settings.datasets_root / "silver" / "requirement_matches.jsonl", matches_out)
        write_jsonl(settings.datasets_root / "silver" / "supplier_review_outcomes.jsonl", outcomes_out)
        # Replace match-related evidence; keep non-review evidence
        existing_ev = []
        for e in read_jsonl(settings.datasets_root / "silver" / "evidence.jsonl"):
            quote = e.get("quote") or ""
            if any(k in quote for k in ("资格审查", "符合性审查", "未提供", "缺少")) and re.search(
                r"(公司|大学|医院|中心)", quote
            ):
                continue
            if any(k in quote for k in ("资格审查不合格", "按废标", "无效投标", "否决其投标")):
                continue
            if e.get("evidence_id") in {x.get("evidence_id") for x in evidence_out}:
                continue
            existing_ev.append(e)
        write_jsonl(settings.datasets_root / "silver" / "evidence.jsonl", existing_ev + evidence_out)
        write_json(ensure_dir(settings.datasets_root / "reports") / "supplier_cleaning_report.json", cleaning_report)
        for name in ("company_profiles.jsonl", "company_materials.jsonl"):
            path = settings.datasets_root / "silver" / name
            if path.exists():
                rows = [r for r in read_jsonl(path) if not r.get("synthetic")]
                write_jsonl(path, rows)

    log_stats(log, "disclosed_matches", {k: stats[k] for k in ("disclosed_suppliers", "matches", "supplier_review_outcomes")})
    return stats
