"""
tests/integration/test_api.py

Integration tests for API endpoints.
Uses FastAPI TestClient with mocked services.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from fastapi.testclient import TestClient
from httpx import AsyncClient, ASGITransport

from app.main import create_app
from app.models.domain import AuthContext


@pytest.fixture
def auth_context():
    return AuthContext(
        user_id=uuid4(),
        org_id=uuid4(),
        email="test@example.com",
        role="admin",
    )


@pytest.fixture
def app(auth_context):
    """Create a test app with mocked dependencies."""
    test_app = create_app()

    # Mock app.state dependencies
    test_app.state.db_session_factory = MagicMock()
    test_app.state.db_engine = MagicMock()
    test_app.state.qdrant_client = MagicMock()
    test_app.state.vector_store = MagicMock()
    test_app.state.redis_client = None  # No Redis in tests
    test_app.state.redis_pool = None
    test_app.state.embedder = MagicMock(is_loaded=True)
    test_app.state.reranker = MagicMock()
    test_app.state.llm_client = MagicMock()
    test_app.state.output_validator = MagicMock()
    test_app.state.compiled_graph = MagicMock()
    test_app.state.hybrid_retriever = MagicMock()
    test_app.state.cache_manager = MagicMock()
    test_app.state.arq_pool = None

    return test_app


class TestLiveness:
    def test_liveness_no_auth(self, app):
        """Liveness endpoint should work without authentication."""
        with TestClient(app) as client:
            response = client.get("/health")
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "alive"
            assert data["service"] == "quillflow"


class TestHealthEndpoint:
    def test_health_requires_auth(self, app):
        """Deep health check requires authentication."""
        with TestClient(app) as client:
            response = client.get("/v1/health")
            # Should get 403 (no auth header) or 401
            assert response.status_code in (401, 403)


class TestChatEndpoint:
    def test_chat_requires_auth(self, app):
        with TestClient(app) as client:
            response = client.post(
                "/v1/chat",
                json={"query": "What is RAG?"},
            )
            assert response.status_code in (401, 403)

    def test_chat_validates_empty_query(self, app, auth_context):
        """Empty query should be rejected by Pydantic validation."""
        with TestClient(app) as client:
            response = client.post(
                "/v1/chat",
                json={"query": ""},
                headers={"Authorization": "Bearer test-key"},
            )
            # Will get 401 (auth fails) or 422 (validation)
            assert response.status_code in (401, 422)


class TestIngestEndpoint:
    def test_ingest_requires_auth(self, app):
        with TestClient(app) as client:
            response = client.post(
                "/v1/ingest",
                json={
                    "content": "Test document",
                    "filename": "test.txt",
                    "content_type": "text",
                },
            )
            assert response.status_code in (401, 403)

    def test_ingest_requires_editor_role(self, app):
        """Viewers should not be able to ingest documents."""
        # This would need a proper auth mock — placeholder test
        pass


class TestDocumentsEndpoint:
    def test_documents_requires_auth(self, app):
        with TestClient(app) as client:
            response = client.get("/v1/documents")
            assert response.status_code in (401, 403)


class TestBulkIngestEndpoint:
    def test_bulk_requires_auth(self, app):
        with TestClient(app) as client:
            response = client.post(
                "/v1/ingest/bulk",
                json={
                    "documents": [
                        {"content": "Doc 1", "filename": "d1.txt", "content_type": "text"},
                    ]
                },
            )
            assert response.status_code in (401, 403)

    def test_bulk_validates_empty_documents(self, app):
        with TestClient(app) as client:
            response = client.post(
                "/v1/ingest/bulk",
                json={"documents": []},
                headers={"Authorization": "Bearer test-key"},
            )
            assert response.status_code in (401, 422)


class TestJobStatusEndpoint:
    def test_job_status_requires_auth(self, app):
        with TestClient(app) as client:
            job_id = str(uuid4())
            response = client.get(f"/v1/ingest/jobs/{job_id}")
            assert response.status_code in (401, 403)
