"""
config/constants.py

Application-wide constants that are NOT configurable via environment.
These are structural/logical constants, not deployment config.
"""

# ── Collection & Index Names ───────────────────────────
QDRANT_DENSE_VECTOR_NAME = "dense"
QDRANT_SPARSE_VECTOR_NAME = "sparse"

# ── Graph Node Names ──────────────────────────────────
# Used as identifiers in LangGraph — must be unique and stable.
NODE_INPUT_FILTER = "input_filter"
NODE_CACHE_CHECK = "cache_check"
NODE_ROUTER = "router"
NODE_RETRIEVER = "retriever"
NODE_PLANNER = "planner"
NODE_WRITER = "writer"
NODE_REDUCER = "reducer"
NODE_VALIDATOR = "validator"
NODE_CACHE_WRITE = "cache_write"

# ── Query Types ────────────────────────────────────────
SIMPLE_QUERY_MAX_CHUNKS = 3    # Simple queries use fewer chunks
COMPLEX_QUERY_MAX_CHUNKS = 10  # Complex queries get full retrieval

# ── Retry & Circuit Breaker ───────────────────────────
LLM_RETRY_BACKOFF_BASE = 2.0       # Exponential backoff base (seconds)
LLM_RETRY_BACKOFF_MAX = 30.0       # Max wait between retries
LLM_CIRCUIT_BREAKER_THRESHOLD = 5  # Failures before circuit opens
LLM_CIRCUIT_BREAKER_TIMEOUT = 60   # Seconds before circuit half-opens

# ── Streaming ─────────────────────────────────────────
SSE_KEEPALIVE_INTERVAL = 15  # Seconds between keepalive pings

# ── Content Limits ────────────────────────────────────
MIN_SECTION_WORDS = 50
MAX_SECTION_WORDS = 900
MAX_PLAN_SECTIONS = 5  # Mirrors settings, but used in Pydantic validators

# ── Bulk Ingestion ────────────────────────────────────
BULK_INGEST_MAX_DOCUMENTS = 500       # Max docs per bulk request
BULK_INGEST_MAX_TOTAL_CHARS = 10_000_000  # ~10MB text total
BULK_INGEST_CONCURRENCY = 5           # Parallel doc processing within a job

# ── Worker ─────────────────────────────────────────
worker_concurrency: int = 5                # Parallel docs within a job
worker_max_jobs: int = 10                  # Max concurrent jobs per worker
worker_job_timeout_seconds: int = 3600     # 1 hour max per job
worker_health_check_interval: int = 30     # Seconds between health pings

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