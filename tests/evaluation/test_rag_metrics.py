"""
tests/evaluation/test_rag_metrics.py

Tests for RAG evaluation metrics.
Unit tests (no LLM calls) for retrieval metrics.
"""

import pytest
from uuid import uuid4

from app.evaluation.metrics import (
    EvalResult,
    EvalSample,
    EvalReport,
    context_precision,
    context_recall,
    _text_overlap,
)
from app.models.domain import Chunk, ChunkMetadata, RetrievedChunk


def _make_chunk(text: str, score: float = 0.9) -> RetrievedChunk:
    """Helper to create test RetrievedChunk objects."""
    return RetrievedChunk(
        chunk=Chunk(
            text=text,
            metadata=ChunkMetadata(
                org_id=uuid4(),
                source_doc_id=uuid4(),
                source_filename="test.txt",
                chunk_index=0,
            ),
        ),
        score=score,
    )


# ═══════════════════════════════════════════════════════════
# Text Overlap Tests
# ═══════════════════════════════════════════════════════════


class TestTextOverlap:
    def test_identical_texts(self):
        assert _text_overlap("hello world", "hello world") == 1.0

    def test_no_overlap(self):
        assert _text_overlap("hello world", "foo bar") == 0.0

    def test_partial_overlap(self):
        overlap = _text_overlap("the quick brown fox", "the slow brown dog")
        assert 0.0 < overlap < 1.0
        # "the" and "brown" overlap → 2/6 unique words
        assert abs(overlap - 2 / 6) < 0.01

    def test_empty_strings(self):
        assert _text_overlap("", "") == 1.0

    def test_one_empty(self):
        assert _text_overlap("hello", "") == 0.0


# ═══════════════════════════════════════════════════════════
# Context Precision Tests
# ═══════════════════════════════════════════════════════════


class TestContextPrecision:
    def test_all_relevant(self):
        retrieved = [
            _make_chunk("machine learning algorithms for classification"),
            _make_chunk("deep learning neural network training"),
        ]
        expected = [
            "machine learning algorithms for classification tasks",
            "deep learning neural network training methods",
        ]
        score = context_precision(retrieved, expected)
        assert score > 0.5

    def test_none_relevant(self):
        retrieved = [
            _make_chunk("cooking recipes for italian pasta"),
            _make_chunk("gardening tips for spring flowers"),
        ]
        expected = [
            "machine learning algorithms",
        ]
        score = context_precision(retrieved, expected)
        assert score == 0.0

    def test_empty_retrieved(self):
        assert context_precision([], ["expected text"]) == 0.0

    def test_empty_expected(self):
        retrieved = [_make_chunk("some text")]
        assert context_precision(retrieved, []) == 1.0

    def test_partial_relevance(self):
        retrieved = [
            _make_chunk("machine learning classification algorithms"),
            _make_chunk("cooking pasta recipes"),
        ]
        expected = ["machine learning classification"]
        score = context_precision(retrieved, expected)
        assert 0.3 <= score <= 0.7  # ~50% relevant


# ═══════════════════════════════════════════════════════════
# Context Recall Tests
# ═══════════════════════════════════════════════════════════


class TestContextRecall:
    def test_all_found(self):
        retrieved = [
            _make_chunk("machine learning algorithms for classification"),
            _make_chunk("deep learning neural network training"),
        ]
        expected = [
            "machine learning algorithms for classification tasks",
            "deep learning neural network training methods",
        ]
        score = context_recall(retrieved, expected)
        assert score > 0.5

    def test_none_found(self):
        retrieved = [
            _make_chunk("cooking recipes"),
        ]
        expected = [
            "machine learning algorithms",
            "deep learning networks",
        ]
        score = context_recall(retrieved, expected)
        assert score == 0.0

    def test_empty_expected(self):
        retrieved = [_make_chunk("some text")]
        assert context_recall(retrieved, []) == 1.0

    def test_empty_retrieved(self):
        assert context_recall([], ["expected text"]) == 0.0

    def test_partial_recall(self):
        retrieved = [
            _make_chunk("machine learning classification algorithms"),
        ]
        expected = [
            "machine learning classification",
            "deep learning neural networks",
        ]
        score = context_recall(retrieved, expected)
        assert 0.3 <= score <= 0.7  # Found 1 of 2


# ═══════════════════════════════════════════════════════════
# EvalResult Tests
# ═══════════════════════════════════════════════════════════


class TestEvalResult:
    def test_passed_with_good_scores(self):
        result = EvalResult(
            sample=EvalSample(query="test", expected_answer="test"),
            faithfulness=0.9,
            answer_relevancy=0.85,
            context_precision=0.8,
        )
        assert result.passed is True

    def test_failed_with_low_faithfulness(self):
        result = EvalResult(
            sample=EvalSample(query="test", expected_answer="test"),
            faithfulness=0.3,
            answer_relevancy=0.9,
        )
        assert result.passed is False

    def test_passed_with_no_scores(self):
        """No scores computed = passes (can't reject what we can't measure)."""
        result = EvalResult(
            sample=EvalSample(query="test", expected_answer="test"),
        )
        assert result.passed is True

    def test_error_result(self):
        result = EvalResult(
            sample=EvalSample(query="test", expected_answer="test"),
            error="Connection failed",
        )
        assert result.error is not None


# ═══════════════════════════════════════════════════════════
# EvalReport Tests
# ═══════════════════════════════════════════════════════════


class TestEvalReport:
    def test_all_passed(self):
        results = [
            EvalResult(
                sample=EvalSample(query=f"q{i}", expected_answer=f"a{i}"),
                faithfulness=0.9,
                answer_relevancy=0.85,
            )
            for i in range(3)
        ]
        report = EvalReport(
            results=results,
            total_samples=3,
            passed_samples=3,
            failed_samples=0,
            error_samples=0,
        )
        assert report.all_passed is True
        assert report.pass_rate == 100.0

    def test_some_failed(self):
        report = EvalReport(
            results=[],
            total_samples=10,
            passed_samples=7,
            failed_samples=2,
            error_samples=1,
        )
        assert report.all_passed is False
        assert report.pass_rate == 70.0

    def test_summary_format(self):
        report = EvalReport(
            results=[],
            total_samples=5,
            passed_samples=4,
            failed_samples=1,
            error_samples=0,
            avg_faithfulness=0.85,
            avg_answer_relevancy=0.9,
            avg_context_precision=None,
            avg_total_latency_ms=1500.0,
        )
        summary = report.summary()
        assert summary["total_samples"] == 5
        assert summary["pass_rate"] == "80.0%"
        assert summary["avg_faithfulness"] == "0.850"
        assert summary["avg_context_precision"] == "n/a"
        assert summary["avg_latency_ms"] == 1500.0

    def test_empty_report(self):
        report = EvalReport(results=[], total_samples=0)
        assert report.pass_rate == 0.0
        assert report.all_passed is True  # No failures = pass
