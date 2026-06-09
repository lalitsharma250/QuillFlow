"""
app/services/retrieval/embedder.py — using official voyageai SDK
"""

from __future__ import annotations

import asyncio
import structlog
from fastapi import FastAPI
import voyageai

from config import get_settings

logger = structlog.get_logger(__name__)


class EmbeddingService:
    DEFAULT_MODEL = "voyage-3"
    MAX_BATCH_SIZE = 128

    def __init__(self, model_name: str | None = None) -> None:
        settings = get_settings()
        self.model_name = model_name or settings.embedding_model_name
        self.dimensions = settings.embedding_dimensions
        self.batch_size = min(settings.embedding_batch_size, self.MAX_BATCH_SIZE)
        self._client: voyageai.AsyncClient | None = None
        self._is_loaded = False

    @property
    def is_loaded(self) -> bool:
        return self._is_loaded

    async def load(self) -> None:
        if self._is_loaded:
            return

        settings = get_settings()
        if not settings.voyage_api_key:
            logger.error("voyage_api_key_not_configured")
            return

        api_key = settings.voyage_api_key.get_secret_value()
        if not api_key:
            logger.error("voyage_api_key_empty")
            return

        try:
            # SDK uses its own HTTP handling — may bypass Cloudflare block
            self._client = voyageai.AsyncClient(api_key=api_key)

            # Validate
            result = await self._client.embed(
                texts=["test"],
                model=self.model_name,
                input_type="query",
            )
            actual_dims = len(result.embeddings[0])
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
                provider="voyage_sdk",
                model=self.model_name,
                dimensions=self.dimensions,
            )
        except Exception as e:
            logger.error("embedding_service_init_failed_degraded", error=str(e)[:300])
            self._client = None
            self._is_loaded = False

    async def embed_text(self, text: str, _validate_call: bool = False) -> list[float]:
        if not _validate_call and not self._is_loaded:
            raise RuntimeError("Embedding service not loaded.")
        result = await self._client.embed(
            texts=[text],
            model=self.model_name,
            input_type="query",
        )
        return result.embeddings[0]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not self._is_loaded:
            raise RuntimeError("Embedding service not loaded.")
        if not texts:
            return []

        all_embeddings: list[list[float]] = []
        for i in range(0, len(texts), self.batch_size):
            batch = texts[i : i + self.batch_size]
            result = await self._client.embed(
                texts=batch,
                model=self.model_name,
                input_type="document",
            )
            all_embeddings.extend(result.embeddings)
            logger.info(
                "batch_embedding_progress",
                processed=min(i + self.batch_size, len(texts)),
                total=len(texts),
            )
        return all_embeddings

    async def close(self) -> None:
        self._client = None
        self._is_loaded = False


async def init_embedder(app: FastAPI) -> None:
    service = EmbeddingService()
    await service.load()
    app.state.embedder = service


async def close_embedder(app: FastAPI) -> None:
    service = getattr(app.state, "embedder", None)
    if service:
        await service.close()