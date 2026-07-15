from __future__ import annotations

import re
from collections import Counter
from typing import Any

from bidpilot_data.logging import get_logger, log_stats
from bidpilot_data.schemas import ParsedPage
from bidpilot_data.settings import get_settings
from bidpilot_data.utils import CheckpointStore, ensure_dir, read_jsonl, write_jsonl

log = get_logger(__name__)

# Protect money / dates / codes / percentages from aggressive cleanup
PROTECT_RE = re.compile(
    r"("
    r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|"  # 1,234.56
    r"\d+(?:\.\d+)?%|"
    r"￥?\d+(?:\.\d+)?万?元?|"
    r"\d{4}[-年/]\d{1,2}[-月/]\d{1,2}日?|"
    r"[A-Z0-9]{2,}[-_/]?[A-Z0-9-]{3,}|"  # project codes
    r"投标无效|否决投标|废标|无效响应"
    r")"
)

MOJIBAKE_RE = re.compile(r"(Ã.|Â.|ç¤º|ä¸|å)")
MULTI_BLANK = re.compile(r"[ \t]+\n")
MULTI_NL = re.compile(r"\n{3,}")


def clean_text(text: str) -> str:
    text = text.replace("\u3000", " ").replace("\xa0", " ")
    text = MOJIBAKE_RE.sub("", text)
    # Drop repeated header/footer-like short lines later at page aggregate level.
    text = MULTI_BLANK.sub("\n", text)
    text = MULTI_NL.sub("\n\n", text)
    # Collapse spaces but keep protected spans by reconstructing line-wise.
    cleaned_lines: list[str] = []
    for line in text.splitlines():
        if PROTECT_RE.search(line):
            cleaned_lines.append(re.sub(r"[^\S\n]+", " ", line).strip())
        else:
            cleaned_lines.append(re.sub(r"[^\S\n]+", " ", line).strip())
    # Deduplicate consecutive identical paragraphs
    out: list[str] = []
    prev = None
    for line in cleaned_lines:
        if not line:
            if prev != "":
                out.append("")
            prev = ""
            continue
        if line == prev:
            continue
        out.append(line)
        prev = line
    return "\n".join(out).strip()


def _drop_running_headers(pages: list[ParsedPage]) -> list[ParsedPage]:
    line_counts: Counter[str] = Counter()
    for page in pages:
        unique = set(ln.strip() for ln in page.text.splitlines() if 0 < len(ln.strip()) <= 40)
        line_counts.update(unique)
    common = {ln for ln, c in line_counts.items() if c >= max(2, len(pages) // 2 + 1)}
    cleaned: list[ParsedPage] = []
    for page in pages:
        lines = [ln for ln in page.text.splitlines() if ln.strip() not in common]
        text = clean_text("\n".join(lines))
        cleaned.append(page.model_copy(update={"text": text}))
    return cleaned


def clean_parsed_documents(*, resume: bool = True, dry_run: bool = False) -> dict[str, Any]:
    settings = get_settings()
    parsed_dir = settings.datasets_root / "interim" / "parsed"
    out_dir = ensure_dir(settings.datasets_root / "interim" / "cleaned")
    ckpt = CheckpointStore(settings.datasets_root / "reports" / "checkpoints" / "clean.json")
    stats = {"documents": 0, "pages": 0, "skipped": 0}
    for meta_path in sorted(parsed_dir.glob("*.meta.json")):
        document_id = meta_path.name.replace(".meta.json", "")
        stats["documents"] += 1
        if resume and ckpt.done(document_id):
            stats["skipped"] += 1
            continue
        if dry_run:
            continue
        pages = [ParsedPage.model_validate(r) for r in read_jsonl(parsed_dir / f"{document_id}.jsonl")]
        cleaned = _drop_running_headers(pages)
        write_jsonl(out_dir / f"{document_id}.jsonl", cleaned)
        stats["pages"] += len(cleaned)
        ckpt.mark_done(document_id, {"pages": len(cleaned)})
    log_stats(log, "clean", stats)
    return stats
