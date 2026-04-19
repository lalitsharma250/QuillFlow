"""
app/services/retrieval/reranker.py

Reranking service using Voyage AI API with rate limiting.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx
import structlog
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)

from app.models.domain import RetrievedChunk, RetrievalMethod
from config import get_settings

logger = structlog.get_logger(__name__)


class RateLimiter:
    """Rate limiter for API calls."""

    def __init__(self, requests_per_minute: int = 3):
        self.min_interval = 60.0 / requests_per_minute
        self._last_request_time = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            time_since_last = now - self._last_request_time
            if time_since_last < self.min_interval:
                wait_time = self.min_interval - time_since_last
                await asyncio.sleep(wait_time)
            self._last_request_time = time.monotonic()


class RateLimitError(Exception):
    """Retryable rate limit error."""
    pass


class RerankerService:
    """Reranks retrieved chunks using Voyage AI rerank API."""

    VOYAGE_API_URL = "https://api.voyageai.com/v1/rerank"
    DEFAULT_MODEL = "rerank-2"
    MAX_DOCUMENTS_PER_REQUEST = 1000

    def __init__(self, model_name: str | None = None) -> None:
        self.model_name = model_name or self.DEFAULT_MODEL
        self._api_key: str | None = None
        self._client: httpx.AsyncClient | None = None
        self._is_loaded = False
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
            timeout=httpx.Timeout(60.0),
            limits=httpx.Limits(max_connections=3, max_keepalive_connections=2),
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
        )

        self._is_loaded = True
        logger.info(
            "reranker_service_ready",
            provider="voyage_ai",
            model=self.model_name,
        )

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=2, min=20, max=120),
        retry=retry_if_exception_type((
            httpx.HTTPError,
            httpx.TimeoutException,
            RateLimitError,
        )),
    )
    async def _call_rerank_api(
        self,
        query: str,
        documents: list[str],
        top_k: int | None = None,
    ) -> list[dict[str, Any]]:
        if self._client is None:
            raise RuntimeError("Reranker service not loaded.")

        # Rate limit before request
        await self._rate_limiter.acquire()

        payload: dict[str, Any] = {
            "query": query,
            "documents": documents,
            "model": self.model_name,
        }

        if top_k is not None:
            payload["top_k"] = min(top_k, len(documents))

        response = await self._client.post(self.VOYAGE_API_URL, json=payload)

        if response.status_code == 429:
            retry_after = int(response.headers.get("retry-after", 30))
            logger.warning("voyage_rerank_rate_limited", retry_after=retry_after)
            await asyncio.sleep(retry_after)
            raise RateLimitError(f"Rate limited, waited {retry_after}s")

        response.raise_for_status()

        data = response.json()
        return data["data"]

    async def rerank(
        self,
        query: str,
        chunks: list[RetrievedChunk],
        top_k: int | None = None,
    ) -> list[RetrievedChunk]:
        if not chunks:
            return []

        if not self._is_loaded:
            logger.warning("reranker_not_loaded_skipping")
            return chunks[:top_k] if top_k else chunks

        settings = get_settings()
        top_k = top_k or settings.reranker_top_k

        documents = [c.chunk.text for c in chunks]

        if len(documents) > self.MAX_DOCUMENTS_PER_REQUEST:
            logger.warning(
                "rerank_batch_too_large_truncating",
                batch_size=len(documents),
            )
            documents = documents[:self.MAX_DOCUMENTS_PER_REQUEST]
            chunks = chunks[:self.MAX_DOCUMENTS_PER_REQUEST]

        try:
            results = await self._call_rerank_api(
                query=query,
                documents=documents,
                top_k=top_k,
            )

            reranked: list[RetrievedChunk] = []
            for result in results:
                idx = result["index"]
                score = result["relevance_score"]
                original_chunk = chunks[idx]
                reranked.append(
                    RetrievedChunk(
                        chunk=original_chunk.chunk,
                        score=score,
                        retrieval_method=RetrievalMethod.RERANKED,
                    )
                )

            logger.debug(
                "reranking_complete",
                input_count=len(chunks),
                output_count=len(reranked),
                top_score=reranked[0].score if reranked else None,
            )

            return reranked

        except Exception as e:
            logger.error(
                "reranking_failed_falling_back",
                error=str(e),
                chunk_count=len(chunks),
            )
            return chunks[:top_k] if top_k else chunks

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None
        self._is_loaded = False


class NoOpReranker:
    """Pass-through reranker for fallback."""

    model_name = "noop"

    @property
    def is_loaded(self) -> bool:
        return True

    async def load(self) -> None:
        pass

    async def rerank(
        self,
        query: str,
        chunks: list[RetrievedChunk],
        top_k: int | None = None,
    ) -> list[RetrievedChunk]:
        if top_k is None:
            return chunks
        return chunks[:top_k]

    async def close(self) -> None:
        pass