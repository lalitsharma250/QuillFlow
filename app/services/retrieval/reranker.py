"""
app/services/retrieval/reranker.py

Cross-encoder reranking service.

After dense + sparse retrieval returns candidates, the reranker
scores each (query, chunk) pair with a cross-encoder model for
much higher precision than embedding similarity alone.

Cross-encoders are slower (they process query+doc together)
but significantly more accurate for relevance scoring.

Model: cross-encoder/ms-marco-MiniLM-L-6-v2
  - Small and fast (~22M params)
  - Trained on MS MARCO passage ranking
  - Good balance of speed and accuracy
"""

from __future__ import annotations

import asyncio
from functools import partial
from typing import TYPE_CHECKING

import structlog

from app.models.domain import RetrievedChunk
from config import get_settings

if TYPE_CHECKING:
    from sentence_transformers import CrossEncoder

logger = structlog.get_logger(__name__)


class RerankerService:
    """
    Cross-encoder reranking for retrieval results.

    Takes candidate chunks from dense/sparse retrieval and re-scores
    them using a cross-encoder model for higher precision.

    Usage:
        reranker = RerankerService()
        await reranker.load()

        reranked = await reranker.rerank(
            query="What is attention?",
            chunks=candidate_chunks,
            top_k=5,
        )
    """

    # Default model — small, fast, good quality
    DEFAULT_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"

    def __init__(self, model_name: str | None = None) -> None:
        self.model_name = model_name or self.DEFAULT_MODEL
        self._model: CrossEncoder | None = None

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    async def load(self) -> None:
        """Load the cross-encoder model. Call once at startup."""
        if self._model is not None:
            return

        logger.info("loading_reranker_model", model=self.model_name)

        loop = asyncio.get_event_loop()
        self._model = await loop.run_in_executor(None, self._load_model_sync)

        logger.info("reranker_model_loaded", model=self.model_name)

    def _load_model_sync(self) -> CrossEncoder:
        """Synchronous model loading."""
        from sentence_transformers import CrossEncoder

        return CrossEncoder(
            self.model_name,
            max_length=512,
            trust_remote_code=False,
        )

    async def rerank(
        self,
        query: str,
        chunks: list[RetrievedChunk],
        top_k: int | None = None,
    ) -> list[RetrievedChunk]:
        """
        Rerank retrieved chunks using the cross-encoder.

        Args:
            query: The original user query
            chunks: Candidate chunks from dense/sparse retrieval
            top_k: Number of top results to return after reranking

        Returns:
            Reranked chunks sorted by cross-encoder score (descending)
        """
        if not chunks:
            return []

        if self._model is None:
            logger.warning("reranker_not_loaded_returning_original_order")
            return chunks[:top_k] if top_k else chunks

        settings = get_settings()
        top_k = top_k or settings.reranker_top_k

        # Build (query, chunk_text) pairs for the cross-encoder
        pairs = [(query, chunk.chunk.text) for chunk in chunks]

        # Score in thread pool (CPU-bound)
        loop = asyncio.get_event_loop()
        raw_scores = await loop.run_in_executor(
            None,
            partial(self._model.predict, pairs, show_progress_bar=False),
        )

        # Normalize scores to 0-1 using sigmoid
        normalized_scores = self._sigmoid_normalize(raw_scores.tolist())

        # Pair with chunks and sort
        scored = list(zip(chunks, normalized_scores))
        scored.sort(key=lambda x: x[1], reverse=True)

        # Build results with updated scores
        results = []
        for original_chunk, rerank_score in scored[:top_k]:
            reranked = RetrievedChunk(
                chunk=original_chunk.chunk,
                score=round(rerank_score, 4),
                retrieval_method=original_chunk.retrieval_method,
            )
            results.append(reranked)

        logger.debug(
            "reranking_complete",
            candidates=len(chunks),
            top_k=top_k,
            results=len(results),
            top_score=results[0].score if results else 0,
            score_range=(
                f"{results[-1].score:.3f}-{results[0].score:.3f}"
                if results else "n/a"
            ),
        )

        return results

    @staticmethod
    def _sigmoid_normalize(scores: list[float]) -> list[float]:
        """
        Normalize raw cross-encoder scores to 0-1 range using sigmoid.
        Cross-encoder outputs are unbounded logits — sigmoid maps them nicely.
        """
        import math

        return [1 / (1 + math.exp(-s)) for s in scores]


class NoOpReranker:
    """
    Passthrough reranker that does nothing.
    Used when reranking is disabled or model isn't available.
    Simply truncates to top_k.
    """

    async def rerank(
        self,
        query: str,
        chunks: list[RetrievedChunk],
        top_k: int | None = None,
    ) -> list[RetrievedChunk]:
        settings = get_settings()
        top_k = top_k or settings.reranker_top_k
        return chunks[:top_k]
