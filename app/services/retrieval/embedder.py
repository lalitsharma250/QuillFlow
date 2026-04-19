"""
app/services/retrieval/embedder.py

Embedding service using Voyage AI API with rate limiting.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx
import structlog
from fastapi import FastAPI
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)

from config import get_settings

logger = structlog.get_logger(__name__)


class RateLimiter:
    """
    Simple rate limiter using token bucket approach.
    Voyage AI tier 1: 300 requests/minute.
    """

    def __init__(self, requests_per_minute: int = 3):
        self.min_interval = 60.0 / requests_per_minute  # Seconds between requests
        self._last_request_time = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """Wait until we can make another request."""
        async with self._lock:
            now = time.monotonic()
            time_since_last = now - self._last_request_time

            if time_since_last < self.min_interval:
                wait_time = self.min_interval - time_since_last
                logger.debug("rate_limit_wait", seconds=round(wait_time, 2))
                await asyncio.sleep(wait_time)

            self._last_request_time = time.monotonic()


class RateLimitError(Exception):
    """Custom exception for rate limit errors (retryable)."""
    pass


class EmbeddingService:
    """
    Generates text embeddings using Voyage AI API.
    """

    VOYAGE_API_URL = "https://api.voyageai.com/v1/embeddings"
    DEFAULT_MODEL = "voyage-3"
    MAX_BATCH_SIZE = 128
    MAX_TOKENS_PER_REQUEST = 120_000

    def __init__(self, model_name: str | None = None) -> None:
        settings = get_settings()
        self.model_name = model_name or settings.embedding_model_name
        self.dimensions = settings.embedding_dimensions
        self.batch_size = min(settings.embedding_batch_size, self.MAX_BATCH_SIZE)
        self._api_key: str | None = None
        self._client: httpx.AsyncClient | None = None
        self._is_loaded = False

        # Rate limiter — Voyage AI free tier: 3 RPM
        self._rate_limiter = RateLimiter(requests_per_minute=300)

    @property
    def is_loaded(self) -> bool:
        return self._is_loaded

    async def load(self) -> None:
        if self._is_loaded:
            return

        settings = get_settings()

        if not hasattr(settings, 'voyage_api_key') or not settings.voyage_api_key:
            raise RuntimeError("VOYAGE_API_KEY not configured.")

        api_key = settings.voyage_api_key.get_secret_value()
        if not api_key:
            raise RuntimeError("VOYAGE_API_KEY is empty")

        self._api_key = api_key
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(60.0),  # Increased timeout
            limits=httpx.Limits(max_connections=5, max_keepalive_connections=2),
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
        )

        # Validate API key with test call
        try:
            test_embedding = await self.embed_text("test", _validate_call=True)
            actual_dims = len(test_embedding)

            if actual_dims != self.dimensions:
                logger.warning(
                    "embedding_dimension_mismatch",
                    expected=self.dimensions,
                    actual=actual_dims,
                )
                self.dimensions = actual_dims

            self._is_loaded = True
            logger.info(
                "embedding_service_ready",
                provider="voyage_ai",
                model=self.model_name,
                dimensions=self.dimensions,
                rate_limit="300 requests/minute",
            )
        except Exception as e:
            logger.error("embedding_service_init_failed", error=str(e))
            if self._client:
                await self._client.aclose()
                self._client = None
            raise RuntimeError(f"Failed to validate Voyage API key: {e}")

    @retry(
        stop=stop_after_attempt(5),  # Retry more for rate limits
        wait=wait_exponential(multiplier=2, min=20, max=120),  # Longer waits
        retry=retry_if_exception_type((
            httpx.HTTPError,
            httpx.TimeoutException,
            RateLimitError,
        )),
    )
    async def _call_voyage_api(
        self,
        texts: list[str],
        input_type: str = "document",
    ) -> list[list[float]]:
        """Call Voyage AI embeddings API with rate limiting and retry."""
        if self._client is None:
            raise RuntimeError("Embedding service not loaded.")

        # Rate limit BEFORE making the request
        await self._rate_limiter.acquire()

        payload: dict[str, Any] = {
            "input": texts,
            "model": self.model_name,
            "input_type": input_type,
        }

        try:
            response = await self._client.post(
                self.VOYAGE_API_URL,
                json=payload,
            )

            # Handle rate limit explicitly with longer backoff
            if response.status_code == 429:
                retry_after = int(response.headers.get("retry-after", 30))
                logger.warning(
                    "voyage_rate_limited",
                    retry_after_seconds=retry_after,
                    text_count=len(texts),
                )
                await asyncio.sleep(retry_after)
                raise RateLimitError(f"Rate limited, waited {retry_after}s")

            response.raise_for_status()

            data = response.json()
            embeddings = [item["embedding"] for item in data["data"]]

            usage = data.get("usage", {})
            logger.debug(
                "voyage_api_call_success",
                text_count=len(texts),
                total_tokens=usage.get("total_tokens", 0),
                input_type=input_type,
            )

            return embeddings

        except httpx.HTTPStatusError as e:
            logger.error(
                "voyage_api_error",
                status_code=e.response.status_code,
                text=e.response.text[:200],
            )
            raise

    async def embed_text(
        self,
        text: str,
        _validate_call: bool = False,
    ) -> list[float]:
        """Embed a single text string (query path)."""
        if not _validate_call and not self._is_loaded:
            raise RuntimeError("Embedding service not loaded.")

        embeddings = await self._call_voyage_api(
            texts=[text],
            input_type="query",
        )
        return embeddings[0]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """
        Embed multiple texts efficiently.
        Automatically batches and rate-limits.
        """
        if not self._is_loaded:
            raise RuntimeError("Embedding service not loaded.")

        if not texts:
            return []

        logger.info(
            "batch_embedding_started",
            text_count=len(texts),
            batch_size=self.batch_size,
            estimated_batches=(len(texts) + self.batch_size - 1) // self.batch_size,
            note="Rate limited to 3 RPM - may take time",
        )

        all_embeddings: list[list[float]] = []

        for i in range(0, len(texts), self.batch_size):
            batch = texts[i : i + self.batch_size]

            try:
                batch_embeddings = await self._call_voyage_api(
                    texts=batch,
                    input_type="document",
                )
                all_embeddings.extend(batch_embeddings)

                logger.info(
                    "batch_embedding_progress",
                    processed=min(i + self.batch_size, len(texts)),
                    total=len(texts),
                )

            except httpx.HTTPStatusError as e:
                if e.response.status_code == 400 and "token" in e.response.text.lower():
                    logger.warning("batch_too_large_splitting", batch_size=len(batch))
                    half = len(batch) // 2
                    for sub_batch in [batch[:half], batch[half:]]:
                        if sub_batch:
                            sub_embeddings = await self._call_voyage_api(
                                texts=sub_batch,
                                input_type="document",
                            )
                            all_embeddings.extend(sub_embeddings)
                else:
                    raise

        logger.info(
            "batch_embedding_complete",
            text_count=len(texts),
            embedding_count=len(all_embeddings),
        )

        return all_embeddings

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None
        self._is_loaded = False


# ═══════════════════════════════════════════════════════════
# FastAPI Lifespan Hooks
# ═══════════════════════════════════════════════════════════


async def init_embedder(app: FastAPI) -> None:
    service = EmbeddingService()
    await service.load()
    app.state.embedder = service


async def close_embedder(app: FastAPI) -> None:
    service = getattr(app.state, "embedder", None)
    if service:
        await service.close()