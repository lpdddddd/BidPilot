from __future__ import annotations

from typing import Any

import pandas as pd

from bidpilot_data.logging import get_logger, log_stats
from bidpilot_data.settings import get_settings
from bidpilot_data.utils import ensure_dir, read_jsonl

log = get_logger(__name__)


def export_priority_review(
    *,
    projects_n: int = 12,
    reqs_per_project: int = 70,
    rag_n: int = 280,
) -> dict[str, Any]:
    """Export gold-candidate review sheets. Target 500-800 requirements, 200-300 RAG."""
    settings = get_settings()
    projects = [
        p
        for p in read_jsonl(settings.datasets_root / "manifests" / "projects.jsonl")
        if p.get("project_code") != "PORTAL_SNAPSHOT"
    ]
    reqs = read_jsonl(settings.datasets_root / "silver" / "requirements.jsonl")
    docs = {d["document_id"]: d for d in read_jsonl(settings.datasets_root / "manifests" / "documents.jsonl")}
    rag = read_jsonl(settings.datasets_root / "eval" / "rag" / "questions.jsonl")

    rank = {"level_b": 0, "level_a": 1, "level_c": 2, "incomplete": 9}
    projects_sorted = sorted(
        projects,
        key=lambda p: (
            rank.get(p.get("bundle_level"), 9),
            -len(p.get("documents") or []),
            p.get("project_name") or "",
        ),
    )
    # Prefer Level B; fill with A then C
    preferred = [p for p in projects_sorted if p.get("bundle_level") == "level_b"]
    fill = [p for p in projects_sorted if p.get("bundle_level") in {"level_a", "level_c"}]
    chosen = (preferred + fill)[:projects_n]
    # Expand project count until we can hit 500-800 req rows if possible
    reqs_per_project = max(50, min(80, reqs_per_project))
    while len(chosen) < len(preferred + fill):
        n_est = sum(
            min(reqs_per_project, sum(1 for r in reqs if r.get("project_id") == p["project_id"])) for p in chosen
        )
        if n_est >= 500:
            break
        nxt = (preferred + fill)[len(chosen) : len(chosen) + 1]
        if not nxt:
            break
        chosen.extend(nxt)

    chosen_ids = {p["project_id"] for p in chosen}

    req_rows: list[dict[str, Any]] = []
    for pid in chosen_ids:
        preqs = [r for r in reqs if r.get("project_id") == pid]

        def score(r: dict[str, Any]) -> tuple:
            cat = r.get("category")
            return (
                0 if cat == "qualification" else 1,
                0 if cat == "scoring" else 1,
                0 if cat == "mandatory_rejection" else 1,
                0 if cat == "project_info" else 1,
                0 if r.get("risk_level") in {"critical", "high"} else 1,
                0 if r.get("mandatory") else 1,
                -(float(r.get("confidence") or 0)),
            )

        preqs = sorted(preqs, key=score)[:reqs_per_project]
        proj = next(p for p in chosen if p["project_id"] == pid)
        for r in preqs:
            doc = docs.get(r.get("document_id") or "", {})
            req_rows.append(
                {
                    "annotation_id": r.get("annotation_id"),
                    "project_id": pid,
                    "project_code": proj.get("project_code"),
                    "project_name": proj.get("project_name"),
                    "bundle_level": proj.get("bundle_level"),
                    "source_url": r.get("source_url") or proj.get("official_project_url"),
                    "document_id": r.get("document_id"),
                    "chunk_id": r.get("chunk_id"),
                    "original_filename": doc.get("original_filename"),
                    "source_page": r.get("source_page"),
                    "source_quote": r.get("original_text"),
                    "auto_category": r.get("category"),
                    "auto_answer": r.get("normalized_requirement"),
                    "auto_normalized_requirement": r.get("normalized_requirement"),
                    "auto_mandatory": r.get("mandatory"),
                    "confidence": r.get("confidence"),
                    "decision": "",
                    "corrected_category": "",
                    "corrected_normalized_requirement": "",
                    "corrected_mandatory": "",
                    "corrected_answer": "",
                    "reviewer": "",
                    "reviewed_at": "",
                    "review_comment": "",
                }
            )

    # Cap to 500-800 band when possible
    if len(req_rows) > 800:
        req_rows = req_rows[:800]

    rag_rows: list[dict[str, Any]] = []
    # Prefer answerable + priority types from chosen / all non-portal projects
    ranked_rag = sorted(
        [q for q in rag if (projects and True)],
        key=lambda q: (
            0 if q.get("project_id") in chosen_ids else 1,
            0 if q.get("question_type") in {"qualification", "scoring", "rejection", "project_basic"} else 1,
            0 if q.get("answerable") else 1,
        ),
    )
    for q in ranked_rag:
        proj = next((p for p in projects if p["project_id"] == q.get("project_id")), None)
        if not proj or proj.get("project_code") == "PORTAL_SNAPSHOT":
            continue
        rag_rows.append(
            {
                "annotation_id": q.get("question_id"),
                "question_id": q.get("question_id"),
                "project_id": q.get("project_id"),
                "project_code": proj.get("project_code"),
                "project_name": proj.get("project_name"),
                "bundle_level": proj.get("bundle_level"),
                "source_url": (q.get("source_urls") or [proj.get("official_project_url")])[0],
                "document_id": (q.get("source_document_ids") or [None])[0],
                "chunk_id": (q.get("gold_chunk_ids") or [None])[0],
                "source_page": ",".join(str(x) for x in (q.get("source_pages") or [])),
                "source_quote": " | ".join(q.get("source_quotes") or []),
                "question": q.get("question"),
                "auto_category": q.get("question_type"),
                "auto_answer": q.get("answer"),
                "answerable": q.get("answerable"),
                "confidence": "",
                "decision": "",
                "corrected_answer": "",
                "corrected_category": "",
                "reviewer": "",
                "reviewed_at": "",
                "review_comment": "",
            }
        )
        if len(rag_rows) >= min(300, max(200, rag_n)):
            break
    rag_rows = rag_rows[:300]

    out_dir = ensure_dir(settings.datasets_root / "review" / "exported")
    pd.DataFrame(req_rows).to_csv(out_dir / "priority_requirements_review.csv", index=False)
    pd.DataFrame(rag_rows).to_csv(out_dir / "priority_rag_review.csv", index=False)
    stats = {
        "projects": [
            {
                "project_id": p["project_id"],
                "project_code": p.get("project_code"),
                "project_name": p.get("project_name"),
                "bundle_level": p.get("bundle_level"),
                "documents": len(p.get("documents") or []),
            }
            for p in chosen
        ],
        "requirements_exported": len(req_rows),
        "rag_exported": len(rag_rows),
        "ok_requirements_band": 500 <= len(req_rows) <= 800,
        "ok_rag_band": 200 <= len(rag_rows) <= 300,
        "gold_target_stage1": "500-800 requirements after human review",
        "rag_gold_target_stage1": "200-300",
        "note": "decision/reviewer left blank; do not auto-accept",
    }
    log_stats(
        log,
        "priority_review_export",
        {"projects": len(chosen), "requirements": len(req_rows), "rag": len(rag_rows)},
    )
    return stats
