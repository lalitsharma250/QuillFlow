"""
app/graph/nodes/router.py

Classifies queries as simple or complex.
Determines the execution path through the rest of the DAG.
"""

from __future__ import annotations

import json

import structlog

from app.graph.state import GraphState
from app.models.domain import QueryType
from app.services.llm.client import LLMClient
from app.services.llm.prompts import router_prompt

logger = structlog.get_logger(__name__)


async def router_node(
    state: GraphState,
    llm_client: LLMClient,
) -> GraphState:
    """
    Classify the query as simple or complex.

    Reads: sanitized_query, model_preference
    Writes: query_type, total_usage
    """
    query = state["sanitized_query"]
    model_preference = state.get("model_preference", "auto")

    # If user explicitly chose a model tier, use it for routing too
    model_tier = "fast"  # Router always uses fast model (cheap)

    system, user_msg = router_prompt(query)

    try:
        response = await llm_client.generate_json(
            messages=[{"role": "user", "content": user_msg}],
            system_prompt=system,
            model_tier=model_tier,
            max_tokens=50,
            temperature=0.1,
        )

        result = json.loads(response.content)
        raw_type = result.get("query_type", "simple").lower()

        if raw_type == "complex":
            query_type = QueryType.COMPLEX
        else:
            query_type = QueryType.SIMPLE

        # Track usage
        existing_usage = state.get("total_usage")
        if existing_usage:
            total_usage = existing_usage + response.usage
        else:
            total_usage = response.usage

        logger.info(
            "query_classified",
            query_type=query_type.value,
            query_preview=query[:80],
            model=response.model,
            latency_ms=response.latency_ms,
        )

        return {
            "query_type": query_type,
            "total_usage": total_usage,
        }

    except Exception as e:
        # Default to simple on classification failure
        logger.warning(
            "router_classification_failed_defaulting_to_simple",
            error=str(e),
        )
        return {
            "query_type": QueryType.SIMPLE,
        }
