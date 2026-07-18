from __future__ import annotations

import io
import json
import uuid

import pytest
from app.services import chunk_tasks, document_tasks
from app.services import document as document_service
from sqlalchemy.orm import sessionmaker

from tests.test_document_upload import FakeStorage

STRUCTURED_TXT = """第一章 招标公告

一、项目概况
本项目为智慧园区综合管理平台采购项目，预算金额为人民币叁佰万元整。项目建设内容包括平台软件开发、系统集成与运维服务。
采购人现邀请符合资格条件的供应商参加投标，欢迎具备相应能力的单位积极响应。

二、投标人资格要求
（一）具有独立承担民事责任的能力，持有有效的营业执照。
（二）具有良好的商业信誉和健全的财务会计制度，近三年财务状况良好。
（三）具有履行合同所必需的设备和专业技术能力，拥有稳定的实施团队。

第二章 投标人须知

第一条 投标文件的组成
投标文件由商务文件、技术文件和资格证明文件三部分组成。商务文件应包含投标函、法定代表人授权书、开标一览表和报价明细表。
技术文件应包含技术方案、实施计划、项目管理机构设置和售后服务承诺。资格证明文件应包含营业执照副本、财务报告和类似业绩证明。

第二条 投标报价
投标报价应包含完成本项目全部工作内容所需的一切费用，包括但不限于人工费、材料费、设备费、税金和利润。
投标人应充分考虑项目实施过程中的各类风险因素，报价一经开启不得更改。

第三章 评标办法

1.1 评标原则
评标委员会遵循公平、公正、科学、择优的原则，对投标文件进行系统评审，确保评标结果客观公正。
1.2 评分标准
本项目采用综合评分法，其中商务部分占百分之三十，技术部分占百分之六十，价格部分占百分之十。
"""


@pytest.fixture()
def storage(monkeypatch) -> FakeStorage:
    fake = FakeStorage()
    monkeypatch.setattr(document_service, "get_document_storage", lambda: fake)
    monkeypatch.setattr(document_tasks, "get_document_storage", lambda: fake)
    monkeypatch.setattr(chunk_tasks, "get_document_storage", lambda: fake)
    return fake


@pytest.fixture()
def task_session_factory(monkeypatch, engine):
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    monkeypatch.setattr(document_tasks, "SESSION_FACTORY", factory)
    monkeypatch.setattr(chunk_tasks, "SESSION_FACTORY", factory)
    return factory


@pytest.fixture()
def forbid_vector_stack(monkeypatch):
    """Chunking must never touch Qdrant, embeddings or LLMs."""

    def _explode(*args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("vector/LLM stack must not be called during chunking")

    import qdrant_client

    monkeypatch.setattr(qdrant_client.QdrantClient, "__init__", _explode)
    return None


@pytest.fixture()
def project_id(client) -> str:
    response = client.post(
        "/api/v1/projects",
        json={"project_code": "CHK-001", "project_name": "切分测试项目"},
    )
    assert response.status_code == 201
    return response.json()["id"]


def _upload_txt(
    client, project_id: str, name: str = "tender.txt", text: str = STRUCTURED_TXT
) -> str:
    response = client.post(
        f"/api/v1/projects/{project_id}/documents/upload",
        files={"file": (name, io.BytesIO(text.encode("utf-8")), "text/plain")},
    )
    assert response.status_code == 201
    return response.json()["id"]


def test_upload_auto_builds_chunks(
    client, storage, task_session_factory, forbid_vector_stack, project_id
):
    doc_id = _upload_txt(client, project_id)

    detail = client.get(f"/api/v1/projects/{project_id}/documents/{doc_id}").json()
    assert detail["parse_status"] == "success"
    chunking = detail["metadata_json"]["chunking"]
    assert chunking["status"] == "success"
    assert chunking["chunk_count"] > 0
    assert chunking["chunker_name"] == "bidpilot-structure-chunker"
    assert chunking["source_sha256"] == detail["sha256"]

    listing = client.get(f"/api/v1/projects/{project_id}/documents/{doc_id}/chunks").json()
    assert listing["total"] == chunking["chunk_count"]
    indexes = [item["chunk_index"] for item in listing["items"]]
    assert indexes == list(range(len(indexes)))

    for item in listing["items"]:
        assert item["content"].strip()
        assert item["token_count"] and item["token_count"] > 0
        assert item["content_hash"] and len(item["content_hash"]) == 64
        # TXT has no reliable page numbers.
        assert item["page_start"] is None and item["page_end"] is None
        meta = item["metadata_json"]
        assert meta["chunker_name"] == "bidpilot-structure-chunker"
        assert meta["tokenizer"]
        assert meta["source_char_end"] > meta["source_char_start"]
        # Provenance: the recorded range reproduces the content from the artifact.
        assert (
            STRUCTURED_TXT[meta["source_char_start"] : meta["source_char_end"]] == item["content"]
        )

    sections = {item["section"] for item in listing["items"] if item["section"]}
    assert sections, "structured tender text must yield recognized sections"
    clauses = {item["clause_id"] for item in listing["items"] if item["clause_id"]}
    assert clauses & {"第一条", "第二条", "1.1", "1.2"} or clauses


def test_chunk_summary_reports_real_aggregates(client, storage, task_session_factory, project_id):
    doc_id = _upload_txt(client, project_id)

    summary = client.get(f"/api/v1/projects/{project_id}/documents/{doc_id}/chunk-summary").json()
    listing = client.get(
        f"/api/v1/projects/{project_id}/documents/{doc_id}/chunks", params={"limit": 200}
    ).json()

    assert summary["status"] == "success"
    assert summary["chunk_count"] == listing["total"]
    assert summary["total_tokens"] == sum(i["token_count"] for i in listing["items"])
    assert summary["section_count"] == len({i["section"] for i in listing["items"] if i["section"]})
    assert summary["chunker_version"]
    assert summary["error"] is None


def test_chunks_pagination(client, storage, task_session_factory, project_id):
    doc_id = _upload_txt(client, project_id)
    all_items = client.get(
        f"/api/v1/projects/{project_id}/documents/{doc_id}/chunks", params={"limit": 200}
    ).json()

    page = client.get(
        f"/api/v1/projects/{project_id}/documents/{doc_id}/chunks",
        params={"skip": 1, "limit": 1},
    ).json()
    assert page["total"] == all_items["total"]
    if all_items["total"] > 1:
        assert len(page["items"]) == 1
        assert page["items"][0]["chunk_index"] == 1


def test_chunk_unparsed_document_returns_409(client, storage, project_id):
    response = client.post(
        f"/api/v1/projects/{project_id}/documents",
        json={"file_name": "manual.txt"},
    )
    doc_id = response.json()["id"]

    trigger = client.post(f"/api/v1/projects/{project_id}/documents/{doc_id}/chunk")
    assert trigger.status_code == 409
    assert "解析" in trigger.json()["detail"]


def test_rebuild_does_not_duplicate_chunks(client, storage, task_session_factory, project_id):
    doc_id = _upload_txt(client, project_id)
    first = client.get(
        f"/api/v1/projects/{project_id}/documents/{doc_id}/chunks", params={"limit": 200}
    ).json()
    assert first["total"] > 0

    rebuild = client.post(f"/api/v1/projects/{project_id}/documents/{doc_id}/chunk")
    assert rebuild.status_code == 200

    second = client.get(
        f"/api/v1/projects/{project_id}/documents/{doc_id}/chunks", params={"limit": 200}
    ).json()
    assert second["total"] == first["total"]
    assert [i["chunk_index"] for i in second["items"]] == list(range(second["total"]))
    assert [i["content_hash"] for i in second["items"]] == [
        i["content_hash"] for i in first["items"]
    ]
    # qdrant_point_id stays null until step 5 wires the vector store.
    assert all(i["qdrant_point_id"] is None for i in second["items"])


def test_pdf_page_index_maps_chunk_pages(client, storage, task_session_factory, project_id, db):
    """Simulates the parsed-PDF state: extracted.txt plus a real page_index
    sidecar, then verifies chunk page ranges come from that mapping."""
    from app.models import Document

    page1 = (
        "第一章 项目概况\n本项目为数据中心机房建设工程，包含土建、机电和智能化三个标段。"
        + "项目建设周期为十二个月，质保期为两年。" * 3
    )
    page2 = (
        "第二章 投标要求\n投标人应具备电子与智能化工程专业承包壹级资质。"
        + "近三年至少完成两个类似项目业绩。" * 3
    )
    text = page1 + "\n" + page2
    spans = [
        {"page": 1, "char_start": 0, "char_end": len(page1)},
        {"page": 2, "char_start": len(page1) + 1, "char_end": len(text)},
    ]

    # Start from a real uploaded document, then rewrite its parse artifacts to
    # the simulated PDF state (text + page index sidecar).
    doc_id = _upload_txt(client, project_id, name="tender-as-pdf.txt")

    document = db.get(Document, uuid.UUID(doc_id))
    text_key = document.metadata_json["extracted_text_storage_key"]
    page_key = f"projects/{project_id}/documents/{doc_id}/parsed/page_index.json"
    storage.objects[text_key] = text.encode("utf-8")
    storage.objects[page_key] = json.dumps({"pages": spans}).encode("utf-8")
    meta = dict(document.metadata_json)
    meta["page_index_storage_key"] = page_key
    document.metadata_json = meta
    db.commit()

    chunk_tasks.run_document_chunking(uuid.UUID(doc_id))

    listing = client.get(
        f"/api/v1/projects/{project_id}/documents/{doc_id}/chunks", params={"limit": 200}
    ).json()
    assert listing["total"] > 0
    pages_seen = set()
    for item in listing["items"]:
        assert item["page_start"] is not None and item["page_end"] is not None
        assert 1 <= item["page_start"] <= item["page_end"] <= 2
        pages_seen.update(range(item["page_start"], item["page_end"] + 1))
        meta = item["metadata_json"]
        # Cross-check against the honest sidecar spans.
        covering = [
            s["page"]
            for s in spans
            if s["char_start"] < meta["core_char_end"] and s["char_end"] > meta["core_char_start"]
        ]
        assert item["page_start"] == min(covering)
        assert item["page_end"] == max(covering)
    assert pages_seen == {1, 2}


def test_chunking_failure_keeps_previous_chunks(
    client, storage, task_session_factory, project_id, monkeypatch
):
    doc_id = _upload_txt(client, project_id)
    first = client.get(
        f"/api/v1/projects/{project_id}/documents/{doc_id}/chunks", params={"limit": 200}
    ).json()
    assert first["total"] > 0

    # Break the chunker, then rebuild: old chunks must survive, status = failed.
    monkeypatch.setattr(
        chunk_tasks, "build_chunks", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    rebuild = client.post(f"/api/v1/projects/{project_id}/documents/{doc_id}/chunk")
    assert rebuild.status_code == 200

    detail = client.get(f"/api/v1/projects/{project_id}/documents/{doc_id}").json()
    assert detail["metadata_json"]["chunking"]["status"] == "failed"
    assert "boom" in detail["metadata_json"]["chunking"]["error"]

    after = client.get(
        f"/api/v1/projects/{project_id}/documents/{doc_id}/chunks", params={"limit": 200}
    ).json()
    assert after["total"] == first["total"]
