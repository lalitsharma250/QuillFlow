"""
app/services/cache/connection.py

Redis connection management.

Uses redis.asyncio for non-blocking operations.
Connection is initialized once at app startup and shared across all requests.
"""

from __future__ import annotations

import structlog
from fastapi import FastAPI
from redis.asyncio import Redis, ConnectionPool

from config import get_settings

logger = structlog.get_logger(__name__)


async def init_redis(app: FastAPI) -> None:
    """
    Initialize Redis connection pool.
    Called during FastAPI lifespan startup.

    Stores on app.state:
      - redis_client: The async Redis client
      - redis_pool: The connection pool (for cleanup)
    """
    settings = get_settings()

    pool = ConnectionPool.from_url(
        settings.redis_url,
        max_connections=20,
        decode_responses=False,  # We handle encoding ourselves (binary for embeddings)
        socket_connect_timeout=5,
        socket_timeout=5,
        retry_on_timeout=True,
    )

    client = Redis(connection_pool=pool)

    # Verify connection
    try:
        await client.ping()
        logger.info("redis_connected", url=settings.redis_url)
    except Exception as e:
        logger.error("redis_connection_failed", error=str(e))
        # Don't crash the app — cache is optional, not critical
        # Services will check if redis is available before using it
        app.state.redis_client = None
        app.state.redis_pool = None
        return

    app.state.redis_client = client
    app.state.redis_pool = pool


async def close_redis(app: FastAPI) -> None:
    """Close Redis connection pool."""
    client: Redis | None = getattr(app.state, "redis_client", None)
    pool: ConnectionPool | None = getattr(app.state, "redis_pool", None)

    if client is not None:
        await client.aclose()

    if pool is not None:
        await pool.disconnect()

    logger.info("redis_disconnected")