from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from bidpilot_data.labeling.llm_client import OpenAICompatibleClient
from bidpilot_data.logging import get_logger, log_stats
from bidpilot_data.schemas import (
    ChunkRecord,
    QualityLevel,
    RequirementAnnotation,
    ReviewStatus,
    RiskLevel,
    TaxonomyCategory,
)
from bidpilot_data.settings import get_settings, load_pipeline_config, load_taxonomy
from bidpilot_data.utils import CheckpointStore, ensure_dir, read_jsonl, stable_uuid, upsert_jsonl_by_key, write_jsonl

log = get_logger(__name__)


class LLMRequirementOut(BaseModel):
    category: TaxonomyCategory
    title: str
    normalized_requirement: str
    mandatory: bool = False
    score: float | None = None
    risk_level: RiskLevel = RiskLevel.medium
    evidence_required: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)


def _rule_label(text: str, taxonomy: dict[str, Any]) -> tuple[TaxonomyCategory, bool, RiskLevel, float, list[str]]:
    cats = taxonomy.get("category_keywords", {})
    rejection = taxonomy.get("rejection_keywords", [])
    mandatory_kw = taxonomy.get("mandatory_keywords", [])
    scores: dict[str, int] = {}
    for cat, kws in cats.items():
        scores[cat] = sum(1 for kw in kws if kw in text)
    best = max(scores, key=scores.get) if scores else "other"
    if scores.get(best, 0) == 0:
        best = "other"
    if any(kw in text for kw in rejection):
        best = "mandatory_rejection"
        risk = RiskLevel.critical
        mandatory = True
        conf = 0.9
    else:
        risk = RiskLevel.high if any(kw in text for kw in mandatory_kw) else RiskLevel.medium
        mandatory = any(kw in text for kw in mandatory_kw)
        conf = min(0.95, 0.45 + 0.1 * scores.get(best, 0))
    evidence = []
    if "营业执照" in text:
        evidence.append("营业执照")
    if "资质" in text:
        evidence.append("资质证书")
    if "业绩" in text:
        evidence.append("类似项目业绩证明")
    return TaxonomyCategory(best), mandatory, risk, conf, evidence


def _candidate_sentences(text: str) -> list[str]:
    parts = [p.strip() for p in re_split(text) if len(p.strip()) >= 8]
    return parts


def re_split(text: str) -> list[str]:
    import re

    return re.split(r"(?<=[。；\n])", text)


def label_requirements(*, mode: str = "rules", resume: bool = True, dry_run: bool = False) -> dict[str, Any]:
    settings = get_settings()
    taxonomy = load_taxonomy()
    cfg = load_pipeline_config().get("labeling", {})
    threshold = float(cfg.get("low_confidence_threshold", 0.55))
    prompt_version = str(cfg.get("prompt_version", "req_label_v1"))

    chunks = [ChunkRecord.model_validate(r) for r in read_jsonl(settings.datasets_root / "interim" / "chunks" / "chunks.jsonl")]
    docs = {d["document_id"]: d for d in read_jsonl(settings.datasets_root / "manifests" / "documents.jsonl")}
    out_path = ensure_dir(settings.datasets_root / "silver") / "requirements.jsonl"
    review_path = ensure_dir(settings.datasets_root / "review" / "pending") / "requirements_pending.jsonl"
    cand_path = ensure_dir(settings.datasets_root / "interim" / "candidates") / "requirement_candidates.jsonl"
    ckpt = CheckpointStore(settings.datasets_root / "reports" / "checkpoints" / f"label_requirements_{mode}.json")

    client = OpenAICompatibleClient() if mode == "llm" else None
    annotations: list[RequirementAnnotation] = []
    pending_review: list[RequirementAnnotation] = []
    stats = {"chunks": 0, "annotations": 0, "pending_review": 0, "skipped": 0, "mode": mode}

    for chunk in chunks:
        stats["chunks"] += 1
        key = f"{chunk.chunk_id}:{mode}"
        if resume and ckpt.done(key):
            stats["skipped"] += 1
            continue
        if dry_run:
            continue
        for sentence in _candidate_sentences(chunk.text):
            # Only keep requirement-like candidates via keyword recall.
            reject_hit = any(kw in sentence for kw in taxonomy.get("rejection_keywords", []))
            mand_hit = any(kw in sentence for kw in taxonomy.get("mandatory_keywords", []))
            kw_hit = any(
                any(kw in sentence for kw in kws) for kws in taxonomy.get("category_keywords", {}).values()
            )
            if not (reject_hit or mand_hit or kw_hit):
                continue

            if mode == "llm" and client and client.available:
                system = (
                    "你是招投标需求标注员。请输出 JSON，字段: category,title,normalized_requirement,"
                    "mandatory,score,risk_level,evidence_required,confidence。"
                    f"category 必须属于: {', '.join(taxonomy.get('categories', []))}。"
                )
                user = f"条款原文:\n{sentence}\n章节:{chunk.section_path}\n页码:{chunk.page_start}"
                try:
                    parsed, meta = client.chat_json(
                        system=system,
                        user=user,
                        schema_model=LLMRequirementOut,
                        temperature=float(cfg.get("temperature", 0.0)),
                    )
                    category = parsed.category
                    mandatory = parsed.mandatory
                    risk = parsed.risk_level
                    conf = parsed.confidence
                    evidence = parsed.evidence_required
                    title = parsed.title
                    normalized = parsed.normalized_requirement
                    score = parsed.score
                    generator = "llm"
                    model_name = meta["model_name"]
                except Exception as exc:  # noqa: BLE001
                    log.warning("llm label failed, fallback rules: %s", exc)
                    category, mandatory, risk, conf, evidence = _rule_label(sentence, taxonomy)
                    title = sentence[:80]
                    normalized = sentence
                    score = None
                    generator = "rules_fallback"
                    model_name = None
                    prompt_version_local = prompt_version
                else:
                    prompt_version_local = prompt_version
            else:
                category, mandatory, risk, conf, evidence = _rule_label(sentence, taxonomy)
                title = sentence[:80]
                normalized = sentence
                score = None
                generator = "rules"
                model_name = None
                prompt_version_local = None

            req_id = str(stable_uuid(f"requirement:{chunk.project_id}:{content_key(sentence)}"))
            ann_id = str(stable_uuid(f"annotation:{req_id}:{generator}"))
            source_url = (docs.get(chunk.document_id) or {}).get("source_url")
            ann = RequirementAnnotation(
                annotation_id=ann_id,
                requirement_id=req_id,
                project_id=chunk.project_id,
                document_id=chunk.document_id,
                chunk_id=chunk.chunk_id,
                requirement_code=None,
                category=category,
                title=title,
                original_text=sentence,
                normalized_requirement=normalized,
                mandatory=mandatory,
                score=score,
                risk_level=risk,
                evidence_required=evidence,
                source_page=chunk.page_start,
                source_section=chunk.section_path,
                confidence=conf,
                quality_level=QualityLevel.silver,
                review_status=ReviewStatus.pending,
                generator=generator,
                prompt_version=prompt_version_local,
                model_name=model_name,
                source_url=source_url,
            )
            # Never auto-mark gold.
            if (
                conf < threshold
                or ann.source_page is None
                or not ann.normalized_requirement.strip()
                or generator.endswith("fallback")
            ):
                pending_review.append(ann)
                stats["pending_review"] += 1
            annotations.append(ann)
            stats["annotations"] += 1
        ckpt.mark_done(key, {"at": datetime.now(timezone.utc).isoformat()})

    if not dry_run:
        upsert_jsonl_by_key(out_path, annotations, "annotation_id")
        upsert_jsonl_by_key(review_path, pending_review, "annotation_id")
        write_jsonl(cand_path, annotations)
    log_stats(log, "label_requirements", stats)
    return stats


def content_key(text: str) -> str:
    from bidpilot_data.utils.hashing import content_fingerprint

    return content_fingerprint(text)
