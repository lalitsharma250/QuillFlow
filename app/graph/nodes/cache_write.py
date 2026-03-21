"""
app/graph/nodes/cache_write.py

Writes approved responses to cache for future reuse.
"""

from __future__ import annotations

from uuid import UUID

import structlog

from app.graph.state import GraphState
from app.services.cache.manager import CacheManager

logger = structlog.get_logger(__name__)


async def cache_write_node(
    state: GraphState,
    cache_manager: CacheManager,
) -> GraphState:
    """
    Write the approved response to both cache tiers.

    Only writes if:
      - Response was approved by validator
      - Cache is available
      - Response is not an error

    Reads: sanitized_query, query_embedding, auth, final_output,
           retrieved_chunks, is_approved, query_type, eval_scores, total_usage
    Writes: cache_written
    """
    is_approved = state.get("is_approved", True)
    error = state.get("error")

    if not is_approved or error:
        logger.debug("cache_write_skipped", approved=is_approved, has_error=bool(error))
        return {"cache_written": False}

    query = state["sanitized_query"]
    query_embedding = state.get("query_embedding")
    auth = state["auth"]
    final_output = state.get("final_output", "")
    chunks = state.get("retrieved_chunks", [])

    # Build the cacheable response dict
    response_dict = {
        "content": final_output,
        "query_type": state.get("query_type", "simple"),
        "sources": [
            {
                "filename": rc.chunk.metadata.source_filename,
                "page_number": rc.chunk.metadata.page_number,
                "section_heading": rc.chunk.metadata.section_heading,
                "chunk_text_preview": rc.chunk.text[:200],
                "relevance_score": rc.score,
            }
            for rc in chunks[:10]  # Cap sources in cache
        ],
    }

    # Collect document IDs for invalidation tracking
    doc_ids: list[UUID] = list({
        rc.chunk.metadata.source_doc_id for rc in chunks
    })

    await cache_manager.store(
        query=query,
        query_embedding=query_embedding,
        org_id=auth.org_id,
        response=response_dict,
        document_ids=doc_ids,
    )

    logger.debug(
        "cache_written",
        org_id=str(auth.org_id)[:8],
        doc_deps=len(doc_ids),
    )

    return {"cache_written": True}