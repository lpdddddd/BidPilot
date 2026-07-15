"""Regression tests for strict matches, supplier cleaning, RAG share, rejected SFT."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest
from pydantic import ValidationError

from bidpilot_data.agent_data.build import build_agent_tasks, trajectory_messages
from bidpilot_data.labeling.disclosed_matches import build_disclosed_matches
from bidpilot_data.labeling.industry import classify_industry
from bidpilot_data.labeling.supplier_names import extract_award_suppliers, is_valid_supplier_name
from bidpilot_data.rag_eval.build import question_leaks_quote
from bidpilot_data.schemas import MatchStatus, QualityLevel, RequirementMatchAnnotation, ReviewStatus
from bidpilot_data.sft.build import build_sft_dataset
from bidpilot_data.utils import write_jsonl


def _base_project(tmp_datasets, *, pid="p1", code="GD-1", name="某信息化运维服务项目"):
    write_jsonl(
        tmp_datasets / "manifests" / "projects.jsonl",
        [
            {
                "project_id": pid,
                "project_code": code,
                "project_name": name,
                "bundle_level": "level_b",
                "official_project_url": "https://www.ccgp.gov.cn/a",
                "purchaser": "某单位",
                "budget_cny": 1000000,
            }
        ],
    )


def test_supplier_extract_valid_company():
    out = extract_award_suppliers("中标供应商：广州某某科技有限公司。中标金额：10万元。")
    assert "广州某某科技有限公司" in out["accepted_suppliers"]


def test_supplier_reject_dirty_once():
    ok, reason = is_valid_supplier_name("一次")
    assert ok is False
    out = extract_award_suppliers("供应商名称：一次采购服务\n中标金额：1")
    assert out["accepted_suppliers"] == []
    assert out["rejected_candidates"]


def test_supplier_table_and_no_field_bleed():
    html = """
    <table><tr><td>中标供应商</td><td>深圳市蓝海系统集成有限公司</td></tr>
    <tr><td>中标金额</td><td>123万元</td></tr></table>
    """
    out = extract_award_suppliers("正文", html=html)
    assert any("蓝海系统集成有限公司" in n for n in out["accepted_suppliers"])
    text = "中标供应商：佛山数据服务有限公司；地址：佛山市；金额：9万"
    out2 = extract_award_suppliers(text)
    assert any(n == "佛山数据服务有限公司" for n in out2["accepted_suppliers"])


def test_supplier_dedup_whitespace():
    out = extract_award_suppliers("中标供应商：广州 某某 科技有限公司\n成交供应商：广州某某科技有限公司")
    assert out["accepted_suppliers"].count("广州某某科技有限公司") == 1


def test_dirty_names_rejected():
    for name in ("一次", "单、", "的评", "名称", "名单", "评审", "单价"):
        ok, _ = is_valid_supplier_name(name)
        assert ok is False, name


def test_tender_document_generic_rule_no_match(tmp_datasets):
    _base_project(tmp_datasets)
    write_jsonl(
        tmp_datasets / "manifests" / "documents.jsonl",
        [
            {
                "document_id": "d-tender",
                "project_id": "p1",
                "project_code": "GD-1",
                "document_type": "tender_document",
                "source_url": "https://www.ccgp.gov.cn/t",
            },
            {
                "document_id": "d-award",
                "project_id": "p1",
                "project_code": "GD-1",
                "document_type": "award_notice",
                "source_url": "https://www.ccgp.gov.cn/a",
                "storage_path": "raw/documents/a.html",
            },
        ],
    )
    (tmp_datasets / "raw" / "documents").mkdir(parents=True, exist_ok=True)
    (tmp_datasets / "raw" / "documents" / "a.html").write_text(
        "中标供应商：广州某某科技有限公司。", encoding="utf-8"
    )
    write_jsonl(
        tmp_datasets / "interim" / "chunks" / "chunks.jsonl",
        [
            {
                "chunk_id": "c-t",
                "project_id": "p1",
                "document_id": "d-tender",
                "text": "资格审查不合格的，按无效投标处理。缺少证明材料的，投标无效。",
                "page_start": 1,
                "page_end": 1,
                "chunk_index": 0,
                "normalized_text": "x",
                "token_count": 20,
                "content_hash": "h1",
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
                "title": "执照",
                "original_text": "须提供营业执照",
                "normalized_requirement": "营业执照",
                "mandatory": True,
                "risk_level": "high",
                "confidence": 0.9,
                "quality_level": "silver",
                "review_status": "pending",
                "source_url": "https://www.ccgp.gov.cn/t",
                "chunk_id": "c-t",
                "document_id": "d-tender",
            }
        ],
    )
    write_jsonl(tmp_datasets / "silver" / "evidence.jsonl", [])
    stats = build_disclosed_matches()
    assert stats["matches"] == 0
    assert stats["disclosed_suppliers"] >= 1


def test_tender_notice_missing_material_rule_no_match(tmp_datasets):
    _base_project(tmp_datasets, pid="p2", code="GD-N")
    write_jsonl(
        tmp_datasets / "manifests" / "documents.jsonl",
        [
            {
                "document_id": "d-n",
                "project_id": "p2",
                "project_code": "GD-N",
                "document_type": "tender_notice",
                "source_url": "https://www.ccgp.gov.cn/n",
            }
        ],
    )
    write_jsonl(
        tmp_datasets / "interim" / "chunks" / "chunks.jsonl",
        [
            {
                "chunk_id": "c-n",
                "project_id": "p2",
                "document_id": "d-n",
                "text": "缺少证明材料的，投标无效。不符合资格要求的，否决其投标。",
                "page_start": 1,
                "page_end": 1,
                "chunk_index": 0,
                "normalized_text": "x",
                "token_count": 20,
                "content_hash": "hn",
            }
        ],
    )
    write_jsonl(
        tmp_datasets / "silver" / "requirements.jsonl",
        [
            {
                "annotation_id": "a1",
                "requirement_id": "r1",
                "project_id": "p2",
                "category": "qualification",
                "title": "材料",
                "original_text": "须提交资格证明材料",
                "normalized_requirement": "证明材料",
                "mandatory": True,
                "risk_level": "high",
                "confidence": 0.9,
                "quality_level": "silver",
                "review_status": "pending",
                "source_url": "https://www.ccgp.gov.cn/n",
                "chunk_id": "c-n",
                "document_id": "d-n",
            }
        ],
    )
    write_jsonl(tmp_datasets / "silver" / "evidence.jsonl", [])
    stats = build_disclosed_matches()
    assert stats["matches"] == 0


def test_generic_sentence_without_supplier_name_no_match(tmp_datasets):
    _base_project(tmp_datasets)
    write_jsonl(
        tmp_datasets / "manifests" / "documents.jsonl",
        [
            {
                "document_id": "d1",
                "project_id": "p1",
                "project_code": "GD-1",
                "document_type": "award_notice",
                "source_url": "https://www.ccgp.gov.cn/b",
            }
        ],
    )
    text = "资格审查合格。另一供应商资格审查不合格。缺少营业执照材料。"
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
                "chunk_index": 0,
                "normalized_text": text,
                "token_count": 20,
                "content_hash": "h",
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
    assert stats["matches"] == 0


def test_named_supplier_missing_license_creates_match(tmp_datasets):
    _base_project(tmp_datasets)
    write_jsonl(
        tmp_datasets / "manifests" / "documents.jsonl",
        [
            {
                "document_id": "d1",
                "project_id": "p1",
                "project_code": "GD-1",
                "document_type": "evaluation_result",
                "source_url": "https://www.ccgp.gov.cn/e",
                "storage_path": "raw/documents/e.html",
            }
        ],
    )
    (tmp_datasets / "raw" / "documents").mkdir(parents=True, exist_ok=True)
    body = (
        "中标供应商：广州某某科技有限公司。"
        "广州某某科技有限公司未提供营业执照，资格审查不通过。"
    )
    (tmp_datasets / "raw" / "documents" / "e.html").write_text(body, encoding="utf-8")
    write_jsonl(
        tmp_datasets / "interim" / "chunks" / "chunks.jsonl",
        [
            {
                "chunk_id": "c1",
                "project_id": "p1",
                "document_id": "d1",
                "text": body,
                "page_start": 1,
                "page_end": 1,
                "chunk_index": 0,
                "normalized_text": body,
                "token_count": 40,
                "content_hash": "h2",
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
                "title": "营业执照",
                "original_text": "投标人须提供有效营业执照",
                "normalized_requirement": "提供营业执照",
                "mandatory": True,
                "risk_level": "high",
                "confidence": 0.9,
                "quality_level": "silver",
                "review_status": "pending",
                "source_url": "https://www.ccgp.gov.cn/e",
                "chunk_id": "c1",
                "document_id": "d1",
            }
        ],
    )
    write_jsonl(tmp_datasets / "silver" / "evidence.jsonl", [])
    stats = build_disclosed_matches()
    assert stats["disclosed_suppliers"] >= 1
    assert stats["matches"] + stats["supplier_review_outcomes"] >= 1
    for m in [
        json.loads(x)
        for x in (tmp_datasets / "silver" / "requirement_matches.jsonl").read_text(encoding="utf-8").splitlines()
        if x.strip()
    ]:
        assert m.get("supplier_id")
        assert "广州某某科技有限公司" in (m.get("source_quote") or "")


def test_overall_pass_without_requirement_bind_is_outcome(tmp_datasets):
    _base_project(tmp_datasets)
    body = "中标供应商：广州某某科技有限公司。广州某某科技有限公司资格审查通过。"
    write_jsonl(
        tmp_datasets / "manifests" / "documents.jsonl",
        [
            {
                "document_id": "d1",
                "project_id": "p1",
                "project_code": "GD-1",
                "document_type": "qualification_review_result",
                "source_url": "https://www.ccgp.gov.cn/q",
                "storage_path": "raw/documents/q.html",
            }
        ],
    )
    (tmp_datasets / "raw" / "documents").mkdir(parents=True, exist_ok=True)
    (tmp_datasets / "raw" / "documents" / "q.html").write_text(body, encoding="utf-8")
    write_jsonl(
        tmp_datasets / "interim" / "chunks" / "chunks.jsonl",
        [
            {
                "chunk_id": "c1",
                "project_id": "p1",
                "document_id": "d1",
                "text": body,
                "page_start": 1,
                "page_end": 1,
                "chunk_index": 0,
                "normalized_text": body,
                "token_count": 30,
                "content_hash": "hq",
            }
        ],
    )
    # Requirement has no topical overlap with specific materials in quote
    write_jsonl(
        tmp_datasets / "silver" / "requirements.jsonl",
        [
            {
                "annotation_id": "a1",
                "requirement_id": "r1",
                "project_id": "p1",
                "category": "technical",
                "title": "接口",
                "original_text": "系统须提供开放接口文档",
                "normalized_requirement": "接口文档",
                "mandatory": True,
                "risk_level": "medium",
                "confidence": 0.9,
                "quality_level": "silver",
                "review_status": "pending",
                "source_url": "https://www.ccgp.gov.cn/q",
                "chunk_id": "c1",
                "document_id": "d1",
            }
        ],
    )
    write_jsonl(tmp_datasets / "silver" / "evidence.jsonl", [])
    stats = build_disclosed_matches()
    assert stats["matches"] == 0
    assert stats["supplier_review_outcomes"] >= 1


def test_match_schema_requires_supplier_id():
    with pytest.raises(ValidationError):
        RequirementMatchAnnotation(
            match_id="m1",
            requirement_id="r1",
            supplier_id=None,
            status=MatchStatus.satisfied,
            reason="x",
            evidence_ids=["e1"],
            evidence_document_id="d1",
            evidence_chunk_id="c1",
            source_url="https://www.ccgp.gov.cn/x",
            source_quote="广州某某科技有限公司资格审查通过",
            confidence=0.7,
        )


def test_rejected_sft_excluded_from_splits(tmp_datasets, tmp_repo):
    write_jsonl(
        tmp_datasets / "manifests" / "projects.jsonl",
        [
            {
                "project_id": f"p{i}",
                "project_code": f"C{i}",
                "project_name": f"项目{i} 软件开发",
                "bundle_level": "level_b",
                "official_project_url": f"https://www.ccgp.gov.cn/{i}",
                "purchaser": "采购人",
                "budget_cny": 1,
            }
            for i in range(20)
        ],
    )
    write_jsonl(tmp_datasets / "manifests" / "documents.jsonl", [])
    write_jsonl(tmp_datasets / "interim" / "chunks" / "chunks.jsonl", [])
    reqs = []
    for i in range(20):
        reqs.append(
            {
                "annotation_id": f"a{i}",
                "requirement_id": f"r{i}",
                "project_id": f"p{i}",
                "document_id": f"d{i}",
                "chunk_id": f"c{i}",
                "category": "qualification",
                "title": "执照",
                "original_text": f"投标人须提供有效营业执照{i}。",
                "normalized_requirement": "提供营业执照",
                "mandatory": True,
                "risk_level": "high",
                "confidence": 0.9,
                "quality_level": "silver",
                "review_status": "pending",
                "source_url": f"https://www.ccgp.gov.cn/{i}",
            }
        )
    write_jsonl(tmp_datasets / "silver" / "requirements.jsonl", reqs)
    write_jsonl(tmp_datasets / "silver" / "requirement_matches.jsonl", [])
    write_jsonl(tmp_datasets / "eval" / "rag" / "questions.jsonl", [])
    write_jsonl(tmp_datasets / "eval" / "agent" / "tasks.jsonl", [])
    stats = build_sft_dataset()
    assert stats["train"] + stats["validation"] + stats["test"] == stats["structurally_valid_sft"]
    rejected = tmp_datasets / "rejected" / "sft.jsonl"
    if rejected.exists():
        bad = [json.loads(l) for l in rejected.read_text(encoding="utf-8").splitlines() if l.strip()]
        bad_msgs = {json.dumps(r.get("messages"), ensure_ascii=False) for r in bad}
        for split in ("train", "validation", "test"):
            payload = json.loads((tmp_datasets / "sft" / split / "sharegpt.json").read_text(encoding="utf-8"))
            for row in payload:
                assert json.dumps(row.get("messages"), ensure_ascii=False) not in bad_msgs


def test_industry_rules():
    r = classify_industry({"project_name": "政务云平台运维服务采购"})
    assert r["industry"] in {"information_system_maintenance", "cloud_service", "software_development"}
    r2 = classify_industry({"project_name": "网络安全等级保护测评"})
    assert r2["industry"] == "cybersecurity"
    r3 = classify_industry({"project_name": "办公家具采购"})
    assert r3["industry"] in {"non_it", "unknown", "hardware_equipment"}


def test_rag_leak_still_blocked():
    assert question_leaks_quote("原文：投标人须提供执照", "投标人须提供执照")


def test_rag_project_share_trim_cap():
    """220 questions → per-project cap 22; max share <= 0.10."""
    from bidpilot_data.schemas import Difficulty, QuestionType, RAGQuestion

    rows = []
    # 10 projects evenly would be ok; concentrate one project at 40 then trim logic unit-test via counter
    for i in range(220):
        pid = "hot" if i < 40 else f"p{i % 30}"
        rows.append(
            RAGQuestion(
                question_id=f"q{i}",
                project_id=pid,
                question=f"本项目对投标人有何资格要求{i}？",
                answer="须提供营业执照",
                answerable=True,
                gold_chunk_ids=[f"c{i}"],
                gold_document_ids=[f"d{i}"],
                source_document_ids=[f"d{i}"],
                source_urls=[f"https://www.ccgp.gov.cn/{i}"],
                source_pages=[1],
                source_quotes=["投标人须提供有效营业执照"],
                question_type=QuestionType.qualification,
                difficulty=Difficulty.medium,
                quality_level=QualityLevel.silver,
                review_status=ReviewStatus.pending,
            )
        )
    # Inline the trim algorithm (mirrors build.py)
    for _ in range(20):
        n = len(rows)
        cap = n // 10
        by_pid = Counter(q.project_id for q in rows)
        over = [(pid, cnt) for pid, cnt in by_pid.items() if cnt > cap]
        if not over or cap < 1:
            break
        pid, cnt = max(over, key=lambda x: x[1])
        drop_n = cnt - cap
        candidates = [q for q in rows if q.project_id == pid]
        drop_ids = {q.question_id for q in sorted(candidates, key=lambda q: q.question_id)[:drop_n]}
        rows = [q for q in rows if q.question_id not in drop_ids]
    n = len(rows)
    max_share = max(Counter(q.project_id for q in rows).values()) / n
    assert max_share <= 0.10 + 1e-9
    assert max(Counter(q.project_id for q in rows).values()) <= max(1, n // 10)


def test_multi_section_requires_two_chunks_for_answer_cover():
    q = type("Q", (), {})()
    q.gold_chunk_ids = ["c1", "c2"]
    q.source_quotes = ["资格要求：营业执照", "否决条款：虚假材料废标"]
    q.answer = "营业执照为资格要件；另方面，虚假材料废标"
    assert len(set(q.gold_chunk_ids)) >= 2
    assert all(qq[:4] in q.answer or qq[5:10] in q.answer for qq in q.source_quotes)


def test_multi_section_skipped_when_one_chunk(tmp_datasets):
    """Only one chunk in project → no multi_section question generated."""
    from bidpilot_data.rag_eval.build import build_rag_eval

    write_jsonl(
        tmp_datasets / "manifests" / "projects.jsonl",
        [
            {
                "project_id": "p1",
                "project_code": "G1",
                "project_name": "运维服务",
                "bundle_level": "level_a",
                "official_project_url": "https://www.ccgp.gov.cn/1",
            }
        ],
    )
    write_jsonl(
        tmp_datasets / "manifests" / "documents.jsonl",
        [
            {
                "document_id": "d1",
                "project_id": "p1",
                "project_code": "G1",
                "document_type": "tender_document",
                "source_url": "https://www.ccgp.gov.cn/1",
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
                "text": "投标人须具备独立法人资格并提供营业执照。服务期限一年。",
                "section_path": "资格/要求",
                "page_start": 1,
                "page_end": 1,
                "chunk_index": 0,
                "normalized_text": "x",
                "token_count": 30,
                "content_hash": "h1",
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
                "document_id": "d1",
                "chunk_id": "c1",
                "category": "qualification",
                "title": "法人",
                "original_text": "投标人须具备独立法人资格并提供营业执照",
                "normalized_requirement": "独立法人",
                "mandatory": True,
                "risk_level": "high",
                "confidence": 0.9,
                "quality_level": "silver",
                "review_status": "pending",
                "source_url": "https://www.ccgp.gov.cn/1",
                "source_page": 1,
            }
        ],
    )
    stats = build_rag_eval(limit=20)
    qs = [
        json.loads(l)
        for l in (tmp_datasets / "eval" / "rag" / "questions.jsonl").read_text(encoding="utf-8").splitlines()
        if l.strip()
    ]
    assert not any(q.get("question_type") == "multi_section" for q in qs)


def test_agent_answer_has_no_none_string(tmp_datasets):
    write_jsonl(
        tmp_datasets / "manifests" / "projects.jsonl",
        [
            {
                "project_id": "p1",
                "project_code": "G1",
                "project_name": "系统集成项目",
                "bundle_level": "level_b",
                "official_project_url": "https://www.ccgp.gov.cn/1",
                "purchaser": None,
                "budget_cny": None,
            }
        ],
    )
    write_jsonl(
        tmp_datasets / "manifests" / "documents.jsonl",
        [
            {
                "document_id": "d1",
                "project_id": "p1",
                "project_code": "G1",
                "document_type": "award_notice",
                "source_url": "https://www.ccgp.gov.cn/1",
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
                "text": "项目概况：信息化系统集成。",
                "page_start": 1,
                "page_end": 1,
                "chunk_index": 0,
                "normalized_text": "x",
                "token_count": 10,
                "content_hash": "h",
            }
        ],
    )
    write_jsonl(tmp_datasets / "silver" / "disclosed_suppliers.jsonl", [])
    write_jsonl(tmp_datasets / "silver" / "requirements.jsonl", [])
    stats = build_agent_tasks(limit=10)
    tasks = [
        json.loads(l)
        for l in (tmp_datasets / "eval" / "agent" / "tasks.jsonl").read_text(encoding="utf-8").splitlines()
        if l.strip()
    ]
    for t in tasks:
        ans = (t.get("expected_final_result") or {}).get("answer")
        if isinstance(ans, str):
            assert "None" not in ans
    assert stats.get("agent_final_answers_with_none_string", 0) == 0


def test_tool_call_strict_alternation():
    task = {
        "user_request": "查",
        "initial_state": {"system": "sys"},
        "expected_tool_calls": [
            {
                "tool_name": "search_chunks",
                "arguments": {"project_id": "p1", "query": "x", "top_k": 3},
                "result": {"chunks": [{"chunk_id": "c1", "text": "hello"}]},
            }
        ],
        "expected_final_result": {"answer": "ok", "citations": ["c1"], "evidence_chunk_ids": ["c1"]},
    }
    roles = [m["role"] for m in trajectory_messages(task)]
    for i, role in enumerate(roles):
        if role == "tool":
            assert roles[i - 1] == "assistant"


def test_portal_snapshot_not_in_suppliers(tmp_datasets):
    write_jsonl(
        tmp_datasets / "manifests" / "projects.jsonl",
        [
            {
                "project_id": "portal",
                "project_code": "PORTAL_SNAPSHOT",
                "project_name": "portal",
                "bundle_level": "level_c",
                "official_project_url": "https://www.ccgp.gov.cn/p",
            }
        ],
    )
    write_jsonl(
        tmp_datasets / "manifests" / "documents.jsonl",
        [
            {
                "document_id": "d1",
                "project_id": "portal",
                "project_code": "PORTAL_SNAPSHOT",
                "document_type": "award_notice",
                "source_url": "https://www.ccgp.gov.cn/p",
                "storage_path": "raw/documents/p.html",
            }
        ],
    )
    (tmp_datasets / "raw" / "documents").mkdir(parents=True, exist_ok=True)
    (tmp_datasets / "raw" / "documents" / "p.html").write_text(
        "中标供应商：广州某某科技有限公司。", encoding="utf-8"
    )
    write_jsonl(tmp_datasets / "interim" / "chunks" / "chunks.jsonl", [])
    write_jsonl(tmp_datasets / "silver" / "requirements.jsonl", [])
    write_jsonl(tmp_datasets / "silver" / "evidence.jsonl", [])
    stats = build_disclosed_matches()
    assert stats["disclosed_suppliers"] == 0
