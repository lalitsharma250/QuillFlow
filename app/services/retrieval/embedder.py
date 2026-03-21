"""
app/services/retrieval/embedder.py

Embedding service using sentence-transformers.

Design decisions:
  - Self-hosted model (no API dependency, no per-token cost)
  - Model loaded once at startup, shared across all requests
  - Batch embedding for efficiency (ingestion pipeline)
  - Single embedding for queries (low latency)
  - Runs on CPU by default (GPU optional via CUDA)

The model (BGE-large-en-v1.5) produces 1024-dim embeddings
and is one of the best open-source embedding models available.
"""

from __future__ import annotations

import asyncio
from functools import partial
from typing import TYPE_CHECKING

import numpy as np
import structlog
from fastapi import FastAPI

from config import get_settings

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer

logger = structlog.get_logger(__name__)


class EmbeddingService:
    """
    Generates text embeddings using a sentence-transformer model.

    The model is loaded into memory once and reused.
    All heavy computation runs in a thread pool to avoid blocking the event loop.

    Usage:
        service = EmbeddingService()
        await service.load()

        # Single query embedding
        embedding = await service.embed_text("What is RAG?")

        # Batch embedding (for ingestion)
        embeddings = await service.embed_batch(["text1", "text2", ...])
    """

    def __init__(self, model_name: str | None = None) -> None:
        settings = get_settings()
        self.model_name = model_name or settings.embedding_model_name
        self.dimensions = settings.embedding_dimensions
        self.batch_size = settings.embedding_batch_size
        self._model: SentenceTransformer | None = None

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    async def load(self) -> None:
        """
        Load the embedding model into memory.
        This is CPU/memory intensive — call once at startup.
        Runs in a thread pool to avoid blocking the event loop.
        """
        if self._model is not None:
            logger.debug("embedding_model_already_loaded", model=self.model_name)
            return

        logger.info("loading_embedding_model", model=self.model_name)

        loop = asyncio.get_event_loop()
        self._model = await loop.run_in_executor(None, self._load_model_sync)

        # Verify dimensions match config
        test_embedding = self._model.encode("test", normalize_embeddings=True)
        actual_dims = len(test_embedding)

        if actual_dims != self.dimensions:
            logger.warning(
                "embedding_dimension_mismatch",
                expected=self.dimensions,
                actual=actual_dims,
                model=self.model_name,
            )
            self.dimensions = actual_dims

        logger.info(
            "embedding_model_loaded",
            model=self.model_name,
            dimensions=self.dimensions,
        )

    def _load_model_sync(self) -> SentenceTransformer:
        """Synchronous model loading (runs in thread pool)."""
        from sentence_transformers import SentenceTransformer

        model = SentenceTransformer(
            self.model_name,
            trust_remote_code=False,
        )
        return model

    async def embed_text(self, text: str) -> list[float]:
        """
        Embed a single text string.
        Used for query embedding (low latency path).

        Args:
            text: The text to embed

        Returns:
            Normalized embedding vector as list of floats

        Raises:
            RuntimeError: If model is not loaded
        """
        if self._model is None:
            raise RuntimeError("Embedding model not loaded. Call load() first.")

        loop = asyncio.get_event_loop()
        embedding = await loop.run_in_executor(
            None,
            partial(
                self._model.encode,
                text,
                normalize_embeddings=True,
                show_progress_bar=False,
            ),
        )

        return embedding.tolist()

    # ✅ UPDATED embedder.py — embed_batch method:

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """
        Embed multiple texts efficiently.

        For CPU: processes in sequential batches (CPU-bound work
        doesn't benefit from async parallelism — it would just
        cause thread contention).

        For GPU: could process larger batches. Adjust batch_size
        in settings based on available VRAM.
        """
        if self._model is None:
            raise RuntimeError("Embedding model not loaded. Call load() first.")

        if not texts:
            return []

        logger.debug(
            "batch_embedding_started",
            text_count=len(texts),
            batch_size=self.batch_size,
        )

        loop = asyncio.get_event_loop()

        # For small sets, encode all at once (avoids batch overhead)
        if len(texts) <= self.batch_size:
            embeddings = await loop.run_in_executor(
                None,
                partial(
                    self._model.encode,
                    texts,
                    normalize_embeddings=True,
                    show_progress_bar=False,
                    batch_size=self.batch_size,
                ),
            )
            return self._to_list(embeddings)

        # For large sets, process in batches to control memory
        # sentence-transformers handles internal parallelism per batch
        all_embeddings: list[list[float]] = []

        for i in range(0, len(texts), self.batch_size):
            batch = texts[i : i + self.batch_size]

            batch_embeddings = await loop.run_in_executor(
                None,
                partial(
                    self._model.encode,
                    batch,
                    normalize_embeddings=True,
                    show_progress_bar=False,
                    batch_size=self.batch_size,
                ),
            )

            all_embeddings.extend(self._to_list(batch_embeddings))

            logger.debug(
                "batch_embedding_progress",
                processed=min(i + self.batch_size, len(texts)),
                total=len(texts),
            )

        return all_embeddings

    @staticmethod
    def _to_list(embeddings) -> list[list[float]]:
        """Convert numpy array or list to list of lists."""
        import numpy as np

        if isinstance(embeddings, np.ndarray):
            return embeddings.tolist()
        return [
            e.tolist() if hasattr(e, "tolist") else e
            for e in embeddings
        ]


# ═══════════════════════════════════════════════════════════
# FastAPI Lifespan Hooks
# ═══════════════════════════════════════════════════════════


async def init_embedder(app: FastAPI) -> None:
    """
    Initialize the embedding service during app startup.
    Stores the service on app.state.embedder.
    """
    service = EmbeddingService()
    await service.load()
    app.state.embedder = service


# No close needed — model is just in memory, GC handles it.