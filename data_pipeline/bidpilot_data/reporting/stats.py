from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from bidpilot_data.settings import get_settings, load_pipeline_config
from bidpilot_data.utils import read_json, read_jsonl, write_json


def _domain(url: str | None) -> str | None:
    if not url:
        return None
    host = urlparse(str(url)).netloc.lower().split(":")[0]
    return host or None


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
    sft_stats = {}
    if (root / "reports" / "sft_build_stats.json").exists():
        sft_stats = read_json(root / "reports" / "sft_build_stats.json")
    match_build = {}
    # Optional match build counters live in last disclosed_matches log; reconstruct
    match_build = {
        "disclosed_suppliers": len(suppliers),
        "evidence_supported_matches": len(matches),
        "unknown_matches": sum(1 for m in matches if m.get("status") == "unknown"),
    }

    real_projects = [p for p in projects if p.get("project_code") != "PORTAL_SNAPSHOT"]
    portal_projects = [p for p in projects if p.get("project_code") == "PORTAL_SNAPSHOT"]

    level_counts = Counter(p.get("bundle_level") for p in real_projects)

    reachable_portal_domains = sorted(
        {
            d
            for p in portal_projects
            for d in [_domain(p.get("official_project_url")), p.get("source_domain")]
            if d
        }
        | {
            d
            for doc in docs
            if doc.get("project_code") == "PORTAL_SNAPSHOT"
            for d in [_domain(doc.get("source_url")), doc.get("source_site")]
            if d
        }
    )
    raw_document_domains = sorted({d for doc in docs if doc.get("project_code") != "PORTAL_SNAPSHOT" for d in [_domain(doc.get("source_url"))] if d})
    project_source_domains = sorted(
        {d for p in real_projects for d in [_domain(p.get("official_project_url")), p.get("source_domain")] if d}
    )
    tender_document_domains = sorted(
        {
            d
            for doc in docs
            if doc.get("document_type") in {"tender_document", "tender"} and doc.get("project_code") != "PORTAL_SNAPSHOT"
            for d in [_domain(doc.get("source_url"))]
            if d
        }
    )
    sft_all = sft_train + sft_val + sft_test
    sft_source_domains = sorted(
        {d for r in sft_all for url in (r.get("source_urls") or []) for d in [_domain(url)] if d}
    )
    reviewed_gold_source_domains = sorted(
        {
            d
            for r in sft_all
            if r.get("quality_level") == "gold" and r.get("review_status") == "reviewed"
            for url in (r.get("source_urls") or [])
            for d in [_domain(url)]
            if d
        }
    )
    domain_gap = {
        "sft_source_domains_count": len(sft_source_domains),
        "sft_source_domains_min": 5,
        "met": len(sft_source_domains) >= 5,
        "gap": max(0, 5 - len(sft_source_domains)),
        "note": "Portal homepage snapshots do not count toward SFT source diversity",
    }

    ext_counts = Counter()
    dtype_counts = Counter()
    for d in docs:
        if d.get("project_code") == "PORTAL_SNAPSHOT":
            continue
        dtype_counts[d.get("document_type") or "other"] += 1
        path = str(d.get("storage_path") or d.get("original_filename") or "")
        ext = Path(path).suffix.lower() or "unknown"
        ext_counts[ext] += 1

    tender_docs = sum(
        1
        for d in docs
        if d.get("document_type") in {"tender_document", "tender"} and d.get("project_code") != "PORTAL_SNAPSHOT"
    )
    award_docs = sum(1 for d in docs if d.get("document_type") in {"award_notice", "result"})
    contract_docs = sum(1 for d in docs if d.get("document_type") in {"contract_notice", "contract"})
    eval_docs = sum(1 for d in docs if d.get("document_type") == "evaluation_result")

    quality_reqs = Counter(r.get("quality_level") for r in silver + gold)
    quality_sft = Counter(r.get("quality_level") for r in sft_all)
    review_status = Counter(r.get("review_status") for r in silver + gold)
    threshold = float(cfg.get("labeling", {}).get("low_confidence_threshold", 0.55))
    pending_status_count = sum(
        1
        for r in silver + gold
        if r.get("review_status") in {None, "pending", "unreviewed"} and r.get("quality_level") != "gold"
    )
    low_confidence_count = sum(
        1 for r in silver + gold if float(r.get("confidence") or 0) < threshold and r.get("quality_level") != "gold"
    )
    review_queue_count = len(pending)

    targets = cfg.get("projects", {})
    gaps = {
        "projects_collected": max(0, int(targets.get("collected_target", 150)) - len(real_projects)),
        "with_tender_document": max(
            0,
            int(targets.get("with_tender_document_min", 100))
            - sum(1 for p in real_projects if p.get("bundle_level") in {"level_a", "level_b", "level_c"}),
        ),
        "level_a": max(0, int(targets.get("level_a_min", 20)) - level_counts.get("level_a", 0)),
        "level_b": max(0, int(targets.get("level_b_min", 40)) - level_counts.get("level_b", 0)),
        "finely_annotated": max(0, int(targets.get("finely_annotated_target", 30)) - len(gold)),
        "heldout": max(0, int(targets.get("heldout_target", 10)) - len({r.get("project_id") for r in sft_test})),
        "requirements_gold": max(0, int(cfg.get("requirements", {}).get("gold_target_min", 0)) - len(gold)),
        "rag_eval": max(0, int(cfg.get("rag_eval", {}).get("target_min", 0)) - len(ragqs)),
        "sft": max(0, int(cfg.get("sft", {}).get("preferred_target", 0)) - len(sft_all)),
        "sft_source_domains": domain_gap["gap"],
        "reviewed_trainable_sft": max(0, 500 - int(sft_stats.get("reviewed_trainable_sft") or 0)),
    }

    fail_reasons = Counter()
    for row in download_pending + discovery_failures:
        reason = str(row.get("reason") or row.get("error") or "unknown")
        fail_reasons[reason[:120]] += 1

    multi_step_agents = sum(1 for a in agents if len(a.get("expected_tool_calls") or []) >= 2)

    stats = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "real_data_only": True,
        "synthetic_forbidden": True,
        "discovered_projects": discovery_batch.get("stats", {}).get("list_hits")
        or discovery_batch.get("stats", {}).get("notices_fetched"),
        "downloaded_projects": len(real_projects),
        "portal_snapshot_projects": len(portal_projects),
        "domain_diversity": {
            "reachable_portal_domains": reachable_portal_domains,
            "raw_document_domains": raw_document_domains,
            "project_source_domains": project_source_domains,
            "tender_document_domains": tender_document_domains,
            "sft_source_domains": sft_source_domains,
            "reviewed_gold_source_domains": reviewed_gold_source_domains,
            "sft_source_domain_gap": domain_gap,
            "note": "reachable_portal_domains are homepage snapshots and must not be counted as training coverage",
        },
        "official_source_distribution": dict(Counter(project_source_domains)),
        "bundle_levels": {
            "level_a": level_counts.get("level_a", 0),
            "level_b": level_counts.get("level_b", 0),
            "level_c": level_counts.get("level_c", 0),
            "incomplete": level_counts.get("incomplete", 0),
        },
        "documents": {
            "total": sum(1 for d in docs if d.get("project_code") != "PORTAL_SNAPSHOT"),
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
            "quality": dict(quality_reqs),
            "review_status": dict(review_status),
            "pending": pending_status_count,
            "low_confidence": low_confidence_count,
            "review_queue": review_queue_count,
            "definitions": {
                "pending": "quality!=gold and review_status in {pending,unreviewed}",
                "low_confidence": f"auto-labeled confidence < {threshold}",
                "review_queue": "rows in review/pending/requirements_pending.jsonl exported for human review",
            },
        },
        "disclosed_suppliers": match_build["disclosed_suppliers"],
        "requirement_matches": {
            "total": len(matches),
            "evidence_supported_matches": match_build["evidence_supported_matches"],
            "unknown": match_build["unknown_matches"],
            "satisfied": sum(1 for m in matches if m.get("status") == "satisfied"),
            "missing": sum(1 for m in matches if m.get("status") == "missing"),
            "partially_satisfied": sum(1 for m in matches if m.get("status") == "partially_satisfied"),
            "uncertain": sum(1 for m in matches if m.get("status") == "uncertain"),
            "verifiable": sum(
                1 for m in matches if m.get("status") in {"satisfied", "missing", "partially_satisfied", "uncertain"}
            ),
        },
        "rag_questions": len(ragqs),
        "agent_tasks": len(agents),
        "agent_multi_step_trajectories": multi_step_agents,
        "sft": {
            "train": len(sft_train),
            "validation": len(sft_val),
            "test": len(sft_test),
            "quality_level": dict(quality_sft),
            "review_status": dict(Counter(r.get("review_status") for r in sft_all)),
            "train_projects": len({r.get("project_id") for r in sft_train}),
            "validation_projects": len({r.get("project_id") for r in sft_val}),
            "test_projects": len({r.get("project_id") for r in sft_test}),
            "structurally_valid_sft": sft_stats.get("structurally_valid_sft"),
            "reviewed_trainable_sft": sft_stats.get("reviewed_trainable_sft"),
            "silver_candidate_sft": sft_stats.get("silver_candidate_sft"),
            "rejected_sft": sft_stats.get("rejected_sft"),
        },
        "download_failures": len(download_pending),
        "discovery_failures": len(discovery_failures),
        "failure_reasons": dict(fail_reasons),
        "human_review_todo": {
            "requirements_pending_status": pending_status_count,
            "requirements_low_confidence": low_confidence_count,
            "requirements_review_queue": review_queue_count,
            "rag_pending": sum(1 for q in ragqs if q.get("review_status") == "pending"),
            "matches_unknown": match_build["unknown_matches"],
            "sft_pending": sum(1 for r in sft_all if r.get("review_status") == "pending"),
        },
        "parse_status_counts": {},
        "targets": cfg,
        "gaps": gaps,
        "validation_ok": validation.get("ok"),
        "notes": [
            "No synthetic companies/projects/answers are generated.",
            "Gaps must not be filled with fictional samples.",
            "Gold labels require human review with page-level evidence.",
            "Portal homepage snapshots are not training coverage.",
            "reviewed_trainable_sft is the only gate for formal LoRA.",
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
            "task_distribution": "datasets/reports/task_distribution.json",
            "dedup_report": "datasets/reports/dedup_report.json",
            "split_distribution": "datasets/reports/split_distribution.json",
        },
        "gaps": stats["gaps"],
        "bundle_levels": stats["bundle_levels"],
        "domain_diversity": stats["domain_diversity"],
    }
    write_json(root / "reports" / "build_manifest.json", manifest)

    md = _render_markdown(stats, discovery_batch)
    (settings.repo_root / "DATASET_BUILD_REPORT.md").write_text(md, encoding="utf-8")
    return stats


def _render_markdown(stats: dict[str, Any], discovery_batch: dict[str, Any]) -> str:
    bl = stats["bundle_levels"]
    docs = stats["documents"]
    gaps = stats["gaps"]
    fails = stats.get("failure_reasons") or {}
    dom = stats.get("domain_diversity") or {}
    sft = stats.get("sft") or {}
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
        "- Portal homepage snapshots are **not** training coverage.",
        "",
        "## Discovery & Download",
        "",
        f"- List hits / notices fetched (batch): `{discovery_batch.get('stats', {})}`",
        f"- Downloaded real projects in manifests: **{stats['downloaded_projects']}** (portal snapshots excluded)",
        f"- Portal snapshot projects (not training): **{stats.get('portal_snapshot_projects')}**",
        f"- Project source domains: `{dom.get('project_source_domains')}`",
        f"- Tender document domains: `{dom.get('tender_document_domains')}`",
        f"- SFT source domains (record coverage): `{dom.get('sft_source_domains')}` gap=`{dom.get('sft_source_domain_gap')}`",
        f"- Reachable portal domains (homepage only): `{dom.get('reachable_portal_domains')}`",
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
        f"- Total (non-portal): **{docs['total']}**",
        f"- PDF: **{docs['pdf']}**, DOCX: **{docs['docx']}**, HTML: **{docs['html']}**",
        f"- Tender documents: **{docs['tender_documents']}**",
        f"- Award notices: **{docs['award_notices']}**",
        f"- Contract notices: **{docs['contract_notices']}**",
        f"- Evaluation results: **{docs['evaluation_results']}**",
        "",
        "## Labels & Matches",
        "",
        f"- Requirements: silver={stats['requirements']['silver']}, gold={stats['requirements']['gold']}",
        f"- pending (review_status): **{stats['requirements']['pending']}**",
        f"- low_confidence: **{stats['requirements']['low_confidence']}**",
        f"- review_queue (exported CSV source): **{stats['requirements']['review_queue']}**",
        f"- Disclosed suppliers: **{stats['disclosed_suppliers']}**",
        f"- Matches: {stats['requirement_matches']}",
        f"- RAG questions: **{stats['rag_questions']}**",
        f"- Agent tasks: **{stats['agent_tasks']}** (multi-step={stats.get('agent_multi_step_trajectories')})",
        "",
        "## SFT Quality Gates",
        "",
        f"- structurally_valid_sft: **{sft.get('structurally_valid_sft')}**",
        f"- reviewed_trainable_sft: **{sft.get('reviewed_trainable_sft')}** (formal LoRA gate)",
        f"- silver_candidate_sft: **{sft.get('silver_candidate_sft')}**",
        f"- rejected_sft: **{sft.get('rejected_sft')}**",
        f"- split counts: train={sft.get('train')}, validation={sft.get('validation')}, test={sft.get('test')}",
        f"- projects: train={sft.get('train_projects')}, validation={sft.get('validation_projects')}, test={sft.get('test_projects')}",
        f"- quality_level: `{sft.get('quality_level')}`",
        f"- review_status: `{sft.get('review_status')}`",
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
        "## Notes",
        "",
        "- incomplete projects stay in raw/manifests; they are excluded from formal SFT.",
        "- level_c used for clause extraction only; level_a/b for RAG/cross-doc tasks.",
        "- Do **not** start formal LoRA until reviewed_trainable_sft meets the gold review target.",
        "- Do not count portal homepage snapshots as SFT source diversity.",
        "",
    ]
    return "\n".join(lines) + "\n"
