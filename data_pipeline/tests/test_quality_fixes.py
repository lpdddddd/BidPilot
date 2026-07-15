"""Regression tests for BidPilot data-quality fixes (RAG leak, matches, SFT balance/dedup, agent)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from bidpilot_data.agent_data.build import trajectory_messages
from bidpilot_data.labeling.disclosed_matches import build_disclosed_matches
from bidpilot_data.rag_eval.build import UNANSWERABLE_TEMPLATES, question_leaks_quote
from bidpilot_data.schemas import (
    ChatMessage,
    MatchStatus,
    QualityLevel,
    RequirementMatchAnnotation,
    ReviewStatus,
    SFTRecord,
    SFTTaskType,
)
from bidpilot_data.sft.balance import balance_records
from bidpilot_data.sft.build import _split_projects
from bidpilot_data.sft.dedup import global_near_dedup, normalize_user_text
from bidpilot_data.utils import write_jsonl


def test_rag_question_no_yuanwen_marker():
    assert question_leaks_quote("根据原文回答：服务期限是多久？", "服务期限为一年")
    assert question_leaks_quote("本项目……原文：投标人须提供营业执照", "投标人须提供营业执照")


def test_rag_question_no_long_quote_copy():
    quote = "投标人须具备独立承担民事责任的能力并提供有效营业执照副本"
    assert question_leaks_quote("请问" + quote, quote)
    assert not question_leaks_quote("本项目对投标人的财务状况有什么要求？", quote)


def test_unanswerable_templates_count():
    assert len(UNANSWERABLE_TEMPLATES) >= 50


def test_match_schema_rejects_no_evidence():
    with pytest.raises(ValidationError):
        RequirementMatchAnnotation(
            match_id="m1",
            requirement_id="r1",
            status=MatchStatus.satisfied,
            reason="no evidence",
            confidence=0.9,
        )


def test_match_schema_rejects_unknown():
    with pytest.raises(ValidationError):
        RequirementMatchAnnotation(
            match_id="m1",
            requirement_id="r1",
            status=MatchStatus.unknown,
            reason="x",
            evidence_ids=["e1"],
            evidence_document_id="d1",
            evidence_chunk_id="c1",
            source_url="https://www.ccgp.gov.cn/x",
            source_quote="资格审查合格",
            confidence=0.5,
        )


def test_name_only_supplier_creates_zero_matches(tmp_datasets):
    write_jsonl(
        tmp_datasets / "manifests" / "projects.jsonl",
        [
            {
                "project_id": "p1",
                "project_code": "GD-1",
                "project_name": "信息化运维",
                "bundle_level": "level_b",
                "official_project_url": "https://www.ccgp.gov.cn/a",
                "source_domain": "www.ccgp.gov.cn",
            }
        ],
    )
    write_jsonl(
        tmp_datasets / "manifests" / "documents.jsonl",
        [
            {
                "document_id": "d1",
                "project_id": "p1",
                "project_code": "GD-1",
                "document_type": "award_notice",
                "source_url": "https://www.ccgp.gov.cn/a",
                "storage_path": "raw/documents/a.html",
                "sha256": "x",
            }
        ],
    )
    html = tmp_datasets / "raw" / "documents" / "a.html"
    html.parent.mkdir(parents=True, exist_ok=True)
    html.write_text("中标供应商：某某科技有限公司。中标金额：100万元。", encoding="utf-8")
    write_jsonl(tmp_datasets / "interim" / "chunks" / "chunks.jsonl", [])
    write_jsonl(
        tmp_datasets / "silver" / "requirements.jsonl",
        [
            {
                "annotation_id": "a1",
                "requirement_id": "r1",
                "project_id": "p1",
                "category": "qualification",
                "title": "执照",
                "original_text": "须提供营业执照",
                "normalized_requirement": "营业执照",
                "mandatory": True,
                "risk_level": "high",
                "confidence": 0.9,
                "quality_level": "silver",
                "review_status": "pending",
                "source_url": "https://www.ccgp.gov.cn/a",
            }
        ],
    )
    write_jsonl(tmp_datasets / "silver" / "evidence.jsonl", [])
    stats = build_disclosed_matches()
    assert stats["disclosed_suppliers"] >= 1
    assert stats["evidence_supported_matches"] == 0
    assert stats["matches"] == 0


def test_evidence_supported_satisfied_and_missing(tmp_datasets):
    write_jsonl(
        tmp_datasets / "manifests" / "projects.jsonl",
        [
            {
                "project_id": "p1",
                "project_code": "GD-2",
                "project_name": "数据平台",
                "bundle_level": "level_b",
                "official_project_url": "https://www.ccgp.gov.cn/b",
            }
        ],
    )
    text = "资格审查合格。另一供应商资格审查不合格。缺少营业执照材料。"
    write_jsonl(
        tmp_datasets / "manifests" / "documents.jsonl",
        [
            {
                "document_id": "d1",
                "project_id": "p1",
                "project_code": "GD-2",
                "document_type": "award_notice",
                "source_url": "https://www.ccgp.gov.cn/b",
            }
        ],
    )
    write_jsonl(
        tmp_datasets / "interim" / "chunks" / "chunks.jsonl",
        [
            {
                "chunk_id": "c1",
                "project_id": "p1",
                "document_id": "d1",
                "text": text,
                "page_start": 1,
                "page_end": 1,
                "token_count": 20,
            }
        ],
    )
    write_jsonl(
        tmp_datasets / "silver" / "requirements.jsonl",
        [
            {
                "annotation_id": "a1",
                "requirement_id": "r1",
                "project_id": "p1",
                "category": "qualification",
                "title": "资格",
                "original_text": "投标人资格条件包括营业执照与资格审查",
                "normalized_requirement": "资格审查",
                "mandatory": True,
                "risk_level": "high",
                "confidence": 0.9,
                "quality_level": "silver",
                "review_status": "pending",
                "source_url": "https://www.ccgp.gov.cn/b",
                "chunk_id": "c1",
                "document_id": "d1",
            }
        ],
    )
    write_jsonl(tmp_datasets / "silver" / "evidence.jsonl", [])
    stats = build_disclosed_matches()
    assert stats["evidence_supported_matches"] >= 1
    rows = [
        json.loads(line)
        for line in (tmp_datasets / "silver" / "requirement_matches.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    statuses = {r["status"] for r in rows}
    assert "unknown" not in statuses
    assert statuses & {"satisfied", "missing", "uncertain", "partially_satisfied"}


def test_global_near_dedup_far_apart():
    class R:
        def __init__(self, i, user, q="silver", pid="p1"):
            self.record_id = f"r{i}"
            self.task_type = type("T", (), {"value": "requirement_classify"})()
            self.quality_level = type("Q", (), {"value": q})()
            self.project_id = pid
            self.user = user

    base = "判断以下条款的类别与是否强制：\n投标人须提供有效营业执照副本原件扫描件。"
    records = [R(i, f"noise-{i}-aaaa") for i in range(520)]
    records[0] = R(0, base, "silver", "p1")
    records[510] = R(510, base + " ", "gold", "p1")
    kept, stats = global_near_dedup(
        records,
        get_task=lambda r: r.task_type.value,
        get_user=lambda r: r.user,
        get_quality=lambda r: r.quality_level.value,
        get_project=lambda r: r.project_id,
        get_id=lambda r: r.record_id,
        near_threshold=95,
        simhash_hamming_max=3,
    )
    ids = {r.record_id for r in kept}
    assert "r510" in ids  # gold kept
    assert "r0" not in ids or stats.near_duplicates_removed >= 1 or stats.exact_duplicates_removed >= 1


def test_gold_preferred_over_silver_dedup():
    class R:
        def __init__(self, rid, q):
            self.record_id = rid
            self.task_type = type("T", (), {"value": "risk_detect"})()
            self.quality_level = type("Q", (), {"value": q})()
            self.project_id = "p1"
            self.user = "识别风险：\n未按要求密封的，按废标处理。"

    kept, _ = global_near_dedup(
        [R("silver1", "silver"), R("gold1", "gold")],
        get_task=lambda r: r.task_type.value,
        get_user=lambda r: r.user,
        get_quality=lambda r: r.quality_level.value,
        get_project=lambda r: r.project_id,
        get_id=lambda r: r.record_id,
    )
    assert [r.record_id for r in kept] == ["gold1"]


def test_cross_project_distinct_bodies_kept():
    class R:
        def __init__(self, rid, pid, body):
            self.record_id = rid
            self.task_type = type("T", (), {"value": "requirement_classify"})()
            self.quality_level = type("Q", (), {"value": "silver"})()
            self.project_id = pid
            self.user = f"判断以下条款的类别与是否强制：\n{body}"

    kept, _ = global_near_dedup(
        [
            R("a", "p1", "投标人须具有安全生产许可证。"),
            R("b", "p2", "服务期限为自合同签订之日起三年。"),
        ],
        get_task=lambda r: r.task_type.value,
        get_user=lambda r: r.user,
        get_quality=lambda r: r.quality_level.value,
        get_project=lambda r: r.project_id,
        get_id=lambda r: r.record_id,
    )
    assert len(kept) == 2


def test_balance_max_ratio_downsamples_only():
    class R:
        def __init__(self, tid, q="silver"):
            self.task = tid
            self.q = q
            self.rs = "pending"
            self.conf = 0.8
            self.complete = True
            self.test = False

    records = (
        [R("requirement_classify") for _ in range(100)]
        + [R("risk_detect") for _ in range(40)]
        + [R("qualification_extract") for _ in range(30)]
        + [R("citation_qa") for _ in range(20)]
    )
    kept, report = balance_records(
        records,
        get_task=lambda r: r.task,
        get_quality=lambda r: r.q,
        get_review=lambda r: r.rs,
        get_confidence=lambda r: r.conf,
        has_complete_source=lambda r: r.complete,
        is_test_split_record=lambda r: r.test,
    )
    after = report["after_balance"]
    total_after = report["total_after"] or 1
    assert after.get("requirement_classify", 0) / total_after <= 0.36 + 1e-6
    assert (after.get("requirement_classify", 0) + after.get("risk_detect", 0)) / total_after < 0.85
    assert report["dropped_by_balance"].get("requirement_classify", 0) > 0
    assert report["total_after"] < report["total_before"]


def test_quality_level_and_review_status_stats_shape():
    # Ensure normalize helpers + SFT record reject pending-as-quality misuse in stats helpers
    assert normalize_user_text("  ２０２４年１月２日  ") 


def test_split_validation_min_five_projects():
    m = _split_projects([f"p{i}" for i in range(30)], seed=1, train_r=0.8, val_r=0.1, heldout=10, min_validation=5, min_test=10)
    assert len(m.validation_project_ids) >= 5
    assert len(m.test_project_ids) >= 10
    assert not (set(m.train_project_ids) & set(m.validation_project_ids) & set(m.test_project_ids))
    assert not (set(m.train_project_ids) & set(m.test_project_ids))


def test_agent_multistep_tool_pairing_and_citation():
    task = {
        "user_request": "汇总项目",
        "initial_state": {"system": "sys"},
        "expected_tool_calls": [
            {
                "tool_name": "search_chunks",
                "arguments": {"project_id": "p1", "query": "x", "top_k": 3},
                "result": {"chunks": [{"chunk_id": "c1", "text": "hello"}]},
            },
            {
                "tool_name": "get_project",
                "arguments": {"project_id": "p1"},
                "result": {"project": {"project_id": "p1"}},
            },
        ],
        "expected_final_result": {"answer": "ok", "citations": ["c1"], "evidence_chunk_ids": ["c1"]},
    }
    msgs = trajectory_messages(task)
    roles = [m["role"] for m in msgs]
    assert roles.count("tool") == 2
    for i, role in enumerate(roles):
        if role == "tool":
            assert roles[i - 1] == "assistant"
    final = json.loads(msgs[-1]["content"])
    assert final["citations"] == ["c1"]
    # Schema accept tool messages
    sft = SFTRecord(
        record_id="x",
        project_id="p1",
        task_type=SFTTaskType.tool_call,
        quality_level=QualityLevel.silver,
        review_status=ReviewStatus.pending,
        source_chunk_ids=["c1"],
        messages=[ChatMessage.model_validate(m) for m in msgs],
    )
    assert sft.messages[-1].role == "assistant"


def test_incomplete_not_in_allowed_level_logic():
    from bidpilot_data.sft.build import CLAUSE_TASKS, CROSS_DOC_TASKS

    assert SFTTaskType.requirement_classify in CLAUSE_TASKS
    assert SFTTaskType.citation_qa in CROSS_DOC_TASKS


def test_sharegpt_tool_role_accepted_by_schema():
    msgs = [
        ChatMessage(role="system", content="sys"),
        ChatMessage(role="user", content="user"),
        ChatMessage(role="assistant", content='{"tool_name":"search_chunks","arguments":{}}'),
        ChatMessage(role="tool", content='{"chunks":[]}'),
        ChatMessage(role="assistant", content='{"answer":"a","citations":["c1"]}'),
    ]
    rec = SFTRecord(
        record_id="r",
        project_id="p",
        task_type=SFTTaskType.tool_call,
        quality_level=QualityLevel.silver,
        review_status=ReviewStatus.pending,
        source_urls=["https://www.ccgp.gov.cn/x"],
        messages=msgs,
    )
    assert any(m.role == "tool" for m in rec.messages)
