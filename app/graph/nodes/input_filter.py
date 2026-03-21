"""
app/graph/nodes/input_filter.py

First node in the DAG. Runs input safety checks.
"""

from __future__ import annotations

import structlog

from app.graph.state import GraphState
from app.services.guardrails.input_filter import FilterAction, InputFilter

logger = structlog.get_logger(__name__)


def input_filter_node(state: GraphState) -> GraphState:
    """
    Run input filters on the query.

    Reads: query
    Writes: filter_result, sanitized_query, error
    """
    query = state["query"]

    filter_service = InputFilter()
    result = filter_service.check(query)

    updates: dict = {
        "filter_result": result,
    }

    if result.action == FilterAction.BLOCK:
        updates["error"] = result.block_reason
        updates["error_node"] = "input_filter"
        updates["sanitized_query"] = query
        logger.warning(
            "query_blocked",
            reason=result.block_reason,
            query_preview=query[:100],
        )
    elif result.action == FilterAction.SANITIZE:
        updates["sanitized_query"] = result.sanitized_query
        logger.info(
            "query_sanitized",
            pii_count=len(result.pii_entities),
            pii_types=[e.entity_type for e in result.pii_entities],
        )
    else:
        updates["sanitized_query"] = query

    return updates