"""
app/graph/nodes/cache_check.py

Checks both L1 (exact) and L2 (semantic) caches.
Also computes query embedding (reused by retriever if cache misses).
"""

from __future__ import annotations

import structlog

from app.graph.state import GraphState
from app.services.cache.manager import CacheManager
from app.services.retrieval.embedder import EmbeddingService

logger = structlog.get_logger(__name__)


async def cache_check_node(
    state: GraphState,
    cache_manager: CacheManager,
    embedder: EmbeddingService,
) -> GraphState:
    """
    Check cache for a previously generated response.

    Also computes the query embedding here (needed for L2 cache
    and reused by the retriever node if cache misses).

    Reads: sanitized_query, auth
    Writes: cache_hit, cached_response, query_embedding
    """
    query = state["sanitized_query"]
    auth = state["auth"]

    # Compute query embedding (reused downstream)
    query_embedding = await embedder.embed_text(query)

    # Check cache
    hit = await cache_manager.lookup(
        query=query,
        query_embedding=query_embedding,
        org_id=auth.org_id,
    )

    if hit is not None:
        logger.info(
            "cache_hit",
            cache_tier=hit.cache_tier,
            org_id=str(auth.org_id)[:8],
        )
        return {
            "cache_hit": True,
            "cached_response": hit.response,
            "query_embedding": query_embedding,
        }

    logger.debug("cache_miss", org_id=str(auth.org_id)[:8])

    return {
        "cache_hit": False,
        "cached_response": None,
        "query_embedding": query_embedding,
    }
