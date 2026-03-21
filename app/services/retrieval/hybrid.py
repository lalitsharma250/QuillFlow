"""
app/services/retrieval/hybrid.py

Hybrid retrieval service — combines dense, sparse, and reranking
into a single interface.

This is the ONLY retrieval interface that graph nodes should use.
It encapsulates the full retrieval strategy:
  1. Dense search (Qdrant vector similarity)
  2. Sparse search (BM25 keyword matching)
  3. Merge + deduplicate results
  4. Rerank with cross-encoder
  5. Return top-K

Graph nodes call: hybrid_retriever.retrieve(query, org_id)
They don't need to know about dense vs sparse vs reranking internals.
"""

from __future__ import annotations

from uuid import UUID

import asyncio
import structlog

from app.models.domain import RetrievedChunk, RetrievalMethod
from app.services.retrieval.embedder import EmbeddingService
from app.services.retrieval.reranker import RerankerService, NoOpReranker
from app.services.retrieval.sparse import SparseRetriever
from app.services.retrieval.vector_store import VectorStoreService
from config import get_settings

logger = structlog.get_logger(__name__)


class HybridRetriever:
    """
    Unified retrieval interface combining dense + sparse + reranking.

    Usage:
        retriever = HybridRetriever(
            embedder=embedding_service,
            vector_store=vector_store_service,
            reranker=reranker_service,  # Optional
        )

        results = await retriever.retrieve(
            query="What is attention mechanism?",
            org_id=uuid,
            top_k=5,
        )
    """

    def __init__(
        self,
        embedder: EmbeddingService,
        vector_store: VectorStoreService,
        reranker: RerankerService | NoOpReranker | None = None,
        sparse_retriever: SparseRetriever | None = None,
    ) -> None:
        self._embedder = embedder
        self._vector_store = vector_store
        self._reranker = reranker or NoOpReranker()
        self._sparse = sparse_retriever or SparseRetriever()
        self._settings = get_settings()

    # ✅ UPDATED hybrid.py — retrieve method (steps 2 and 3):

    async def retrieve(
        self,
        query: str,
        org_id: UUID,
        top_k: int | None = None,
        dense_top_k: int | None = None,
        use_sparse: bool = True,
        use_reranker: bool = True,
        document_ids: list[UUID] | None = None,
    ) -> list[RetrievedChunk]:
        settings = self._settings
        top_k = top_k or settings.reranker_top_k
        dense_top_k = dense_top_k or settings.retrieval_top_k

        # ── Step 1: Embed the query ───────────────────
        query_embedding = await self._embedder.embed_text(query)

        # ── Steps 2 & 3: Dense + Sparse IN PARALLEL ──
        dense_results, sparse_results = await self._parallel_search(
            query=query,
            query_embedding=query_embedding,
            org_id=org_id,
            dense_top_k=dense_top_k,
            use_sparse=use_sparse,
            document_ids=document_ids,
        )

        logger.debug(
            "parallel_retrieval_complete",
            dense_results=len(dense_results),
            sparse_results=len(sparse_results),
        )

        # ── Step 4: Merge + deduplicate ───────────────
        merged = self._merge_results(dense_results, sparse_results)

        # ── Step 5: Rerank (optional) ─────────────────
        if use_reranker and merged:
            final_results = await self._reranker.rerank(
                query=query,
                chunks=merged,
                top_k=top_k,
            )
        else:
            final_results = merged[:top_k]

        logger.info(
            "hybrid_retrieval_complete",
            query_length=len(query),
            org_id=str(org_id),
            dense_candidates=len(dense_results),
            sparse_candidates=len(sparse_results),
            final_results=len(final_results),
            top_score=final_results[0].score if final_results else None,
        )

        return final_results

    async def _parallel_search(
        self,
        query: str,
        query_embedding: list[float],
        org_id: UUID,
        dense_top_k: int,
        use_sparse: bool,
        document_ids: list[UUID] | None,
    ) -> tuple[list[RetrievedChunk], list[RetrievedChunk]]:
        """
        Run dense and sparse search in parallel.

        Dense search goes to Qdrant (I/O bound — network call).
        Sparse search is CPU-bound but fast (BM25 on small candidate set).

        Strategy:
          - If sparse is enabled, we need dense results first for BM25 corpus
          - BUT we can fetch a broader set from Qdrant and run BM25 on it
          - Alternative: run dense with 2x top_k, split for BM25 scoring

        For true parallelism, we'd need a separate sparse index.
        Current approach: fetch more from dense, then BM25 re-scores.
        This is still fast because BM25 on <100 docs is <1ms.
        """

        # Dense search (I/O bound — Qdrant network call)
        dense_coro = self._vector_store.search(
            query_embedding=query_embedding,
            org_id=org_id,
            top_k=dense_top_k * 2 if use_sparse else dense_top_k,
            document_ids=document_ids,
        )

        dense_results = await dense_coro

        # Sparse search on dense candidates (CPU bound, <1ms)
        sparse_results: list[RetrievedChunk] = []
        if use_sparse and dense_results:
            dense_chunks = [r.chunk for r in dense_results]
            sparse_results = self._sparse.search(
                query=query,
                chunks=dense_chunks,
                top_k=dense_top_k,
            )

        return dense_results[:dense_top_k], sparse_results

    def _merge_results(
        self,
        dense: list[RetrievedChunk],
        sparse: list[RetrievedChunk],
    ) -> list[RetrievedChunk]:
        """
        Merge dense and sparse results with deduplication.

        Strategy: Reciprocal Rank Fusion (RRF)
          - Each result gets a score based on its rank in each list
          - RRF score = sum(1 / (k + rank)) across all lists
          - k=60 is standard (dampens the effect of high ranks)
          - Results are deduplicated by chunk ID
        """
        if not sparse:
            return dense

        k = 60  # RRF constant
        chunk_scores: dict[str, float] = {}
        chunk_map: dict[str, RetrievedChunk] = {}

        # Score dense results by rank
        for rank, result in enumerate(dense):
            chunk_id = str(result.chunk.id)
            rrf_score = 1.0 / (k + rank + 1)
            chunk_scores[chunk_id] = chunk_scores.get(chunk_id, 0) + rrf_score
            chunk_map[chunk_id] = result

        # Score sparse results by rank
        for rank, result in enumerate(sparse):
            chunk_id = str(result.chunk.id)
            rrf_score = 1.0 / (k + rank + 1)
            chunk_scores[chunk_id] = chunk_scores.get(chunk_id, 0) + rrf_score
            # Keep the chunk from whichever list had it first
            if chunk_id not in chunk_map:
                chunk_map[chunk_id] = result

        # Sort by combined RRF score
        sorted_ids = sorted(
            chunk_scores.keys(),
            key=lambda cid: chunk_scores[cid],
            reverse=True,
        )

        # Build merged results with RRF scores normalized to 0-1
        max_rrf = max(chunk_scores.values()) if chunk_scores else 1.0
        merged: list[RetrievedChunk] = []

        for chunk_id in sorted_ids:
            original = chunk_map[chunk_id]
            normalized_score = round(chunk_scores[chunk_id] / max_rrf, 4)

            merged.append(
                RetrievedChunk(
                    chunk=original.chunk,
                    score=normalized_score,
                    retrieval_method=RetrievalMethod.HYBRID,
                )
            )

        return merged