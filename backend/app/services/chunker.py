"""Structure-aware, rule-based chunking for parsed tender documents.

Design goals, in priority order:
1. Provenance: every chunk records exact character ranges relative to the
   stable parse artifact (extracted.txt) and, for PDFs, real page ranges
   derived from the parser's page index. Nothing is guessed.
2. Structure awareness: common Chinese tender headings (第X章, 一、, （一）,
   1.1.1, 第X条, 附件...) drive section paths and boundaries.
3. Determinism: same input text always yields the same chunks.

Boundary priority: heading > paragraph > list/table row > sentence-final
punctuation > hard token split (last resort).

Parameters follow data_pipeline/configs/pipeline.yaml (chunking section):
min_tokens=40, max_tokens=800, overlap_tokens=80; target_tokens=500 fills the
gap between min and max for greedy assembly.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from functools import lru_cache

CHUNKER_NAME = "bidpilot-structure-chunker"
CHUNKER_VERSION = "1.0.0"


@dataclass(frozen=True)
class ChunkerConfig:
    min_tokens: int = 40
    target_tokens: int = 500
    max_tokens: int = 800
    overlap_tokens: int = 80


@dataclass
class PageSpanIn:
    page: int
    char_start: int
    char_end: int


@dataclass
class PlannedChunk:
    chunk_index: int
    content: str
    content_hash: str
    token_count: int
    section: str | None
    clause_id: str | None
    section_path: list[str]
    heading_level: int | None
    chunk_kind: str
    source_char_start: int
    source_char_end: int
    core_char_start: int
    core_char_end: int
    overlap_prefix_chars: int
    page_start: int | None
    page_end: int | None


@dataclass
class ChunkPlanResult:
    chunks: list[PlannedChunk]
    tokenizer: str
    section_count: int
    total_tokens: int


# --------------------------------------------------------------- tokenization


class _Tokenizer:
    """cl100k_base via tiktoken; falls back to an honest approximation and
    reports which one was actually used."""

    def __init__(self) -> None:
        self.name = "cl100k_base"
        self._encoding = None
        try:
            import tiktoken

            self._encoding = tiktoken.get_encoding("cl100k_base")
        except Exception:  # noqa: BLE001 - offline environments
            self.name = "approx-cjk-latin-v1"

    def count(self, text: str) -> int:
        if self._encoding is not None:
            return len(self._encoding.encode(text, disallowed_special=()))
        cn = len(re.findall(r"[\u4e00-\u9fff]", text))
        latin = len(re.findall(r"[A-Za-z0-9]+", text))
        other = len(re.findall(r"[^\sA-Za-z0-9\u4e00-\u9fff]", text))
        return max(1, cn + latin + other // 2)

    def tail_chars(self, text: str, max_tokens: int) -> int:
        """Character length of the suffix of `text` holding ~max_tokens tokens."""
        if self._encoding is not None:
            tokens = self._encoding.encode(text, disallowed_special=())
            if len(tokens) <= max_tokens:
                return len(text)
            return len(self._encoding.decode(tokens[-max_tokens:]))
        # Approximation: assume ~1.5 chars per token for mixed CJK text.
        return min(len(text), int(max_tokens * 1.5))

    def hard_split_points(self, text: str, max_tokens: int) -> list[int]:
        """Split offsets (relative to text) so每段 <= max_tokens tokens."""
        if self._encoding is not None:
            tokens = self._encoding.encode(text, disallowed_special=())
            points: list[int] = []
            pos = 0
            for i in range(0, len(tokens), max_tokens):
                part = self._encoding.decode(tokens[i : i + max_tokens])
                pos += len(part)
                if pos < len(text):
                    points.append(pos)
            return points
        step = max(1, int(max_tokens * 1.5))
        return list(range(step, len(text), step))


@lru_cache(maxsize=1)
def get_tokenizer() -> _Tokenizer:
    return _Tokenizer()


# ----------------------------------------------------------- heading detection

_CHAPTER_RE = re.compile(r"^第\s*[一二三四五六七八九十百千0-9]+\s*(章|部分|节|篇)")
_CLAUSE_RE = re.compile(r"^(第\s*[一二三四五六七八九十百千0-9]+\s*条)")
_CN_NUM_RE = re.compile(r"^[一二三四五六七八九十]{1,3}、")
_PAREN_CN_RE = re.compile(r"^（[一二三四五六七八九十]{1,3}）")
_NUM_RE = re.compile(r"^(\d{1,2}(?:\.\d{1,2}){0,3})[\.、．\s）)]")
_KEYWORD_RE = re.compile(
    r"^(附件|附表|附录|评分办法|评分标准|资格要求|技术要求|商务要求|投标须知|"
    r"合同条款|开标|评标|废标|投标人须知)"
)
_SENTENCE_END_RE = re.compile(r"[。！？；]|\n")
_LIST_LINE_RE = re.compile(r"^(\d{1,3}[\.、．)）]|[-*•·]|（[一二三四五六七八九十0-9]{1,3}）)")

_MAX_HEADING_CHARS = 60


@dataclass
class _Heading:
    level: int
    title: str
    clause_id: str | None


def detect_heading(line: str) -> _Heading | None:
    stripped = line.strip()
    if not stripped or len(stripped) > _MAX_HEADING_CHARS:
        return None
    if _CHAPTER_RE.match(stripped):
        return _Heading(level=1, title=stripped, clause_id=None)
    # Lines that read like sentences are body text, not headings.
    if stripped.endswith(("，", "、", "：", ":", "；", "。", "！", "？")):
        return None
    clause = _CLAUSE_RE.match(stripped)
    if clause:
        return _Heading(level=3, title=stripped, clause_id=clause.group(1).replace(" ", ""))
    if _CN_NUM_RE.match(stripped):
        return _Heading(level=2, title=stripped, clause_id=None)
    if _PAREN_CN_RE.match(stripped):
        return _Heading(level=3, title=stripped, clause_id=None)
    num = _NUM_RE.match(stripped)
    if num:
        depth = num.group(1).count(".") + 1
        return _Heading(level=min(1 + depth, 5), title=stripped, clause_id=num.group(1))
    if _KEYWORD_RE.match(stripped):
        return _Heading(level=2, title=stripped, clause_id=None)
    return None


# ------------------------------------------------------------------ splitting


@dataclass
class _Block:
    start: int  # char offset in source text (inclusive)
    end: int  # char offset in source text (exclusive)
    kind: str  # heading | list | table | paragraph
    section_path: tuple[str, ...]
    clause_id: str | None
    heading_level: int | None


def _iter_lines(text: str) -> list[tuple[str, int, int]]:
    lines: list[tuple[str, int, int]] = []
    pos = 0
    for raw in text.split("\n"):
        lines.append((raw, pos, pos + len(raw)))
        pos += len(raw) + 1
    return lines


def _classify_line(stripped: str) -> str:
    if "\t" in stripped:
        return "table"
    if _LIST_LINE_RE.match(stripped):
        return "list"
    return "paragraph"


def _build_blocks(text: str) -> list[_Block]:
    section_stack: list[tuple[int, str]] = []
    current_clause: str | None = None
    blocks: list[_Block] = []

    pending: list[tuple[int, int, str]] = []  # (start, end, kind) of lines in block
    pending_heading_level: int | None = None

    def flush() -> None:
        nonlocal pending, pending_heading_level
        if not pending:
            return
        start = pending[0][0]
        end = pending[-1][1]
        kinds = {kind for _, _, kind in pending if kind != "heading"}
        if pending_heading_level is not None and not kinds:
            kind = "heading"
        elif len(kinds) == 1:
            kind = next(iter(kinds))
        else:
            kind = "paragraph"
        blocks.append(
            _Block(
                start=start,
                end=end,
                kind=kind,
                section_path=tuple(title for _, title in section_stack),
                clause_id=current_clause,
                heading_level=pending_heading_level,
            )
        )
        pending = []
        pending_heading_level = None

    for raw, start, _end in _iter_lines(text):
        stripped = raw.strip()
        if not stripped:
            flush()
            continue

        # Track offsets of the stripped content, not the raw padding.
        lead = len(raw) - len(raw.lstrip())
        line_start = start + lead
        line_end = line_start + len(stripped)

        heading = detect_heading(stripped)
        if heading is not None:
            flush()
            while section_stack and section_stack[-1][0] >= heading.level:
                section_stack.pop()
            section_stack.append((heading.level, heading.title))
            if heading.clause_id is not None:
                current_clause = heading.clause_id
            elif heading.level <= 2:
                current_clause = None
            pending.append((line_start, line_end, "heading"))
            pending_heading_level = heading.level
            continue

        pending.append((line_start, line_end, _classify_line(stripped)))

    flush()
    return blocks


def _split_oversized(
    text: str,
    block: _Block,
    config: ChunkerConfig,
    tokenizer: _Tokenizer,
) -> list[tuple[int, int]]:
    """Split one oversized block into (start, end) ranges, preferring sentence
    boundaries and hard-splitting only as a last resort."""
    body = text[block.start : block.end]
    boundaries = [m.end() for m in _SENTENCE_END_RE.finditer(body)]
    if not boundaries or boundaries[-1] != len(body):
        boundaries.append(len(body))

    ranges: list[tuple[int, int]] = []
    seg_start = 0
    prev = 0
    for boundary in boundaries:
        candidate = body[seg_start:boundary]
        if tokenizer.count(candidate) > config.max_tokens and prev > seg_start:
            ranges.append((seg_start, prev))
            seg_start = prev
        prev = boundary
    if seg_start < len(body):
        ranges.append((seg_start, len(body)))

    # Hard-split any range still exceeding max_tokens (single huge sentence).
    final: list[tuple[int, int]] = []
    for start, end in ranges:
        segment = body[start:end]
        if tokenizer.count(segment) <= config.max_tokens:
            final.append((start, end))
            continue
        points = tokenizer.hard_split_points(segment, config.max_tokens)
        prev_point = 0
        for point in [*points, len(segment)]:
            if point > prev_point:
                final.append((start + prev_point, start + point))
            prev_point = point

    return [(block.start + s, block.start + e) for s, e in final if body[s:e].strip()]


def normalize_for_hash(content: str) -> str:
    return re.sub(r"\s+", " ", content).strip()


def _trim_range(text: str, start: int, end: int) -> tuple[int, int]:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end


def _pages_for_range(
    spans: list[PageSpanIn] | None, start: int, end: int
) -> tuple[int | None, int | None]:
    if not spans:
        return None, None
    hit = [s.page for s in spans if s.char_start < end and s.char_end > start]
    if not hit:
        return None, None
    return min(hit), max(hit)


def _common_prefix(paths: list[tuple[str, ...]]) -> tuple[str, ...]:
    if not paths:
        return ()
    prefix = list(paths[0])
    for path in paths[1:]:
        i = 0
        while i < len(prefix) and i < len(path) and prefix[i] == path[i]:
            i += 1
        prefix = prefix[:i]
    return tuple(prefix)


@dataclass
class _Core:
    start: int
    end: int
    top: str | None  # top-level section (chapter); chunks never cross it
    paths: list[tuple[str, ...]] = field(default_factory=list)
    clauses: list[str | None] = field(default_factory=list)
    heading_level: int | None = None
    kinds: set[str] = field(default_factory=set)

    @property
    def section_path(self) -> tuple[str, ...]:
        return _common_prefix(self.paths) if self.paths else ()

    @property
    def clause_id(self) -> str | None:
        # First clause actually covered by this chunk (chunks may span several).
        return next((c for c in self.clauses if c is not None), None)


def build_chunks(
    text: str,
    *,
    page_spans: list[PageSpanIn] | None = None,
    config: ChunkerConfig | None = None,
) -> ChunkPlanResult:
    config = config or ChunkerConfig()
    tokenizer = get_tokenizer()
    blocks = _build_blocks(text)

    section_paths_seen = {b.section_path for b in blocks if b.section_path}

    def _core_from_block(block: _Block, start: int, end: int) -> _Core:
        return _Core(
            start=start,
            end=end,
            top=block.section_path[0] if block.section_path else None,
            paths=[block.section_path],
            clauses=[block.clause_id],
            heading_level=block.heading_level,
            kinds={block.kind},
        )

    # Phase 1: greedy assembly. Sub-sections may merge, chapters never mix.
    cores: list[_Core] = []
    buffer: _Core | None = None
    buffer_tokens = 0

    def flush_buffer() -> None:
        nonlocal buffer, buffer_tokens
        if buffer is not None:
            cores.append(buffer)
        buffer = None
        buffer_tokens = 0

    for block in blocks:
        block_text = text[block.start : block.end]
        if not block_text.strip():
            continue
        block_tokens = tokenizer.count(block_text)
        block_top = block.section_path[0] if block.section_path else None

        if buffer is not None and buffer.top != block_top:
            flush_buffer()

        if block_tokens > config.max_tokens:
            flush_buffer()
            cores.extend(
                _core_from_block(block, start, end)
                for start, end in _split_oversized(text, block, config, tokenizer)
            )
            continue

        if buffer is not None and buffer_tokens + block_tokens > config.max_tokens:
            flush_buffer()

        if buffer is None:
            buffer = _core_from_block(block, block.start, block.end)
            buffer_tokens = block_tokens
        else:
            buffer.end = block.end
            buffer.paths.append(block.section_path)
            buffer.clauses.append(block.clause_id)
            buffer.kinds.add(block.kind)
            buffer_tokens += block_tokens

        if buffer_tokens >= config.target_tokens:
            flush_buffer()

    flush_buffer()

    # Phase 2: merge undersized cores into a same-chapter neighbor when the
    # merge stays within max_tokens. Headings and stub cores prefer merging
    # forward (a heading belongs to the content it introduces).
    merged: list[_Core] = []
    index = 0
    while index < len(cores):
        core = cores[index]
        core.start, core.end = _trim_range(text, core.start, core.end)
        if core.end <= core.start:
            index += 1
            continue
        core_tokens = tokenizer.count(text[core.start : core.end])
        if core_tokens < config.min_tokens:
            nxt = cores[index + 1] if index + 1 < len(cores) else None
            if (
                nxt is not None
                and nxt.top == core.top
                and tokenizer.count(text[core.start : nxt.end]) <= config.max_tokens
            ):
                nxt.start = core.start
                nxt.paths = core.paths + nxt.paths
                nxt.clauses = core.clauses + nxt.clauses
                nxt.kinds |= core.kinds
                if nxt.heading_level is None:
                    nxt.heading_level = core.heading_level
                index += 1
                continue
            if (
                merged
                and merged[-1].top == core.top
                and tokenizer.count(text[merged[-1].start : core.end]) <= config.max_tokens
            ):
                merged[-1].end = core.end
                merged[-1].paths.extend(core.paths)
                merged[-1].clauses.extend(core.clauses)
                merged[-1].kinds |= core.kinds
                index += 1
                continue
        merged.append(core)
        index += 1

    # Phase 3: apply overlap from the previous same-chapter chunk and emit.
    chunks: list[PlannedChunk] = []
    total_tokens = 0

    for index, core in enumerate(merged):
        overlap_start = core.start
        if config.overlap_tokens > 0 and index > 0 and merged[index - 1].top == core.top:
            prev = merged[index - 1]
            prev_text = text[prev.start : prev.end]
            tail_len = tokenizer.tail_chars(prev_text, config.overlap_tokens)
            overlap_start = max(prev.end - tail_len, prev.start)

        content = text[overlap_start : core.end]
        if not content.strip():
            continue
        token_count = tokenizer.count(content)
        page_start, page_end = _pages_for_range(page_spans, core.start, core.end)
        section_path = core.section_path
        section = section_path[-1] if section_path else None

        kinds = core.kinds - {"heading"}
        chunk_kind = next(iter(kinds)) if len(kinds) == 1 else ("mixed" if kinds else "heading")

        chunks.append(
            PlannedChunk(
                chunk_index=len(chunks),
                content=content,
                content_hash=hashlib.sha256(
                    normalize_for_hash(content).encode("utf-8")
                ).hexdigest(),
                token_count=token_count,
                section=section,
                clause_id=core.clause_id,
                section_path=list(section_path),
                heading_level=core.heading_level,
                chunk_kind=chunk_kind,
                source_char_start=overlap_start,
                source_char_end=core.end,
                core_char_start=core.start,
                core_char_end=core.end,
                overlap_prefix_chars=core.start - overlap_start,
                page_start=page_start,
                page_end=page_end,
            )
        )
        total_tokens += token_count

    return ChunkPlanResult(
        chunks=chunks,
        tokenizer=tokenizer.name,
        section_count=len(section_paths_seen),
        total_tokens=total_tokens,
    )
