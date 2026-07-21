"""Health routes are mounted at application root; this module is kept for reuse."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.ask import LlmHealthResponse
from app.schemas.health import HealthResponse, ReadyResponse
from app.services.health import HealthService
from app.services.llm_client import get_llm_client

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok")


@router.get("/ready", response_model=ReadyResponse)
def ready(db: Session = Depends(get_db)) -> ReadyResponse:
    return HealthService().readiness(db)


@router.get("/api/v1/health/llm", response_model=LlmHealthResponse)
def llm_health() -> LlmHealthResponse:
    """Real LLM connectivity probe. Does not claim the model is warm if unreachable."""
    probe = get_llm_client().health_check()
    return LlmHealthResponse(
        status=probe["status"],
        enabled=probe["enabled"],
        model=probe["model"],
        base_url=probe["base_url"],
        reachable=probe["reachable"],
        detail=probe["detail"],
        latency_ms=probe["latency_ms"],
    )
