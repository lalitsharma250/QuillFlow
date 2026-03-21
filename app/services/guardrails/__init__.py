"""
app/services/guardrails — Input/output safety for QuillFlow.

Two-phase safety:
  1. Input filtering (BEFORE LLM call):
     - PII detection and stripping
     - Prompt injection detection
     - Content policy enforcement
     - Query length/complexity limits

  2. Output validation (AFTER LLM response):
     - Faithfulness scoring (is answer grounded in context?)
     - PII leak detection (did the LLM leak sensitive data?)
     - Relevancy scoring (does answer address the query?)
     - Safety classification (toxic, harmful content)

Components:
  - input_filter.py:     Pre-LLM safety checks
  - output_validator.py: Post-LLM quality and safety checks
"""
