"""
app/dependencies.py

FastAPI dependency injection functions.

These provide access to shared resources (DB sessions, clients)
in a clean, testable way. Endpoints declare what they need,
and FastAPI injects it automatically.

In tests, you can override any of these with mock implementations.
"""

from collections.abc import AsyncGenerator

from arq import ArqRedis
from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from config import Settings, get_settings
from app.services.retrieval.hybrid import HybridRetriever
from app.services.guardrails.output_validator import OutputValidator
from app.services.llm.client import LLMClient


# ── Settings ───────────────────────────────────────────

def get_app_settings() -> Settings:
    """
    Inject settings into any endpoint or service.
    Usage: def my_endpoint(settings: Settings = Depends(get_app_settings))
    """
    return get_settings()


# ── Database Session ───────────────────────────────────

async def get_db_session(request: Request) -> AsyncGenerator[AsyncSession, None]:
    """
    Yields an async SQLAlchemy session per request.
    Automatically commits on success, rolls back on exception.

    The session factory is stored on app.state during lifespan startup.
    """
    session_factory = request.app.state.db_session_factory

    async with session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# ── Qdrant Client ─────────────────────────────────────

def get_qdrant_client(request: Request):
    """
    Returns the Qdrant client initialized during app startup.
    Stored on app.state.qdrant_client.
    """
    return request.app.state.qdrant_client


# ── Redis Client ──────────────────────────────────────

def get_redis_client(request: Request):
    """
    Returns the Redis client initialized during app startup.
    Stored on app.state.redis_client.
    """
    return request.app.state.redis_client


# ── Embedding Model ───────────────────────────────────

def get_embedder(request: Request):
    """
    Returns the embedding model loaded during app startup.
    Stored on app.state.embedder.
    """
    return request.app.state.embedder

# Add to existing dependencies.py:

async def get_arq_pool(request: Request) -> ArqRedis:
    """
    Returns the ARQ Redis pool for enqueuing background jobs.
    Initialized during app lifespan startup.
    """
    return request.app.state.arq_pool

def get_vector_store(request: Request):
    """Returns the VectorStoreService initialized during app startup."""
    return request.app.state.vector_store


def get_hybrid_retriever(request: Request):
    """
    Returns a configured HybridRetriever.
    Assembled from services on app.state.
    """

    return HybridRetriever(
        embedder=request.app.state.embedder,
        vector_store=request.app.state.vector_store,
        reranker=getattr(request.app.state, "reranker", None),
    )

def get_cache_manager(request: Request):
    """
    Returns a CacheManager instance.
    Uses the Redis client from app.state (may be None if Redis unavailable).
    """
    from app.services.cache.manager import CacheManager

    redis_client = getattr(request.app.state, "redis_client", None)
    return CacheManager(redis=redis_client)

# Add to existing app/dependencies.py:

def get_compiled_graph(request: Request):
    """
    Returns the compiled LangGraph DAG.
    Built once during app startup and reused for all requests.
    """
    return request.app.state.compiled_graph


def get_llm_client(request: Request) -> LLMClient:
    """Returns the LLM client."""
    return request.app.state.llm_client


def get_output_validator(request: Request) -> OutputValidator:
    """Returns the output validator."""
    return request.app.state.output_validator