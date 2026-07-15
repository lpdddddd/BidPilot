from __future__ import annotations

import re
from typing import Any

from bidpilot_data.labeling.evidence import make_evidence
from bidpilot_data.logging import get_logger, log_stats
from bidpilot_data.schemas import MatchStatus, QualityLevel, RequirementMatchAnnotation, ReviewStatus
from bidpilot_data.settings import get_settings
from bidpilot_data.utils import ensure_dir, read_jsonl, stable_uuid, write_jsonl

log = get_logger(__name__)


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


def build_disclosed_matches(*, dry_run: bool = False) -> dict[str, Any]:
    """Build RequirementMatch rows only from public evidence; else unknown."""
    settings = get_settings()
    reqs = read_jsonl(settings.datasets_root / "silver" / "requirements.jsonl")
    chunks = {c["chunk_id"]: c for c in read_jsonl(settings.datasets_root / "interim" / "chunks" / "chunks.jsonl")}
    docs = {d["document_id"]: d for d in read_jsonl(settings.datasets_root / "manifests" / "documents.jsonl")}
    projects = read_jsonl(settings.datasets_root / "manifests" / "projects.jsonl")

    award_docs_by_project: dict[str, list[dict[str, Any]]] = {}
    for d in docs.values():
        dtype = d.get("document_type")
        if dtype in {"award_notice", "result", "contract_notice", "contract", "evaluation_result"}:
            award_docs_by_project.setdefault(d["project_id"], []).append(d)

    suppliers_out: list[dict[str, Any]] = []
    matches_out: list[dict[str, Any]] = []
    evidence_out: list[dict[str, Any]] = []

    # Map project_id -> disclosed supplier names from award HTML/text chunks
    disclosed: dict[str, list[dict[str, Any]]] = {}
    for project in projects:
        pid = project["project_id"]
        for doc in award_docs_by_project.get(pid, []):
            # Prefer parsed chunk text for this document
            texts = [c["text"] for c in chunks.values() if c.get("document_id") == doc["document_id"]]
            blob = "\n".join(texts)
            if not blob and doc.get("storage_path"):
                path = settings.datasets_root / doc["storage_path"]
                if path.exists() and path.suffix.lower() in {".html", ".htm", ".txt"}:
                    blob = path.read_text(encoding="utf-8", errors="ignore")
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

    # For each requirement, create unknown matches unless public qualification result exists.
    for req in reqs:
        pid = req["project_id"]
        suppliers = disclosed.get(pid) or []
        if not suppliers:
            match = RequirementMatchAnnotation(
                match_id=str(stable_uuid(f"match:{req['requirement_id']}:none")),
                requirement_id=req["requirement_id"],
                company_profile_id=None,
                supplier_id=None,
                status=MatchStatus.unknown,
                reason="官方公开材料不足，无法验证",
                evidence_ids=[],
                confidence=0.0,
                quality_level=QualityLevel.silver,
                review_status=ReviewStatus.pending,
            )
            matches_out.append(match.model_dump(mode="json"))
            continue

        for sup in suppliers:
            # Never infer satisfaction from award alone.
            doc = sup["document"]
            quote = f"公开文件披露供应商：{sup['name']}"
            # Bind a real chunk when supplier name appears in parsed text
            evid_chunk = None
            for c in chunks.values():
                if c.get("document_id") == doc["document_id"] and sup["name"] in (c.get("text") or ""):
                    evid_chunk = c
                    break
            if evid_chunk is None:
                for c in chunks.values():
                    if c.get("project_id") == pid and sup["name"] in (c.get("text") or ""):
                        evid_chunk = c
                        break
            page_number = evid_chunk.get("page_start") if evid_chunk else 1
            if evid_chunk and sup["name"] in (evid_chunk.get("text") or ""):
                # Prefer a short surrounding quote from chunk
                text = evid_chunk["text"]
                idx = text.find(sup["name"])
                quote = text[max(0, idx - 20) : idx + len(sup["name"]) + 40].strip() or quote
            ev = make_evidence(
                project_id=pid,
                document_id=doc["document_id"],
                source_url=doc.get("source_url") or project.get("official_project_url") or "https://www.ccgp.gov.cn/",
                quote=quote,
                page_number=page_number,
                chunk_id=evid_chunk.get("chunk_id") if evid_chunk else None,
            )
            evidence_out.append(ev.model_dump(mode="json"))
            match = RequirementMatchAnnotation(
                match_id=str(stable_uuid(f"match:{req['requirement_id']}:{sup['supplier_id']}")),
                requirement_id=req["requirement_id"],
                company_profile_id=None,
                supplier_id=sup["supplier_id"],
                status=MatchStatus.unknown,
                reason="仅依据中标/成交公告披露供应商名称，不能推断满足全部资格要求",
                evidence_ids=[ev.evidence_id],
                evidence_document_id=doc["document_id"],
                evidence_chunk_id=evid_chunk.get("chunk_id") if evid_chunk else None,
                confidence=0.2,
                quality_level=QualityLevel.silver,
                review_status=ReviewStatus.pending,
            )
            matches_out.append(match.model_dump(mode="json"))

    stats = {
        "suppliers": len(suppliers_out),
        "matches": len(matches_out),
        "evidence": len(evidence_out),
        "unknown_matches": sum(1 for m in matches_out if m.get("status") == "unknown"),
        "dry_run": dry_run,
    }
    if not dry_run:
        write_jsonl(ensure_dir(settings.datasets_root / "silver") / "disclosed_suppliers.jsonl", suppliers_out)
        write_jsonl(settings.datasets_root / "silver" / "requirement_matches.jsonl", matches_out)
        write_jsonl(ensure_dir(settings.datasets_root / "silver") / "evidence.jsonl", evidence_out)
        # Remove any legacy synthetic company artifacts from formal silver path.
        for name in ("company_profiles.jsonl", "company_materials.jsonl"):
            path = settings.datasets_root / "silver" / name
            if path.exists():
                rows = [r for r in read_jsonl(path) if not r.get("synthetic")]
                write_jsonl(path, rows)
    log_stats(log, "disclosed_matches", stats)
    return stats
