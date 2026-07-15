from typing import Literal

from sqlalchemy.orm import Session

from app.schemas.health import ReadyResponse, ServiceStatus
from app.services.infra_clients import (
    check_minio,
    check_postgres,
    check_qdrant,
    check_redis,
)


class HealthService:
    def readiness(self, db: Session) -> ReadyResponse:
        results = [
            check_postgres(db),
            check_redis(),
            check_minio(),
            check_qdrant(),
        ]
        services = [
            ServiceStatus(
                name=item.name,
                status="ok" if item.ok else "error",
                detail=item.detail,
            )
            for item in results
        ]
        ok_count = sum(1 for item in results if item.ok)
        status: Literal["ok", "degraded", "error"]
        if ok_count == len(results):
            status = "ok"
        elif ok_count == 0:
            status = "error"
        else:
            status = "degraded"
        return ReadyResponse(status=status, services=services)
