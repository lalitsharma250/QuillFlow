"""
tests/unit/test_state.py

Tests for graph state creation and validation.
"""

import pytest
from uuid import uuid4

from app.graph.builder import create_initial_state
from app.models.domain import AuthContext


class TestCreateInitialState:
    @pytest.fixture
    def auth(self):
        return AuthContext(
            user_id=uuid4(),
            org_id=uuid4(),
            email="test@example.com",
            role="editor",
        )

    def test_creates_valid_state(self, auth):
        state = create_initial_state(
            query="What is RAG?",
            auth=auth,
        )
        assert state["query"] == "What is RAG?"
        assert state["auth"] == auth
        assert state["model_preference"] == "auto"
        assert state["include_sources"] is True
        assert state["stream"] is True
        assert state["retry_count"] == 0
        assert state["error"] is None
        assert state["cache_hit"] is False

    def test_custom_preferences(self, auth):
        state = create_initial_state(
            query="Explain transformers",
            auth=auth,
            model_preference="strong",
            include_sources=False,
            max_sections=3,
            stream=False,
        )
        assert state["model_preference"] == "strong"
        assert state["include_sources"] is False
        assert state["max_sections_override"] == 3
        assert state["stream"] is False

    def test_response_id_generated(self, auth):
        state = create_initial_state(query="test", auth=auth)
        assert state["response_id"] is not None
        assert len(state["response_id"]) > 0

    def test_custom_response_id(self, auth):
        state = create_initial_state(
            query="test",
            auth=auth,
            response_id="custom-id-123",
        )
        assert state["response_id"] == "custom-id-123"

    def test_conversation_id_optional(self, auth):
        state = create_initial_state(query="test", auth=auth)
        assert state["conversation_id"] is None

        state_with_conv = create_initial_state(
            query="test",
            auth=auth,
            conversation_id="conv-456",
        )
        assert state_with_conv["conversation_id"] == "conv-456"
