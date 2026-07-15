from __future__ import annotations

import re
from typing import Any

from bidpilot_data.labeling.evidence import make_evidence
from bidpilot_data.logging import get_logger, log_stats
from bidpilot_data.schemas import MatchStatus, QualityLevel, RequirementMatchAnnotation, ReviewStatus
from bidpilot_data.settings import get_settings
from bidpilot_data.utils import ensure_dir, read_jsonl, stable_uuid, write_jsonl

log = get_logger(__name__)

# Patterns that indicate actual qualification / compliance review outcomes (not mere award naming).
POSITIVE_EVIDENCE = [
    (r"资格审查\s*(合格|通过)", MatchStatus.satisfied),
    (r"符合性审查\s*(合格|通过)", MatchStatus.satisfied),
    (r"(通过|符合)\s*资格审查", MatchStatus.satisfied),
    (r"响应文件\s*(合格|符合要求)", MatchStatus.satisfied),
    (r"评审结论[：:]\s*合格", MatchStatus.satisfied),
]
NEGATIVE_EVIDENCE = [
    (r"资格审查\s*(不合格|不通过)", MatchStatus.missing),
    (r"符合性审查\s*(不合格|不通过)", MatchStatus.missing),
    (r"(未通过|不符合)\s*资格审查", MatchStatus.missing),
    (r"缺少[^\n]{0,30}(材料|证明|证书|执照)", MatchStatus.missing),
    (r"无效投标|按废标处理|否决其投标", MatchStatus.missing),
]
UNCERTAIN_EVIDENCE = [
    (r"待补正|需澄清|尚需核实", MatchStatus.uncertain),
    (r"部分通过|有条件通过", MatchStatus.partially_satisfied),
]


def _extract_award_suppliers(text: str) -> list[str]:
    names: list[str] = []
    patterns = [
        r"中标供应商[名称]*[：:\s]*([^\n；;]+)",
        r"成交供应商[名称]*[：:\s]*([^\n；;]+)",
        r"供应商名称[：:\s]*([^\n；;]+)",
        r"中标人[：:\s]*([^\n；;]+)",
    ]
    for pat in patterns:
        for m in re.finditer(pat, text):
            name = re.sub(r"\s+", "", m.group(1)).strip(" 。；;")
            if 2 <= len(name) <= 80 and name not in names:
                names.append(name)
    return names


def _doc_blob(settings: Any, doc: dict[str, Any], chunks: dict[str, dict[str, Any]]) -> tuple[str, dict[str, Any] | None]:
    texts = [c["text"] for c in chunks.values() if c.get("document_id") == doc["document_id"]]
    blob = "\n".join(texts)
    evid_chunk = None
    for c in chunks.values():
        if c.get("document_id") == doc["document_id"]:
            evid_chunk = c
            break
    if not blob and doc.get("storage_path"):
        path = settings.datasets_root / doc["storage_path"]
        if path.exists() and path.suffix.lower() in {".html", ".htm", ".txt"}:
            blob = path.read_text(encoding="utf-8", errors="ignore")
    return blob, evid_chunk


def _find_requirement_for_span(reqs: list[dict[str, Any]], pid: str, span: str) -> dict[str, Any] | None:
    """Bind a match to a requirement when the evidence span is related to that requirement text."""
    best = None
    best_score = 0
    for r in reqs:
        if r.get("project_id") != pid:
            continue
        text = (r.get("original_text") or "") + (r.get("normalized_requirement") or "") + (r.get("title") or "")
        # Keyword overlap
        keys = [k for k in ("资格", "业绩", "证书", "人员", "财务", "营业执照", "社保", "无效", "废标") if k in span]
        score = sum(1 for k in keys if k in text)
        if any(tok in text for tok in re.findall(r"[\u4e00-\u9fff]{2,8}", span)[:6]):
            score += 1
        if score > best_score:
            best_score = score
            best = r
    return best if best_score > 0 else None


def _scan_evidence_matches(
    *,
    pid: str,
    blob: str,
    doc: dict[str, Any],
    evid_chunk: dict[str, Any] | None,
    reqs: list[dict[str, Any]],
    supplier_id: str | None,
    supplier_name: str | None,
) -> list[tuple[MatchStatus, str, dict[str, Any]]]:
    """Return list of (status, quote, requirement) grounded in public review evidence."""
    found: list[tuple[MatchStatus, str, dict[str, Any]]] = []
    patterns = POSITIVE_EVIDENCE + NEGATIVE_EVIDENCE + UNCERTAIN_EVIDENCE
    for pat, status in patterns:
        for m in re.finditer(pat, blob):
            start = max(0, m.start() - 40)
            end = min(len(blob), m.end() + 40)
            quote = re.sub(r"\s+", " ", blob[start:end]).strip()
            # Prefer spans that mention the supplier if present
            if supplier_name and supplier_name not in blob[max(0, m.start() - 200) : m.end() + 200]:
                # still allow project-level review conclusion without name in the same window
                pass
            req = _find_requirement_for_span(reqs, pid, quote)
            if req is None:
                continue
            found.append((status, quote[:220], req))
    return found


def build_disclosed_matches(*, dry_run: bool = False) -> dict[str, Any]:
    """Disclosed suppliers from award notices; RequirementMatch only with review evidence."""
    settings = get_settings()
    reqs = read_jsonl(settings.datasets_root / "silver" / "requirements.jsonl")
    chunks = {c["chunk_id"]: c for c in read_jsonl(settings.datasets_root / "interim" / "chunks" / "chunks.jsonl")}
    docs = {d["document_id"]: d for d in read_jsonl(settings.datasets_root / "manifests" / "documents.jsonl")}
    projects = [
        p
        for p in read_jsonl(settings.datasets_root / "manifests" / "projects.jsonl")
        if p.get("project_code") != "PORTAL_SNAPSHOT"
    ]

    award_docs_by_project: dict[str, list[dict[str, Any]]] = {}
    evidence_docs_by_project: dict[str, list[dict[str, Any]]] = {}
    for d in docs.values():
        if d.get("project_code") == "PORTAL_SNAPSHOT":
            continue
        dtype = d.get("document_type")
        if dtype in {"award_notice", "result", "contract_notice", "contract"}:
            award_docs_by_project.setdefault(d["project_id"], []).append(d)
        if dtype in {
            "award_notice",
            "result",
            "evaluation_result",
            "contract_notice",
            "tender_document",
            "tender_notice",
        }:
            evidence_docs_by_project.setdefault(d["project_id"], []).append(d)

    suppliers_out: list[dict[str, Any]] = []
    matches_out: list[dict[str, Any]] = []
    evidence_out: list[dict[str, Any]] = []
    skipped = 0
    seen_match_keys: set[str] = set()

    disclosed: dict[str, list[dict[str, Any]]] = {}
    for project in projects:
        pid = project["project_id"]
        for doc in award_docs_by_project.get(pid, []):
            blob, _ = _doc_blob(settings, doc, chunks)
            for name in _extract_award_suppliers(blob):
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

    # Matches ONLY when public qualification/compliance language is present — never cartesian product.
    # Scan per-chunk so quotes always live inside the cited chunk text.
    for project in projects:
        pid = project["project_id"]
        suppliers = disclosed.get(pid) or []
        docs_to_scan = evidence_docs_by_project.get(pid) or []
        any_evidence_for_project = False
        p_chunks = [c for c in chunks.values() if c.get("project_id") == pid]
        for doc in docs_to_scan:
            if not doc.get("source_url"):
                continue
            doc_chunks = [c for c in p_chunks if c.get("document_id") == doc["document_id"]]
            if not doc_chunks:
                # Fallback: whole-doc blob once, then bind to best chunk containing the quote
                blob, _ = _doc_blob(settings, doc, chunks)
                if blob:
                    doc_chunks = [{"chunk_id": None, "document_id": doc["document_id"], "text": blob, "page_start": 1, "project_id": pid}]
            supplier_contexts: list[tuple[str | None, str | None]] = [(None, None)]
            for s in suppliers:
                supplier_contexts.append((s["supplier_id"], s["name"]))
            for chunk_row in doc_chunks:
                text = chunk_row.get("text") or ""
                if len(text) < 20:
                    continue
                for sid, sname in supplier_contexts:
                    for status, quote, req in _scan_evidence_matches(
                        pid=pid,
                        blob=text,
                        doc=doc,
                        evid_chunk=chunk_row if chunk_row.get("chunk_id") else None,
                        reqs=reqs,
                        supplier_id=sid,
                        supplier_name=sname,
                    ):
                        # Quote must be supported by this chunk
                        from bidpilot_data.labeling.evidence import quote_supported_by_chunk

                        if not quote_supported_by_chunk(quote, text):
                            skipped += 1
                            continue
                        # Require a real chunk_id
                        cid = chunk_row.get("chunk_id")
                        if not cid:
                            # Locate a real chunk containing the quote
                            for c in p_chunks:
                                if quote_supported_by_chunk(quote, c.get("text") or ""):
                                    chunk_row = c
                                    cid = c.get("chunk_id")
                                    text = c.get("text") or ""
                                    break
                        if not cid:
                            skipped += 1
                            continue
                        any_evidence_for_project = True
                        key = f"{req['requirement_id']}|{sid or 'none'}|{status.value}|{quote[:40]}|{cid}"
                        if key in seen_match_keys:
                            continue
                        seen_match_keys.add(key)
                        ev = make_evidence(
                            project_id=pid,
                            document_id=doc["document_id"],
                            source_url=doc["source_url"],
                            quote=quote,
                            page_number=chunk_row.get("page_start") or 1,
                            chunk_id=cid,
                        )
                        evidence_out.append(ev.model_dump(mode="json"))
                        match = RequirementMatchAnnotation(
                            match_id=str(stable_uuid(f"match:{key}")),
                            requirement_id=req["requirement_id"],
                            company_profile_id=None,
                            supplier_id=sid,
                            status=status,
                            reason=f"依据公开评审/审查表述判定为{status.value}",
                            evidence_ids=[ev.evidence_id],
                            evidence_document_id=doc["document_id"],
                            evidence_chunk_id=cid,
                            source_url=doc.get("source_url"),
                            source_quote=quote[:220],
                            confidence=0.7 if status != MatchStatus.uncertain else 0.45,
                            quality_level=QualityLevel.silver,
                            review_status=ReviewStatus.pending,
                        )
                        matches_out.append(match.model_dump(mode="json"))
        if suppliers and not any_evidence_for_project:
            skipped += len(suppliers) * max(1, sum(1 for r in reqs if r.get("project_id") == pid))

    stats = {
        "disclosed_suppliers": len(suppliers_out),
        "evidence_supported_matches": len(matches_out),
        "skipped_match_due_to_insufficient_evidence": skipped,
        "matches": len(matches_out),
        "evidence": len(evidence_out),
        "by_status": {},
        "dry_run": dry_run,
    }
    from collections import Counter

    stats["by_status"] = dict(Counter(m.get("status") for m in matches_out))
    if not dry_run:
        write_jsonl(ensure_dir(settings.datasets_root / "silver") / "disclosed_suppliers.jsonl", suppliers_out)
        write_jsonl(settings.datasets_root / "silver" / "requirement_matches.jsonl", matches_out)
        # Keep prior requirement/other evidence; drop previous match-only orphans by regenerating match evidence
        match_eids = {m.get("evidence_ids", [None])[0] for m in matches_out if m.get("evidence_ids")}
        existing_ev = []
        for e in read_jsonl(settings.datasets_root / "silver" / "evidence.jsonl"):
            # Keep evidence that is not being replaced and still looks requirement-linked
            # (heuristic: if quote looks like review outcome AND will be rewritten, skip old ones)
            quote = e.get("quote") or ""
            if any(k in quote for k in ("资格审查", "符合性审查", "无效投标", "废标处理")) and e.get(
                "evidence_id"
            ) not in match_eids:
                continue
            if e.get("evidence_id") in {x.get("evidence_id") for x in evidence_out}:
                continue
            existing_ev.append(e)
        write_jsonl(ensure_dir(settings.datasets_root / "silver") / "evidence.jsonl", existing_ev + evidence_out)
        for name in ("company_profiles.jsonl", "company_materials.jsonl"):
            path = settings.datasets_root / "silver" / name
            if path.exists():
                rows = [r for r in read_jsonl(path) if not r.get("synthetic")]
                write_jsonl(path, rows)
    log_stats(log, "disclosed_matches", stats)
    return stats
