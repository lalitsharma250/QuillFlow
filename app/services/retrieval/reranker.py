"""
app/services/retrieval/reranker.py

Reranking service using official Voyage AI SDK.
"""

from __future__ import annotations

import structlog
import voyageai

from app.models.domain import RetrievedChunk, RetrievalMethod
from config import get_settings

logger = structlog.get_logger(__name__)


class RerankerService:
    """Reranks retrieved chunks using Voyage AI rerank (SDK)."""

    DEFAULT_MODEL = "rerank-2"
    MAX_DOCUMENTS_PER_REQUEST = 1000

    def __init__(self, model_name: str | None = None) -> None:
        self.model_name = model_name or self.DEFAULT_MODEL
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
            logger.error("voyage_api_key_not_configured_reranker")
            return

        api_key = settings.voyage_api_key.get_secret_value()
        if not api_key:
            logger.error("voyage_api_key_empty_reranker")
            return

        try:
            self._client = voyageai.AsyncClient(api_key=api_key)
            self._is_loaded = True
            logger.info(
                "reranker_service_ready",
                provider="voyage_sdk",
                model=self.model_name,
            )
        except Exception as e:
            logger.error("reranker_init_failed_degraded", error=str(e)[:200])
            self._client = None
            self._is_loaded = False

    async def rerank(
        self,
        query: str,
        chunks: list[RetrievedChunk],
        top_k: int | None = None,
    ) -> list[RetrievedChunk]:
        if not chunks:
            return []

        if not self._is_loaded or self._client is None:
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
            result = await self._client.rerank(
                query=query,
                documents=documents,
                model=self.model_name,
                top_k=min(top_k, len(documents)),
            )

            # SDK returns result.results — each has .index and .relevance_score
            reranked: list[RetrievedChunk] = []
            for r in result.results:
                original_chunk = chunks[r.index]
                reranked.append(
                    RetrievedChunk(
                        chunk=original_chunk.chunk,
                        score=r.relevance_score,
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
                error=str(e)[:200],
                chunk_count=len(chunks),
            )
            return chunks[:top_k] if top_k else chunks

    async def close(self) -> None:
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