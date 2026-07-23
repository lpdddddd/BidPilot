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
    from app.services.llm_model_resolve import resolve_llm_load_target
    from app.services.model_registry import public_model_info

    probe = get_llm_client().health_check()
    try:
        load_target = resolve_llm_load_target()
    except FileNotFoundError as exc:
        load_target = f"invalid:{exc}"
    info = public_model_info()
    finetune = info.get("active_finetune") or {}
    extra = []
    if finetune.get("display_name"):
        extra.append(f"finetune={finetune.get('display_name')}")
    if finetune.get("version"):
        extra.append(f"version={finetune.get('version')}")
    if finetune.get("train_track"):
        extra.append(f"track={finetune.get('train_track')}")
    detail = probe["detail"]
    parts = [p for p in [detail, f"load_target={load_target}", *extra] if p]
    return LlmHealthResponse(
        status=probe["status"],
        enabled=probe["enabled"],
        model=probe["model"],
        base_url=probe["base_url"],
        reachable=probe["reachable"],
        detail="; ".join(parts),
        latency_ms=probe["latency_ms"],
    )


@router.get("/api/v1/models/active")
def active_model() -> dict:
    """Public active model / LoRA registration info for UI."""
    from app.services.model_registry import public_model_info

    return public_model_info()
