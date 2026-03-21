"""
tests/unit/test_edges.py

Tests for conditional edge routing logic.
"""

import pytest

from app.graph.edges import (
    after_cache_check,
    after_input_filter,
    after_retriever,
    after_validator,
    END,
)
from app.models.domain import QueryType
from config.constants import (
    NODE_CACHE_CHECK,
    NODE_CACHE_WRITE,
    NODE_PLANNER,
    NODE_REDUCER,
    NODE_ROUTER,
)


class TestAfterInputFilter:
    def test_blocked_query_ends(self):
        state = {"error": "Query blocked by content policy"}
        assert after_input_filter(state) == END

    def test_clean_query_continues(self):
        state = {}
        assert after_input_filter(state) == NODE_CACHE_CHECK

    def test_sanitized_query_continues(self):
        state = {"sanitized_query": "cleaned query"}
        assert after_input_filter(state) == NODE_CACHE_CHECK


class TestAfterCacheCheck:
    def test_cache_hit_ends(self):
        state = {"cache_hit": True, "cached_response": {"content": "cached"}}
        assert after_cache_check(state) == END

    def test_cache_miss_continues(self):
        state = {"cache_hit": False}
        assert after_cache_check(state) == NODE_ROUTER

    def test_default_continues(self):
        state = {}
        assert after_cache_check(state) == NODE_ROUTER


class TestAfterRetriever:
    def test_simple_skips_planner(self):
        state = {"query_type": QueryType.SIMPLE}
        assert after_retriever(state) == NODE_REDUCER

    def test_complex_goes_to_planner(self):
        state = {"query_type": QueryType.COMPLEX}
        assert after_retriever(state) == NODE_PLANNER

    def test_default_is_simple(self):
        state = {}
        assert after_retriever(state) == NODE_REDUCER


class TestAfterValidator:
    def test_approved_goes_to_cache(self):
        state = {"is_approved": True}
        assert after_validator(state) == NODE_CACHE_WRITE

    def test_rejected_ends(self):
        state = {"is_approved": False}
        assert after_validator(state) == END

    def test_default_ends(self):
        state = {}
        assert after_validator(state) == END