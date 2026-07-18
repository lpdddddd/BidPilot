from __future__ import annotations

import hashlib
import io

import pytest
from app.core.config import get_settings
from app.services import chunk_tasks, document_tasks
from app.services import document as document_service
from app.services.storage import StorageError
from sqlalchemy.orm import sessionmaker


class FakeStorage:
    """In-memory stand-in for MinIO used by unit tests."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.removed: list[str] = []

    def put_bytes(self, key: str, data: bytes, *, content_type: str) -> None:
        self.objects[key] = data

    def get_bytes(self, key: str) -> bytes:
        if key not in self.objects:
            raise StorageError(f"missing object: {key}")
        return self.objects[key]

    def remove(self, key: str) -> None:
        self.removed.append(key)
        self.objects.pop(key, None)

    def presigned_download_url(self, key: str, *, file_name: str) -> str:
        if key not in self.objects:
            raise StorageError(f"missing object: {key}")
        return f"http://fake-minio/{key}?signed=1"


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
def project_id(client) -> str:
    response = client.post(
        "/api/v1/projects",
        json={"project_code": "UPL-001", "project_name": "上传测试项目"},
    )
    assert response.status_code == 201
    return response.json()["id"]


def _upload(client, project_id: str, file_name: str, content: bytes, **form):
    return client.post(
        f"/api/v1/projects/{project_id}/documents/upload",
        files={"file": (file_name, io.BytesIO(content), "application/octet-stream")},
        data=form,
    )


def _blank_pdf_bytes() -> bytes:
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def test_upload_txt_success(client, storage, task_session_factory, project_id):
    content = "招标编号 ZB-2026-001\n本项目为测试采购。".encode()
    response = _upload(client, project_id, "tender_notes.txt", content)
    assert response.status_code == 201
    body = response.json()
    assert body["file_name"] == "tender_notes.txt"
    assert body["sha256"] == hashlib.sha256(content).hexdigest()
    assert body["file_size"] == len(content)

    # Original object landed under the project-scoped key.
    original_key = body["storage_key"]
    assert original_key.startswith(f"projects/{project_id}/documents/{body['id']}/original/")
    assert storage.objects[original_key] == content

    # Background parse ran with its own session and produced the artifact.
    detail = client.get(f"/api/v1/projects/{project_id}/documents/{body['id']}")
    assert detail.status_code == 200
    parsed = detail.json()
    assert parsed["parse_status"] == "success"
    meta = parsed["metadata_json"]
    assert meta["extracted_characters"] == len(content.decode("utf-8"))
    text_key = meta["extracted_text_storage_key"]
    assert text_key.endswith("/parsed/extracted.txt")
    assert storage.objects[text_key].decode("utf-8") == content.decode("utf-8")

    preview = client.get(f"/api/v1/projects/{project_id}/documents/{body['id']}/preview")
    assert preview.status_code == 200
    assert "招标编号" in preview.json()["preview"]
    assert preview.json()["truncated"] is False

    download = client.get(f"/api/v1/projects/{project_id}/documents/{body['id']}/download")
    assert download.status_code == 200
    assert download.json()["download_url"].startswith("http://fake-minio/")


def test_upload_document_type_inference(client, storage, task_session_factory, project_id):
    response = _upload(client, project_id, "某项目招标文件.txt", "内容".encode())
    assert response.status_code == 201
    assert response.json()["document_type"] == "tender"

    response = _upload(client, project_id, "notes.txt", "内容".encode(), document_type="contract")
    assert response.status_code == 201
    assert response.json()["document_type"] == "contract"


def test_upload_too_large_returns_413(client, storage, project_id, monkeypatch):
    monkeypatch.setattr(get_settings(), "max_upload_size_bytes", 128)
    response = _upload(client, project_id, "big.txt", b"x" * 1024)
    assert response.status_code == 413
    assert "message" in response.json()


def test_upload_unsupported_type_returns_415(client, storage, project_id):
    response = _upload(client, project_id, "legacy.doc", b"old word file")
    assert response.status_code == 415
    body = response.json()
    assert "message" in body and "detail" in body
    # Nothing should be written to storage for rejected uploads.
    assert storage.objects == {}


def test_scanned_pdf_marked_ocr_required(client, storage, task_session_factory, project_id):
    response = _upload(client, project_id, "scanned.pdf", _blank_pdf_bytes())
    assert response.status_code == 201
    doc_id = response.json()["id"]

    detail = client.get(f"/api/v1/projects/{project_id}/documents/{doc_id}").json()
    assert detail["parse_status"] == "ocr_required"
    assert detail["is_scanned"] is True
    assert detail["page_count"] == 1
    assert "OCR" in detail["metadata_json"]["parse_error"]

    preview = client.get(f"/api/v1/projects/{project_id}/documents/{doc_id}/preview")
    assert preview.status_code == 409
    assert "message" in preview.json()


def test_preview_unparsed_document_returns_conflict(client, storage, project_id):
    # Metadata-only document stays pending because no parse task is scheduled.
    response = client.post(
        f"/api/v1/projects/{project_id}/documents",
        json={"file_name": "manual.txt"},
    )
    assert response.status_code == 201
    doc_id = response.json()["id"]

    preview = client.get(f"/api/v1/projects/{project_id}/documents/{doc_id}/preview")
    assert preview.status_code == 409
    assert "pending" in preview.json()["detail"]


def test_reparse_resets_and_reruns(client, storage, task_session_factory, project_id):
    response = _upload(client, project_id, "readme.txt", "第一版内容".encode())
    assert response.status_code == 201
    doc_id = response.json()["id"]

    reparse = client.post(f"/api/v1/projects/{project_id}/documents/{doc_id}/reparse")
    assert reparse.status_code == 200
    # The response is produced before the background task runs.
    assert reparse.json()["parse_status"] == "pending"

    detail = client.get(f"/api/v1/projects/{project_id}/documents/{doc_id}").json()
    assert detail["parse_status"] == "success"


def test_binary_disguised_as_txt_fails_parsing(client, storage, task_session_factory, project_id):
    # PNG header + random-ish binary payload renamed to .txt must not be
    # reported as successfully parsed text.
    binary = b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 8
    response = _upload(client, project_id, "disguised.txt", binary)
    assert response.status_code == 201
    doc_id = response.json()["id"]

    detail = client.get(f"/api/v1/projects/{project_id}/documents/{doc_id}").json()
    assert detail["parse_status"] == "failed"
    assert detail["metadata_json"]["parse_error"]
    # No extracted artifact should exist for a failed parse.
    assert not any(key.endswith("extracted.txt") for key in storage.objects)


def test_utf8_and_gbk_txt_still_parse(client, storage, task_session_factory, project_id):
    utf8 = _upload(client, project_id, "utf8.txt", "第一章 总则：本项目为测试。".encode())
    assert utf8.status_code == 201
    detail = client.get(f"/api/v1/projects/{project_id}/documents/{utf8.json()['id']}").json()
    assert detail["parse_status"] == "success"

    gbk = _upload(client, project_id, "gbk.txt", "第一章 总则：本项目为测试。".encode("gb18030"))
    assert gbk.status_code == 201
    detail = client.get(f"/api/v1/projects/{project_id}/documents/{gbk.json()['id']}").json()
    assert detail["parse_status"] == "success"


def test_upload_storage_failure_returns_503(client, project_id, monkeypatch):
    class BrokenStorage(FakeStorage):
        def put_bytes(self, key: str, data: bytes, *, content_type: str) -> None:
            raise StorageError("minio down")

    monkeypatch.setattr(document_service, "get_document_storage", lambda: BrokenStorage())
    response = _upload(client, project_id, "notes.txt", b"content")
    assert response.status_code == 503

    listing = client.get(f"/api/v1/projects/{project_id}/documents").json()
    assert listing["total"] == 0
