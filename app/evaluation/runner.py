"""
app/evaluation/runner.py

Batch evaluation runner.

Runs a set of evaluation samples through the full QuillFlow pipeline
and computes aggregate metrics. Designed to be:
  1. Called from CI (scripts/run_evaluation.py)
  2. Called from tests (tests/evaluation/test_rag_metrics.py)
  3. Called from admin API (future)

The runner:
  - Loads eval samples from a JSON file or list
  - Runs each sample through retrieval + generation
  - Computes all metrics
  - Generates an aggregate report
  - Returns pass/fail for CI gating
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from uuid import UUID, uuid4

import structlog

from app.evaluation.metrics import (
    EvalReport,
    EvalResult,
    EvalSample,
    answer_correctness,
    answer_relevancy_score,
    context_precision,
    context_recall,
    faithfulness_score,
)
from app.services.llm.client import LLMClient
from app.services.retrieval.hybrid import HybridRetriever

logger = structlog.get_logger(__name__)


class EvalRunner:
    """
    Batch evaluation runner for QuillFlow.

    Usage:
        runner = EvalRunner(
            retriever=hybrid_retriever,
            llm_client=llm_client,
            org_id=eval_org_id,
        )

        # From file
        report = await runner.run_from_file("eval_data/samples.json")

        # From list
        samples = [EvalSample(query="What is RAG?", expected_answer="...")]
        report = await runner.run(samples)

        # Check results
        if not report.all_passed:
            sys.exit(1)  # Fail CI
    """

    def __init__(
        self,
        retriever: HybridRetriever,
        llm_client: LLMClient,
        org_id: UUID,
        max_concurrency: int = 3,
    ) -> None:
        self._retriever = retriever
        self._llm = llm_client
        self._org_id = org_id
        self._max_concurrency = max_concurrency

    async def run(self, samples: list[EvalSample]) -> EvalReport:
        """
        Run evaluation on a list of samples.

        Args:
            samples: List of EvalSample objects

        Returns:
            EvalReport with aggregate metrics and per-sample results
        """
        logger.info("eval_run_started", sample_count=len(samples))

        semaphore = asyncio.Semaphore(self._max_concurrency)
        results: list[EvalResult] = []

        async def _eval_one(sample: EvalSample) -> EvalResult:
            async with semaphore:
                return await self._evaluate_sample(sample)

        tasks = [_eval_one(sample) for sample in samples]
        results = await asyncio.gather(*tasks)

        report = self._aggregate_results(list(results))

        logger.info(
            "eval_run_complete",
            **report.summary(),
        )

        return report

    async def run_from_file(self, filepath: str | Path) -> EvalReport:
        """
        Load samples from a JSON file and run evaluation.

        Expected file format:
        [
            {
                "query": "What is RAG?",
                "expected_answer": "RAG is...",
                "expected_chunk_texts": ["Retrieval augmented...", ...],
                "metadata": {"category": "definition"}
            },
            ...
        ]
        """
        path = Path(filepath)
        if not path.exists():
            raise FileNotFoundError(f"Eval dataset not found: {path}")

        with open(path) as f:
            raw_samples = json.load(f)

        samples = [
            EvalSample(
                query=s["query"],
                expected_answer=s["expected_answer"],
                expected_chunk_texts=s.get("expected_chunk_texts", []),
                metadata=s.get("metadata", {}),
            )
            for s in raw_samples
        ]

        logger.info(
            "eval_samples_loaded",
            filepath=str(path),
            sample_count=len(samples),
        )

        return await self.run(samples)

    async def _evaluate_sample(self, sample: EvalSample) -> EvalResult:
        """
        Evaluate a single sample through the full pipeline.

        Steps:
          1. Retrieve context chunks
          2. Generate answer
          3. Compute retrieval metrics
          4. Compute generation metrics
        """
        result = EvalResult(sample=sample)

        try:
            # ── Step 1: Retrieve ───────────────────────
            retrieval_start = time.monotonic()

            retrieved_chunks = await self._retriever.retrieve(
                query=sample.query,
                org_id=self._org_id,
                top_k=10,
            )

            result.retrieval_latency_ms = (time.monotonic() - retrieval_start) * 1000
            result.retrieved_chunks = retrieved_chunks

            # ── Step 2: Generate answer ────────────────
            generation_start = time.monotonic()

            from app.services.llm.prompts import simple_answer_prompt

            system, user_msg = simple_answer_prompt(
                query=sample.query,
                context_chunks=retrieved_chunks,
            )

            response = await self._llm.generate(
                messages=[{"role": "user", "content": user_msg}],
                system_prompt=system,
                model_tier="fast",
                temperature=0.3,
            )

            result.generated_answer = response.content
            result.generation_latency_ms = (time.monotonic() - generation_start) * 1000
            result.total_latency_ms = result.retrieval_latency_ms + result.generation_latency_ms

            # ── Step 3: Retrieval metrics ──────────────
            if sample.expected_chunk_texts:
                result.context_precision = context_precision(
                    retrieved_chunks=retrieved_chunks,
                    expected_chunk_texts=sample.expected_chunk_texts,
                )
                result.context_recall = context_recall(
                    retrieved_chunks=retrieved_chunks,
                    expected_chunk_texts=sample.expected_chunk_texts,
                )

            # ── Step 4: Generation metrics ─────────────
            result.faithfulness = await faithfulness_score(
                query=sample.query,
                answer=result.generated_answer,
                context_chunks=retrieved_chunks,
                llm_client=self._llm,
            )

            result.answer_relevancy = await answer_relevancy_score(
                query=sample.query,
                answer=result.generated_answer,
                llm_client=self._llm,
            )

            logger.debug(
                "eval_sample_complete",
                query_preview=sample.query[:80],
                faithfulness=result.faithfulness,
                relevancy=result.answer_relevancy,
                precision=result.context_precision,
                recall=result.context_recall,
                latency_ms=round(result.total_latency_ms, 1),
            )

        except Exception as e:
            result.error = str(e)
            logger.error(
                "eval_sample_failed",
                query_preview=sample.query[:80],
                error=str(e),
            )

        return result

    def _aggregate_results(self, results: list[EvalResult]) -> EvalReport:
        """Compute aggregate metrics across all results."""
        report = EvalReport(results=results)
        report.total_samples = len(results)

        # Collect non-None scores
        precisions = [r.context_precision for r in results if r.context_precision is not None]
        recalls = [r.context_recall for r in results if r.context_recall is not None]
        faithfulness_scores = [r.faithfulness for r in results if r.faithfulness is not None]
        relevancy_scores = [r.answer_relevancy for r in results if r.answer_relevancy is not None]
        latencies = [r.total_latency_ms for r in results if r.total_latency_ms > 0]

        # Compute averages
        report.avg_context_precision = _safe_avg(precisions)
        report.avg_context_recall = _safe_avg(recalls)
        report.avg_faithfulness = _safe_avg(faithfulness_scores)
        report.avg_answer_relevancy = _safe_avg(relevancy_scores)
        report.avg_total_latency_ms = _safe_avg(latencies) or 0.0

        # Count pass/fail
        for result in results:
            if result.error:
                report.error_samples += 1
            elif result.passed:
                report.passed_samples += 1
            else:
                report.failed_samples += 1

        return report


def _safe_avg(values: list[float]) -> float | None:
    """Compute average, returning None for empty lists."""
    if not values:
        return None
    return sum(values) / len(values)
