import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.v1 import health
from app.api.v1.router import api_router
from app.core.config import get_settings

logger = logging.getLogger("bidpilot.api")

settings = get_settings()

app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description="BidPilot API - tender analysis and compliance review scaffold",
)

# No cookie/session auth yet, so credentials stay disabled; origins come from
# CORS_ORIGINS (comma-separated) and never default to "*".
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _error_body(message: str, detail: object) -> dict[str, object]:
    """Unified error envelope: frontend relies on `message` + `detail`."""
    return {"message": message, "detail": detail}


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(_: Request, exc: StarletteHTTPException) -> JSONResponse:
    detail = exc.detail
    message = detail if isinstance(detail, str) else "请求失败"
    return JSONResponse(
        status_code=exc.status_code,
        content=_error_body(message, detail),
        headers=getattr(exc, "headers", None),
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content=_error_body("请求参数校验失败", exc.errors()),
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    detail = str(exc) if settings.debug else "internal_server_error"
    return JSONResponse(
        status_code=500,
        content=_error_body("服务器内部错误", detail),
    )


app.include_router(health.router)
app.include_router(api_router, prefix=settings.api_v1_prefix)


@app.get("/")
def root() -> dict[str, str]:
    return {"service": settings.app_name, "docs": "/docs"}
