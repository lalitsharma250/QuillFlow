"""
app/services/retrieval/sparse.py

BM25 sparse retrieval.

Complements dense (vector) search by catching keyword matches
that embedding models sometimes miss. Especially useful for:
  - Exact technical terms, acronyms, product names
  - Queries where specific words matter more than semantic meaning

Implementation:
  - Uses a simple in-memory BM25 index built from chunk texts
  - Rebuilt per-query from Qdrant payload (filtered by org_id)
  - For production scale, consider Qdrant's built-in sparse vectors
    or an external search engine (Elasticsearch/Meilisearch)

This is intentionally simple for MVP. The reranker downstream
handles quality — sparse retrieval just needs decent recall.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from uuid import UUID
import structlog

from app.models.domain import Chunk, ChunkMetadata, RetrievedChunk, RetrievalMethod

logger = structlog.get_logger(__name__)


# ── Simple tokenizer ──────────────────────────────────────
_WORD_PATTERN = re.compile(r"\w+")

# Common English stop words to skip
_STOP_WORDS = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "need", "dare", "ought",
    "used", "to", "of", "in", "for", "on", "with", "at", "by", "from",
    "as", "into", "through", "during", "before", "after", "above", "below",
    "between", "out", "off", "over", "under", "again", "further", "then",
    "once", "here", "there", "when", "where", "why", "how", "all", "each",
    "every", "both", "few", "more", "most", "other", "some", "such", "no",
    "nor", "not", "only", "own", "same", "so", "than", "too", "very",
    "just", "because", "but", "and", "or", "if", "while", "that", "this",
    "it", "its", "i", "me", "my", "we", "our", "you", "your", "he", "him",
    "his", "she", "her", "they", "them", "their", "what", "which", "who",
})


def _tokenize(text: str) -> list[str]:
    """Lowercase tokenization with stop word removal."""
    words = _WORD_PATTERN.findall(text.lower())
    return [w for w in words if w not in _STOP_WORDS and len(w) > 1]


@dataclass
class BM25Index:
    """
    In-memory BM25 index over a set of documents.

    BM25 scoring parameters:
      k1: Term frequency saturation (1.2-2.0 typical)
      b:  Length normalization (0.75 typical)
    """

    k1: float = 1.5
    b: float = 0.75

    # Internal state
    _doc_freqs: dict[str, int] = field(default_factory=dict)
    _doc_lengths: list[int] = field(default_factory=list)
    _doc_term_freqs: list[Counter] = field(default_factory=list)
    _avg_doc_length: float = 0.0
    _num_docs: int = 0

    def build(self, documents: list[str]) -> None:
        """
        Build the BM25 index from a list of document texts.

        Args:
            documents: List of text strings to index
        """
        self._num_docs = len(documents)
        self._doc_freqs = {}
        self._doc_lengths = []
        self._doc_term_freqs = []

        for doc_text in documents:
            tokens = _tokenize(doc_text)
            term_freq = Counter(tokens)

            self._doc_term_freqs.append(term_freq)
            self._doc_lengths.append(len(tokens))

            # Update document frequency (how many docs contain each term)
            for term in set(tokens):
                self._doc_freqs[term] = self._doc_freqs.get(term, 0) + 1

        total_length = sum(self._doc_lengths)
        self._avg_doc_length = total_length / self._num_docs if self._num_docs > 0 else 0

    def score(self, query: str) -> list[float]:
        """
        Score all indexed documents against a query.

        Args:
            query: Search query text

        Returns:
            List of BM25 scores (one per indexed document), same order as build()
        """
        query_tokens = _tokenize(query)
        scores = [0.0] * self._num_docs

        for token in query_tokens:
            if token not in self._doc_freqs:
                continue

            df = self._doc_freqs[token]
            # IDF component (with smoothing to avoid negative values)
            idf = math.log(
                (self._num_docs - df + 0.5) / (df + 0.5) + 1.0
            )

            for doc_idx in range(self._num_docs):
                tf = self._doc_term_freqs[doc_idx].get(token, 0)
                if tf == 0:
                    continue

                doc_len = self._doc_lengths[doc_idx]

                # BM25 TF component with length normalization
                tf_norm = (tf * (self.k1 + 1)) / (
                    tf + self.k1 * (1 - self.b + self.b * doc_len / self._avg_doc_length)
                )

                scores[doc_idx] += idf * tf_norm

        return scores


class SparseRetriever:
    """
    BM25-based sparse retrieval service.

    Builds an ephemeral BM25 index from provided chunks and scores
    a query against them. Used alongside dense retrieval for hybrid search.

    Usage:
        retriever = SparseRetriever()
        results = retriever.search(
            query="transformer architecture",
            chunks=chunks_from_qdrant,
            top_k=10,
        )
    """

    def __init__(self, k1: float = 1.5, b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b

    def search(
        self,
        query: str,
        chunks: list[Chunk],
        top_k: int = 10,
    ) -> list[RetrievedChunk]: 
        """
        Score chunks against a query using BM25.

        Args:
            query: Search query
            chunks: Pre-fetched chunks to score (typically from a broader Qdrant fetch)
            top_k: Number of top results to return

        Returns:
            Top-K chunks sorted by BM25 score, normalized to 0-1 range
        """
        if not chunks or not query.strip():
            return []

        # Build ephemeral index
        texts = [chunk.text for chunk in chunks]
        index = BM25Index(k1=self.k1, b=self.b)
        index.build(texts)

        # Score
        raw_scores = index.score(query)

        # Normalize scores to 0-1 range
        max_score = max(raw_scores) if raw_scores else 1.0
        if max_score == 0:
            max_score = 1.0

        normalized_scores = [s / max_score for s in raw_scores]

        # Pair chunks with scores and sort
        scored_chunks = list(zip(chunks, normalized_scores))
        scored_chunks.sort(key=lambda x: x[1], reverse=True)

        # Take top-K
        results = []
        for chunk, score in scored_chunks[:top_k]:
            if score > 0:
                results.append(
                    RetrievedChunk(
                        chunk=chunk,
                        score=round(score, 4),
                        retrieval_method=RetrievalMethod.SPARSE,
                    )
                )

        logger.debug(
            "sparse_search_complete",
            query_tokens=len(_tokenize(query)),
            candidates=len(chunks),
            results=len(results),
            top_score=results[0].score if results else 0,
        )

        return results
