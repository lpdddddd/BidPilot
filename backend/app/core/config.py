from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "BidPilot"
    app_env: str = "development"
    debug: bool = True
    api_v1_prefix: str = "/api/v1"

    # Comma-separated list of allowed CORS origins for the dev frontend.
    # Never defaults to "*": explicit localhost origins only.
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    database_url: str = "postgresql+psycopg://bidpilot:change_me_postgres@localhost:5432/bidpilot"

    redis_url: str = "redis://localhost:6379/0"

    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "change_me_minio"
    minio_bucket: str = "bidpilot-documents"
    minio_secure: bool = False

    qdrant_url: str = "http://localhost:6333"

    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    model_name: str = "gpt-4o-mini"

    llamafactory_home: str = ""

    max_upload_size_bytes: int = 50 * 1024 * 1024
    allowed_upload_mime_types: tuple[str, ...] = (
        "application/pdf",
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.ms-excel",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "text/plain",
        "application/json",
    )
    # Extensions accepted by the upload endpoint; parsing support may still
    # mark a file failed/ocr_required after inspection.
    allowed_upload_extensions: tuple[str, ...] = (
        "pdf",
        "docx",
        "txt",
        "html",
        "htm",
        "xlsx",
    )
    presigned_url_expire_seconds: int = 15 * 60


    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
