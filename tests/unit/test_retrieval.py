"""
tests/unit/test_retrieval.py

Tests for the hybrid retrieval merge logic.
Dense search and Qdrant are tested in integration tests.
"""

import pytest
from uuid import uuid4

from app.models.domain import Chunk, ChunkMetadata, RetrievedChunk, RetrievalMethod
from app.services.retrieval.hybrid import HybridRetriever


def _make_retrieved_chunk(
    text: str,
    score: float,
    method: RetrievalMethod = RetrievalMethod.DENSE,
    chunk_id=None,
) -> RetrievedChunk:
    """Helper to create test RetrievedChunk objects."""
    cid = chunk_id or uuid4()
    return RetrievedChunk(
        chunk=Chunk(
            id=cid,
            text=text,
            metadata=ChunkMetadata(
                org_id=uuid4(),
                source_doc_id=uuid4(),
                source_filename="test.txt",
                chunk_index=0,
            ),
        ),
        score=score,
        retrieval_method=method,
    )


class TestMergeResults:
    """Test the RRF merge logic in isolation."""

    def _get_merger(self):
        """Get access to the merge method without full initialization."""
        # We can call _merge_results as a static-like method
        # by creating a minimal instance
        return HybridRetriever.__new__(HybridRetriever)

    def test_dense_only(self):
        merger = self._get_merger()
        dense = [
            _make_retrieved_chunk("doc1", 0.9),
            _make_retrieved_chunk("doc2", 0.8),
        ]
        result = merger._merge_results(dense, [])
        assert len(result) == 2
        # Order preserved when no sparse results
        assert result[0].chunk.text == "doc1"

    def test_deduplication(self):
        merger = self._get_merger()
        shared_id = uuid4()

        dense = [_make_retrieved_chunk("doc1", 0.9, chunk_id=shared_id)]
        sparse = [_make_retrieved_chunk("doc1", 0.7, chunk_id=shared_id)]

        result = merger._merge_results(dense, sparse)
        # Same chunk appears only once
        assert len(result) == 1
        # Score should be boosted (appeared in both lists)

    def test_rrf_boosts_dual_presence(self):
        merger = self._get_merger()
        shared_id = uuid4()
        unique_id = uuid4()

        dense = [
            _make_retrieved_chunk("shared_doc", 0.9, chunk_id=shared_id),
            _make_retrieved_chunk("dense_only", 0.85, chunk_id=unique_id),
        ]
        sparse = [
            _make_retrieved_chunk("shared_doc", 0.8, chunk_id=shared_id),
        ]

        result = merger._merge_results(dense, sparse)
        # shared_doc should rank higher (boosted by appearing in both)
        assert result[0].chunk.text == "shared_doc"

    def test_merged_method_is_hybrid(self):
        merger = self._get_merger()
        shared_id = uuid4()

        dense = [_make_retrieved_chunk("doc", 0.9, chunk_id=shared_id)]
        sparse = [_make_retrieved_chunk("doc", 0.8, chunk_id=shared_id)]

        result = merger._merge_results(dense, sparse)
        assert result[0].retrieval_method == RetrievalMethod.HYBRID

    def test_scores_normalized_to_one(self):
        merger = self._get_merger()
        dense = [
            _make_retrieved_chunk("doc1", 0.9),
            _make_retrieved_chunk("doc2", 0.7),
            _make_retrieved_chunk("doc3", 0.5),
        ]
        sparse = [
            _make_retrieved_chunk("doc4", 0.8),
        ]

        result = merger._merge_results(dense, sparse)
        # Top result should have score 1.0 (normalized)
        assert result[0].score == 1.0
        # All scores should be 0-1
        for r in result:
            assert 0.0 <= r.score <= 1.0