"""
tests/integration/test_graph_pipeline.py

Integration tests for the LangGraph DAG.
Uses mocked services to test the graph execution flow.
"""

import pytest
import json
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from app.graph.builder import build_graph, create_initial_state
from app.graph.state import GraphState
from app.models.domain import (
    AuthContext,
    ContentPlan,
    EvalScores,
    QueryType,
    SectionPlan,
)
from app.models.responses import TokenUsage
from app.services.guardrails.output_validator import ValidationResult


class TestGraphSimpleQuery:
    """Test the graph execution path for simple queries."""

    @pytest.fixture
    def simple_graph_services(self, mock_llm_client, mock_retriever, mock_embedder, mock_cache_manager, mock_output_validator):
        """Configure mocks for a simple query flow."""

        # Router returns "simple"
        from app.services.llm.client import LLMResponse

        router_response = LLMResponse(
            content='{"query_type": "simple"}',
            model="mock",
            usage=TokenUsage(input_tokens=50, output_tokens=10, total_tokens=60),
            response_id="r1",
            latency_ms=30,
            finish_reason="stop",
        )

        answer_response = LLMResponse(
            content="RAG combines retrieval with generation for grounded answers.",
            model="mock",
            usage=TokenUsage(input_tokens=200, output_tokens=50, total_tokens=250),
            response_id="r2",
            latency_ms=100,
            finish_reason="stop",
        )

        # generate_json for router, generate for answer
        call_count = {"n": 0}
        original_generate_json = mock_llm_client.generate_json

        async def _mock_generate_json(**kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return router_response
            # Faithfulness/relevancy checks
            return LLMResponse(
                content='{"faithfulness_score": 0.9, "reasoning": "good"}',
                model="mock",
                usage=TokenUsage(input_tokens=100, output_tokens=30, total_tokens=130),
                response_id=f"eval-{call_count['n']}",
                latency_ms=50,
                finish_reason="stop",
            )

        mock_llm_client.generate_json = AsyncMock(side_effect=_mock_generate_json)
        mock_llm_client.generate = AsyncMock(return_value=answer_response)

        return {
            "llm_client": mock_llm_client,
            "retriever": mock_retriever,
            "embedder": mock_embedder,
            "cache_manager": mock_cache_manager,
            "output_validator": mock_output_validator,
        }

    async def test_simple_query_flow(self, simple_graph_services, auth_context):
        """Simple query should: filter → cache → route → retrieve → reduce → validate → cache_write."""
        services = simple_graph_services

        graph = build_graph(
            llm_client=services["llm_client"],
            embedder=services["embedder"],
            hybrid_retriever=services["retriever"],
            cache_manager=services["cache_manager"],
            output_validator=services["output_validator"],
        )

        initial_state = create_initial_state(
            query="What is RAG?",
            auth=auth_context,
            model_preference="fast",
            stream=False,
        )

        final_state = await graph.ainvoke(initial_state)

        # Verify flow
        assert final_state.get("error") is None
        assert final_state.get("sanitized_query") == "What is RAG?"
        assert final_state.get("cache_hit") is False
        assert final_state.get("query_type") == QueryType.SIMPLE
        assert len(final_state.get("retrieved_chunks", [])) > 0
        assert final_state.get("final_output") is not None
        assert len(final_state["final_output"]) > 0
        assert final_state.get("is_approved") is True

    async def test_simple_query_skips_planner_and_writer(self, simple_graph_services, auth_context):
        """Simple queries should NOT go through planner or writer nodes."""
        services = simple_graph_services

        graph = build_graph(
            llm_client=services["llm_client"],
            embedder=services["embedder"],
            hybrid_retriever=services["retriever"],
            cache_manager=services["cache_manager"],
            output_validator=services["output_validator"],
        )

        initial_state = create_initial_state(
            query="What is RAG?",
            auth=auth_context,
        )

        final_state = await graph.ainvoke(initial_state)

        # Plan and section_drafts should NOT be set
        assert final_state.get("plan") is None
        assert final_state.get("section_drafts") is None or final_state.get("section_drafts") == []


class TestGraphComplexQuery:
    """Test the graph execution path for complex queries."""

    @pytest.fixture
    def complex_graph_services(self, mock_llm_client, mock_retriever, mock_embedder, mock_cache_manager, mock_output_validator):
        """Configure mocks for a complex query flow."""
        from app.services.llm.client import LLMResponse

        call_sequence = {"n": 0}

        plan_json = json.dumps({
            "title": "Guide to RAG",
            "sections": [
                {"heading": "Introduction", "description": "Basics", "word_budget": 200, "key_points": []},
                {"heading": "Architecture", "description": "Components", "word_budget": 300, "key_points": []},
            ],
            "total_word_budget": 500,
            "target_audience": "technical",
        })

        writer_json_1 = json.dumps({
            "heading": "Introduction",
            "content": "RAG is a powerful technique that combines retrieval with generation.",
            "sources_used": ["rag_overview.pdf › p.1"],
        })

        writer_json_2 = json.dumps({
            "heading": "Architecture",
            "content": "A RAG system consists of a retriever, vector database, and language model.",
            "sources_used": ["rag_overview.pdf › p.2"],
        })

        async def _mock_generate_json(**kwargs):
            call_sequence["n"] += 1
            messages = kwargs.get("messages", [])
            system = kwargs.get("system_prompt", "")

            # Router call
            if "classify" in system.lower() or "classifier" in system.lower():
                return LLMResponse(
                    content='{"query_type": "complex"}',
                    model="mock", usage=TokenUsage(input_tokens=50, output_tokens=10, total_tokens=60),
                    response_id=f"call-{call_sequence['n']}", latency_ms=30, finish_reason="stop",
                )

            # Planner call
            if "planner" in system.lower() or "content plan" in system.lower():
                return LLMResponse(
                    content=plan_json,
                    model="mock", usage=TokenUsage(input_tokens=200, output_tokens=100, total_tokens=300),
                    response_id=f"call-{call_sequence['n']}", latency_ms=100, finish_reason="stop",
                )

            # Writer calls
            if "writer" in system.lower() or "write one section" in system.lower():
                content = writer_json_1 if call_sequence["n"] % 2 == 0 else writer_json_2
                return LLMResponse(
                    content=content,
                    model="mock", usage=TokenUsage(input_tokens=150, output_tokens=80, total_tokens=230),
                    response_id=f"call-{call_sequence['n']}", latency_ms=80, finish_reason="stop",
                )

            # Eval calls (faithfulness/relevancy)
            return LLMResponse(
                content='{"faithfulness_score": 0.85, "relevancy_score": 0.9, "reasoning": "good"}',
                model="mock", usage=TokenUsage(input_tokens=100, output_tokens=30, total_tokens=130),
                response_id=f"call-{call_sequence['n']}", latency_ms=50, finish_reason="stop",
            )

        mock_llm_client.generate_json = AsyncMock(side_effect=_mock_generate_json)
        mock_llm_client.generate = AsyncMock(return_value=LLMResponse(
            content="# Guide to RAG\n\n## Introduction\n\nRAG is powerful...\n\n## Architecture\n\nA RAG system consists of...",
            model="mock", usage=TokenUsage(input_tokens=300, output_tokens=200, total_tokens=500),
            response_id="reducer", latency_ms=150, finish_reason="stop",
        ))

        return {
            "llm_client": mock_llm_client,
            "retriever": mock_retriever,
            "embedder": mock_embedder,
            "cache_manager": mock_cache_manager,
            "output_validator": mock_output_validator,
        }

    async def test_complex_query_flow(self, complex_graph_services, auth_context):
        """Complex query should: filter → cache → route → retrieve → plan → write → reduce → validate."""
        services = complex_graph_services

        graph = build_graph(
            llm_client=services["llm_client"],
            embedder=services["embedder"],
            hybrid_retriever=services["retriever"],
            cache_manager=services["cache_manager"],
            output_validator=services["output_validator"],
        )

        initial_state = create_initial_state(
            query="Write a comprehensive guide to RAG systems",
            auth=auth_context,
            model_preference="strong",
            stream=False,
        )

        final_state = await graph.ainvoke(initial_state)

        assert final_state.get("error") is None
        assert final_state.get("query_type") == QueryType.COMPLEX
        assert final_state.get("plan") is not None
        assert len(final_state.get("plan").sections) == 2
        assert final_state.get("section_drafts") is not None
        assert len(final_state.get("section_drafts")) == 2
        assert final_state.get("final_output") is not None
        assert "RAG" in final_state["final_output"]
        assert final_state.get("is_approved") is True


class TestGraphEdgeCases:
    """Test error handling and edge cases in the graph."""

    async def test_blocked_query(self, mock_llm_client, mock_retriever, mock_embedder, mock_cache_manager, mock_output_validator, auth_context):
        """Blocked queries should terminate early with error."""
        graph = build_graph(
            llm_client=mock_llm_client,
            embedder=mock_embedder,
            hybrid_retriever=mock_retriever,
            cache_manager=mock_cache_manager,
            output_validator=mock_output_validator,
        )

        initial_state = create_initial_state(
            query="How to make a bomb",
            auth=auth_context,
        )

        final_state = await graph.ainvoke(initial_state)

        assert final_state.get("error") is not None
        assert "content policy" in final_state["error"].lower() or "blocked" in final_state["error"].lower()
        # Should NOT have retrieved chunks or generated output
        assert final_state.get("retrieved_chunks") is None or final_state.get("retrieved_chunks") == []

    async def test_cache_hit_returns_early(self, mock_llm_client, mock_retriever, mock_embedder, mock_cache_manager, mock_output_validator, auth_context):
        """Cache hits should return immediately without LLM calls."""
        from app.services.cache.manager import CacheLookupResult

        # Configure cache to return a hit
        mock_cache_manager.lookup = AsyncMock(return_value=CacheLookupResult(
            response={"content": "Cached answer about RAG", "query_type": "simple"},
            cache_tier="exact",
        ))

        graph = build_graph(
            llm_client=mock_llm_client,
            embedder=mock_embedder,
            hybrid_retriever=mock_retriever,
            cache_manager=mock_cache_manager,
            output_validator=mock_output_validator,
        )

        initial_state = create_initial_state(
            query="What is RAG?",
            auth=auth_context,
        )

        final_state = await graph.ainvoke(initial_state)

        assert final_state.get("cache_hit") is True
        assert final_state.get("cached_response") is not None
        # Router should NOT have been called
        assert final_state.get("query_type") is None

    async def test_pii_sanitization(self, mock_llm_client, mock_retriever, mock_embedder, mock_cache_manager, mock_output_validator, auth_context):
        """Queries with PII should be sanitized before processing."""
        from app.services.llm.client import LLMResponse

        mock_llm_client.generate_json = AsyncMock(return_value=LLMResponse(
            content='{"query_type": "simple"}',
            model="mock", usage=TokenUsage(), response_id="r", latency_ms=30, finish_reason="stop",
        ))
        mock_llm_client.generate = AsyncMock(return_value=LLMResponse(
            content="Here is information about the topic.",
            model="mock", usage=TokenUsage(), response_id="r2", latency_ms=50, finish_reason="stop",
        ))

        graph = build_graph(
            llm_client=mock_llm_client,
            embedder=mock_embedder,
            hybrid_retriever=mock_retriever,
            cache_manager=mock_cache_manager,
            output_validator=mock_output_validator,
        )

        initial_state = create_initial_state(
            query="My email is john@example.com, what is RAG?",
            auth=auth_context,
        )

        final_state = await graph.ainvoke(initial_state)

        # Query should have been sanitized
        sanitized = final_state.get("sanitized_query", "")
        assert "john@example.com" not in sanitized
        assert "[EMAIL_1]" in sanitized
