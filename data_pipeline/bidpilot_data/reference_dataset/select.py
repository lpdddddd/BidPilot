"""Select high-quality projects and diversify category coverage."""

from __future__ import annotations

import random
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bidpilot_data.reference_dataset.schema import CATEGORY_BUCKETS
from bidpilot_data.utils import read_jsonl

# Map silver taxonomy categories → diversification buckets
_CATEGORY_MAP: dict[str, str] = {
    "qualification": "qualification",
    "certification": "qualification",
    "personnel": "qualification",
    "performance": "qualification",
    "financial": "qualification",
    "technical": "technical",
    "service": "technical",
    "commercial": "commercial",
    "pricing": "commercial",
    "contract": "commercial",
    "legal": "commercial",
    "scoring": "scoring",
    "delivery": "delivery",
    "submission": "delivery",
    "mandatory_rejection": "risk",
    "other": "risk",
    "project_info": "risk",
}

MIN_CHUNK_CHARS = 80
MIN_PROJECT_CHUNKS = 3
MIN_LONG_CHUNKS = 1


@dataclass
class CorpusIndexes:
    projects: dict[str, dict[str, Any]]
    documents: dict[str, dict[str, Any]]
    chunks: dict[str, dict[str, Any]]
    chunks_by_project: dict[str, list[dict[str, Any]]]
    requirements: list[dict[str, Any]]
    requirements_by_project: dict[str, list[dict[str, Any]]]
    evidence: list[dict[str, Any]]
    evidence_by_project: dict[str, list[dict[str, Any]]]
    evidence_by_chunk: dict[str, list[dict[str, Any]]]
    rag_questions: list[dict[str, Any]]
    rag_by_project: dict[str, list[dict[str, Any]]]
    matches: list[dict[str, Any]]
    suppliers: list[dict[str, Any]]
    suppliers_by_project: dict[str, list[dict[str, Any]]]


@dataclass
class SelectedProject:
    project_id: str
    project: dict[str, Any]
    score: float
    categories: Counter = field(default_factory=Counter)
    chunk_count: int = 0
    long_chunk_count: int = 0
    evidence_count: int = 0
    requirement_count: int = 0


def map_category(raw: str | None) -> str:
    if not raw:
        return "risk"
    return _CATEGORY_MAP.get(str(raw), "risk")


def load_corpus(datasets_root: Path) -> CorpusIndexes:
    root = Path(datasets_root)
    projects = {
        p["project_id"]: p
        for p in read_jsonl(root / "manifests" / "projects.jsonl")
        if p.get("project_id")
        and p.get("project_code") != "PORTAL_SNAPSHOT"
        and not str(p.get("project_name") or "").startswith("official_portal_snapshot")
    }
    documents = {d["document_id"]: d for d in read_jsonl(root / "manifests" / "documents.jsonl") if d.get("document_id")}
    chunks_raw = read_jsonl(root / "interim" / "chunks" / "chunks.jsonl")
    chunks = {c["chunk_id"]: c for c in chunks_raw if c.get("chunk_id")}
    chunks_by_project: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for c in chunks.values():
        pid = c.get("project_id")
        if pid in projects:
            chunks_by_project[pid].append(c)

    requirements = [
        r
        for r in read_jsonl(root / "silver" / "requirements.jsonl")
        if r.get("project_id") in projects and r.get("chunk_id") and r.get("original_text")
    ]
    requirements_by_project: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in requirements:
        requirements_by_project[r["project_id"]].append(r)

    evidence = [e for e in read_jsonl(root / "silver" / "evidence.jsonl") if e.get("project_id") in projects]
    evidence_by_project: dict[str, list[dict[str, Any]]] = defaultdict(list)
    evidence_by_chunk: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for e in evidence:
        evidence_by_project[e["project_id"]].append(e)
        if e.get("chunk_id"):
            evidence_by_chunk[e["chunk_id"]].append(e)

    rag_questions = [q for q in read_jsonl(root / "eval" / "rag" / "questions.jsonl") if q.get("project_id") in projects]
    rag_by_project: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for q in rag_questions:
        rag_by_project[q["project_id"]].append(q)

    matches = read_jsonl(root / "silver" / "requirement_matches.jsonl")
    suppliers = [s for s in read_jsonl(root / "silver" / "disclosed_suppliers.jsonl") if not s.get("synthetic")]
    suppliers_by_project: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for s in suppliers:
        if s.get("project_id"):
            suppliers_by_project[s["project_id"]].append(s)

    return CorpusIndexes(
        projects=projects,
        documents=documents,
        chunks=chunks,
        chunks_by_project=dict(chunks_by_project),
        requirements=requirements,
        requirements_by_project=dict(requirements_by_project),
        evidence=evidence,
        evidence_by_project=dict(evidence_by_project),
        evidence_by_chunk=dict(evidence_by_chunk),
        rag_questions=rag_questions,
        rag_by_project=dict(rag_by_project),
        matches=matches,
        suppliers=suppliers,
        suppliers_by_project=dict(suppliers_by_project),
    )


def _project_quality_score(corpus: CorpusIndexes, project_id: str) -> SelectedProject | None:
    proj = corpus.projects.get(project_id)
    if not proj:
        return None
    chunks = corpus.chunks_by_project.get(project_id) or []
    if len(chunks) < MIN_PROJECT_CHUNKS:
        return None
    long_chunks = [c for c in chunks if len((c.get("text") or "").strip()) >= MIN_CHUNK_CHARS]
    if len(long_chunks) < MIN_LONG_CHUNKS:
        return None
    reqs = corpus.requirements_by_project.get(project_id) or []
    if not reqs:
        return None
    # Prefer projects with evidence; allow high-quality req+chunk projects without evidence.
    evidence = corpus.evidence_by_project.get(project_id) or []
    # Parse-like completeness: docs exist and chunks reference known documents.
    doc_ids = {c.get("document_id") for c in chunks if c.get("document_id")}
    if not doc_ids or any(d not in corpus.documents for d in doc_ids):
        # Soft: allow if docs missing from manifest but chunks present (fixtures).
        if not any(c.get("document_id") for c in chunks):
            return None

    cats: Counter = Counter()
    for r in reqs:
        cats[map_category(r.get("category"))] += 1

    level = proj.get("bundle_level") or "incomplete"
    level_bonus = {"level_a": 3.0, "level_b": 2.5, "level_c": 1.5, "incomplete": 0.5}.get(level, 0.0)
    score = (
        level_bonus
        + min(3.0, len(long_chunks) / 20.0)
        + min(2.0, len(reqs) / 50.0)
        + min(2.0, len(evidence) / 10.0)
        + (1.0 if evidence else 0.0)
        + (0.5 if corpus.rag_by_project.get(project_id) else 0.0)
    )
    return SelectedProject(
        project_id=project_id,
        project=proj,
        score=score,
        categories=cats,
        chunk_count=len(chunks),
        long_chunk_count=len(long_chunks),
        evidence_count=len(evidence),
        requirement_count=len(reqs),
    )


def select_projects(
    corpus: CorpusIndexes,
    *,
    seed: int = 42,
    max_projects: int | None = 40,
    prefer_with_evidence: bool = True,
) -> list[SelectedProject]:
    """Filter high-quality projects and diversify category coverage with fixed seed."""
    rng = random.Random(seed)
    candidates: list[SelectedProject] = []
    for pid in sorted(corpus.projects):
        sp = _project_quality_score(corpus, pid)
        if sp is None:
            continue
        if prefer_with_evidence and sp.evidence_count == 0 and sp.score < 2.0:
            continue
        candidates.append(sp)

    # Seeded tie-break then sort by score for deterministic top pool
    keyed = [(-sp.score, rng.random(), sp.project_id, sp) for sp in candidates]
    keyed.sort()
    candidates = [sp for *_, sp in keyed]

    selected: list[SelectedProject] = []
    covered: Counter = Counter()
    # Round-robin fill to diversify categories
    bucket_queues: dict[str, list[SelectedProject]] = {b: [] for b in CATEGORY_BUCKETS}
    for sp in candidates:
        # Assign project to its dominant category bucket
        if sp.categories:
            dominant = sp.categories.most_common(1)[0][0]
        else:
            dominant = "risk"
        bucket_queues.setdefault(dominant, []).append(sp)

    # Interleave buckets
    max_n = max_projects or len(candidates)
    used: set[str] = set()
    while len(selected) < max_n:
        progressed = False
        for bucket in CATEGORY_BUCKETS:
            queue = bucket_queues.get(bucket) or []
            while queue:
                sp = queue.pop(0)
                if sp.project_id in used:
                    continue
                selected.append(sp)
                used.add(sp.project_id)
                covered.update(sp.categories)
                progressed = True
                break
            if len(selected) >= max_n:
                break
        if not progressed:
            break

    # Fill remainder by score if still short
    if len(selected) < max_n:
        for sp in candidates:
            if sp.project_id in used:
                continue
            selected.append(sp)
            used.add(sp.project_id)
            if len(selected) >= max_n:
                break

    return selected


def selection_fingerprint(selected: list[SelectedProject]) -> list[str]:
    return [s.project_id for s in selected]
