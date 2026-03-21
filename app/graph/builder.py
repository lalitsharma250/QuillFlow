"""
app/graph/builder.py

Assembles the complete LangGraph DAG for QuillFlow.

The builder wires together:
  - All nodes (with their service dependencies injected)
  - All conditional edges (routing logic)
  - Entry and exit points

The resulting graph is a compiled, executable state machine.
"""

from __future__ import annotations

from functools import partial

import structlog
from langgraph.graph import END, StateGraph

from app.graph.state import GraphState
from app.graph import edges
from app.graph.nodes.input_filter import input_filter_node
from app.graph.nodes.cache_check import cache_check_node
from app.graph.nodes.router import router_node
from app.graph.nodes.retriever import retriever_node
from app.graph.nodes.planner import planner_node
from app.graph.nodes.writer import writer_node
from app.graph.nodes.reducer import reducer_node
from app.graph.nodes.validator import validator_node
from app.graph.nodes.cache_write import cache_write_node
from app.services.cache.manager import CacheManager
from app.services.guardrails.output_validator import OutputValidator
from app.services.llm.client import LLMClient
from app.services.retrieval.embedder import EmbeddingService
from app.services.retrieval.hybrid import HybridRetriever
from app.models.domain import AuthContext
from config.constants import (
    NODE_CACHE_CHECK,
    NODE_CACHE_WRITE,
    NODE_INPUT_FILTER,
    NODE_PLANNER,
    NODE_REDUCER,
    NODE_RETRIEVER,
    NODE_ROUTER,
    NODE_VALIDATOR,
    NODE_WRITER,
)

logger = structlog.get_logger(__name__)


def build_graph(
    llm_client: LLMClient,
    embedder: EmbeddingService,
    hybrid_retriever: HybridRetriever,
    cache_manager: CacheManager,
    output_validator: OutputValidator,
) -> StateGraph:
    """
    Build and compile the QuillFlow DAG.

    All service dependencies are injected here via functools.partial.
    Nodes themselves are thin — they just call services.

    Args:
        llm_client: LLM client for all generation calls
        embedder: Embedding service for query embedding
        hybrid_retriever: Retrieval service (dense + sparse + rerank)
        cache_manager: Two-tier cache manager
        output_validator: Post-generation quality checker

    Returns:
        Compiled StateGraph ready for execution
    """
    # ── Create graph ───────────────────────────────────
    graph = StateGraph(GraphState)

    # ── Add nodes (inject dependencies via partial) ────

    # Input filter is synchronous (no I/O)
    graph.add_node(NODE_INPUT_FILTER, input_filter_node)

    # Cache check needs embedder + cache
    graph.add_node(
        NODE_CACHE_CHECK,
        partial(cache_check_node, cache_manager=cache_manager, embedder=embedder),
    )

    # Router needs LLM
    graph.add_node(
        NODE_ROUTER,
        partial(router_node, llm_client=llm_client),
    )

    # Retriever needs hybrid retriever
    graph.add_node(
        NODE_RETRIEVER,
        partial(retriever_node, hybrid_retriever=hybrid_retriever, llm_client=llm_client),
    )

    # Planner needs LLM
    graph.add_node(
        NODE_PLANNER,
        partial(planner_node, llm_client=llm_client),
    )

    # Writer needs LLM
    graph.add_node(
        NODE_WRITER,
        partial(writer_node, llm_client=llm_client),
    )

    # Reducer needs LLM
    graph.add_node(
        NODE_REDUCER,
        partial(reducer_node, llm_client=llm_client),
    )

    # Validator needs output validator
    graph.add_node(
        NODE_VALIDATOR,
        partial(validator_node, output_validator=output_validator),
    )

    # Cache write needs cache manager
    graph.add_node(
        NODE_CACHE_WRITE,
        partial(cache_write_node, cache_manager=cache_manager),
    )

    # ── Set entry point ────────────────────────────────
    graph.set_entry_point(NODE_INPUT_FILTER)

    # ── Add edges ──────────────────────────────────────

    # Input filter → cache check or END
    graph.add_conditional_edges(
        NODE_INPUT_FILTER,
        edges.after_input_filter,
        {
            NODE_CACHE_CHECK: NODE_CACHE_CHECK,
            END: END,
        },
    )

    # Cache check → router or END (cache hit)
    graph.add_conditional_edges(
        NODE_CACHE_CHECK,
        edges.after_cache_check,
        {
            NODE_ROUTER: NODE_ROUTER,
            END: END,
        },
    )

    # Router → retriever (always)
    graph.add_edge(NODE_ROUTER, NODE_RETRIEVER)

    # Retriever → planner (complex) or reducer (simple)
    graph.add_conditional_edges(
        NODE_RETRIEVER,
        edges.after_retriever,
        {
            NODE_PLANNER: NODE_PLANNER,
            NODE_REDUCER: NODE_REDUCER,
        },
    )

    # Planner → writer
    graph.add_edge(NODE_PLANNER, NODE_WRITER)

    # Writer → reducer
    graph.add_edge(NODE_WRITER, NODE_REDUCER)

    # Reducer → validator
    graph.add_conditional_edges(
        NODE_REDUCER,
        edges.after_reducer,
        {
            NODE_VALIDATOR: NODE_VALIDATOR,
            NODE_CACHE_WRITE: NODE_CACHE_WRITE,
        },
    )

    # Validator → cache write or END
    graph.add_conditional_edges(
        NODE_VALIDATOR,
        edges.after_validator,
        {
            NODE_CACHE_WRITE: NODE_CACHE_WRITE,
            END: END,
        },
    )

    # Cache write → END
    graph.add_edge(NODE_CACHE_WRITE, END)

    # ── Compile ────────────────────────────────────────
    compiled = graph.compile()

    logger.info("quillflow_graph_compiled", node_count=9)

    return compiled


def create_initial_state(
    query: str,
    auth: AuthContext,
    conversation_id: str | None = None,
    model_preference: str = "auto",
    include_sources: bool = True,
    max_sections: int | None = None,
    stream: bool = True,
    response_id: str | None = None,
    history: list | None = None,
) -> GraphState:
    from uuid import uuid4

    return GraphState(
        query=query,
        auth=auth,
        conversation_id=conversation_id,
        model_preference=model_preference,
        include_sources=include_sources,
        max_sections_override=max_sections,
        stream=stream,
        response_id=response_id or str(uuid4()),
        retry_count=0,
        error=None,
        error_node=None,
        cache_hit=False,
        history=history or [],
    )
