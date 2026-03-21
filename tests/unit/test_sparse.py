"""
tests/unit/test_sparse.py

Tests for BM25 sparse retrieval.
"""

import pytest
from uuid import uuid4

from app.models.domain import Chunk, ChunkMetadata, RetrievalMethod
from app.services.retrieval.sparse import BM25Index, SparseRetriever, _tokenize


class TestTokenizer:
    def test_basic_tokenization(self):
        tokens = _tokenize("The quick brown fox jumps over the lazy dog")
        assert "quick" in tokens
        assert "brown" in tokens
        assert "fox" in tokens
        # Stop words removed
        assert "the" not in tokens
        assert "over" not in tokens

    def test_lowercase(self):
        tokens = _tokenize("Hello WORLD")
        assert "hello" in tokens
        assert "world" in tokens

    def test_empty_string(self):
        assert _tokenize("") == []

    def test_stop_words_only(self):
        tokens = _tokenize("the a an is are")
        assert tokens == []

    def test_single_char_removed(self):
        tokens = _tokenize("I a x go")
        # "i", "a", "x" are single char or stop words
        assert "go" in tokens


class TestBM25Index:
    def test_basic_scoring(self):
        index = BM25Index()
        docs = [
            "machine learning algorithms for classification",
            "deep learning neural networks transformers",
            "cooking recipes for italian pasta dishes",
        ]
        index.build(docs)

        scores = index.score("machine learning")
        # First doc should score highest (exact match)
        assert scores[0] > scores[2]

    def test_empty_query(self):
        index = BM25Index()
        index.build(["some document text"])
        scores = index.score("")
        assert scores == [0.0]

    def test_no_match(self):
        index = BM25Index()
        index.build(["machine learning algorithms"])
        scores = index.score("cooking recipes")
        assert scores[0] == 0.0

    def test_multiple_term_match(self):
        index = BM25Index()
        docs = [
            "transformer attention mechanism",
            "attention is all you need paper",
            "convolutional neural networks",
        ]
        index.build(docs)

        scores = index.score("transformer attention")
        # First doc matches both terms
        assert scores[0] > scores[1]
        assert scores[0] > scores[2]


class TestSparseRetriever:
    def _make_chunk(self, text: str) -> Chunk:
        return Chunk(
            text=text,
            metadata=ChunkMetadata(
                org_id=uuid4(),
                source_doc_id=uuid4(),
                source_filename="test.txt",
                chunk_index=0,
            ),
        )

    def test_basic_search(self):
        retriever = SparseRetriever()
        chunks = [
            self._make_chunk("machine learning classification algorithms"),
            self._make_chunk("deep learning neural network training"),
            self._make_chunk("cooking italian pasta recipes"),
        ]

        results = retriever.search("machine learning", chunks, top_k=2)
        assert len(results) <= 2
        assert results[0].chunk.text == chunks[0].text
        assert results[0].retrieval_method == RetrievalMethod.SPARSE

    def test_empty_query(self):
        retriever = SparseRetriever()
        chunks = [self._make_chunk("some text")]
        results = retriever.search("", chunks, top_k=5)
        assert results == []

    def test_empty_chunks(self):
        retriever = SparseRetriever()
        results = retriever.search("query", [], top_k=5)
        assert results == []

    def test_scores_normalized(self):
        retriever = SparseRetriever()
        chunks = [
            self._make_chunk("transformer attention mechanism self attention"),
            self._make_chunk("recurrent neural network lstm gru"),
        ]

        results = retriever.search("transformer attention", chunks, top_k=2)
        for result in results:
            assert 0.0 <= result.score <= 1.0

    def test_top_k_respected(self):
        retriever = SparseRetriever()
        chunks = [self._make_chunk(f"document about topic {i}") for i in range(20)]

        results = retriever.search("document topic", chunks, top_k=3)
        assert len(results) <= 3