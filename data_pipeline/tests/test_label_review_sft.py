import json
from pathlib import Path

import pandas as pd

from bidpilot_data.labeling.llm_client import repair_json
from bidpilot_data.labeling.requirements import _rule_label
from bidpilot_data.review.workflow import export_review_csv, import_review_csv
from bidpilot_data.settings import load_taxonomy
from bidpilot_data.sft.build import _split_projects, build_sft_dataset
from bidpilot_data.schemas import QualityLevel, ReviewStatus
from bidpilot_data.utils import write_jsonl


def test_rule_recall_rejection():
    taxonomy = load_taxonomy()
    cat, mandatory, risk, conf, _ = _rule_label("未按要求密封的，按废标处理。", taxonomy)
    assert cat.value == "mandatory_rejection"
    assert mandatory is True
    assert risk.value == "critical"
    assert conf >= 0.8


def test_repair_json():
    obj = repair_json('```json\n{"category":"qualification","mandatory":true}\n```')
    assert obj["category"] == "qualification"


def test_review_import_gold_and_block_without_reviewer(tmp_datasets):
    ann = {
        "annotation_id": "ann-1",
        "requirement_id": "req-1",
        "project_id": "proj-1",
        "category": "qualification",
        "title": "执照",
        "original_text": "须提供营业执照",
        "normalized_requirement": "提供营业执照",
        "mandatory": True,
        "score": None,
        "risk_level": "high",
        "evidence_required": [],
        "source_page": 1,
        "confidence": 0.9,
        "quality_level": "silver",
        "review_status": "pending",
        "generator": "rules",
        "source_url": "file:///tmp/x",
    }
    write_jsonl(tmp_datasets / "silver" / "requirements.jsonl", [ann])
    write_jsonl(tmp_datasets / "review" / "pending" / "requirements_pending.jsonl", [ann])
    export_review_csv()
    csv_path = tmp_datasets / "review" / "exported" / "requirements_review.csv"
    df = pd.read_csv(csv_path, dtype=str).fillna("")
    df.loc[0, "decision"] = "accept"
    df.loc[0, "reviewer"] = ""
    bad = tmp_datasets / "review" / "exported" / "bad.csv"
    df.to_csv(bad, index=False)
    stats_bad = import_review_csv(bad)
    assert stats_bad["gold_upgraded"] == 0
    assert stats_bad["errors"] >= 1

    df.loc[0, "reviewer"] = "alice"
    good = tmp_datasets / "review" / "exported" / "good.csv"
    df.to_csv(good, index=False)
    stats = import_review_csv(good)
    assert stats["gold_upgraded"] == 1
    # idempotent
    stats2 = import_review_csv(good)
    assert stats2["gold_upgraded"] == 1
    gold = json.loads((tmp_datasets / "gold" / "requirements.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert gold["quality_level"] == QualityLevel.gold.value
    assert gold["review_status"] == ReviewStatus.reviewed.value
    assert gold["reviewer"] == "alice"


def test_project_split_no_leakage():
    m = _split_projects([f"p{i}" for i in range(20)], seed=42, train_r=0.8, val_r=0.1, heldout=5)
    assert not (set(m.train_project_ids) & set(m.test_project_ids))
    assert not (set(m.train_project_ids) & set(m.validation_project_ids))
    assert len(m.heldout_project_ids) >= 1


def test_sft_sharegpt_and_dataset_info(tmp_datasets, tmp_repo):
    write_jsonl(
        tmp_datasets / "manifests" / "projects.jsonl",
        [
            {
                "project_id": "proj-train",
                "project_code": "T-1",
                "project_name": "训练项目",
                "bundle_level": "level_b",
                "official_project_url": "https://www.ccgp.gov.cn/train",
                "source_domain": "www.ccgp.gov.cn",
            },
            {
                "project_id": "proj-test",
                "project_code": "T-2",
                "project_name": "测试项目",
                "bundle_level": "level_b",
                "official_project_url": "https://www.ccgp.gov.cn/test",
                "source_domain": "www.ccgp.gov.cn",
            },
        ],
    )
    write_jsonl(tmp_datasets / "manifests" / "documents.jsonl", [])
    write_jsonl(
        tmp_datasets / "silver" / "requirements.jsonl",
        [
            {
                "annotation_id": "a1",
                "requirement_id": "r1",
                "project_id": "proj-train",
                "document_id": "d1",
                "chunk_id": "c1",
                "category": "qualification",
                "title": "执照",
                "original_text": "投标人须提供有效营业执照。",
                "normalized_requirement": "提供营业执照",
                "mandatory": True,
                "risk_level": "high",
                "evidence_required": ["营业执照"],
                "source_page": 1,
                "confidence": 0.9,
                "quality_level": "silver",
                "review_status": "pending",
                "generator": "rules",
                "source_url": "https://www.ccgp.gov.cn/train",
            },
            {
                "annotation_id": "a2",
                "requirement_id": "r2",
                "project_id": "proj-test",
                "document_id": "d2",
                "chunk_id": "c2",
                "category": "scoring",
                "title": "评分",
                "original_text": "采用综合评分法，技术分60分。",
                "normalized_requirement": "综合评分法",
                "mandatory": False,
                "risk_level": "medium",
                "evidence_required": [],
                "source_page": 2,
                "confidence": 0.8,
                "quality_level": "silver",
                "review_status": "pending",
                "generator": "rules",
                "source_url": "https://www.ccgp.gov.cn/test",
            },
        ],
    )
    write_jsonl(tmp_datasets / "silver" / "requirement_matches.jsonl", [])
    write_jsonl(tmp_datasets / "eval" / "rag" / "questions.jsonl", [])
    write_jsonl(tmp_datasets / "eval" / "agent" / "tasks.jsonl", [])
    stats = build_sft_dataset()
    assert stats["total"] >= 2
    info = json.loads((tmp_repo / "training" / "llamafactory" / "data" / "dataset_info.json").read_text())
    assert "bidpilot_sft_train" in info
    assert "bidpilot_sft_train_qwen3" in info
    train = json.loads((tmp_datasets / "sft" / "train" / "sharegpt.json").read_text())
    if train:
        assert "messages" in train[0]
        assert train[0]["messages"][-1]["role"] == "assistant"
        json.loads(train[0]["messages"][-1]["content"])
