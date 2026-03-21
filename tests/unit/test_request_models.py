"""
tests/unit/test_request_models.py

Tests for API request validation.
"""

import pytest

from app.models.requests import ChatRequest, IngestRequest


class TestChatRequest:
    def test_valid_request(self):
        req = ChatRequest(query="What is RAG?")
        assert req.query == "What is RAG?"
        assert req.stream is True  # default
        assert req.model_preference == "auto"  # default

    def test_query_stripped(self):
        req = ChatRequest(query="  hello world  ")
        assert req.query == "hello world"

    def test_empty_query_rejected(self):
        with pytest.raises(ValueError):
            ChatRequest(query="")

    def test_whitespace_only_rejected(self):
        with pytest.raises(ValueError):
            ChatRequest(query="   \n\t  ")

    def test_invalid_model_preference_rejected(self):
        with pytest.raises(ValueError, match="model_preference"):
            ChatRequest(query="test", model_preference="turbo")

    def test_model_preference_normalized(self):
        req = ChatRequest(query="test", model_preference="FAST")
        assert req.model_preference == "fast"

    def test_max_sections_capped(self):
        with pytest.raises(ValueError):
            ChatRequest(query="test", max_sections=20)


class TestIngestRequest:
    def test_valid_request(self):
        req = IngestRequest(
            content="Some document text",
            filename="doc.txt",
            content_type="text",
        )
        assert req.content_type == "text"
        assert req.metadata == {}

    def test_invalid_content_type(self):
        with pytest.raises(ValueError):
            IngestRequest(
                content="text",
                filename="doc.docx",
                content_type="docx",
            )

    def test_metadata_too_many_keys(self):
        big_meta = {f"key_{i}": f"value_{i}" for i in range(25)}
        with pytest.raises(ValueError, match="more than 20"):
            IngestRequest(
                content="text",
                filename="doc.txt",
                metadata=big_meta,
            )

    def test_metadata_value_too_long(self):
        with pytest.raises(ValueError, match="max 1000 chars"):
            IngestRequest(
                content="text",
                filename="doc.txt",
                metadata={"key": "x" * 1500},
            )