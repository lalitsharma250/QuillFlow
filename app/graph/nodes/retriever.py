"""
app/graph/nodes/retriever.py

Retrieves relevant chunks using hybrid search.
Includes LLM-based query rewriting for follow-up queries.
"""

from __future__ import annotations

import structlog

from app.graph.state import GraphState
from app.services.llm.client import LLMClient
from app.services.retrieval.hybrid import HybridRetriever
from app.services.llm.prompts import query_rewrite_prompt
from config import get_settings

logger = structlog.get_logger(__name__)
settings = get_settings()


# Queries shorter than this with history are candidates for rewriting
REWRITE_THRESHOLD = 30

# Words that indicate a specific standalone query (no rewrite needed)
SPECIFIC_STARTERS = [
    "what", "how", "why", "when", "where", "which",
    "explain", "describe", "compare", "define", "list",
]


def _needs_rewrite(query: str, history: list[dict]) -> bool:
    """Determine if a query needs rewriting based on context."""
    if not history:
        return False

    query_lower = query.lower().strip()

    # Short vague queries always need rewriting
    if len(query) < 15:
        return True

    # Check if query is already specific
    if any(query_lower.startswith(s) for s in SPECIFIC_STARTERS):
        return False

    # Medium-length queries that might be follow-ups
    if len(query) < REWRITE_THRESHOLD:
        return True

    return False


async def retriever_node(
    state: GraphState,
    hybrid_retriever: HybridRetriever,
    llm_client: LLMClient,
) -> GraphState:
    """
    Retrieve relevant chunks using hybrid search.
    
    If the query is a vague follow-up (e.g., "yes", "tell me more"),
    uses LLM to rewrite it into a standalone search query first.
    """
    query = state.get("sanitized_query") or state["query"]
    auth = state["auth"]
    history = state.get("history", [])

    # ── Query Rewriting ────────────────────────────────
    search_query = query

    if _needs_rewrite(query, history):
        try:
            system, user_msg = query_rewrite_prompt(query, history)

            rewrite_response = await llm_client.generate(
                messages=[{"role": "user", "content": user_msg}],
                system_prompt=system,
                model_tier="fast",
                max_tokens=100,
                temperature=0.1,
            )

            rewritten = rewrite_response.content.strip().strip('"').strip("'")

            # Only use rewrite if it's reasonable
            if rewritten and len(rewritten) > 5 and len(rewritten) < 500:
                search_query = rewritten
                logger.info(
                    "query_rewritten",
                    original=query,
                    rewritten=search_query[:100],
                    model="fast",
                )
            else:
                logger.debug("query_rewrite_skipped", reason="invalid_rewrite")

        except Exception as e:
            logger.warning("query_rewrite_failed", error=str(e))
            # Fall back to original query

    # ── Hybrid Retrieval ───────────────────────────────
    query_type_str = state.get("query_type", "simple")
    top_k = 10

    chunks = await hybrid_retriever.retrieve(
        query=search_query,
        org_id=auth.org_id,
        top_k=top_k,
    )

    RELEVANCE_THRESHOLD = settings.relevancy_threshold
    filtered_chunks = [c for c in chunks if c.score >= RELEVANCE_THRESHOLD]

    # Ensure we have at least 1 chunk (even if low score)
    if not filtered_chunks and chunks:
        filtered_chunks = [chunks[0]]

    logger.info(
        "retrieval_complete",
        original_query=query[:50],
        search_query=search_query[:50],
        chunks_before_filter=len(chunks),
        chunks_after_filter=len(filtered_chunks),
        query_type=query_type_str,
        top_score=filtered_chunks[0].score if filtered_chunks else None,
    )

    return {
        "retrieved_chunks": chunks,
        "query_embedding": state.get("query_embedding"),
    }