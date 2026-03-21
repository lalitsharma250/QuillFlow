"""
tests/integration/test_retrieval.py

Integration tests for the retrieval pipeline.
Uses mocked embedder but tests the full retrieval flow.
"""

import pytest
from uuid import uuid4

from app.services.retrieval.sparse import SparseRetriever, BM25Index
from app.services.retrieval.hybrid import HybridRetriever
from app.models.domain import Chunk, ChunkMetadata, RetrievedChunk, RetrievalMethod


class TestSparseRetrieverIntegration:
    """Test BM25 sparse retrieval with realistic data."""

    def _make_chunks(self) -> list[Chunk]:
        org_id = uuid4()
        doc_id = uuid4()

        texts = [
            "Retrieval augmented generation combines search with language models",
            "Vector databases store embeddings for similarity search",
            "The transformer architecture uses self-attention mechanisms",
            "Cooking Italian pasta requires fresh ingredients and patience",
            "HNSW is an algorithm for approximate nearest neighbor search",
            "Fine-tuning language models improves task-specific performance",
            "Garden maintenance includes watering pruning and fertilizing",
            "Prompt engineering helps get better results from LLMs",
        ]

        return [
            Chunk(
                id=uuid4(),
                text=text,
                metadata=ChunkMetadata(
                    org_id=org_id,
                    source_doc_id=doc_id,
                    source_filename="test.txt",
                    chunk_index=i,
                ),
            )
            for i, text in enumerate(texts)
        ]

    def test_relevant_results_ranked_higher(self):
        retriever = SparseRetriever()
        chunks = self._make_chunks()

        results = retriever.search("retrieval augmented generation RAG", chunks, top_k=3)

        assert len(results) > 0
        # First result should be about RAG
        assert "retrieval" in results[0].chunk.text.lower() or "generation" in results[0].chunk.text.lower()

    def test_irrelevant_query_low_scores(self):
        retriever = SparseRetriever()
        chunks = self._make_chunks()

        results = retriever.search("quantum physics black holes", chunks, top_k=3)

        # Should have low or zero scores
        if results:
            assert results[0].score < 0.5

    def test_keyword_matching(self):
        retriever = SparseRetriever()
        chunks = self._make_chunks()

        results = retriever.search("HNSW approximate nearest neighbor", chunks, top_k=3)

        assert len(results) > 0
        assert "hnsw" in results[0].chunk.text.lower()


class TestHybridRetrieverMerge:
    """Test the RRF merge logic with realistic data."""

    def test_hybrid_merge_deduplicates(self):
        retriever = HybridRetriever.__new__(HybridRetriever)

        shared_id = uuid4()
        org_id = uuid4()

        def _make_rc(text, score, chunk_id=None, method=RetrievalMethod.DENSE):
            return RetrievedChunk(
                chunk=Chunk(
                    id=chunk_id or uuid4(),
                    text=text,
                    metadata=ChunkMetadata(
                        org_id=org_id, source_doc_id=uuid4(),
                        source_filename="test.txt", chunk_index=0,
                    ),
                ),
                score=score,
                retrieval_method=method,
            )

        dense = [
            _make_rc("RAG combines retrieval", 0.95, shared_id),
            _make_rc("Transformers use attention", 0.85),
            _make_rc("Vector DBs store embeddings", 0.80),
        ]

        sparse = [
            _make_rc("RAG combines retrieval", 0.90, shared_id, RetrievalMethod.SPARSE),
            _make_rc("Prompt engineering helps", 0.70, method=RetrievalMethod.SPARSE),
        ]

        merged = retriever._merge_results(dense, sparse)

        # No duplicates
        ids = [str(r.chunk.id) for r in merged]
        assert len(ids) == len(set(ids))

        # Shared chunk should rank highest (boosted by appearing in both)
        assert str(merged[0].chunk.id) == str(shared_id)

        # Total unique results
        assert len(merged) == 4  # 3 dense + 1 unique sparse
