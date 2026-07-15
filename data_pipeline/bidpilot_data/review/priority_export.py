from __future__ import annotations

from typing import Any

import pandas as pd

from bidpilot_data.logging import get_logger, log_stats
from bidpilot_data.settings import get_settings
from bidpilot_data.utils import ensure_dir, read_jsonl

log = get_logger(__name__)


def export_priority_review(*, projects_n: int = 10, reqs_per_project: int = 70) -> dict[str, Any]:
    """Export gold-candidate review sheets for the highest-completeness projects."""
    settings = get_settings()
    projects = read_jsonl(settings.datasets_root / "manifests" / "projects.jsonl")
    reqs = read_jsonl(settings.datasets_root / "silver" / "requirements.jsonl")
    docs = {d["document_id"]: d for d in read_jsonl(settings.datasets_root / "manifests" / "documents.jsonl")}
    rag = read_jsonl(settings.datasets_root / "eval" / "rag" / "questions.jsonl")

    rank = {"level_a": 0, "level_b": 1, "level_c": 2, "incomplete": 3}
    projects_sorted = sorted(
        projects,
        key=lambda p: (
            rank.get(p.get("bundle_level"), 9),
            -len(p.get("documents") or []),
            p.get("project_name") or "",
        ),
    )
    chosen = [p for p in projects_sorted if p.get("bundle_level") in {"level_a", "level_b", "level_c"}][:projects_n]
    chosen_ids = {p["project_id"] for p in chosen}

    req_rows = []
    for pid in chosen_ids:
        preqs = [r for r in reqs if r.get("project_id") == pid]
        # Prefer mandatory / high risk / qualification / scoring
        def score(r: dict[str, Any]) -> tuple:
            return (
                0 if r.get("mandatory") else 1,
                0 if r.get("risk_level") in {"critical", "high"} else 1,
                0 if r.get("category") in {"qualification", "scoring", "mandatory_rejection", "project_info"} else 1,
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
                    "auto_normalized_requirement": r.get("normalized_requirement"),
                    "auto_mandatory": r.get("mandatory"),
                    "confidence": r.get("confidence"),
                    "decision": "",
                    "corrected_category": "",
                    "corrected_normalized_requirement": "",
                    "corrected_mandatory": "",
                    "reviewer": "",
                    "reviewed_at": "",
                    "review_comment": "",
                }
            )

    rag_rows = []
    for q in rag:
        if q.get("project_id") not in chosen_ids:
            continue
        proj = next(p for p in chosen if p["project_id"] == q["project_id"])
        rag_rows.append(
            {
                "question_id": q.get("question_id"),
                "project_code": proj.get("project_code"),
                "project_name": proj.get("project_name"),
                "bundle_level": proj.get("bundle_level"),
                "source_url": (q.get("source_urls") or [proj.get("official_project_url")])[0],
                "document_id": (q.get("source_document_ids") or [None])[0],
                "chunk_id": (q.get("gold_chunk_ids") or [None])[0],
                "source_page": ",".join(str(x) for x in (q.get("source_pages") or [])),
                "source_quote": " | ".join(q.get("source_quotes") or []),
                "question": q.get("question"),
                "auto_answer": q.get("answer"),
                "answerable": q.get("answerable"),
                "decision": "",
                "corrected_answer": "",
                "reviewer": "",
                "reviewed_at": "",
                "review_comment": "",
            }
        )
    # Cap first-stage RAG review target 200-300 if available
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
        "gold_target_stage1": "500-800 requirements after human review",
        "rag_gold_target_stage1": "200-300",
    }
    log_stats(log, "priority_review_export", {"projects": len(chosen), "requirements": len(req_rows), "rag": len(rag_rows)})
    return stats
