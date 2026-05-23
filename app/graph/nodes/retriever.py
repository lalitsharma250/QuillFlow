"""
app/graph/nodes/retriever.py

Retrieves relevant chunks using hybrid search.
Includes LLM-based query rewriting for follow-up queries.
"""

from __future__ import annotations

import re
import structlog

from app.graph.state import GraphState
from app.services.llm.client import LLMClient
from app.services.retrieval.hybrid import HybridRetriever
from app.services.llm.prompts import query_rewrite_prompt
from config import get_settings

logger = structlog.get_logger(__name__)
settings = get_settings()

CONTEXT_DEPENDENT_PATTERNS = [
    r"\b(it|its|this|that|these|those|they|them|their)\b",
    r"\b(more|further|else|other)\b",
    r"^(yes|no|okay|sure|right)\b",
    r"^(why|how|when|what)\?*$", 
    r"^(tell|explain|elaborate|continue)\s+more",
    r"^(and|but|so)\b",  
]

_CONTEXT_REGEX = re.compile("|".join(CONTEXT_DEPENDENT_PATTERNS), re.IGNORECASE)


def _needs_rewrite(query: str, history: list[dict]) -> bool:
    """
    Determine if a query needs rewriting based on context.

    Returns True if:
      - History exists AND
      - Query is very short (<15 chars), OR
      - Query contains context-dependent words (pronouns, "more", etc.)
    """
    if not history:
        return False

    query_stripped = query.strip()

    if len(query_stripped) < 15:
        return True

    if _CONTEXT_REGEX.search(query_stripped):
        return True

    return False


async def retriever_node(
    state: GraphState,
    hybrid_retriever: HybridRetriever,
    llm_client: LLMClient,
) -> GraphState:
    """
    Retrieve relevant chunks using hybrid search.

    For follow-up queries that reference prior context (e.g., "What are its benefits?"),
    uses LLM to rewrite into a standalone search query first.
    """
    query = state.get("sanitized_query") or state["query"]
    auth = state["auth"]
    history = state.get("history", [])

    relevant_history = history
    if history and history[-1].get("role") == "user" and history[-1].get("content") == query:
        relevant_history = history[:-1]

    # ── Query Rewriting ────────────────────────────────
    search_query = query
    was_rewritten = False

    if _needs_rewrite(query, relevant_history):
        try:
            system, user_msg = query_rewrite_prompt(query, relevant_history)

            rewrite_response = await llm_client.generate(
                messages=[{"role": "user", "content": user_msg}],
                system_prompt=system,
                model_tier="fast",
                max_tokens=150,
                temperature=0.1,
            )

            rewritten = rewrite_response.content.strip().strip('"').strip("'")

            if rewritten and 5 < len(rewritten) < 500:
                search_query = rewritten
                was_rewritten = True
                logger.info(
                    "query_rewritten",
                    original=query[:80],
                    rewritten=search_query[:80],
                    history_turns=len(relevant_history),
                )
            else:
                logger.warning(
                    "query_rewrite_invalid",
                    original=query[:80],
                    rewrite_preview=rewritten[:80],
                )

        except Exception as e:
            logger.warning("query_rewrite_failed", error=str(e))

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
        original_query=query[:80],
        search_query=search_query[:80],
        was_rewritten=was_rewritten,
        history_turns=len(relevant_history),
        chunks_before_filter=len(chunks),
        chunks_after_filter=len(filtered_chunks),
        query_type=query_type_str,
        top_score=filtered_chunks[0].score if filtered_chunks else None,
        top_filename=filtered_chunks[0].chunk.metadata.source_filename if filtered_chunks else None,
    )

    return {
        "retrieved_chunks": filtered_chunks,  
        "query_embedding": state.get("query_embedding"),
    }