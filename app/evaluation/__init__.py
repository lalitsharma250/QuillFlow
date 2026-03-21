"""
app/evaluation — RAG evaluation framework for QuillFlow.

Purpose:
  - Measure retrieval quality (are we finding the right chunks?)
  - Measure generation quality (are answers faithful and relevant?)
  - Run as part of CI to catch regressions
  - Generate reports for monitoring dashboards

Components:
  - metrics.py: Individual metric implementations
  - runner.py:  Batch evaluation orchestrator
  - datasets/:  Test datasets (query + expected answer pairs)

Metrics implemented:
  - Context Precision:  Are retrieved chunks relevant to the query?
  - Context Recall:     Did we retrieve all chunks needed to answer?
  - Faithfulness:       Is the answer grounded in retrieved context?
  - Answer Relevancy:   Does the answer actually address the query?
  - End-to-End Latency: How long does the full pipeline take?
"""
