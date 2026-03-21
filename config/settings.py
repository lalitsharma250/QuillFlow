"""
config/settings.py

Single source of truth for all application configuration.
Every setting comes from environment variables (12-factor app).
Never hardcode secrets or connection strings.
"""

from functools import lru_cache

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    All config is read from environment variables prefixed with QUILL_.
    Example: QUILL_LLM_API_KEY=sk-... maps to settings.llm_api_key

    For local dev, values are read from .env file automatically.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="QUILL_",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── App ────────────────────────────────────────────
    app_name: str = "QuillFlow"
    app_version: str = "0.1.0"
    debug: bool = False
    log_level: str = "INFO"
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    # ── LLM Provider ──────────────────────────────────
    llm_provider_base_url: str = "https://openrouter.ai/api/v1"
    llm_api_key: SecretStr = Field(description="API key for LLM provider (OpenRouter)")
    llm_model_fast: str = "anthropic/claude-sonnet-4-20250514"
    llm_model_strong: str = "anthropic/claude-opus-4-6"
    llm_max_retries: int = 3
    llm_timeout_seconds: int = 60
    llm_max_tokens_per_request: int = 4096

    # ── Embeddings ─────────────────────────────────────
    embedding_model_name: str = "BAAI/bge-large-en-v1.5"
    embedding_dimensions: int = 1024
    embedding_batch_size: int = 64

    # ── Qdrant ─────────────────────────────────────────
    qdrant_host: str = "localhost"
    qdrant_port: int = 6333
    qdrant_grpc_port: int = 6334
    qdrant_collection_name: str = "quillflow_chunks"
    qdrant_prefer_grpc: bool = True

    # ── Postgres ───────────────────────────────────────
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_user: str = "quillflow"
    postgres_password: SecretStr = Field(default=SecretStr("quillflow_dev"))
    postgres_db: str = "quillflow"

    @property
    def postgres_dsn(self) -> str:
        """Async Postgres connection string for SQLAlchemy."""
        password = self.postgres_password.get_secret_value()
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    # ── Redis ──────────────────────────────────────────
    redis_url: str = "redis://localhost:6379/0"
    cache_ttl_seconds: int = 86400          # 24 hours
    semantic_cache_threshold: float = 0.95  # Cosine similarity for cache hit

    @property
    def worker_redis_settings(self) -> dict:
        """Parse redis_url into ARQ-compatible dict."""
        from urllib.parse import urlparse

        parsed = urlparse(self.redis_url)
        return {
            "host": parsed.hostname or "localhost",
            "port": parsed.port or 6379,
            "database": int(parsed.path.lstrip("/") or 0),
        }
    
    # ── JWT Authentication ─────────────────────────────
    jwt_secret_key: str = "change-this-to-a-random-secret-in-production"
    jwt_access_token_hours: int = 1          # Access token expires in 1 hour
    jwt_refresh_token_days: int = 7          # Refresh token expires in 7 days

    @field_validator("jwt_secret_key")
    @classmethod
    def validate_jwt_secret(cls, v: str) -> str:
        if v in ("change-this-to-a-random-secret-in-production", ""):
            import warnings
            warnings.warn(
                "⚠️  JWT_SECRET_KEY is using default value! "
                "Set QUILL_JWT_SECRET_KEY in .env for security.",
                stacklevel=2,
            )
        if len(v) < 16:
            raise ValueError("JWT secret key must be at least 16 characters")
        return v
     # ── Worker ─────────────────────────────────────────
    worker_concurrency: int = 5
    worker_max_jobs: int = 10
    worker_job_timeout_seconds: int = 3600
    worker_health_check_interval: int = 30

    # ── Guardrails ─────────────────────────────────────
    max_plan_sections: int = 5
    max_total_tokens_per_request: int = 20_000
    max_input_length: int = 10_000          # Characters

    # ── Retrieval ──────────────────────────────────────
    retrieval_top_k: int = 10               # Chunks to retrieve
    reranker_top_k: int = 5                 # Chunks after reranking
    chunk_size: int = 512                   # Tokens per chunk
    chunk_overlap: int = 64                 # Token overlap between chunks

    # ── Evaluation Thresholds ──────────────────────────
    eval_faithfulness_threshold: float = 0.7
    eval_relevancy_threshold: float = 0.7
    eval_context_precision_threshold: float = 0.7

    # ── Data Leakage Prevention ────────────────────────
    dlp_enabled: bool = True
    dlp_strip_pii_before_llm: bool = True       # Strip PII from prompts
    dlp_scan_output: bool = True                 # Scan LLM output for leaked PII
    dlp_allowed_llm_providers: list[str] = [     # Which providers can receive data
        "anthropic",
        "openai",
    ]
    dlp_block_sensitive_topics: bool = False
    
    relevancy_threshold: float = 0.1  # Minimum score to include in sources (for both API and UI)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Cached settings singleton.
    Call this instead of Settings() directly — avoids re-reading .env on every call.
    """
    return Settings()