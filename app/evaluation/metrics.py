"""
app/evaluation/metrics.py

RAG-specific evaluation metrics.

Two categories:
  1. Retrieval metrics (no LLM needed — compare against ground truth)
  2. Generation metrics (LLM-as-judge — score quality of answers)

All metrics return a float between 0.0 and 1.0 (higher = better).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import structlog

from app.models.domain import RetrievedChunk
from app.services.llm.client import LLMClient
from app.services.llm.retry import LLMError

logger = structlog.get_logger(__name__)


# ═══════════════════════════════════════════════════════════
# Data Types
# ═══════════════════════════════════════════════════════════


@dataclass
class EvalSample:
    """
    A single evaluation sample.

    Contains a query, expected answer (ground truth), and optionally
    the expected source chunks that should be retrieved.
    """

    query: str
    expected_answer: str
    expected_chunk_texts: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


@dataclass
class EvalResult:
    """
    Evaluation result for a single sample.

    Contains all computed metrics plus the actual outputs
    for debugging.
    """

    sample: EvalSample
    # Retrieval metrics
    context_precision: float | None = None
    context_recall: float | None = None
    # Generation metrics
    faithfulness: float | None = None
    answer_relevancy: float | None = None
    # Actual outputs
    retrieved_chunks: list[RetrievedChunk] = field(default_factory=list)
    generated_answer: str = ""
    # Timing
    retrieval_latency_ms: float = 0.0
    generation_latency_ms: float = 0.0
    total_latency_ms: float = 0.0
    # Errors
    error: str | None = None

    @property
    def passed(self) -> bool:
        """Check if all computed metrics meet minimum thresholds."""
        from config import get_settings

        settings = get_settings()
        checks = []

        if self.faithfulness is not None:
            checks.append(self.faithfulness >= settings.eval_faithfulness_threshold)
        if self.answer_relevancy is not None:
            checks.append(self.answer_relevancy >= settings.eval_relevancy_threshold)
        if self.context_precision is not None:
            checks.append(self.context_precision >= settings.eval_context_precision_threshold)

        return all(checks) if checks else True


@dataclass
class EvalReport:
    """
    Aggregated evaluation report across all samples.
    """

    results: list[EvalResult]
    # Aggregate scores (averages)
    avg_context_precision: float | None = None
    avg_context_recall: float | None = None
    avg_faithfulness: float | None = None
    avg_answer_relevancy: float | None = None
    avg_total_latency_ms: float = 0.0
    # Pass/fail
    total_samples: int = 0
    passed_samples: int = 0
    failed_samples: int = 0
    error_samples: int = 0

    @property
    def pass_rate(self) -> float:
        if self.total_samples == 0:
            return 0.0
        return round(self.passed_samples / self.total_samples * 100, 1)

    @property
    def all_passed(self) -> bool:
        return self.failed_samples == 0 and self.error_samples == 0

    def summary(self) -> dict:
        """Generate a summary dict for logging and CI output."""
        return {
            "total_samples": self.total_samples,
            "passed": self.passed_samples,
            "failed": self.failed_samples,
            "errors": self.error_samples,
            "pass_rate": f"{self.pass_rate}%",
            "avg_context_precision": self._fmt(self.avg_context_precision),
            "avg_context_recall": self._fmt(self.avg_context_recall),
            "avg_faithfulness": self._fmt(self.avg_faithfulness),
            "avg_answer_relevancy": self._fmt(self.avg_answer_relevancy),
            "avg_latency_ms": round(self.avg_total_latency_ms, 1),
        }

    @staticmethod
    def _fmt(v: float | None) -> str:
        return f"{v:.3f}" if v is not None else "n/a"


# ═══════════════════════════════════════════════════════════
# Retrieval Metrics (No LLM needed)
# ═══════════════════════════════════════════════════════════


def context_precision(
    retrieved_chunks: list[RetrievedChunk],
    expected_chunk_texts: list[str],
    similarity_threshold: float = 0.5,
) -> float:
    """
    Context Precision: What fraction of retrieved chunks are relevant?

    Measures: precision = relevant_retrieved / total_retrieved

    A retrieved chunk is considered "relevant" if it has significant
    text overlap with any expected chunk.

    Args:
        retrieved_chunks: Chunks returned by the retriever
        expected_chunk_texts: Ground truth texts that should be retrieved
        similarity_threshold: Minimum text overlap ratio to count as match

    Returns:
        Precision score 0.0-1.0
    """
    if not retrieved_chunks:
        return 0.0

    if not expected_chunk_texts:
        # No ground truth — can't compute precision
        return 1.0

    relevant_count = 0

    for rc in retrieved_chunks:
        chunk_text = rc.chunk.text.lower()
        for expected in expected_chunk_texts:
            overlap = _text_overlap(chunk_text, expected.lower())
            if overlap >= similarity_threshold:
                relevant_count += 1
                break

    return relevant_count / len(retrieved_chunks)


def context_recall(
    retrieved_chunks: list[RetrievedChunk],
    expected_chunk_texts: list[str],
    similarity_threshold: float = 0.5,
) -> float:
    """
    Context Recall: What fraction of expected chunks were retrieved?

    Measures: recall = relevant_retrieved / total_expected

    Args:
        retrieved_chunks: Chunks returned by the retriever
        expected_chunk_texts: Ground truth texts that should be retrieved
        similarity_threshold: Minimum text overlap ratio to count as match

    Returns:
        Recall score 0.0-1.0
    """
    if not expected_chunk_texts:
        return 1.0

    if not retrieved_chunks:
        return 0.0

    found_count = 0
    retrieved_texts = [rc.chunk.text.lower() for rc in retrieved_chunks]

    for expected in expected_chunk_texts:
        expected_lower = expected.lower()
        for retrieved_text in retrieved_texts:
            if _text_overlap(retrieved_text, expected_lower) >= similarity_threshold:
                found_count += 1
                break

    return found_count / len(expected_chunk_texts)


def _text_overlap(text_a: str, text_b: str) -> float:
    """
    Compute text overlap ratio between two strings.

    Uses word-level Jaccard similarity:
      overlap = |words_a ∩ words_b| / |words_a ∪ words_b|

    Returns:
        Overlap ratio 0.0-1.0
    """
    words_a = set(text_a.split())
    words_b = set(text_b.split())

    if not words_a and not words_b:
        return 1.0

    intersection = words_a & words_b
    union = words_a | words_b

    if not union:
        return 0.0

    return len(intersection) / len(union)


# ═══════════════════════════════════════════════════════════
# Generation Metrics (LLM-as-Judge)
# ═══════════════════════════════════════════════════════════


async def faithfulness_score(
    query: str,
    answer: str,
    context_chunks: list[RetrievedChunk],
    llm_client: LLMClient,
) -> float | None:
    """
    Faithfulness: Is the answer grounded in the retrieved context?

    Uses LLM-as-judge to evaluate whether claims in the answer
    are supported by the provided context.

    Returns:
        Score 0.0-1.0 or None if evaluation fails
    """
    if not answer or not context_chunks:
        return None

    from app.services.llm.prompts import faithfulness_check_prompt

    system, user_msg = faithfulness_check_prompt(
        query=query,
        context_chunks=context_chunks,
        answer=answer,
    )

    try:
        response = await llm_client.generate_json(
            messages=[{"role": "user", "content": user_msg}],
            system_prompt=system,
            model_tier="fast",
            max_tokens=500,
            temperature=0.1,
        )

        result = json.loads(response.content)
        score = float(result.get("faithfulness_score", 0.0))
        return max(0.0, min(1.0, score))

    except (LLMError, json.JSONDecodeError, ValueError) as e:
        logger.warning("faithfulness_eval_failed", error=str(e))
        return None


async def answer_relevancy_score(
    query: str,
    answer: str,
    llm_client: LLMClient,
) -> float | None:
    """
    Answer Relevancy: Does the answer actually address the query?

    Uses LLM-as-judge to evaluate whether the answer is on-topic
    and addresses what was asked.

    Returns:
        Score 0.0-1.0 or None if evaluation fails
    """
    if not answer:
        return None

    from app.services.llm.prompts import relevancy_check_prompt

    system, user_msg = relevancy_check_prompt(
        query=query,
        answer=answer,
    )

    try:
        response = await llm_client.generate_json(
            messages=[{"role": "user", "content": user_msg}],
            system_prompt=system,
            model_tier="fast",
            max_tokens=500,
            temperature=0.1,
        )

        result = json.loads(response.content)
        score = float(result.get("relevancy_score", 0.0))
        return max(0.0, min(1.0, score))

    except (LLMError, json.JSONDecodeError, ValueError) as e:
        logger.warning("relevancy_eval_failed", error=str(e))
        return None


async def answer_correctness(
    generated_answer: str,
    expected_answer: str,
    llm_client: LLMClient,
) -> float | None:
    """
    Answer Correctness: How close is the generated answer to the expected answer?

    Uses LLM-as-judge to compare the generated answer against
    a ground truth expected answer.

    Returns:
        Score 0.0-1.0 or None if evaluation fails
    """
    if not generated_answer or not expected_answer:
        return None

    system = (
        "You are an answer correctness evaluator. Compare a generated answer "
        "against an expected (ground truth) answer.\n\n"
        "Scoring:\n"
        "- 1.0: Generated answer conveys the same information as expected\n"
        "- 0.7-0.9: Mostly correct with minor differences or missing details\n"
        "- 0.4-0.6: Partially correct, significant information missing or wrong\n"
        "- 0.0-0.3: Mostly incorrect or completely off-topic\n\n"
        "Focus on factual correctness, not writing style.\n\n"
        "Respond with JSON only:\n"
        '{"correctness_score": number, "reasoning": "string"}'
    )

    user_msg = (
        f"Expected answer:\n{expected_answer}\n\n"
        f"Generated answer:\n{generated_answer}\n\n"
        f"Evaluate correctness:"
    )

    try:
        response = await llm_client.generate_json(
            messages=[{"role": "user", "content": user_msg}],
            system_prompt=system,
            model_tier="fast",
            max_tokens=500,
            temperature=0.1,
        )

        result = json.loads(response.content)
        score = float(result.get("correctness_score", 0.0))
        return max(0.0, min(1.0, score))

    except (LLMError, json.JSONDecodeError, ValueError) as e:
        logger.warning("correctness_eval_failed", error=str(e))
        return None
