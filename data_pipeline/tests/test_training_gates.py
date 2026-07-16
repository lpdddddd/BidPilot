"""Tests for full cross-split scan, LF gates, multi_section dual evidence, readiness."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bidpilot_data.rag_eval.build import multi_section_dual_answer_ok, split_multi_section_answer
from bidpilot_data.reporting.training_readiness import build_training_readiness_report
from bidpilot_data.sft.cross_split import analyze_cross_split_similarity, classify_overlap
from bidpilot_data.utils import write_json, write_jsonl


def _seed_split_records(tmp_datasets: Path, *, n_train: int = 3, n_val: int = 2, n_test: int = 2) -> None:
    chunks = []
    docs = []
    for split, n, base in (("train", n_train, 0), ("validation", n_val, 100), ("test", n_test, 200)):
        rows = []
        for i in range(n):
            pid = f"p-{split}-{i}"
            did = f"d-{split}-{i}"
            cid = f"c-{split}-{i}"
            text = f"项目独立条款{split}{i}：采购需求为专属内容{i}，预算{1000+i}万元。"
            chunks.append(
                {
                    "chunk_id": cid,
                    "project_id": pid,
                    "document_id": did,
                    "text": text,
                    "page_start": 1,
                    "section_path": f"{split}/sec{i}",
                }
            )
            docs.append(
                {
                    "document_id": did,
                    "project_id": pid,
                    "source_url": f"https://www.ccgp.gov.cn/{split}/{i}",
                    "document_type": "tender_document",
                }
            )
            rows.append(
                {
                    "record_id": f"r-{split}-{i}",
                    "project_id": pid,
                    "task_type": "requirement_classify",
                    "quality_level": "silver",
                    "source_chunk_ids": [cid],
                    "source_document_ids": [did],
                    "source_urls": [f"https://www.ccgp.gov.cn/{split}/{i}"],
                    "messages": [
                        {"role": "system", "content": "sys"},
                        {"role": "user", "content": f"用户问题{split}{i} {text}"},
                        {"role": "assistant", "content": json.dumps({"category": "qualification"}, ensure_ascii=False)},
                    ],
                }
            )
        write_jsonl(tmp_datasets / "sft" / split / "records.jsonl", rows)
    write_jsonl(tmp_datasets / "interim" / "chunks" / "chunks.jsonl", chunks)
    write_jsonl(tmp_datasets / "manifests" / "documents.jsonl", docs)


def test_full_scan_finds_leak_beyond_400th_chunk(tmp_datasets):
    """Leakage involving the 401st+ chunk item must still be detected (no sample cap)."""
    chunks = []
    docs = []
    train_rows = []
    test_rows = []
    leak_text = "关键业务评分表权重：技术40分商务20分价格40分，资格条件须有信息系统集成资质。"
    for i in range(420):
        pid = f"ptrain-{i}"
        did = f"dtrain-{i}"
        cid = f"ctrain-{i}"
        text = f"无关模板文本编号{i}。" if i != 410 else leak_text
        chunks.append({"chunk_id": cid, "project_id": pid, "document_id": did, "text": text, "page_start": 1})
        docs.append({"document_id": did, "project_id": pid, "source_url": f"https://www.ccgp.gov.cn/t/{i}"})
        train_rows.append(
            {
                "record_id": f"rt{i}",
                "project_id": pid,
                "task_type": "scoring_extract",
                "source_chunk_ids": [cid],
                "source_document_ids": [did],
                "source_urls": [f"https://www.ccgp.gov.cn/t/{i}"],
                "messages": [
                    {"role": "user", "content": f"u{i}"},
                    {"role": "assistant", "content": "{}"},
                ],
            }
        )
    # test item duplicates leak_text from index 410
    chunks.append(
        {
            "chunk_id": "ctest-leak",
            "project_id": "ptest-1",
            "document_id": "dtest-1",
            "text": leak_text,
            "page_start": 1,
        }
    )
    docs.append({"document_id": "dtest-1", "project_id": "ptest-1", "source_url": "https://www.ccgp.gov.cn/x/1"})
    test_rows.append(
        {
            "record_id": "rtest",
            "project_id": "ptest-1",
            "task_type": "scoring_extract",
            "source_chunk_ids": ["ctest-leak"],
            "source_document_ids": ["dtest-1"],
            "source_urls": ["https://www.ccgp.gov.cn/x/1"],
            "messages": [{"role": "user", "content": "u"}, {"role": "assistant", "content": "{}"}],
        }
    )
    write_jsonl(tmp_datasets / "interim" / "chunks" / "chunks.jsonl", chunks)
    write_jsonl(tmp_datasets / "manifests" / "documents.jsonl", docs)
    write_jsonl(tmp_datasets / "sft" / "train" / "records.jsonl", train_rows)
    write_jsonl(tmp_datasets / "sft" / "validation" / "records.jsonl", [])
    write_jsonl(tmp_datasets / "sft" / "test" / "records.jsonl", test_rows)
    report = analyze_cross_split_similarity()
    assert report["full_scan"] is True
    assert report["chunks_scanned"] >= 401
    assert report["ok"] is False
    assert report["severe_business_overlap"] + report["exact_duplicate"] >= 1


def test_train_validation_and_val_test_leaks_detected(tmp_datasets):
    _seed_split_records(tmp_datasets)
    base_chunks = list(
        json.loads(l) for l in (tmp_datasets / "interim" / "chunks" / "chunks.jsonl").read_text().splitlines() if l.strip()
    )
    docs = list(
        json.loads(l) for l in (tmp_datasets / "manifests" / "documents.jsonl").read_text().splitlines() if l.strip()
    )
    leak = "技术参数：CPU不少于32核，内存256GB；评分因素技术分60。"
    base_chunks.append(
        {"chunk_id": "c-tv", "project_id": "p-train-0", "document_id": "d-tv-a", "text": leak, "page_start": 1}
    )
    base_chunks.append(
        {"chunk_id": "c-tv2", "project_id": "p-validation-0", "document_id": "d-tv-b", "text": leak, "page_start": 1}
    )
    base_chunks.append(
        {"chunk_id": "c-vt", "project_id": "p-validation-1", "document_id": "d-vt-a", "text": leak + "补充", "page_start": 1}
    )
    base_chunks.append(
        {"chunk_id": "c-vt2", "project_id": "p-test-0", "document_id": "d-vt-b", "text": leak + "补充", "page_start": 1}
    )
    docs += [
        {"document_id": "d-tv-a", "project_id": "p-train-0", "source_url": "https://www.ccgp.gov.cn/a"},
        {"document_id": "d-tv-b", "project_id": "p-validation-0", "source_url": "https://www.ccgp.gov.cn/b"},
        {"document_id": "d-vt-a", "project_id": "p-validation-1", "source_url": "https://www.ccgp.gov.cn/c"},
        {"document_id": "d-vt-b", "project_id": "p-test-0", "source_url": "https://www.ccgp.gov.cn/d"},
    ]
    write_jsonl(tmp_datasets / "interim" / "chunks" / "chunks.jsonl", base_chunks)
    write_jsonl(tmp_datasets / "manifests" / "documents.jsonl", docs)
    # attach chunks to records
    for split, cid in (("train", "c-tv"), ("validation", "c-tv2"), ("validation", "c-vt"), ("test", "c-vt2")):
        path = tmp_datasets / "sft" / split / "records.jsonl"
        rows = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
        rows[0]["source_chunk_ids"] = list(dict.fromkeys((rows[0].get("source_chunk_ids") or []) + [cid]))
        write_jsonl(path, rows)

    report = analyze_cross_split_similarity(threshold=90)
    pairs = report.get("pairs") or []
    split_pairs = {p.get("split_pair") for p in pairs}
    assert "train/validation" in split_pairs or report["severe_business_overlap"] >= 1
    assert "validation/test" in split_pairs or report["fail_count"] >= 1


def test_identical_sft_qa_across_splits_is_severe(tmp_datasets):
    _seed_split_records(tmp_datasets, n_train=1, n_val=1, n_test=1)
    qa_user = "判断以下条款类别：投标人须提供等保三级测评报告。"
    qa_asst = '{"category":"qualification","mandatory":true}'
    for split in ("train", "test"):
        path = tmp_datasets / "sft" / split / "records.jsonl"
        rows = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
        rows[0]["messages"] = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": qa_user},
            {"role": "assistant", "content": qa_asst},
        ]
        write_jsonl(path, rows)
    report = analyze_cross_split_similarity()
    assert report["ok"] is False
    assert any(
        p.get("kind") in {"severe_business_overlap", "exact_duplicate"}
        and (p.get("left", {}).get("item_kind") == "sft_qa" or p.get("right", {}).get("item_kind") == "sft_qa")
        for p in report.get("pairs") or []
    )


def test_template_overlap_vs_business_not_exempt():
    left = {
        "project_id": "a",
        "document_id": "d1",
        "text": "根据《中华人民共和国政府采购法》投标人不得存在下列情形，提供虚假材料谋取中标。",
        "kind": "chunk",
    }
    right = {
        "project_id": "b",
        "document_id": "d2",
        "text": "根据《中华人民共和国政府采购法》投标人不得存在下列情形，与采购人、采购代理机构恶意串通。",
        "kind": "chunk",
    }
    kind, reason = classify_overlap(left, right, sim=99.0, exact=False)
    assert kind == "template_overlap"

    biz_l = {
        "project_id": "a",
        "document_id": "d1",
        "text": "评分表：技术参数响应满分30分，资格条件须具备信息系统集成资质，分值权重见下表。",
        "kind": "chunk",
    }
    biz_r = {
        "project_id": "b",
        "document_id": "d2",
        "text": "评分表：技术参数响应满分30分，资格条件须具备信息系统集成资质，分值权重见下表。",
        "kind": "chunk",
    }
    kind2, _ = classify_overlap(biz_l, biz_r, sim=100.0, exact=True)
    assert kind2 == "severe_business_overlap"


def _load_validate_sft_real():
    import importlib.util

    repo_script = Path(__file__).resolve().parents[2] / "training" / "llamafactory" / "scripts" / "validate_sft_real.py"
    spec = importlib.util.spec_from_file_location("validate_sft_real_mod", repo_script)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(mod)
    return mod


def test_llamafactory_missing_blocked(tmp_repo, monkeypatch):
    mod = _load_validate_sft_real()
    monkeypatch.delenv("LLAMAFACTORY_HOME", raising=False)
    monkeypatch.setattr(mod.shutil, "which", lambda _: None)
    monkeypatch.setattr(
        mod,
        "detect_llamafactory",
        lambda: {
            "cli": None,
            "LLAMAFACTORY_HOME": None,
            "importable": False,
            "import_error": "No module named llamafactory",
        },
    )
    ext = mod.run_llamafactory_preprocess(tmp_repo)
    assert ext["status"] == "blocked_dependency_missing"
    assert ext["preprocess_executed"] is False
    merged = mod.merge_report({"ok": True, "errors": [], "datasets": {}}, ext)
    assert merged["ok"] is False
    assert merged["external_llamafactory_validation"] == "blocked_dependency_missing"


def test_tool_role_and_empty_final_fail():
    mod = _load_validate_sft_real()

    bad_tool = [
        {"role": "user", "content": "x"},
        {"role": "tool", "content": "{}"},
        {"role": "assistant", "content": '{"answer":"a","citations":["c1"]}'},
    ]
    assert any("tool not after assistant" in e for e in mod.validate_messages(bad_tool, 0))

    empty_final = [
        {"role": "user", "content": "x"},
        {"role": "assistant", "content": '{"tool_name":"search_chunks","arguments":{}}'},
        {"role": "tool", "content": "{}"},
        {"role": "assistant", "content": "{}"},
    ]
    assert any("missing answer/clarify" in e for e in mod.validate_messages(empty_final, 0))


def test_multi_section_missing_second_url_and_single_answer():
    assert multi_section_dual_answer_ok(
        "营业执照为资格要件；另方面，虚假材料将否决投标",
        "投标人须提供营业执照",
        "提供虚假材料的否决其投标",
    )
    assert not multi_section_dual_answer_ok(
        "营业执照为资格要件；另方面，详见上文",
        "投标人须提供营业执照",
        "服务期限为三年并按季度付款",
    )
    parts = split_multi_section_answer("部分A；另方面，部分B")
    assert parts == ("部分A", "部分B")


def test_gold_zero_closes_training_gates(tmp_datasets):
    write_jsonl(tmp_datasets / "manifests" / "projects.jsonl", [])
    write_jsonl(tmp_datasets / "silver" / "requirement_matches.jsonl", [])
    write_json(
        tmp_datasets / "reports" / "sft_build_stats.json",
        {
            "structurally_valid_sft": 100,
            "reviewed_trainable_sft": 0,
            "rejected_sft": 0,
            "train": 80,
            "validation": 10,
            "test": 10,
            "quality_level": {"silver": 100, "gold": 0},
            "by_task": {"requirement_classify": 50, "risk_detect": 50},
        },
    )
    write_json(tmp_datasets / "reports" / "rag_quality_report.json", {"ok": True, "questions": 10})
    write_json(tmp_datasets / "reports" / "rag_validation_report.json", {"ok": True})
    write_json(tmp_datasets / "reports" / "agent_quality_report.json", {"tasks": 10})
    write_json(tmp_datasets / "reports" / "validation_report.json", {"ok": True})
    write_json(
        tmp_datasets / "reports" / "cross_split_similarity_report.json",
        {"ok": True, "full_scan": True, "project_leaks": []},
    )
    write_json(
        tmp_datasets / "reports" / "llamafactory_real_validation.json",
        {"ok": False, "internal": {"ok": True}, "external_llamafactory_validation": "blocked_dependency_missing", "preprocess_executed": False},
    )
    write_json(tmp_datasets / "reports" / "split_distribution.json", {"train": {"source_domain": {"www.ccgp.gov.cn": 1}, "task_type": {"requirement_classify": 1}}})
    report = build_training_readiness_report()
    assert report["ready_for_pilot_lora"] is False
    assert report["ready_for_formal_lora"] is False
    assert report["ready_for_human_review"] is True or report["stage"] in {"ready_for_human_review", "blocked"}


def test_project_sets_mutex_reported(tmp_datasets):
    _seed_split_records(tmp_datasets, n_train=2, n_val=2, n_test=2)
    # Force same project id into train and test
    train = [json.loads(l) for l in (tmp_datasets / "sft" / "train" / "records.jsonl").read_text().splitlines() if l.strip()]
    test = [json.loads(l) for l in (tmp_datasets / "sft" / "test" / "records.jsonl").read_text().splitlines() if l.strip()]
    test[0]["project_id"] = train[0]["project_id"]
    write_jsonl(tmp_datasets / "sft" / "test" / "records.jsonl", test)
    report = analyze_cross_split_similarity()
    assert report["ok"] is False
    assert report.get("project_leaks")
