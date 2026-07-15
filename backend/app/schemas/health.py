from typing import Literal

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"


class ServiceStatus(BaseModel):
    name: str
    status: Literal["ok", "error"]
    detail: str | None = None


class ReadyResponse(BaseModel):
    status: Literal["ok", "degraded", "error"]
    services: list[ServiceStatus]
