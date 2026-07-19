from __future__ import annotations

import hashlib

from app.services.chunker import (
    ChunkerConfig,
    PageSpanIn,
    build_chunks,
    detect_heading,
    normalize_for_hash,
)

SAMPLE_TENDER = """第一章 招标公告

一、项目概况
本项目为市政道路改造工程，采购人为某市住建局。项目位于城东片区，全长约三点五公里。
本次招标采用公开招标方式，欢迎符合条件的投标人参加。

二、投标人资格要求
（一）具有独立法人资格，持有有效的营业执照。
（二）具备市政公用工程施工总承包贰级及以上资质。
（三）近三年内无重大安全事故记录。

第二章 投标人须知

第一条 投标文件的组成
投标文件应包括商务标、技术标和资格证明文件三部分。商务标应当包含投标函、开标一览表和已标价工程量清单。
技术标应当包含施工组织设计、项目管理机构配备情况和拟投入的主要施工机械设备。

第二条 投标截止时间
投标文件应于规定时间前递交至指定地点，逾期送达的投标文件恕不接受。

第三章 评标办法

1.1 评标原则
评标委员会按照公平、公正、科学、择优的原则进行评审。
1.2 评分标准
商务标占百分之三十，技术标占百分之六十，信用评价占百分之十。
"""


def test_heading_detection():
    assert detect_heading("第一章 招标公告").level == 1
    assert detect_heading("一、项目概况").level == 2
    assert detect_heading("（一）具有独立法人资格，持有有效的营业执照。") is None  # 句子不是标题
    assert detect_heading("（二）资质要求").level == 3
    clause = detect_heading("第一条 投标文件的组成")
    assert clause is not None and clause.clause_id == "第一条"
    num = detect_heading("1.1 评标原则")
    assert num is not None and num.clause_id == "1.1"
    assert detect_heading("评分办法") is not None
    assert detect_heading("这是一个普通的长句子，用来描述项目的具体内容和实施方案。") is None


def test_multi_section_text_yields_stable_chunks():
    config = ChunkerConfig(min_tokens=10, target_tokens=60, max_tokens=120, overlap_tokens=10)
    result = build_chunks(SAMPLE_TENDER, config=config)

    assert len(result.chunks) >= 3
    assert result.section_count >= 3
    assert result.total_tokens == sum(c.token_count for c in result.chunks)

    for i, chunk in enumerate(result.chunks):
        assert chunk.chunk_index == i
        assert chunk.content.strip()
        assert chunk.token_count > 0
        expected_hash = hashlib.sha256(
            normalize_for_hash(chunk.content).encode("utf-8")
        ).hexdigest()
        assert chunk.content_hash == expected_hash
        # Provenance: the source range must reproduce the content exactly.
        assert SAMPLE_TENDER[chunk.source_char_start : chunk.source_char_end] == chunk.content
        assert chunk.core_char_start - chunk.source_char_start == chunk.overlap_prefix_chars
        # No pages for plain text.
        assert chunk.page_start is None and chunk.page_end is None

    # Determinism: rebuilding produces identical output.
    again = build_chunks(SAMPLE_TENDER, config=config)
    assert [(c.content, c.content_hash) for c in again.chunks] == [
        (c.content, c.content_hash) for c in result.chunks
    ]


def test_sections_and_clauses_are_detected():
    config = ChunkerConfig(min_tokens=10, target_tokens=60, max_tokens=120, overlap_tokens=0)
    result = build_chunks(SAMPLE_TENDER, config=config)

    sections = {c.section for c in result.chunks if c.section}
    assert any("资格" in s or "招标公告" in s or "须知" in s or "评标" in s for s in sections)

    clause_ids = {c.clause_id for c in result.chunks if c.clause_id}
    assert clause_ids & {"第一条", "第二条", "1.1", "1.2"}

    # section_path is hierarchical: chapter first.
    for chunk in result.chunks:
        if len(chunk.section_path) >= 2:
            assert chunk.section_path[0].startswith("第")


def test_oversized_paragraph_is_split_within_max_tokens():
    long_paragraph = "本条款用于验证超长段落的拆分行为。" * 300
    config = ChunkerConfig(min_tokens=10, target_tokens=100, max_tokens=150, overlap_tokens=0)
    result = build_chunks(long_paragraph, config=config)

    assert len(result.chunks) > 1
    for chunk in result.chunks:
        assert chunk.token_count <= config.max_tokens
        assert long_paragraph[chunk.source_char_start : chunk.source_char_end] == chunk.content


def test_final_token_count_never_exceeds_max_tokens():
    """Core text near max_tokens must not blow past the limit once the
    overlap prefix is added; the overlap budget shrinks instead."""
    long_text = "第一章 总则\n" + "投标文件应当按照招标文件的要求编制并如实填写全部内容。" * 200
    config = ChunkerConfig(min_tokens=10, target_tokens=100, max_tokens=100, overlap_tokens=80)
    result = build_chunks(long_text, config=config)

    assert len(result.chunks) > 2
    for chunk in result.chunks:
        assert chunk.token_count <= config.max_tokens, (
            f"chunk {chunk.chunk_index} has {chunk.token_count} tokens "
            f"(overlap {chunk.overlap_prefix_chars} chars)"
        )
    # Overlap should still exist where the budget allows it.
    default_result = build_chunks(SAMPLE_TENDER, config=ChunkerConfig())
    for chunk in default_result.chunks:
        assert chunk.token_count <= ChunkerConfig().max_tokens


def test_overlap_only_between_same_chapter_neighbors():
    config = ChunkerConfig(min_tokens=5, target_tokens=40, max_tokens=80, overlap_tokens=15)
    result = build_chunks(SAMPLE_TENDER, config=config)

    tops = []
    for chunk in result.chunks:
        tops.append(chunk.section_path[0] if chunk.section_path else None)

    for i, chunk in enumerate(result.chunks):
        if chunk.overlap_prefix_chars > 0:
            assert i > 0, "first chunk can never have overlap"
            assert tops[i] == tops[i - 1], "overlap must stay within the same chapter"
            prev = result.chunks[i - 1]
            # The overlap prefix is the tail of the previous chunk's core text
            # (plus the original separating whitespace, fully traceable).
            prefix = chunk.content[: chunk.overlap_prefix_chars]
            assert SAMPLE_TENDER[chunk.source_char_start : chunk.core_char_start] == prefix
            assert prev.content.endswith(prefix.strip())

    # Chapter boundaries: chunks at a new chapter start have no overlap.
    for i in range(1, len(result.chunks)):
        if tops[i] != tops[i - 1]:
            assert result.chunks[i].overlap_prefix_chars == 0


def test_page_mapping_uses_real_spans():
    text = "第一页的招标内容较为简短。\n第二页包含投标人资格要求等重要内容。\n第三页为附件说明。"
    # Honest spans built from the actual string layout.
    first_end = text.index("\n")
    second_end = text.index("\n", first_end + 1)
    spans = [
        PageSpanIn(page=1, char_start=0, char_end=first_end),
        PageSpanIn(page=2, char_start=first_end + 1, char_end=second_end),
        PageSpanIn(page=3, char_start=second_end + 1, char_end=len(text)),
    ]
    config = ChunkerConfig(min_tokens=1, target_tokens=10, max_tokens=30, overlap_tokens=0)
    result = build_chunks(text, page_spans=spans, config=config)

    assert result.chunks, "expected at least one chunk"
    for chunk in result.chunks:
        assert chunk.page_start is not None and chunk.page_end is not None
        assert 1 <= chunk.page_start <= chunk.page_end <= 3
        # Verify the page range really covers the chunk's core characters.
        covering = [
            s.page
            for s in spans
            if s.char_start < chunk.core_char_end and s.char_end > chunk.core_char_start
        ]
        assert chunk.page_start == min(covering)
        assert chunk.page_end == max(covering)

    without_pages = build_chunks(text, page_spans=None, config=config)
    assert all(c.page_start is None and c.page_end is None for c in without_pages.chunks)


def test_no_fabricated_sections_for_plain_prose():
    prose = "这是一段没有任何标题结构的普通文字。它描述了一些背景信息。\n\n这是第二段普通文字。"
    config = ChunkerConfig(min_tokens=1, target_tokens=50, max_tokens=100)
    result = build_chunks(prose, config=config)
    for chunk in result.chunks:
        assert chunk.section is None
        assert chunk.section_path == []
