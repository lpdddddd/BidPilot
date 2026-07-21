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
    qdrant_collection_name: str = "bidpilot_chunks_v1"

    opensearch_url: str = "http://localhost:9200"
    opensearch_index_name: str = "bidpilot_chunks_v1"

    embedding_provider: str = "sentence_transformers"
    embedding_model_name: str = "BAAI/bge-small-zh-v1.5"
    # bge family recommends an instruction prefix for short queries.
    embedding_query_prefix: str = "为这个句子生成表示以用于检索相关文章："
    embedding_batch_size: int = 32
    reranker_model_name: str = "BAAI/bge-reranker-base"

    retrieval_dense_top_k: int = 30
    retrieval_bm25_top_k: int = 30
    retrieval_fusion_top_k: int = 20
    retrieval_rerank_top_k: int = 8
    retrieval_rrf_k: int = 60
    retrieval_dense_weight: float = 1.0
    retrieval_bm25_weight: float = 1.0

    # Reserved OpenAI-compatible settings (data pipeline / legacy).
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    model_name: str = "gpt-4o-mini"

    # Grounded RAG (vLLM OpenAI-compatible). Separate from OPENAI_* so local
    # Qwen3-8B and cloud OpenAI configs do not collide.
    llm_enabled: bool = False
    llm_base_url: str = "http://localhost:8001/v1"
    llm_api_key: str = "local"
    llm_model: str = "bidpilot-qwen3-8b"
    llm_timeout_seconds: float = 120.0
    llm_max_tokens: int = 1024
    llm_temperature: float = 0.1
    rag_context_top_k: int = 8
    rag_max_context_tokens: int = 10000
    # bge-reranker-base emits unbounded logits; keep a low floor so weakly
    # related chunks are dropped without being overly aggressive.
    rag_min_rerank_score: float = -5.0

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
