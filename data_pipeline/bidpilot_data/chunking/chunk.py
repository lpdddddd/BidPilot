from __future__ import annotations

import re
from typing import Any

from bidpilot_data.logging import get_logger, log_stats
from bidpilot_data.schemas import ChunkRecord, DocumentRecord, ParsedPage
from bidpilot_data.settings import get_settings, load_pipeline_config
from bidpilot_data.utils import CheckpointStore, content_fingerprint, ensure_dir, read_jsonl, stable_uuid, write_jsonl
from bidpilot_data.utils.hashing import normalize_text

log = get_logger(__name__)

SECTION_RE = re.compile(
    r"^(第[一二三四五六七八九十百千0-9]+[章节条款项].*|[0-9]+(\.[0-9]+){0,3}\s+\S+|（[一二三四五六七八九十]+）\S*)"
)
CLAUSE_RE = re.compile(r"([0-9]+(\.[0-9]+){0,3}|第[一二三四五六七八九十]+条)")


def estimate_tokens(text: str) -> int:
    # Lightweight approximation: Chinese chars ~1 token, latin words ~1.
    cn = len(re.findall(r"[\u4e00-\u9fff]", text))
    en = len(re.findall(r"[A-Za-z0-9_]+", text))
    return max(1, cn + en)


def split_by_sections(text: str) -> list[tuple[str | None, str | None, str]]:
    lines = text.splitlines()
    blocks: list[tuple[str | None, str | None, str]] = []
    current_section: str | None = None
    current_clause: str | None = None
    buf: list[str] = []

    def flush() -> None:
        nonlocal buf
        body = "\n".join(buf).strip()
        if body:
            blocks.append((current_section, current_clause, body))
        buf = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            buf.append("")
            continue
        if SECTION_RE.match(stripped):
            flush()
            current_section = stripped[:200]
            m = CLAUSE_RE.search(stripped)
            current_clause = m.group(1) if m else current_clause
            buf.append(stripped)
        else:
            buf.append(stripped)
    flush()
    return blocks or [(None, None, text.strip())]


def secondary_token_split(text: str, max_tokens: int, overlap: int) -> list[str]:
    if estimate_tokens(text) <= max_tokens:
        return [text]
    # Prefer sentence-ish boundaries
    parts = re.split(r"(?<=[。！？；\n])", text)
    chunks: list[str] = []
    buf = ""
    for part in parts:
        if not part:
            continue
        if estimate_tokens(buf + part) <= max_tokens:
            buf += part
        else:
            if buf.strip():
                chunks.append(buf.strip())
            # overlap tail
            if overlap > 0 and chunks:
                tail = chunks[-1]
                # rough overlap by chars
                ov = tail[- overlap * 2 :]
                buf = ov + part
            else:
                buf = part
    if buf.strip():
        chunks.append(buf.strip())
    return chunks or [text]


def chunk_documents(*, resume: bool = True, dry_run: bool = False) -> dict[str, Any]:
    settings = get_settings()
    cfg = load_pipeline_config().get("chunking", {})
    max_tokens = int(cfg.get("max_tokens", 800))
    overlap = int(cfg.get("overlap_tokens", 80))
    min_tokens = int(cfg.get("min_tokens", 40))

    docs = {d["document_id"]: DocumentRecord.model_validate(d) for d in read_jsonl(settings.datasets_root / "manifests" / "documents.jsonl")}
    cleaned_dir = settings.datasets_root / "interim" / "cleaned"
    out_path = ensure_dir(settings.datasets_root / "interim" / "chunks") / "chunks.jsonl"
    ckpt = CheckpointStore(settings.datasets_root / "reports" / "checkpoints" / "chunk.json")

    existing = [] if not resume else read_jsonl(out_path)
    kept = [r for r in existing if not resume or True]
    # Rebuild excluding docs being reprocessed when not resume; when resume skip done docs.
    done_ids = {r.get("document_id") for r in existing} if resume else set()
    records: list[ChunkRecord] = []
    if resume:
        records.extend(ChunkRecord.model_validate(r) for r in existing)

    stats = {"documents": 0, "chunks": 0, "skipped": 0}
    for path in sorted(cleaned_dir.glob("*.jsonl")):
        document_id = path.stem
        doc = docs.get(document_id)
        if doc is None:
            continue
        stats["documents"] += 1
        if resume and ckpt.done(document_id):
            stats["skipped"] += 1
            continue
        if dry_run:
            continue
        pages = [ParsedPage.model_validate(r) for r in read_jsonl(path)]
        # Remove previous chunks for this document when re-running
        records = [r for r in records if r.document_id != document_id]
        chunk_index = 0
        for page in pages:
            for section, clause, body in split_by_sections(page.text):
                for piece in secondary_token_split(body, max_tokens, overlap):
                    if estimate_tokens(piece) < min_tokens and len(piece) < 20:
                        continue
                    chunk_id = str(stable_uuid(f"chunk:{document_id}:{chunk_index}:{content_fingerprint(piece)}"))
                    rec = ChunkRecord(
                        chunk_id=chunk_id,
                        project_id=doc.project_id,
                        document_id=document_id,
                        page_start=page.page_number,
                        page_end=page.page_number,
                        section_path=section,
                        clause_number=clause,
                        chunk_index=chunk_index,
                        text=piece,
                        normalized_text=normalize_text(piece),
                        token_count=estimate_tokens(piece),
                        content_hash=content_fingerprint(piece),
                    )
                    records.append(rec)
                    chunk_index += 1
        stats["chunks"] += chunk_index
        ckpt.mark_done(document_id, {"chunks": chunk_index})

    if not dry_run:
        write_jsonl(out_path, records)
    log_stats(log, "chunk", stats)
    return stats
