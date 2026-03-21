"""
tests/unit/test_llm_client.py

Tests for the LLM client.
Uses mocking — no real API calls.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.llm.client import LLMClient, LLMResponse, StreamChunk, _estimate_cost


class TestCostEstimation:
    def test_sonnet_pricing(self):
        cost = _estimate_cost(
            "anthropic/claude-sonnet-4-20250514",
            input_tokens=1000,
            output_tokens=500,
        )
        # Input: 1000/1M * $3 = $0.003
        # Output: 500/1M * $15 = $0.0075
        # Total: $0.0105
        assert abs(cost - 0.0105) < 0.0001

    def test_opus_pricing(self):
        cost = _estimate_cost(
            "anthropic/claude-opus-4-6",
            input_tokens=1000,
            output_tokens=500,
        )
        # Input: 1000/1M * $15 = $0.015
        # Output: 500/1M * $75 = $0.0375
        # Total: $0.0525
        assert abs(cost - 0.0525) < 0.0001

    def test_unknown_model_uses_default(self):
        cost = _estimate_cost(
            "unknown/model",
            input_tokens=1000,
            output_tokens=500,
        )
        # Should use default pricing, not crash
        assert cost > 0

    def test_zero_tokens(self):
        cost = _estimate_cost("anthropic/claude-sonnet-4-20250514", 0, 0)
        assert cost == 0.0


class TestLLMResponseProperties:
    def test_was_truncated(self):
        from app.models.responses import TokenUsage

        response = LLMResponse(
            content="partial...",
            model="test",
            usage=TokenUsage(),
            response_id="test-id",
            latency_ms=100,
            finish_reason="length",
        )
        assert response.was_truncated is True

    def test_not_truncated(self):
        from app.models.responses import TokenUsage

        response = LLMResponse(
            content="complete answer",
            model="test",
            usage=TokenUsage(),
            response_id="test-id",
            latency_ms=100,
            finish_reason="stop",
        )
        assert response.was_truncated is False


class TestStreamChunk:
    def test_is_final(self):
        from app.models.responses import TokenUsage

        chunk = StreamChunk(content="", finish_reason="stop")
        assert chunk.is_final is True

    def test_not_final(self):
        chunk = StreamChunk(content="hello")
        assert chunk.is_final is False


class TestJsonCleaning:
    def test_removes_markdown_fences(self):
        client = LLMClient.__new__(LLMClient)  # Skip __init__
        assert client._clean_json_response('```json\n{"key": "value"}\n```') == '{"key": "value"}'

    def test_removes_plain_fences(self):
        client = LLMClient.__new__(LLMClient)
        assert client._clean_json_response('```\n{"key": "value"}\n```') == '{"key": "value"}'

    def test_strips_whitespace(self):
        client = LLMClient.__new__(LLMClient)
        assert client._clean_json_response('  {"key": "value"}  ') == '{"key": "value"}'

    def test_no_fences_passthrough(self):
        client = LLMClient.__new__(LLMClient)
        assert client._clean_json_response('{"key": "value"}') == '{"key": "value"}'