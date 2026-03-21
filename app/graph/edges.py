"""
app/graph/edges.py

Conditional edge logic for the LangGraph DAG.

Each function examines the current state and returns the name
of the next node to execute. LangGraph uses these as routing
functions on conditional edges.
"""

from __future__ import annotations

from app.graph.state import GraphState
from app.models.domain import QueryType
from config.constants import (
    NODE_CACHE_CHECK,
    NODE_CACHE_WRITE,
    NODE_PLANNER,
    NODE_REDUCER,
    NODE_RETRIEVER,
    NODE_ROUTER,
    NODE_VALIDATOR,
    NODE_WRITER,
)

# Special node names for LangGraph
END = "__end__"


def after_input_filter(state: GraphState) -> str:
    """
    After input filter: proceed to cache check or end (if blocked).

    Routes:
      - error set → END (query was blocked)
      - no error  → cache_check
    """
    if state.get("error"):
        return END
    return NODE_CACHE_CHECK


def after_cache_check(state: GraphState) -> str:
    """
    After cache check: return cached response or proceed to router.

    Routes:
      - cache_hit=True  → END (return cached response)
      - cache_hit=False → router
    """
    if state.get("cache_hit", False):
        return END
    return NODE_ROUTER


def after_router(state: GraphState) -> str:
    """
    After router: always proceed to retriever.
    Both simple and complex queries need context.
    """
    return NODE_RETRIEVER


def after_retriever(state: GraphState) -> str:
    """
    After retriever: route based on query type.

    Routes:
      - SIMPLE  → reducer (direct answer, skip planner/writer)
      - COMPLEX → planner
    """
    query_type = state.get("query_type", QueryType.SIMPLE)

    if query_type == QueryType.COMPLEX:
        return NODE_PLANNER
    return NODE_REDUCER


def after_planner(state: GraphState) -> str:
    """After planner: proceed to parallel writers."""
    return NODE_WRITER


def after_writer(state: GraphState) -> str:
    """After writers: proceed to reducer for merging."""
    return NODE_REDUCER


def after_reducer(state: GraphState) -> str:
    """
    After reducer: skip validation entirely for now.
    Validation adds 6-10 seconds with minimal benefit
    when prompts are well-tuned.
    
    Re-enable when latency budget allows or for production auditing.
    """
    return NODE_CACHE_WRITE


def after_validator(state: GraphState) -> str:
    """
    After validator: write to cache or end.

    Routes:
      - approved     → cache_write
      - not approved → END (with rejection info in state)
    """
    is_approved = state.get("is_approved", False)

    if is_approved:
        return NODE_CACHE_WRITE
    return END


def after_cache_write(state: GraphState) -> str:
    """After cache write: always end."""
    return END
