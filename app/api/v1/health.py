"""
app/api/v1/health.py

Health check endpoints.
Two levels:
  - /health          — Basic liveness (is the process running?)
  - /v1/health       — Deep health (check all dependencies)
"""

from __future__ import annotations

import time

import structlog
from fastapi import APIRouter, Request

from app.models.responses import ComponentHealth, HealthResponse
from config import get_settings

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["system"])


@router.get("/health", response_model=HealthResponse)
async def deep_health_check(request: Request) -> HealthResponse:
    """
    Deep health check — verifies all dependencies are reachable.

    Checks:
      - Postgres: connection pool alive
      - Qdrant: collection accessible
      - Redis: ping succeeds
      - LLM: circuit breaker status
      - Embedding model: loaded in memory
    """
    settings = get_settings()
    checks: dict[str, ComponentHealth] = {}

    # ── Postgres ───────────────────────────────────────
    checks["postgres"] = await _check_postgres(request)

    # ── Qdrant ─────────────────────────────────────────
    checks["qdrant"] = await _check_qdrant(request)

    # ── Redis ──────────────────────────────────────────
    checks["redis"] = await _check_redis(request)

    # ── LLM ────────────────────────────────────────────
    checks["llm"] = _check_llm(request)

    # ── Embedding Model ────────────────────────────────
    checks["embedder"] = _check_embedder(request)

    # ── Graph ──────────────────────────────────────────
    checks["graph"] = _check_graph(request)

    # Overall status
    all_healthy = all(c.status == "healthy" for c in checks.values())
    any_unhealthy = any(c.status == "unhealthy" for c in checks.values())

    if all_healthy:
        overall = "healthy"
    elif any_unhealthy:
        overall = "unhealthy"
    else:
        overall = "degraded"

    return HealthResponse(
        status=overall,
        service="quillflow",
        version=settings.app_version,
        checks=checks,
    )


async def _check_postgres(request: Request) -> ComponentHealth:
    """Check Postgres connectivity."""
    session_factory = getattr(request.app.state, "db_session_factory", None)
    if session_factory is None:
        return ComponentHealth(status="unhealthy", message="Not initialized")

    start = time.monotonic()
    try:
        from sqlalchemy import text

        async with session_factory() as session:
            await session.execute(text("SELECT 1"))
        latency = (time.monotonic() - start) * 1000
        return ComponentHealth(status="healthy", latency_ms=round(latency, 2))
    except Exception as e:
        latency = (time.monotonic() - start) * 1000
        return ComponentHealth(
            status="unhealthy",
            latency_ms=round(latency, 2),
            message=str(e)[:200],
        )


async def _check_qdrant(request: Request) -> ComponentHealth:
    """Check Qdrant connectivity."""
    vector_store = getattr(request.app.state, "vector_store", None)
    if vector_store is None:
        return ComponentHealth(status="unhealthy", message="Not initialized")

    try:
        is_healthy, latency = await vector_store.health_check()
        return ComponentHealth(
            status="healthy" if is_healthy else "unhealthy",
            latency_ms=latency,
        )
    except Exception as e:
        return ComponentHealth(status="unhealthy", message=str(e)[:200])


async def _check_redis(request: Request) -> ComponentHealth:
    """Check Redis connectivity."""
    redis_client = getattr(request.app.state, "redis_client", None)
    if redis_client is None:
        return ComponentHealth(status="degraded", message="Not connected (cache disabled)")

    start = time.monotonic()
    try:
        await redis_client.ping()
        latency = (time.monotonic() - start) * 1000
        return ComponentHealth(status="healthy", latency_ms=round(latency, 2))
    except Exception as e:
        latency = (time.monotonic() - start) * 1000
        return ComponentHealth(
            status="degraded",
            latency_ms=round(latency, 2),
            message=str(e)[:200],
        )


def _check_llm(request: Request) -> ComponentHealth:
    """Check LLM client circuit breaker status."""
    llm_client = getattr(request.app.state, "llm_client", None)
    if llm_client is None:
        return ComponentHealth(status="unhealthy", message="Not initialized")

    circuit_status = llm_client.get_circuit_status()
    any_open = any(s == "open" for s in circuit_status.values())

    return ComponentHealth(
        status="degraded" if any_open else "healthy",
        message=f"Circuit breakers: {circuit_status}",
    )


def _check_embedder(request: Request) -> ComponentHealth:
    """Check if embedding model is loaded."""
    embedder = getattr(request.app.state, "embedder", None)
    if embedder is None:
        return ComponentHealth(status="unhealthy", message="Not initialized")

    if embedder.is_loaded:
        return ComponentHealth(status="healthy")
    return ComponentHealth(status="unhealthy", message="Model not loaded")


def _check_graph(request: Request) -> ComponentHealth:
    """Check if the LangGraph DAG is compiled."""
    graph = getattr(request.app.state, "compiled_graph", None)
    if graph is None:
        return ComponentHealth(status="unhealthy", message="Not compiled")
    return ComponentHealth(status="healthy")
