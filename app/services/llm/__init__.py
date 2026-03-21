"""
app/services/llm — LLM client layer for QuillFlow.

Components:
  - client.py:  Unified LLM client (Claude via OpenRouter, model routing)
  - prompts.py: All prompt templates used by graph nodes
  - retry.py:   Retry with exponential backoff + circuit breaker

Design principles:
  1. All LLM calls go through LLMClient — never call OpenRouter directly
  2. Every call returns a structured LLMResponse with usage tracking
  3. Prompts are centralized — no string formatting scattered in nodes
  4. Retry/circuit breaker is transparent to callers
"""
