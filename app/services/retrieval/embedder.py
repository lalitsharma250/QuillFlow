"""
app/services/retrieval/embedder.py

Embedding service with a configurable provider backend.

Provider is selected by `settings.embedding_provider`:
  - "voyage" (default): official voyageai SDK. Works locally; Cloudflare
    IP-blocks Voyage from Render, so prod uses OpenAI instead.
  - "openai": OpenAI embeddings via langchain-openai. Used on Render.

The public `EmbeddingService` facade is unchanged so all call sites
(graph builder, ingestion pipeline, workers, scripts) keep working.
"""

from __future__ import annotations

import structlog
from fastapi import FastAPI

from config import get_settings

logger = structlog.get_logger(__name__)


class _BaseEmbedder:
    """Provider backend interface."""

    provider: str = "base"

    def __init__(self, model_name: str, dimensions: int, batch_size: int) -> None:
        self.model_name = model_name
        self.dimensions = dimensions
        self.batch_size = batch_size

    async def load(self) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    async def embed_query(self, text: str) -> list[float]:  # pragma: no cover
        raise NotImplementedError

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:  # pragma: no cover
        raise NotImplementedError

    async def close(self) -> None:
        pass


class _VoyageBackend(_BaseEmbedder):
    """Official voyageai SDK backend."""

    provider = "voyage_sdk"

    def __init__(self, model_name: str, dimensions: int, batch_size: int) -> None:
        super().__init__(model_name, dimensions, batch_size)
        self._client = None

    async def load(self) -> None:
        import voyageai

        settings = get_settings()
        if not settings.voyage_api_key:
            raise RuntimeError("voyage_api_key_not_configured")
        api_key = settings.voyage_api_key.get_secret_value()
        if not api_key:
            raise RuntimeError("voyage_api_key_empty")

        self._client = voyageai.AsyncClient(api_key=api_key)
        # Validate + reconcile dimensions
        result = await self._client.embed(
            texts=["test"], model=self.model_name, input_type="query"
        )
        actual = len(result.embeddings[0])
        if actual != self.dimensions:
            logger.warning(
                "embedding_dimension_mismatch", expected=self.dimensions, actual=actual
            )
            self.dimensions = actual

    async def embed_query(self, text: str) -> list[float]:
        result = await self._client.embed(
            texts=[text], model=self.model_name, input_type="query"
        )
        return result.embeddings[0]

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        all_embeddings: list[list[float]] = []
        for i in range(0, len(texts), self.batch_size):
            batch = texts[i : i + self.batch_size]
            result = await self._client.embed(
                texts=batch, model=self.model_name, input_type="document"
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


class _OpenAIBackend(_BaseEmbedder):
    """OpenAI embeddings via langchain-openai (already a project dependency)."""

    provider = "openai"

    def __init__(self, model_name: str, dimensions: int, batch_size: int) -> None:
        super().__init__(model_name, dimensions, batch_size)
        self._client = None

    async def load(self) -> None:
        from langchain_openai import OpenAIEmbeddings

        settings = get_settings()
        if not settings.openai_api_key:
            raise RuntimeError("openai_api_key_not_configured")
        api_key = settings.openai_api_key.get_secret_value()
        if not api_key:
            raise RuntimeError("openai_api_key_empty")

        self._client = OpenAIEmbeddings(
            model=self.model_name,
            api_key=api_key,
            dimensions=self.dimensions,
            chunk_size=self.batch_size,
        )
        # Validate with a single embedding round-trip
        vec = await self._client.aembed_query("test")
        actual = len(vec)
        if actual != self.dimensions:
            logger.warning(
                "embedding_dimension_mismatch", expected=self.dimensions, actual=actual
            )
            self.dimensions = actual

    async def embed_query(self, text: str) -> list[float]:
        return await self._client.aembed_query(text)

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        # langchain handles internal batching via chunk_size
        embeddings = await self._client.aembed_documents(texts)
        logger.info("batch_embedding_progress", processed=len(texts), total=len(texts))
        return embeddings

    async def close(self) -> None:
        self._client = None


def _make_backend(model_name: str | None) -> _BaseEmbedder:
    settings = get_settings()
    batch_size = min(settings.embedding_batch_size, EmbeddingService.MAX_BATCH_SIZE)
    if settings.embedding_provider == "openai":
        return _OpenAIBackend(
            model_name=model_name or settings.openai_embedding_model,
            dimensions=settings.openai_embedding_dimensions,
            batch_size=batch_size,
        )
    return _VoyageBackend(
        model_name=model_name or settings.embedding_model_name,
        dimensions=settings.embedding_dimensions,
        batch_size=batch_size,
    )


class EmbeddingService:
    """
    Provider-agnostic embedding facade. Public surface is unchanged:
    model_name, dimensions, batch_size, is_loaded, load(), embed_text(),
    embed_batch(), close().
    """

    DEFAULT_MODEL = "voyage-3"
    MAX_BATCH_SIZE = 128

    def __init__(self, model_name: str | None = None) -> None:
        self._backend = _make_backend(model_name)
        self._is_loaded = False

    @property
    def model_name(self) -> str:
        return self._backend.model_name

    @property
    def dimensions(self) -> int:
        return self._backend.dimensions

    @property
    def batch_size(self) -> int:
        return self._backend.batch_size

    @property
    def provider(self) -> str:
        return self._backend.provider

    @property
    def is_loaded(self) -> bool:
        return self._is_loaded

    async def load(self) -> None:
        if self._is_loaded:
            return
        try:
            await self._backend.load()
            self._is_loaded = True
            logger.info(
                "embedding_service_ready",
                provider=self._backend.provider,
                model=self._backend.model_name,
                dimensions=self._backend.dimensions,
            )
        except Exception as e:
            # Graceful degradation: app boots, embedding-dependent paths fail loudly.
            logger.error("embedding_service_init_failed_degraded", error=str(e)[:300])
            self._is_loaded = False

    async def embed_text(self, text: str, _validate_call: bool = False) -> list[float]:
        if not _validate_call and not self._is_loaded:
            raise RuntimeError("Embedding service not loaded.")
        return await self._backend.embed_query(text)

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not self._is_loaded:
            raise RuntimeError("Embedding service not loaded.")
        if not texts:
            return []
        return await self._backend.embed_documents(texts)

    async def close(self) -> None:
        await self._backend.close()
        self._is_loaded = False


async def init_embedder(app: FastAPI) -> None:
    service = EmbeddingService()
    await service.load()
    app.state.embedder = service


async def close_embedder(app: FastAPI) -> None:
    service = getattr(app.state, "embedder", None)
    if service:
        await service.close()
