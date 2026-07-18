from __future__ import annotations

import hashlib
import re
import unicodedata
from uuid import UUID, uuid4

from fastapi import HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import Document
from app.models.enums import DocumentType, ParseStatus
from app.repositories.document import DocumentRepository
from app.repositories.project import ProjectRepository
from app.schemas.document import (
    DocumentCreate,
    DocumentDownloadResponse,
    DocumentListResponse,
    DocumentPreviewResponse,
    DocumentRead,
)
from app.services.storage import StorageError, get_document_storage

_UPLOAD_CHUNK_SIZE = 1024 * 1024
_PREVIEW_MAX_CHARS = 5000

# Filename keywords for a light, honest document_type guess (metadata only).
_TYPE_KEYWORDS: tuple[tuple[str, DocumentType], ...] = (
    ("招标", DocumentType.tender),
    ("采购文件", DocumentType.tender),
    ("公告", DocumentType.announcement),
    ("澄清", DocumentType.amendment),
    ("答疑", DocumentType.amendment),
    ("补遗", DocumentType.amendment),
    ("变更", DocumentType.amendment),
    ("中标", DocumentType.result),
    ("成交结果", DocumentType.result),
    ("合同", DocumentType.contract),
    ("资质", DocumentType.qualification),
)


def _safe_file_name(raw_name: str) -> str:
    name = raw_name.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    name = unicodedata.normalize("NFKC", name)
    name = re.sub(r"[\x00-\x1f\x7f]", "", name).strip()
    if len(name) > 255:
        stem, _, ext = name.rpartition(".")
        name = f"{stem[: 250 - len(ext)]}.{ext}" if stem else name[:255]
    return name


def _infer_document_type(file_name: str) -> DocumentType:
    for keyword, doc_type in _TYPE_KEYWORDS:
        if keyword in file_name:
            return doc_type
    return DocumentType.other


class DocumentService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.documents = DocumentRepository(db)
        self.projects = ProjectRepository(db)
        self.settings = get_settings()

    # ---------------------------------------------------------------- helpers

    def _require_project(self, project_id: UUID):  # noqa: ANN202
        project = self.projects.get_by_id(project_id)
        if project is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Project not found",
            )
        return project

    def _require_document(self, project_id: UUID, document_id: UUID) -> Document:
        document = self.db.get(Document, document_id)
        if document is None or document.project_id != project_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Document not found",
            )
        return document

    def _read_limited(self, upload: UploadFile) -> bytes:
        """Stream the upload into memory, enforcing the size limit while
        reading instead of trusting Content-Length."""
        max_size = self.settings.max_upload_size_bytes
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = upload.file.read(_UPLOAD_CHUNK_SIZE)
            if not chunk:
                break
            total += len(chunk)
            if total > max_size:
                raise HTTPException(
                    status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                    detail=f"文件超过大小限制（最大 {max_size} 字节）",
                )
            chunks.append(chunk)
        return b"".join(chunks)

    # ----------------------------------------------------------------- upload

    def upload_document(
        self,
        project_id: UUID,
        upload: UploadFile,
        *,
        document_type_raw: str | None,
    ) -> DocumentRead:
        project = self._require_project(project_id)

        raw_name = upload.filename or ""
        file_name = _safe_file_name(raw_name)
        if not file_name or "." not in file_name:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="文件名无效或缺少扩展名",
            )

        extension = file_name.rsplit(".", 1)[-1].lower()
        if extension not in self.settings.allowed_upload_extensions:
            allowed = ", ".join(self.settings.allowed_upload_extensions)
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail=f"不支持的文件类型 .{extension}（支持: {allowed}）",
            )

        if document_type_raw:
            try:
                document_type = DocumentType(document_type_raw)
            except ValueError as exc:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"无效的文档类型: {document_type_raw}",
                ) from exc
        else:
            document_type = _infer_document_type(file_name)

        content = self._read_limited(upload)
        if not content:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="上传的文件为空",
            )
        sha256 = hashlib.sha256(content).hexdigest()

        document_id = uuid4()
        storage_key = f"projects/{project_id}/documents/{document_id}/original/{file_name}"

        storage = get_document_storage()
        try:
            storage.put_bytes(
                storage_key,
                content,
                content_type=upload.content_type or "application/octet-stream",
            )
        except StorageError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"对象存储不可用：{exc}",
            ) from exc

        try:
            document = Document(
                id=document_id,
                project_id=project.id,
                organization_id=project.organization_id,
                document_type=document_type,
                file_name=file_name,
                mime_type=upload.content_type,
                storage_bucket=self.settings.minio_bucket,
                storage_key=storage_key,
                sha256=sha256,
                file_size=len(content),
                parse_status=ParseStatus.pending,
                metadata_json={"original_file_name": raw_name, "source_sha256": sha256},
            )
            self.db.add(document)
            self.db.commit()
            self.db.refresh(document)
        except Exception:
            # Do not leave an untracked object behind if the DB write failed.
            self.db.rollback()
            storage.remove(storage_key)
            raise

        return DocumentRead.model_validate(document)

    # ---------------------------------------------------------------- reparse

    def request_reparse(self, project_id: UUID, document_id: UUID) -> DocumentRead:
        document = self._require_document(project_id, document_id)
        document.parse_status = ParseStatus.pending
        meta = dict(document.metadata_json or {})
        meta["parse_error"] = None
        document.metadata_json = meta
        self.db.commit()
        self.db.refresh(document)
        return DocumentRead.model_validate(document)

    # ----------------------------------------------------------------- reads

    def get_document(self, project_id: UUID, document_id: UUID) -> DocumentRead:
        document = self._require_document(project_id, document_id)
        return DocumentRead.model_validate(document)

    def get_preview(
        self,
        project_id: UUID,
        document_id: UUID,
        *,
        max_chars: int = _PREVIEW_MAX_CHARS,
    ) -> DocumentPreviewResponse:
        document = self._require_document(project_id, document_id)
        meta = document.metadata_json or {}

        if document.parse_status != ParseStatus.success:
            reason = meta.get("parse_error") or f"当前解析状态为 {document.parse_status.value}"
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"文档尚未成功解析，无法预览（{reason}）",
            )

        text_key = meta.get("extracted_text_storage_key")
        if not isinstance(text_key, str) or not text_key:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="解析产物缺失，请尝试重新解析",
            )

        try:
            text = get_document_storage().get_bytes(text_key).decode("utf-8")
        except StorageError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"读取解析产物失败：{exc}",
            ) from exc

        truncated = len(text) > max_chars
        extracted_characters = meta.get("extracted_characters")
        return DocumentPreviewResponse(
            document_id=document.id,
            parse_status=document.parse_status,
            page_count=document.page_count,
            extracted_characters=(
                extracted_characters if isinstance(extracted_characters, int) else len(text)
            ),
            preview=text[:max_chars],
            truncated=truncated,
            max_chars=max_chars,
        )

    def get_download(self, project_id: UUID, document_id: UUID) -> DocumentDownloadResponse:
        document = self._require_document(project_id, document_id)
        try:
            url = get_document_storage().presigned_download_url(
                document.storage_key,
                file_name=document.file_name,
            )
        except StorageError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"生成下载链接失败：{exc}",
            ) from exc
        return DocumentDownloadResponse(
            download_url=url,
            expires_in_seconds=self.settings.presigned_url_expire_seconds,
            file_name=document.file_name,
        )

    # ------------------------------------------------------- legacy endpoints

    def create_metadata(self, project_id: UUID, data: DocumentCreate) -> DocumentRead:
        project = self._require_project(project_id)

        if data.file_size is not None and data.file_size > self.settings.max_upload_size_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail=f"File exceeds max size of {self.settings.max_upload_size_bytes} bytes",
            )

        if data.mime_type and data.mime_type not in self.settings.allowed_upload_mime_types:
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail=f"Unsupported mime type: {data.mime_type}",
            )

        storage_bucket = data.storage_bucket or self.settings.minio_bucket
        storage_key = (
            data.storage_key or f"projects/{project_id}/documents/{uuid4()}/{data.file_name}"
        )

        document = self.documents.create(
            project_id=project.id,
            organization_id=project.organization_id,
            data=data,
            storage_bucket=storage_bucket,
            storage_key=storage_key,
        )
        self.db.commit()
        self.db.refresh(document)
        return DocumentRead.model_validate(document)

    def list_documents(
        self,
        project_id: UUID,
        *,
        skip: int = 0,
        limit: int = 100,
    ) -> DocumentListResponse:
        self._require_project(project_id)
        items, total = self.documents.list_by_project(
            project_id=project_id,
            skip=skip,
            limit=limit,
        )
        return DocumentListResponse(
            items=[DocumentRead.model_validate(item) for item in items],
            total=total,
        )
