"""
scripts/run_evaluation.py

CLI script to run the evaluation suite.

Usage:
    # Run with default eval dataset
    python -m scripts.run_evaluation

    

```python
    # Run with custom dataset
    python -m scripts.run_evaluation --dataset eval_data/custom.json

    # Run with specific org ID (for data isolation)
    python -m scripts.run_evaluation --org-id <uuid>

    # Fail with exit code 1 if any sample fails (for CI)
    python -m scripts.run_evaluation --strict
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path
from uuid import UUID, uuid4

from config import get_settings
from config.logging import setup_logging


# Default eval samples for quick testing
DEFAULT_SAMPLES = [
    {
        "query": "What is Retrieval Augmented Generation?",
        "expected_answer": (
            "Retrieval Augmented Generation (RAG) is a technique that combines "
            "information retrieval with text generation. It retrieves relevant "
            "documents from a knowledge base and provides them as context to a "
            "language model to generate grounded responses."
        ),
        "expected_chunk_texts": [],
        "metadata": {"category": "definition", "difficulty": "easy"},
    },
    {
        "query": "How does the transformer architecture work?",
        "expected_answer": (
            "The Transformer architecture uses self-attention mechanisms to "
            "process input sequences in parallel. It was introduced in the "
            "'Attention Is All You Need' paper and relies on multi-head "
            "attention instead of recurrence or convolutions."
        ),
        "expected_chunk_texts": [],
        "metadata": {"category": "architecture", "difficulty": "medium"},
    },
    {
        "query": "What are vector databases used for in RAG systems?",
        "expected_answer": (
            "Vector databases store document embeddings and enable fast "
            "similarity search using algorithms like HNSW. In RAG systems, "
            "they are used to find the most relevant document chunks for a "
            "given query by comparing embedding vectors."
        ),
        "expected_chunk_texts": [],
        "metadata": {"category": "infrastructure", "difficulty": "medium"},
    },
]


async def main():
    parser = argparse.ArgumentParser(description="Run QuillFlow evaluation suite")
    parser.add_argument(
        "--dataset",
        type=str,
        default=None,
        help="Path to eval dataset JSON file",
    )
    parser.add_argument(
        "--org-id",
        type=str,
        default=None,
        help="Organization UUID for data isolation",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit with code 1 if any sample fails",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=3,
        help="Max concurrent evaluations",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Path to write JSON report",
    )
    args = parser.parse_args()

    setup_logging()
    settings = get_settings()

    # ── Initialize services ────────────────────────────
    from qdrant_client import AsyncQdrantClient
    from app.services.retrieval.embedder import EmbeddingService
    from app.services.retrieval.vector_store import VectorStoreService
    from app.services.retrieval.hybrid import HybridRetriever
    from app.services.retrieval.reranker import RerankerService, NoOpReranker
    from app.services.llm.client import LLMClient
    from app.evaluation.runner import EvalRunner
    from app.evaluation.metrics import EvalSample

    # Embedder
    embedder = EmbeddingService()
    await embedder.load()

    # Qdrant
    qdrant_client = AsyncQdrantClient(
        host=settings.qdrant_host,
        port=settings.qdrant_port,
    )
    vector_store = VectorStoreService(client=qdrant_client)

    # Reranker (optional)
    try:
        reranker = RerankerService()
        await reranker.load()
    except Exception:
        reranker = NoOpReranker()

    # Hybrid retriever
    retriever = HybridRetriever(
        embedder=embedder,
        vector_store=vector_store,
        reranker=reranker,
    )

    # LLM client
    llm_client = LLMClient()

    # Org ID
    org_id = UUID(args.org_id) if args.org_id else uuid4()

    # ── Load samples ───────────────────────────────────
    if args.dataset:
        runner = EvalRunner(
            retriever=retriever,
            llm_client=llm_client,
            org_id=org_id,
            max_concurrency=args.concurrency,
        )
        report = await runner.run_from_file(args.dataset)
    else:
        samples = [
            EvalSample(
                query=s["query"],
                expected_answer=s["expected_answer"],
                expected_chunk_texts=s.get("expected_chunk_texts", []),
                metadata=s.get("metadata", {}),
            )
            for s in DEFAULT_SAMPLES
        ]
        runner = EvalRunner(
            retriever=retriever,
            llm_client=llm_client,
            org_id=org_id,
            max_concurrency=args.concurrency,
        )
        report = await runner.run(samples)

    # ── Print report ───────────────────────────────────
    summary = report.summary()
    print("\n" + "=" * 60)
    print("  QuillFlow Evaluation Report")
    print("=" * 60)
    for key, value in summary.items():
        print(f"  {key:.<30} {value}")
    print("=" * 60)

    # Print failed samples
    if report.failed_samples > 0 or report.error_samples > 0:
        print("\n  Failed/Error Samples:")
        print("-" * 60)
        for result in report.results:
            if not result.passed or result.error:
                print(f"\n  Query: {result.sample.query[:80]}")
                if result.error:
                    print(f"  Error: {result.error[:200]}")
                else:
                    print(f"  Faithfulness: {result.faithfulness}")
                    print(f"  Relevancy:    {result.answer_relevancy}")
                    print(f"  Precision:    {result.context_precision}")
        print("-" * 60)

    # ── Write JSON report ──────────────────────────────
    if args.output:
        output_path = Path(args.output)
        output_data = {
            "summary": summary,
            "results": [
                {
                    "query": r.sample.query,
                    "expected_answer": r.sample.expected_answer,
                    "generated_answer": r.generated_answer[:500],
                    "faithfulness": r.faithfulness,
                    "answer_relevancy": r.answer_relevancy,
                    "context_precision": r.context_precision,
                    "context_recall": r.context_recall,
                    "total_latency_ms": round(r.total_latency_ms, 1),
                    "passed": r.passed,
                    "error": r.error,
                }
                for r in report.results
            ],
        }
        with open(output_path, "w") as f:
            json.dump(output_data, f, indent=2, default=str)
        print(f"\n  Report written to: {output_path}")

    # ── Cleanup ────────────────────────────────────────
    await qdrant_client.close()

    # ── Exit code ──────────────────────────────────────
    if args.strict and not report.all_passed:
        print(f"\n  ❌ FAILED: {report.failed_samples} failed, {report.error_samples} errors")
        sys.exit(1)
    else:
        print(f"\n  ✅ PASSED: {report.pass_rate}% pass rate")
        sys.exit(0)


if __name__ == "__main__":
    asyncio.run(main())
