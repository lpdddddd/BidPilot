"""MinIO-backed object storage for original files and parse artifacts.

All access goes through DocumentStorage so tests can substitute an in-memory
implementation via `get_document_storage` monkeypatching.
"""

from __future__ import annotations

import contextlib
import io
from datetime import timedelta

from app.core.config import Settings, get_settings
from app.services.infra_clients import get_minio_client


class StorageError(RuntimeError):
    """Raised when the object storage backend is unavailable or fails."""


class DocumentStorage:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.bucket = self.settings.minio_bucket

    def _client(self):  # noqa: ANN202 - Minio client type kept internal
        return get_minio_client(self.settings)

    def put_bytes(self, key: str, data: bytes, *, content_type: str) -> None:
        try:
            self._client().put_object(
                self.bucket,
                key,
                io.BytesIO(data),
                length=len(data),
                content_type=content_type,
            )
        except Exception as exc:  # noqa: BLE001 - surface any backend failure
            raise StorageError(f"对象存储写入失败: {exc}") from exc

    def get_bytes(self, key: str) -> bytes:
        response = None
        try:
            response = self._client().get_object(self.bucket, key)
            return bytes(response.read())
        except Exception as exc:  # noqa: BLE001
            raise StorageError(f"对象存储读取失败: {exc}") from exc
        finally:
            if response is not None:
                response.close()
                response.release_conn()

    def remove(self, key: str) -> None:
        """Best-effort delete used for upload rollback; never raises."""
        with contextlib.suppress(Exception):
            self._client().remove_object(self.bucket, key)

    def presigned_download_url(self, key: str, *, file_name: str) -> str:
        try:
            url = self._client().presigned_get_object(
                self.bucket,
                key,
                expires=timedelta(seconds=self.settings.presigned_url_expire_seconds),
                response_headers={
                    "response-content-disposition": f'attachment; filename="{file_name}"'
                },
            )
            return str(url)
        except Exception as exc:  # noqa: BLE001
            raise StorageError(f"生成下载链接失败: {exc}") from exc


def get_document_storage() -> DocumentStorage:
    return DocumentStorage()
