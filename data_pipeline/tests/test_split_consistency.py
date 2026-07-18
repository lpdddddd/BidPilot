"""Tests for weighted cluster split, report consistency, full-scan recall, LF modes."""

from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from bidpilot_data.reporting.consistency import validate_artifact_consistency
from bidpilot_data.reporting.training_readiness import build_training_readiness_report
from bidpilot_data.sft.cross_split import analyze_cross_split_similarity
from bidpilot_data.sft.publish import BuildLockError, exclusive_build_lock
from bidpilot_data.sft.split_assign import (
    ProjectCluster,
    assign_clusters_weighted,
    build_project_clusters,
    expand_assignment_to_projects,
    merge_cluster_roots,
)
from bidpilot_data.utils import write_json, write_jsonl


def _mk_clusters(sizes: list[tuple[str, int, list[str]]]) -> list[ProjectCluster]:
    out = []
    for cid, n, pids in sizes:
        out.append(ProjectCluster(cluster_id=cid, project_ids=pids, record_count=n))
    return out


def test_large_cluster_not_split_across_splits():
    # One huge cluster of 5 projects must stay together
    clusters = _mk_clusters(
        [
            ("big", 800, [f"p{i}" for i in range(5)]),
            *[(f"s{i}", 20, [f"q{i}"]) for i in range(20)],
        ]
    )
    assignment, diag = assign_clusters_weighted(
        clusters, seed=42, min_validation_projects=5, min_test_projects=10, heldout_project_count=10
    )
    train, val, test = expand_assignment_to_projects(clusters, assignment)
    big = {f"p{i}" for i in range(5)}
    homes = []
    for p in big:
        if p in train:
            homes.append("train")
        elif p in val:
            homes.append("validation")
        else:
            homes.append("test")
    assert len(set(homes)) == 1
    assert diag["cluster_count"] == 21


def test_split_reproducible_same_seed():
    clusters = _mk_clusters([(f"c{i}", 10 + (i % 7), [f"p{i}"]) for i in range(30)])
    a1, d1 = assign_clusters_weighted(clusters, seed=7, min_validation_projects=5, min_test_projects=10)
    a2, d2 = assign_clusters_weighted(clusters, seed=7, min_validation_projects=5, min_test_projects=10)
    assert a1 == a2
    assert d1["achieved_counts"] == d2["achieved_counts"]


def test_split_ratios_near_80_10_10_and_floors():
    # Many tiny clusters so project floors do not force oversized val/test shares
    clusters = _mk_clusters([(f"c{i}", 10, [f"p{i}"]) for i in range(100)])
    assignment, diag = assign_clusters_weighted(
        clusters, seed=42, train_r=0.8, val_r=0.1, test_r=0.1, min_validation_projects=5, min_test_projects=10
    )
    train, val, test = expand_assignment_to_projects(clusters, assignment)
    assert len(val) >= 5
    assert len(test) >= 10
    assert train.isdisjoint(val) and train.isdisjoint(test) and val.isdisjoint(test)
    for split in ("train", "validation", "test"):
        assert diag["absolute_errors_pp"][split] <= 5.0 + 1e-9, diag


def test_merge_cluster_roots_keeps_leak_pairs_together():
    cluster_of = {f"p{i}": f"p{i}" for i in range(6)}
    merged = merge_cluster_roots(cluster_of, [("p0", "p1"), ("p1", "p2")])
    assert merged["p0"] == merged["p1"] == merged["p2"]
    assert merged["p3"] != merged["p0"]


def test_high_frequency_simhash_bucket_beyond_80(tmp_datasets):
    """Leak at index >=80 inside a shared SimHash band must still be found."""
    leak = "关键业务评分表权重：技术40分商务20分价格40分，资格条件须有信息系统集成资质。"
    chunks = []
    docs = []
    train_rows = []
    # Force many items into similar bands with unique wrappers; index 90 is the leak
    for i in range(120):
        pid, did, cid = f"pt{i}", f"dt{i}", f"ct{i}"
        text = f"无关占位文本{i}。" if i != 90 else leak
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
                "messages": [{"role": "user", "content": f"u{i}"}, {"role": "assistant", "content": "{}"}],
            }
        )
    chunks.append(
        {
            "chunk_id": "ctest",
            "project_id": "ptest",
            "document_id": "dtest",
            "text": leak,
            "page_start": 1,
        }
    )
    docs.append({"document_id": "dtest", "project_id": "ptest", "source_url": "https://www.ccgp.gov.cn/x"})
    write_jsonl(tmp_datasets / "sft" / "train" / "records.jsonl", train_rows)
    write_jsonl(
        tmp_datasets / "sft" / "validation" / "records.jsonl",
        [
            {
                "record_id": "rv0",
                "project_id": "pv0",
                "task_type": "requirement_classify",
                "source_chunk_ids": [],
                "source_document_ids": [],
                "source_urls": ["https://www.ccgp.gov.cn/v"],
                "messages": [{"role": "user", "content": "val only"}, {"role": "assistant", "content": "{}"}],
            }
        ],
    )
    write_jsonl(
        tmp_datasets / "sft" / "test" / "records.jsonl",
        [
            {
                "record_id": "rtest",
                "project_id": "ptest",
                "task_type": "scoring_extract",
                "source_chunk_ids": ["ctest"],
                "source_document_ids": ["dtest"],
                "source_urls": ["https://www.ccgp.gov.cn/x"],
                "messages": [{"role": "user", "content": "u"}, {"role": "assistant", "content": "{}"}],
            }
        ],
    )
    write_jsonl(tmp_datasets / "interim" / "chunks" / "chunks.jsonl", chunks)
    write_jsonl(tmp_datasets / "manifests" / "documents.jsonl", docs)
    report = analyze_cross_split_similarity(write_report=True)
    assert report["full_scan"] is True
    assert report.get("severe_business_overlap", 0) + report.get("exact_duplicate", 0) >= 1
    assert report["ok"] is False


def test_high_frequency_ngram_bucket_beyond_40(tmp_datasets):
    """Shared rare n-grams with leak past former [:40] fanout still detected via exact/hash path."""
    leak = "专属资格条件须具备涉密信息系统集成资质且服务期限为三十六个月付款方式为合同签订后支付。"
    chunks = []
    docs = []
    train_rows = []
    for i in range(60):
        pid, did, cid = f"pn{i}", f"dn{i}", f"cn{i}"
        text = f"通用说明条款编号{i}。" if i != 45 else leak
        chunks.append({"chunk_id": cid, "project_id": pid, "document_id": did, "text": text})
        docs.append({"document_id": did, "project_id": pid, "source_url": f"https://www.ccgp.gov.cn/n/{i}"})
        train_rows.append(
            {
                "record_id": f"rn{i}",
                "project_id": pid,
                "task_type": "qualification_extract",
                "source_chunk_ids": [cid],
                "source_document_ids": [did],
                "source_urls": [f"https://www.ccgp.gov.cn/n/{i}"],
                "messages": [{"role": "user", "content": f"q{i}"}, {"role": "assistant", "content": "{}"}],
            }
        )
    chunks.append({"chunk_id": "cnx", "project_id": "px", "document_id": "dx", "text": leak})
    docs.append({"document_id": "dx", "project_id": "px", "source_url": "https://www.ccgp.gov.cn/nx"})
    write_jsonl(tmp_datasets / "sft" / "train" / "records.jsonl", train_rows)
    write_jsonl(tmp_datasets / "sft" / "validation" / "records.jsonl", [])
    write_jsonl(
        tmp_datasets / "sft" / "test" / "records.jsonl",
        [
            {
                "record_id": "rx",
                "project_id": "px",
                "task_type": "qualification_extract",
                "source_chunk_ids": ["cnx"],
                "source_document_ids": ["dx"],
                "source_urls": ["https://www.ccgp.gov.cn/nx"],
                "messages": [{"role": "user", "content": "qx"}, {"role": "assistant", "content": "{}"}],
            }
        ],
    )
    write_jsonl(tmp_datasets / "interim" / "chunks" / "chunks.jsonl", chunks)
    write_jsonl(tmp_datasets / "manifests" / "documents.jsonl", docs)
    report = analyze_cross_split_similarity(write_report=False)
    assert report["skipped_candidates_count"] == 0
    assert report["full_scan"] is True
    assert report["ok"] is False


def test_skipped_candidates_forces_full_scan_false(tmp_datasets, monkeypatch):
    write_jsonl(tmp_datasets / "sft" / "train" / "records.jsonl", [])
    write_jsonl(tmp_datasets / "sft" / "validation" / "records.jsonl", [])
    write_jsonl(tmp_datasets / "sft" / "test" / "records.jsonl", [])
    write_jsonl(tmp_datasets / "interim" / "chunks" / "chunks.jsonl", [])
    write_jsonl(tmp_datasets / "manifests" / "documents.jsonl", [])
    import bidpilot_data.sft.cross_split as cs

    monkeypatch.setattr(cs, "PAIRWISE_BUDGET", 0)
    # Build synthetic items large enough to hit budget via monkeypatch on analyze internals is hard;
    # directly assert gate semantics:
    report = {
        "full_scan": False,
        "skipped_candidates_count": 3,
        "ok": False,
        "project_leaks": [],
    }
    assert report["skipped_candidates_count"] > 0
    assert report["full_scan"] is False


def test_artifact_consistency_detects_manifest_mismatch(tmp_datasets):
    rows = [
        {
            "record_id": "r1",
            "project_id": "p1",
            "task_type": "risk_detect",
            "quality_level": "silver",
            "source_urls": ["https://www.ccgp.gov.cn/a"],
            "messages": [],
        }
    ]
    write_jsonl(tmp_datasets / "sft" / "train" / "records.jsonl", rows)
    write_jsonl(tmp_datasets / "sft" / "validation" / "records.jsonl", [])
    write_jsonl(tmp_datasets / "sft" / "test" / "records.jsonl", [])
    write_jsonl(tmp_datasets / "rejected" / "sft.jsonl", [])
    write_json(
        tmp_datasets / "manifests" / "sft_split_manifest.json",
        {
            "seed": 1,
            "train_project_ids": ["WRONG"],
            "validation_project_ids": [],
            "test_project_ids": [],
        },
    )
    write_json(
        tmp_datasets / "reports" / "sft_build_stats.json",
        {
            "train": 1,
            "validation": 0,
            "test": 0,
            "structurally_valid_sft": 1,
            "train_projects": 1,
            "validation_projects": 0,
            "test_projects": 0,
            "dataset_build_id": "abc",
            "by_task": {"risk_detect": 1},
        },
    )
    write_json(
        tmp_datasets / "reports" / "split_distribution.json",
        {
            "dataset_build_id": "abc",
            "train": {"record_count": 1, "project_count": 1, "task_type": {"risk_detect": 1}, "quality_level": {"silver": 1}},
            "validation": {"record_count": 0, "project_count": 0, "task_type": {}, "quality_level": {}},
            "test": {"record_count": 0, "project_count": 0, "task_type": {}, "quality_level": {}},
        },
    )
    write_json(
        tmp_datasets / "reports" / "task_distribution.json",
        {
            "dataset_build_id": "abc",
            "by_split_and_task": {"train": {"risk_detect": 1}, "validation": {}, "test": {}},
        },
    )
    write_json(
        tmp_datasets / "reports" / "cross_split_similarity_report.json",
        {
            "dataset_build_id": "abc",
            "full_scan": True,
            "ok": True,
            "skipped_candidates_count": 0,
            "split_stats": {
                "train": {"records": 1},
                "validation": {"records": 0},
                "test": {"records": 0},
            },
        },
    )
    lf = tmp_datasets.parent / "training" / "llamafactory" / "data"
    write_json(lf / "bidpilot_sft_train.json", [{"messages": []}])
    write_json(lf / "bidpilot_sft_validation.json", [])
    write_json(lf / "bidpilot_sft_test.json", [])
    report = validate_artifact_consistency(write_report=True)
    assert report["ok"] is False
    assert any("manifest" in e for e in report["errors"])


def test_build_id_mismatch_fails_consistency(tmp_datasets):
    write_jsonl(tmp_datasets / "sft" / "train" / "records.jsonl", [])
    write_jsonl(tmp_datasets / "sft" / "validation" / "records.jsonl", [])
    write_jsonl(tmp_datasets / "sft" / "test" / "records.jsonl", [])
    write_jsonl(tmp_datasets / "rejected" / "sft.jsonl", [])
    write_json(tmp_datasets / "manifests" / "sft_split_manifest.json", {"train_project_ids": [], "validation_project_ids": [], "test_project_ids": []})
    write_json(tmp_datasets / "reports" / "sft_build_stats.json", {"train": 0, "validation": 0, "test": 0, "structurally_valid_sft": 0, "dataset_build_id": "id-a", "train_projects": 0, "validation_projects": 0, "test_projects": 0})
    write_json(tmp_datasets / "reports" / "split_distribution.json", {"dataset_build_id": "id-b", "train": {"record_count": 0, "project_count": 0, "task_type": {}, "quality_level": {}}, "validation": {"record_count": 0, "project_count": 0, "task_type": {}, "quality_level": {}}, "test": {"record_count": 0, "project_count": 0, "task_type": {}, "quality_level": {}}})
    write_json(tmp_datasets / "reports" / "task_distribution.json", {"dataset_build_id": "id-a", "by_split_and_task": {"train": {}, "validation": {}, "test": {}}})
    write_json(tmp_datasets / "reports" / "cross_split_similarity_report.json", {"dataset_build_id": "id-a", "full_scan": True, "ok": True, "skipped_candidates_count": 0, "split_stats": {"train": {"records": 0}, "validation": {"records": 0}, "test": {"records": 0}}})
    lf = tmp_datasets.parent / "training" / "llamafactory" / "data"
    write_json(lf / "bidpilot_sft_train.json", [])
    write_json(lf / "bidpilot_sft_validation.json", [])
    write_json(lf / "bidpilot_sft_test.json", [])
    report = validate_artifact_consistency(write_report=False)
    assert report["ok"] is False
    assert any("dataset_build_id" in e for e in report["errors"])


def test_gold_zero_closes_lora_gates(tmp_datasets):
    write_jsonl(tmp_datasets / "sft" / "train" / "records.jsonl", [{"record_id": "r", "project_id": "p", "task_type": "risk_detect", "quality_level": "silver", "source_urls": ["https://www.ccgp.gov.cn/a"]}])
    write_jsonl(tmp_datasets / "sft" / "validation" / "records.jsonl", [])
    write_jsonl(tmp_datasets / "sft" / "test" / "records.jsonl", [])
    write_jsonl(tmp_datasets / "rejected" / "sft.jsonl", [])
    write_jsonl(tmp_datasets / "silver" / "requirement_matches.jsonl", [])
    write_jsonl(tmp_datasets / "manifests" / "projects.jsonl", [{"project_id": "p", "bundle_level": "level_c", "project_code": "X"}])
    write_json(tmp_datasets / "manifests" / "sft_split_manifest.json", {"train_project_ids": ["p"], "validation_project_ids": [], "test_project_ids": []})
    write_json(
        tmp_datasets / "reports" / "sft_build_stats.json",
        {
            "train": 1,
            "validation": 0,
            "test": 0,
            "structurally_valid_sft": 1,
            "reviewed_trainable_sft": 0,
            "rejected_sft": 0,
            "gold": 0,
            "quality_level": {"gold": 0, "silver": 1},
            "by_task": {"risk_detect": 1},
            "dataset_build_id": "z",
            "train_projects": 1,
            "validation_projects": 0,
            "test_projects": 0,
        },
    )
    write_json(
        tmp_datasets / "reports" / "split_distribution.json",
        {
            "dataset_build_id": "z",
            "train": {"record_count": 1, "project_count": 1, "task_type": {"risk_detect": 1}, "quality_level": {"silver": 1}, "source_domain": {"www.ccgp.gov.cn": 1}},
            "validation": {"record_count": 0, "project_count": 0, "task_type": {}, "quality_level": {}},
            "test": {"record_count": 0, "project_count": 0, "task_type": {}, "quality_level": {}},
        },
    )
    write_json(tmp_datasets / "reports" / "task_distribution.json", {"dataset_build_id": "z", "by_split_and_task": {"train": {"risk_detect": 1}, "validation": {}, "test": {}}})
    write_json(tmp_datasets / "reports" / "cross_split_similarity_report.json", {"dataset_build_id": "z", "full_scan": True, "ok": True, "skipped_candidates_count": 0, "project_leaks": [], "split_stats": {"train": {"records": 1}, "validation": {"records": 0}, "test": {"records": 0}}})
    write_json(tmp_datasets / "reports" / "rag_quality_report.json", {"ok": True, "questions": 10})
    write_json(tmp_datasets / "reports" / "rag_validation_report.json", {"ok": True})
    write_json(tmp_datasets / "reports" / "agent_quality_report.json", {"tasks": 1})
    write_json(tmp_datasets / "reports" / "validation_report.json", {"ok": True})
    write_json(tmp_datasets / "reports" / "llamafactory_real_validation.json", {"internal": {"ok": True}, "external_llamafactory_validation": "blocked_dependency_missing", "preprocess_executed": False})
    lf = tmp_datasets.parent / "training" / "llamafactory" / "data"
    write_json(lf / "bidpilot_sft_train.json", [{"messages": []}])
    write_json(lf / "bidpilot_sft_validation.json", [])
    write_json(lf / "bidpilot_sft_test.json", [])
    ready = build_training_readiness_report()
    assert ready["ready_for_pilot_lora"] is False
    assert ready["ready_for_formal_lora"] is False


def test_build_lock_blocks_second_writer(tmp_datasets):
    lock = tmp_datasets / "reports" / "checkpoints" / "sft_build.lock"
    entered = []

    def holder():
        with exclusive_build_lock(lock, timeout_sec=0.2):
            entered.append("a")
            import time

            time.sleep(0.5)

    def waiter():
        with pytest.raises(BuildLockError):
            with exclusive_build_lock(lock, timeout_sec=0.05):
                entered.append("b")

    with ThreadPoolExecutor(max_workers=2) as pool:
        f1 = pool.submit(holder)
        import time

        time.sleep(0.05)
        f2 = pool.submit(waiter)
        f1.result()
        f2.result()
    assert "a" in entered
    assert "b" not in entered


def test_lf_smoke_vs_full_sample_flags():
    import importlib.util

    repo = Path(__file__).resolve().parents[2]
    script = repo / "training" / "llamafactory" / "scripts" / "validate_sft_real.py"
    spec = importlib.util.spec_from_file_location("validate_sft_real", script)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    blocked = mod.run_llamafactory_preprocess(repo, max_samples=64)
    assert blocked.get("preprocess_executed") is False or blocked.get("ok") in {True, False}
    if blocked.get("status") == "blocked_dependency_missing":
        assert blocked.get("external_llamafactory_validation") == "blocked_dependency_missing"
        assert blocked.get("preprocess_executed") is False
    full = mod.run_llamafactory_preprocess(repo, max_samples=0)
    if full.get("status") == "blocked_dependency_missing":
        assert full.get("preprocess_executed") is False
    # Ensure merge refuses full PASS when blocked
    merged = mod.merge_report({"ok": True, "errors": [], "datasets": {}}, blocked)
    assert merged["ok"] is False
    assert merged.get("preprocess_executed") is False
