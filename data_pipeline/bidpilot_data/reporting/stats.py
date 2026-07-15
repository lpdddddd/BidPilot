from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bidpilot_data.settings import get_settings, load_pipeline_config
from bidpilot_data.utils import read_json, read_jsonl, write_json


def build_reports() -> dict[str, Any]:
    settings = get_settings()
    cfg = load_pipeline_config()
    root = settings.datasets_root

    docs = read_jsonl(root / "manifests" / "documents.jsonl")
    sources = read_jsonl(root / "manifests" / "sources.jsonl")
    projects = read_jsonl(root / "manifests" / "projects.jsonl")
    chunks = read_jsonl(root / "interim" / "chunks" / "chunks.jsonl")
    silver = read_jsonl(root / "silver" / "requirements.jsonl")
    gold = read_jsonl(root / "gold" / "requirements.jsonl")
    pending = read_jsonl(root / "review" / "pending" / "requirements_pending.jsonl")
    matches = read_jsonl(root / "silver" / "requirement_matches.jsonl")
    suppliers = read_jsonl(root / "silver" / "disclosed_suppliers.jsonl")
    ragqs = read_jsonl(root / "eval" / "rag" / "questions.jsonl")
    agents = read_jsonl(root / "eval" / "agent" / "tasks.jsonl")
    sft_train = read_jsonl(root / "sft" / "train" / "records.jsonl")
    sft_val = read_jsonl(root / "sft" / "validation" / "records.jsonl")
    sft_test = read_jsonl(root / "sft" / "test" / "records.jsonl")
    download_pending = read_jsonl(root / "reports" / "download_pending.jsonl")
    discovery_failures = read_jsonl(root / "reports" / "discovery_failures.jsonl")
    discovery_batch = {}
    dbp = root / "reports" / "discovery_batch_report.json"
    if dbp.exists():
        discovery_batch = read_json(dbp)
    validation = {}
    vpath = root / "reports" / "validation_report.json"
    if vpath.exists():
        validation = read_json(vpath)

    level_counts = Counter(p.get("bundle_level") for p in projects)
    domain_counts = Counter()
    for p in projects:
        domain_counts[p.get("source_domain") or "unknown"] += 1
    for s in sources:
        domain_counts[s.get("source_site") or "unknown"] += 0

    ext_counts = Counter()
    dtype_counts = Counter()
    for d in docs:
        dtype_counts[d.get("document_type") or "other"] += 1
        path = str(d.get("storage_path") or d.get("original_filename") or "")
        ext = Path(path).suffix.lower() or "unknown"
        ext_counts[ext] += 1

    tender_docs = sum(1 for d in docs if d.get("document_type") in {"tender_document", "tender"})
    award_docs = sum(1 for d in docs if d.get("document_type") in {"award_notice", "result"})
    contract_docs = sum(1 for d in docs if d.get("document_type") in {"contract_notice", "contract"})
    eval_docs = sum(1 for d in docs if d.get("document_type") == "evaluation_result")

    quality_reqs = Counter(r.get("quality_level") for r in silver + gold)
    quality_sft = Counter(r.get("quality_level") for r in sft_train + sft_val + sft_test)
    review_status = Counter(r.get("review_status") for r in silver + gold)

    targets = cfg.get("projects", {})
    gaps = {
        "projects_collected": max(0, int(targets.get("collected_target", 150)) - len(projects)),
        "with_tender_document": max(
            0,
            int(targets.get("with_tender_document_min", 100))
            - sum(1 for p in projects if p.get("bundle_level") in {"level_a", "level_b", "level_c"}),
        ),
        "level_a": max(0, int(targets.get("level_a_min", 20)) - level_counts.get("level_a", 0)),
        "level_b": max(0, int(targets.get("level_b_min", 40)) - level_counts.get("level_b", 0)),
        "finely_annotated": max(0, int(targets.get("finely_annotated_target", 30)) - len(gold)),
        "heldout": max(0, int(targets.get("heldout_target", 10)) - len({r.get("project_id") for r in sft_test})),
        "requirements_gold": max(0, int(cfg.get("requirements", {}).get("gold_target_min", 0)) - len(gold)),
        "rag_eval": max(0, int(cfg.get("rag_eval", {}).get("target_min", 0)) - len(ragqs)),
        "sft": max(0, int(cfg.get("sft", {}).get("preferred_target", 0)) - (len(sft_train) + len(sft_val) + len(sft_test))),
    }

    fail_reasons = Counter()
    for row in download_pending + discovery_failures:
        reason = str(row.get("reason") or row.get("error") or "unknown")
        fail_reasons[reason[:120]] += 1

    stats = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "real_data_only": True,
        "synthetic_forbidden": True,
        "discovered_projects": discovery_batch.get("stats", {}).get("list_hits")
        or discovery_batch.get("stats", {}).get("notices_fetched"),
        "downloaded_projects": len(projects),
        "official_source_distribution": dict(domain_counts),
        "bundle_levels": {
            "level_a": level_counts.get("level_a", 0),
            "level_b": level_counts.get("level_b", 0),
            "level_c": level_counts.get("level_c", 0),
            "incomplete": level_counts.get("incomplete", 0),
        },
        "documents": {
            "total": len(docs),
            "by_type": dict(dtype_counts),
            "by_extension": dict(ext_counts),
            "tender_documents": tender_docs,
            "award_notices": award_docs,
            "contract_notices": contract_docs,
            "evaluation_results": eval_docs,
            "pdf": ext_counts.get(".pdf", 0),
            "docx": ext_counts.get(".docx", 0) + ext_counts.get(".doc", 0),
            "html": ext_counts.get(".html", 0) + ext_counts.get(".htm", 0),
        },
        "sources": len(sources),
        "chunks": len(chunks),
        "requirements": {
            "silver": len(silver),
            "gold": len(gold),
            "pending_review": len(pending),
            "quality": dict(quality_reqs),
            "review_status": dict(review_status),
        },
        "disclosed_suppliers": len(suppliers),
        "requirement_matches": {
            "total": len(matches),
            "unknown": sum(1 for m in matches if m.get("status") == "unknown"),
            "satisfied": sum(1 for m in matches if m.get("status") == "satisfied"),
            "missing": sum(1 for m in matches if m.get("status") == "missing"),
            "partially_satisfied": sum(1 for m in matches if m.get("status") == "partially_satisfied"),
            "verifiable": sum(1 for m in matches if m.get("status") in {"satisfied", "missing", "partially_satisfied"}),
        },
        "rag_questions": len(ragqs),
        "agent_tasks": len(agents),
        "sft": {
            "train": len(sft_train),
            "validation": len(sft_val),
            "test": len(sft_test),
            "quality": dict(quality_sft),
            "train_projects": len({r.get("project_id") for r in sft_train}),
            "validation_projects": len({r.get("project_id") for r in sft_val}),
            "test_projects": len({r.get("project_id") for r in sft_test}),
        },
        "download_failures": len(download_pending),
        "discovery_failures": len(discovery_failures),
        "failure_reasons": dict(fail_reasons),
        "human_review_todo": {
            "requirements_pending": len(pending),
            "rag_pending": sum(1 for q in ragqs if q.get("review_status") == "pending"),
            "matches_unknown": sum(1 for m in matches if m.get("status") == "unknown"),
            "sft_pending": sum(1 for r in sft_train + sft_val + sft_test if r.get("review_status") == "pending"),
        },
        "parse_status_counts": {},
        "targets": cfg,
        "gaps": gaps,
        "validation_ok": validation.get("ok"),
        "notes": [
            "No synthetic companies/projects/answers are generated.",
            "Gaps must not be filled with fictional samples.",
            "Gold labels require human review with page-level evidence.",
        ],
    }
    for d in docs:
        st = d.get("parse_status", "unknown")
        stats["parse_status_counts"][st] = stats["parse_status_counts"].get(st, 0) + 1

    write_json(root / "reports" / "dataset_statistics.json", stats)
    manifest = {
        "generated_at": stats["generated_at"],
        "artifacts": {
            "projects": "datasets/manifests/projects.jsonl",
            "documents": "datasets/manifests/documents.jsonl",
            "chunks": "datasets/interim/chunks/chunks.jsonl",
            "silver_requirements": "datasets/silver/requirements.jsonl",
            "gold_requirements": "datasets/gold/requirements.jsonl",
            "evidence": "datasets/silver/evidence.jsonl",
            "sft_train": "datasets/sft/train/sharegpt.json",
            "validation_report": "datasets/reports/validation_report.json",
            "discovery_batch": "datasets/reports/discovery_batch_report.json",
        },
        "gaps": stats["gaps"],
        "bundle_levels": stats["bundle_levels"],
    }
    write_json(root / "reports" / "build_manifest.json", manifest)

    # Markdown report at repo root
    md = _render_markdown(stats, discovery_batch)
    (settings.repo_root / "DATASET_BUILD_REPORT.md").write_text(md, encoding="utf-8")
    return stats


def _render_markdown(stats: dict[str, Any], discovery_batch: dict[str, Any]) -> str:
    bl = stats["bundle_levels"]
    docs = stats["documents"]
    gaps = stats["gaps"]
    fails = stats.get("failure_reasons") or {}
    lines = [
        "# BidPilot Dataset Build Report",
        "",
        f"Generated at: `{stats['generated_at']}`",
        "",
        "## Policy",
        "",
        "- Real public procurement / public-resource projects only.",
        "- Synthetic companies, virtual qualifications, fictional awards, and fabricated gold answers are **forbidden**.",
        "- Auto extracts are silver/pending; gold requires human review with official URL, page, and quote.",
        "",
        "## Discovery & Download",
        "",
        f"- List hits / notices fetched (batch): `{discovery_batch.get('stats', {})}`",
        f"- Downloaded real projects in manifests: **{stats['downloaded_projects']}**",
        f"- Official source distribution: `{stats['official_source_distribution']}`",
        f"- Discovery failures: **{stats['discovery_failures']}**",
        f"- Download pending / failures: **{stats['download_failures']}**",
        "",
        "### Failure reasons",
        "",
    ]
    if fails:
        for k, v in sorted(fails.items(), key=lambda x: -x[1])[:30]:
            lines.append(f"- `{k}`: {v}")
    else:
        lines.append("- None recorded.")
    lines += [
        "",
        "## Project Bundle Levels",
        "",
        f"- level_a: **{bl['level_a']}**",
        f"- level_b: **{bl['level_b']}**",
        f"- level_c: **{bl['level_c']}**",
        f"- incomplete: **{bl['incomplete']}**",
        "",
        "## Documents",
        "",
        f"- Total: **{docs['total']}**",
        f"- PDF: **{docs['pdf']}**, DOCX: **{docs['docx']}**, HTML: **{docs['html']}**",
        f"- Tender documents: **{docs['tender_documents']}**",
        f"- Award notices: **{docs['award_notices']}**",
        f"- Contract notices: **{docs['contract_notices']}**",
        f"- Evaluation results: **{docs['evaluation_results']}**",
        "",
        "## Labels & Matches",
        "",
        f"- Requirements silver/gold/pending: {stats['requirements']}",
        f"- Disclosed suppliers: **{stats['disclosed_suppliers']}**",
        f"- Matches: {stats['requirement_matches']}",
        f"- RAG questions: **{stats['rag_questions']}**",
        f"- SFT: {stats['sft']}",
        "",
        "## Target Gaps (not filled with fiction)",
        "",
    ]
    for k, v in gaps.items():
        lines.append(f"- {k}: **{v}** remaining")
    lines += [
        "",
        "## Human Review TODO",
        "",
        f"`{stats['human_review_todo']}`",
        "",
        f"Validation ok: **{stats.get('validation_ok')}**",
        "",
    ]
    return "\n".join(lines) + "\n"
