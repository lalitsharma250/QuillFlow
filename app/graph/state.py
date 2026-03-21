"""
app/graph/state.py

The typed state that flows through every node in the LangGraph DAG.

Rules:
  1. Every field is optional (total=False) — nodes populate progressively
  2. Each node reads what it needs and writes what it produces
  3. State is the ONLY way nodes communicate — no side channels
  4. Pydantic domain models are used for complex fields
  5. Metadata fields track execution for observability
"""

from __future__ import annotations

from typing import Annotated, TypedDict
from uuid import UUID

from langgraph.graph.message import add_messages

from app.models.domain import (
    AuthContext,
    ContentPlan,
    EvalScores,
    QueryType,
    RetrievedChunk,
    SectionDraft,
)
from app.models.responses import TokenUsage
from app.services.guardrails.input_filter import InputFilterResult
from app.services.guardrails.output_validator import ValidationResult


class GraphState(TypedDict, total=False):
    """
    LangGraph state schema for QuillFlow.

    Populated progressively as the DAG executes:
      Entry        → query, auth, config
      Input Filter → filter_result, sanitized_query
      Cache Check  → cache_hit, cached_response
      Router       → query_type
      Retriever    → retrieved_chunks
      Planner      → plan
      Writers      → section_drafts
      Reducer      → final_output
      Validator    → validation_result, eval_scores               
      Cache Write  → cache_written
    """

    # ── Input (set at DAG entry) ───────────────────────
    query: str
    auth: AuthContext
    conversation_id: str | None
    model_preference: str           # "auto", "fast", "strong"
    include_sources: bool
    max_sections_override: int | None
    stream: bool

    # ── Input Filter output ────────────────────────────
    filter_result: InputFilterResult
    sanitized_query: str            # Query after PII stripping (or original if clean)

    # ── Cache Check output ─────────────────────────────
    cache_hit: bool
    cached_response: dict | None
    query_embedding: list[float] | None  # Computed during cache check, reused by retriever

    # ── Router output ──────────────────────────────────
    query_type: QueryType

    # ── Retriever output ───────────────────────────────
    retrieved_chunks: list[RetrievedChunk]

    # ── Planner output ─────────────────────────────────
    plan: ContentPlan

    # ── Writer outputs ─────────────────────────────────
    section_drafts: list[SectionDraft]

    # ── Reducer output ─────────────────────────────────
    final_output: str

    # ── Validator output ───────────────────────────────
    validation_result: ValidationResult
    eval_scores: EvalScores
    is_approved: bool

    # ── Cache Write ────────────────────────────────────
    cache_written: bool

    # ── Accumulated token usage ────────────────────────
    total_usage: TokenUsage

    # ── Error handling ─────────────────────────────────
    error: str | None
    error_node: str | None

    # ── Execution metadata ─────────────────────────────
    response_id: str
    retry_count: int

    history: list[dict] # For multi-turn context, accumulated messages so far (role + content)