"""
app/api/middleware/rate_limit.py

Per-user rate limiting using Redis sliding window.

Limits:
  - Chat queries: 60 per minute per user
  - Ingestion: 20 per minute per user
  - Bulk ingestion: 5 per minute per user

Falls back to no limiting if Redis is unavailable.
"""

from __future__ import annotations

import time

import structlog
from fastapi import Depends, HTTPException, Request
from redis.asyncio import Redis

from app.api.middleware.auth import get_auth_context
from app.models.domain import AuthContext

logger = structlog.get_logger(__name__)

# Rate limit configurations: (max_requests, window_seconds)
RATE_LIMITS = {
    "chat": (60, 60),
    "ingest": (20, 60),
    "ingest_bulk": (5, 60),
}


async def _check_rate_limit(
    redis: Redis | None,
    user_id: str,
    action: str,
) -> None:
    """
    Check and enforce rate limit using Redis sliding window.

    Uses a sorted set with timestamps as scores.
    On each request:
      1. Remove entries older than the window
      2. Count remaining entries
      3. If count >= limit, reject
      4. Otherwise, add current timestamp

    Raises:
        HTTPException 429 if rate limit exceeded
    """
    if redis is None:
        return  # No Redis = no rate limiting

    max_requests, window_seconds = RATE_LIMITS.get(action, (100, 60))
    key = f"quillflow:ratelimit:{action}:{user_id}"
    now = time.time()
    window_start = now - window_seconds

    try:
        pipeline = redis.pipeline()
        # Remove old entries
        pipeline.zremrangebyscore(key, 0, window_start)
        # Count current entries
        pipeline.zcard(key)
        # Add current request
        pipeline.zadd(key, {str(now): now})
        # Set expiry on the key
        pipeline.expire(key, window_seconds + 10)

        results = await pipeline.execute()
        current_count = results[1]  # zcard result

        if current_count >= max_requests:
            logger.warning(
                "rate_limit_exceeded",
                user_id=user_id,
                action=action,
                count=current_count,
                limit=max_requests,
            )
            raise HTTPException(
                status_code=429,
                detail=f"Rate limit exceeded. Max {max_requests} {action} requests per {window_seconds}s.",
                headers={"Retry-After": str(window_seconds)},
            )

    except HTTPException:
        raise
    except Exception as e:
        # Rate limiting failure should not block requests
        logger.warning("rate_limit_check_failed", error=str(e))


class RateLimiter:
    """
    FastAPI dependency for rate limiting.

    Usage:
        @router.post("/chat")
        async def chat(
            _rate_limit: None = Depends(RateLimiter("chat")),
            auth: AuthContext = Depends(require_viewer),
        ):
            ...
    """

    def __init__(self, action: str) -> None:
        self.action = action

    async def __call__(
        self,
        request: Request,
        auth: AuthContext = Depends(get_auth_context),
    ) -> None:
        redis = getattr(request.app.state, "redis_client", None)
        await _check_rate_limit(
            redis=redis,
            user_id=str(auth.user_id),
            action=self.action,
        )
