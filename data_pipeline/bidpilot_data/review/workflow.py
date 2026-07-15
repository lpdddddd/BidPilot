from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from bidpilot_data.logging import get_logger, log_stats
from bidpilot_data.schemas import QualityLevel, RequirementAnnotation, ReviewDecision, ReviewStatus, TaxonomyCategory
from bidpilot_data.settings import get_settings
from bidpilot_data.utils import append_jsonl, ensure_dir, read_jsonl, upsert_jsonl_by_key, write_json

log = get_logger(__name__)


REVIEW_COLUMNS = [
    "annotation_id",
    "project_id",
    "project_name",
    "project_code",
    "source_url",
    "original_filename",
    "source_page",
    "original_text",
    "predicted_category",
    "predicted_normalized_requirement",
    "predicted_mandatory",
    "predicted_score",
    "decision",
    "corrected_category",
    "corrected_normalized_requirement",
    "corrected_mandatory",
    "corrected_score",
    "reviewer",
    "review_comment",
]


def _project_lookup() -> dict[str, dict[str, Any]]:
    settings = get_settings()
    return {p["project_id"]: p for p in read_jsonl(settings.datasets_root / "manifests" / "projects.jsonl")}


def _doc_lookup() -> dict[str, dict[str, Any]]:
    settings = get_settings()
    return {d["document_id"]: d for d in read_jsonl(settings.datasets_root / "manifests" / "documents.jsonl")}


def export_review_csv(output: Path | None = None) -> dict[str, Any]:
    settings = get_settings()
    projects = _project_lookup()
    docs = _doc_lookup()
    pending_path = settings.datasets_root / "review" / "pending" / "requirements_pending.jsonl"
    silver_path = settings.datasets_root / "silver" / "requirements.jsonl"
    rows_src = read_jsonl(pending_path) or read_jsonl(silver_path)
    out_dir = ensure_dir(settings.datasets_root / "review" / "exported")
    out = output or (out_dir / "requirements_review.csv")

    rows = []
    for r in rows_src:
        proj = projects.get(r.get("project_id") or "", {})
        doc = docs.get(r.get("document_id") or "", {})
        rows.append(
            {
                "annotation_id": r.get("annotation_id"),
                "project_id": r.get("project_id"),
                "project_name": proj.get("project_name") or "",
                "project_code": proj.get("project_code") or "",
                "source_url": r.get("source_url") or proj.get("official_project_url") or doc.get("source_url"),
                "original_filename": doc.get("original_filename") or "",
                "source_page": r.get("source_page"),
                "original_text": r.get("original_text"),
                "predicted_category": r.get("category"),
                "predicted_normalized_requirement": r.get("normalized_requirement"),
                "predicted_mandatory": r.get("mandatory"),
                "predicted_score": r.get("score"),
                "decision": "",
                "corrected_category": "",
                "corrected_normalized_requirement": "",
                "corrected_mandatory": "",
                "corrected_score": "",
                "reviewer": "",
                "review_comment": "",
            }
        )
    pd.DataFrame(rows, columns=REVIEW_COLUMNS).to_csv(out, index=False)

    # Additional review sheets
    rag = read_jsonl(settings.datasets_root / "eval" / "rag" / "questions.jsonl")
    rag_rows = []
    for q in rag:
        proj = projects.get(q.get("project_id") or "", {})
        rag_rows.append(
            {
                "question_id": q.get("question_id"),
                "project_name": proj.get("project_name") or "",
                "project_code": proj.get("project_code") or "",
                "source_url": (q.get("source_urls") or [proj.get("official_project_url")])[0],
                "original_filename": "",
                "source_page": ",".join(str(p) for p in (q.get("source_pages") or [])),
                "original_text": " | ".join(q.get("source_quotes") or []),
                "auto_question": q.get("question"),
                "auto_answer": q.get("answer"),
                "decision": "",
                "corrected_answer": "",
                "reviewer": "",
                "review_comment": "",
            }
        )
    pd.DataFrame(rag_rows).to_csv(out_dir / "rag_review.csv", index=False)

    matches = read_jsonl(settings.datasets_root / "silver" / "requirement_matches.jsonl")
    match_rows = []
    for m in matches:
        match_rows.append(
            {
                "match_id": m.get("match_id"),
                "requirement_id": m.get("requirement_id"),
                "supplier_id": m.get("supplier_id"),
                "status": m.get("status"),
                "reason": m.get("reason"),
                "source_url": "",
                "original_text": "",
                "decision": "",
                "reviewer": "",
                "review_comment": "",
            }
        )
    pd.DataFrame(match_rows).to_csv(out_dir / "matches_review.csv", index=False)

    sft = read_jsonl(settings.datasets_root / "sft" / "train" / "records.jsonl")[:500]
    sft_rows = []
    for r in sft:
        user = next((m["content"] for m in r.get("messages", []) if m.get("role") == "user"), "")
        assistant = next((m["content"] for m in r.get("messages", []) if m.get("role") == "assistant"), "")
        proj = projects.get(r.get("project_id") or "", {})
        sft_rows.append(
            {
                "record_id": r.get("record_id"),
                "project_name": proj.get("project_name") or "",
                "project_code": proj.get("project_code") or "",
                "source_url": (r.get("source_urls") or [proj.get("official_project_url") or ""])[0],
                "task_type": r.get("task_type"),
                "user": user,
                "assistant": assistant,
                "decision": "",
                "reviewer": "",
                "review_comment": "",
            }
        )
    pd.DataFrame(sft_rows).to_csv(out_dir / "sft_review.csv", index=False)

    stats = {
        "exported": len(rows),
        "path": str(out),
        "rag_review": len(rag_rows),
        "matches_review": len(match_rows),
        "sft_review": len(sft_rows),
    }
    log_stats(log, "review_export", stats)
    return stats


def import_review_csv(file_path: Path, *, dry_run: bool = False) -> dict[str, Any]:
    settings = get_settings()
    df = pd.read_csv(file_path)
    for col in REVIEW_COLUMNS:
        if col not in df.columns:
            # backward compatible with older exports
            if col in {"project_name", "project_code", "original_filename"}:
                df[col] = ""
            else:
                raise ValueError(f"missing column: {col}")

    silver_path = settings.datasets_root / "silver" / "requirements.jsonl"
    gold_path = ensure_dir(settings.datasets_root / "gold") / "requirements.jsonl"
    rejected_path = ensure_dir(settings.datasets_root / "rejected") / "requirements.jsonl"
    log_path = settings.datasets_root / "review" / "imported" / "review_log.jsonl"
    pending_path = settings.datasets_root / "review" / "pending" / "requirements_pending.jsonl"

    by_id = {r["annotation_id"]: r for r in read_jsonl(silver_path)}
    gold = {r["annotation_id"]: r for r in read_jsonl(gold_path)}
    rejected = {r["annotation_id"]: r for r in read_jsonl(rejected_path)}
    pending = {r["annotation_id"]: r for r in read_jsonl(pending_path)}

    stats = {"rows": 0, "gold_upgraded": 0, "rejected": 0, "skipped": 0, "errors": 0}

    for _, row in df.iterrows():
        stats["rows"] += 1
        annotation_id = str(row["annotation_id"])
        decision_raw = str(row.get("decision") or "").strip().lower()
        if decision_raw in {"nan", "none"}:
            decision_raw = ""
        reviewer_raw = row.get("reviewer")
        if pd.isna(reviewer_raw):
            reviewer = ""
        else:
            reviewer = str(reviewer_raw).strip()

        if not decision_raw:
            stats["skipped"] += 1
            continue
        if decision_raw not in {d.value for d in ReviewDecision}:
            # accept common alias
            if decision_raw == "correct":
                decision_raw = "corrected"
            else:
                stats["errors"] += 1
                continue
        decision = ReviewDecision(decision_raw)
        base = by_id.get(annotation_id) or pending.get(annotation_id) or gold.get(annotation_id)
        if not base:
            stats["errors"] += 1
            continue

        if decision in {ReviewDecision.accept, ReviewDecision.corrected} and not reviewer:
            stats["errors"] += 1
            continue

        updated = dict(base)
        if decision == ReviewDecision.corrected:
            if str(row.get("corrected_category") or "").strip():
                updated["category"] = str(row["corrected_category"]).strip()
            if str(row.get("corrected_normalized_requirement") or "").strip():
                updated["normalized_requirement"] = str(row["corrected_normalized_requirement"]).strip()
            if str(row.get("corrected_mandatory") or "").strip() != "":
                updated["mandatory"] = str(row["corrected_mandatory"]).strip().lower() in {"1", "true", "yes"}
            if str(row.get("corrected_score") or "").strip() not in {"", "nan"}:
                try:
                    updated["score"] = float(row["corrected_score"])
                except ValueError:
                    pass

        if decision in {ReviewDecision.accept, ReviewDecision.corrected}:
            updated["quality_level"] = QualityLevel.gold.value
            updated["review_status"] = ReviewStatus.reviewed.value
            updated["reviewer"] = reviewer
            updated["reviewed_at"] = datetime.now(timezone.utc).isoformat()
            if not updated.get("source_page"):
                stats["errors"] += 1
                continue
            try:
                RequirementAnnotation.model_validate(updated)
            except Exception:
                stats["errors"] += 1
                continue
            if not dry_run:
                gold[annotation_id] = updated
                pending.pop(annotation_id, None)
            stats["gold_upgraded"] += 1
        elif decision == ReviewDecision.reject:
            updated["review_status"] = ReviewStatus.reviewed.value
            updated["reviewer"] = reviewer or None
            if not dry_run:
                rejected[annotation_id] = updated
                pending.pop(annotation_id, None)
                gold.pop(annotation_id, None)
            stats["rejected"] += 1
        else:
            stats["skipped"] += 1
            continue

        if not dry_run:
            append_jsonl(
                ensure_dir(log_path.parent) / log_path.name,
                {
                    "annotation_id": annotation_id,
                    "decision": decision.value,
                    "reviewer": reviewer,
                    "review_comment": None if pd.isna(row.get("review_comment")) else str(row.get("review_comment")),
                    "imported_at": datetime.now(timezone.utc).isoformat(),
                },
            )

    if not dry_run:
        upsert_jsonl_by_key(gold_path, list(gold.values()), "annotation_id")
        upsert_jsonl_by_key(rejected_path, list(rejected.values()), "annotation_id")
        write_json(settings.datasets_root / "review" / "imported" / "last_import_stats.json", stats)
        # pending refresh
        upsert_jsonl_by_key(pending_path, list(pending.values()), "annotation_id")

    log_stats(log, "review_import", stats)
    return stats
