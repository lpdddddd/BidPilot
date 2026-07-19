from __future__ import annotations

from dataclasses import dataclass

from minio import Minio
from opensearchpy import OpenSearch
from qdrant_client import QdrantClient
from redis import Redis
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str | None = None


def check_postgres(db: Session) -> CheckResult:
    try:
        db.execute(text("SELECT 1"))
        return CheckResult(name="postgres", ok=True)
    except Exception as exc:  # noqa: BLE001 - readiness must surface any failure
        return CheckResult(name="postgres", ok=False, detail=str(exc))


def check_redis(settings: Settings | None = None) -> CheckResult:
    settings = settings or get_settings()
    try:
        client = Redis.from_url(settings.redis_url, socket_connect_timeout=2)
        client.ping()
        client.close()
        return CheckResult(name="redis", ok=True)
    except Exception as exc:  # noqa: BLE001
        return CheckResult(name="redis", ok=False, detail=str(exc))


def get_minio_client(settings: Settings | None = None) -> Minio:
    settings = settings or get_settings()
    return Minio(
        settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        secure=settings.minio_secure,
    )


def check_minio(settings: Settings | None = None) -> CheckResult:
    settings = settings or get_settings()
    try:
        client = get_minio_client(settings)
        client.bucket_exists(settings.minio_bucket)
        return CheckResult(name="minio", ok=True)
    except Exception as exc:  # noqa: BLE001
        return CheckResult(name="minio", ok=False, detail=str(exc))


def check_qdrant(settings: Settings | None = None) -> CheckResult:
    settings = settings or get_settings()
    try:
        client = QdrantClient(url=settings.qdrant_url, timeout=2)
        client.get_collections()
        return CheckResult(name="qdrant", ok=True)
    except Exception as exc:  # noqa: BLE001
        return CheckResult(name="qdrant", ok=False, detail=str(exc))


def get_opensearch_client(settings: Settings | None = None) -> OpenSearch:
    settings = settings or get_settings()
    return OpenSearch(hosts=[settings.opensearch_url], timeout=10)


def check_opensearch(settings: Settings | None = None) -> CheckResult:
    settings = settings or get_settings()
    try:
        client = OpenSearch(hosts=[settings.opensearch_url], timeout=2)
        health = client.cluster.health()
        cluster_status = health.get("status")
        if cluster_status in ("green", "yellow"):
            return CheckResult(name="opensearch", ok=True)
        return CheckResult(name="opensearch", ok=False, detail=f"cluster status {cluster_status}")
    except Exception as exc:  # noqa: BLE001
        return CheckResult(name="opensearch", ok=False, detail=str(exc))
