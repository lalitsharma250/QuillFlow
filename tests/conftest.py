"""
tests/conftest.py

Shared fixtures for the entire test suite.

Fixture categories:
  1. Mock services (no external dependencies — for unit tests)
  2. Real services (require docker-compose — for integration tests)
  3. Test app (FastAPI TestClient with mocked auth)
  4. Test data factories (create domain objects quickly)
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.models.domain import (
    AuthContext,
    Chunk,
    ChunkMetadata,
    ContentPlan,
    Document,
    DocumentStatus,
    EvalScores,
    QueryType,
    RetrievedChunk,
    RetrievalMethod,
    SectionDraft,
    SectionPlan,
)
from app.models.responses import TokenUsage
from config import get_settings


# ═══════════════════════════════════════════════════════════
# Event Loop
# ═══════════════════════════════════════════════════════════


@pytest.fixture(scope="session")
def event_loop():
    """Create a single event loop for the entire test session."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


# ═══════════════════════════════════════════════════════════
# Auth Fixtures
# ═══════════════════════════════════════════════════════════


@pytest.fixture
def org_id() -> UUID:
    """A test organization ID."""
    return uuid4()


@pytest.fixture
def user_id() -> UUID:
    """A test user ID."""
    return uuid4()


@pytest.fixture
def auth_context(user_id: UUID, org_id: UUID) -> AuthContext:
    """An authenticated admin user context."""
    return AuthContext(
        user_id=user_id,
        org_id=org_id,
        email="test@quillflow.dev",
        role="admin",
    )


@pytest.fixture
def viewer_auth(org_id: UUID) -> AuthContext:
    """An authenticated viewer user context."""
    return AuthContext(
        user_id=uuid4(),
        org_id=org_id,
        email="viewer@quillflow.dev",
        role="viewer",
    )


@pytest.fixture
def editor_auth(org_id: UUID) -> AuthContext:
    """An authenticated editor user context."""
    return AuthContext(
        user_id=uuid4(),
        org_id=org_id,
        email="editor@quillflow.dev",
        role="editor",
    )


# ═══════════════════════════════════════════════════════════
# Mock LLM Client
# ═══════════════════════════════════════════════════════════


@pytest.fixture
def mock_llm_client() -> MagicMock:
    """
    Mock LLM client that returns predictable responses.
    Use this for unit tests that don't need real LLM calls.
    """
    from app.services.llm.client import LLMResponse

    client = MagicMock()

    # Default generate response
    default_response = LLMResponse(
        content="This is a test response from the mock LLM.",
        model="mock-model",
        usage=TokenUsage(
            input_tokens=100,
            output_tokens=50,
            total_tokens=150,
            estimated_cost_usd=0.001,
        ),
        response_id="mock-response-id",
        latency_ms=50.0,
        finish_reason="stop",
    )

    client.generate = AsyncMock(return_value=default_response)
    client.generate_json = AsyncMock(return_value=default_response)
    client.health_check = AsyncMock(return_value=(True, 50.0))
    client.get_circuit_status = MagicMock(return_value={"fast": "closed", "strong": "closed"})

    return client


@pytest.fixture
def mock_llm_router_response(mock_llm_client: MagicMock) -> MagicMock:
    """Configure mock LLM to return a router classification response."""
    from app.services.llm.client import LLMResponse

    router_response = LLMResponse(
        content='{"query_type": "simple"}',
        model="mock-model",
        usage=TokenUsage(input_tokens=50, output_tokens=10, total_tokens=60),
        response_id="mock-router",
        latency_ms=30.0,
        finish_reason="stop",
    )
    mock_llm_client.generate_json = AsyncMock(return_value=router_response)
    return mock_llm_client


@pytest.fixture
def mock_llm_planner_response(mock_llm_client: MagicMock) -> MagicMock:
    """Configure mock LLM to return a planner response."""
    import json
    from app.services.llm.client import LLMResponse

    plan_json = json.dumps({
        "title": "Test Article",
        "sections": [
            {
                "heading": "Introduction",
                "description": "Cover the basics",
                "word_budget": 200,
                "key_points": ["point one"],
            },
            {
                "heading": "Details",
                "description": "Go deeper",
                "word_budget": 300,
                "key_points": [],
            },
        ],
        "total_word_budget": 500,
        "target_audience": "general",
    })

    planner_response = LLMResponse(
        content=plan_json,
        model="mock-model",
        usage=TokenUsage(input_tokens=200, output_tokens=100, total_tokens=300),
        response_id="mock-planner",
        latency_ms=100.0,
        finish_reason="stop",
    )
    mock_llm_client.generate_json = AsyncMock(return_value=planner_response)
    return mock_llm_client


# ═══════════════════════════════════════════════════════════
# Mock Retriever
# ═══════════════════════════════════════════════════════════


@pytest.fixture
def mock_retriever(org_id: UUID) -> MagicMock:
    """Mock hybrid retriever that returns predictable chunks."""
    retriever = MagicMock()

    sample_chunks = [
        RetrievedChunk(
            chunk=Chunk(
                id=uuid4(),
                text=(
                    "Retrieval Augmented Generation (RAG) combines information "
                    "retrieval with text generation to produce grounded responses."
                ),
                metadata=ChunkMetadata(
                    org_id=org_id,
                    source_doc_id=uuid4(),
                    source_filename="rag_overview.pdf",
                    page_number=1,
                    section_heading="Introduction",
                    chunk_index=0,
                ),
            ),
            score=0.95,
            retrieval_method=RetrievalMethod.HYBRID,
        ),
        RetrievedChunk(
            chunk=Chunk(
                id=uuid4(),
                text=(
                    "The key components of a RAG system include a retriever, "
                    "a vector database, and a language model for generation."
                ),
                metadata=ChunkMetadata(
                    org_id=org_id,
                    source_doc_id=uuid4(),
                    source_filename="rag_overview.pdf",
                    page_number=2,
                    section_heading="Components",
                    chunk_index=1,
                ),
            ),
            score=0.88,
            retrieval_method=RetrievalMethod.DENSE,
        ),
    ]

    retriever.retrieve = AsyncMock(return_value=sample_chunks)
    return retriever


# ═══════════════════════════════════════════════════════════
# Mock Embedder
# ═══════════════════════════════════════════════════════════


@pytest.fixture
def mock_embedder() -> MagicMock:
    """Mock embedding service that returns fixed-dimension vectors."""
    embedder = MagicMock()
    embedder.is_loaded = True
    embedder.dimensions = 1024
    embedder.batch_size = 64

    # Return a deterministic embedding based on text hash
    async def _mock_embed_text(text: str) -> list[float]:
        import hashlib
        h = hashlib.md5(text.encode()).hexdigest()
        # Generate a pseudo-random but deterministic 1024-dim vector
        return [float(int(h[i % 32], 16)) / 15.0 for i in range(1024)]

    async def _mock_embed_batch(texts: list[str]) -> list[list[float]]:
        return [await _mock_embed_text(t) for t in texts]

    embedder.embed_text = _mock_embed_text
    embedder.embed_batch = _mock_embed_batch
    embedder.load = AsyncMock()

    return embedder


# ═══════════════════════════════════════════════════════════
# Mock Cache
# ═══════════════════════════════════════════════════════════


@pytest.fixture
def mock_cache_manager() -> MagicMock:
    """Mock cache manager that always misses."""
    from app.services.cache.manager import CacheManager

    cache = MagicMock(spec=CacheManager)
    cache.is_available = False
    cache.lookup = AsyncMock(return_value=None)
    cache.store = AsyncMock()
    cache.invalidate_by_document = AsyncMock(return_value={"exact": 0, "semantic": 0, "total": 0})
    return cache


# ═══════════════════════════════════════════════════════════
# Mock Output Validator
# ═══════════════════════════════════════════════════════════


@pytest.fixture
def mock_output_validator() -> MagicMock:
    """Mock output validator that always approves."""
    from app.services.guardrails.output_validator import ValidationResult

    validator = MagicMock()
    validator.validate = AsyncMock(return_value=ValidationResult(
        is_approved=True,
        eval_scores=EvalScores(faithfulness=0.9, answer_relevancy=0.85),
    ))
    return validator


# ═══════════════════════════════════════════════════════════
# Test Database (requires Postgres)
# ═══════════════════════════════════════════════════════════


@pytest_asyncio.fixture
async def db_engine():
    """
    Create a test database engine.
    Uses the configured Postgres with a test-specific database.
    Skips if Postgres is unavailable.
    """
    settings = get_settings()

    try:
        from sqlalchemy.ext.asyncio import create_async_engine
        from app.db.engine import create_tables, drop_tables

        engine = create_async_engine(
            settings.postgres_dsn,
            pool_size=5,
            echo=False,
        )

        # Create tables
        await create_tables(engine)

        yield engine

        # Cleanup
        await drop_tables(engine)
        await engine.dispose()

    except Exception as e:
        pytest.skip(f"Postgres not available: {e}")


@pytest_asyncio.fixture
async def db_session(db_engine):
    """Create a test database session."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    session_factory = async_sessionmaker(db_engine, expire_on_commit=False)

    async with session_factory() as session:
        yield session
        await session.rollback()


@pytest_asyncio.fixture
async def db_session_with_org(db_session):
    """Create a test database session with a pre-created organization."""
    from app.db.models import OrganizationRecord

    org = OrganizationRecord(name=f"Test Org {uuid4().hex[:8]}")
    db_session.add(org)
    await db_session.flush()

    yield db_session, org.id

    # Rollback handles cleanup


# ═══════════════════════════════════════════════════════════
# Test Redis (requires Redis)
# ═══════════════════════════════════════════════════════════


@pytest_asyncio.fixture
async def redis_client():
    """
    Create a test Redis client using database 15.
    Skips if Redis is unavailable.
    """
    try:
        from redis.asyncio import Redis

        client = Redis(host="localhost", port=6379, db=15)
        await client.ping()
        await client.flushdb()

        yield client

        await client.flushdb()
        await client.aclose()
    except Exception as e:
        pytest.skip(f"Redis not available: {e}")


# ═══════════════════════════════════════════════════════════
# Test FastAPI App
# ═══════════════════════════════════════════════════════════


@pytest.fixture
def test_app(
    auth_context: AuthContext,
    mock_llm_client: MagicMock,
    mock_retriever: MagicMock,
    mock_embedder: MagicMock,
    mock_cache_manager: MagicMock,
    mock_output_validator: MagicMock,
) -> FastAPI:
    """
    Create a test FastAPI app with all dependencies mocked.
    Auth is bypassed — all requests use the provided auth_context.
    """
    from app.main import create_app
    from app.api.middleware.auth import get_auth_context

    app = create_app()

    # Mock all app.state dependencies
    app.state.db_session_factory = MagicMock()
    app.state.db_engine = MagicMock()
    app.state.qdrant_client = MagicMock()
    app.state.vector_store = MagicMock()
    app.state.redis_client = None
    app.state.redis_pool = None
    app.state.embedder = mock_embedder
    app.state.reranker = MagicMock()
    app.state.llm_client = mock_llm_client
    app.state.output_validator = mock_output_validator
    app.state.hybrid_retriever = mock_retriever
    app.state.cache_manager = mock_cache_manager
    app.state.arq_pool = None
    app.state.compiled_graph = None  # Will be set per test if needed

    # Override auth dependency to bypass API key validation
    app.dependency_overrides[get_auth_context] = lambda: auth_context

    return app


@pytest_asyncio.fixture
async def async_client(test_app: FastAPI) -> AsyncGenerator[AsyncClient, None]:
    """Async HTTP client for testing FastAPI endpoints."""
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


# ═══════════════════════════════════════════════════════════
# Data Factories
# ═══════════════════════════════════════════════════════════


class DataFactory:
    """
    Factory for creating test domain objects.
    Use this instead of manually constructing objects in every test.
    """

    @staticmethod
    def chunk(
        text: str = "Default chunk text for testing.",
        org_id: UUID | None = None,
        doc_id: UUID | None = None,
        filename: str = "test.pdf",
        page: int = 1,
        chunk_index: int = 0,
    ) -> Chunk:
        return Chunk(
            id=uuid4(),
            text=text,
            metadata=ChunkMetadata(
                org_id=org_id or uuid4(),
                source_doc_id=doc_id or uuid4(),
                source_filename=filename,
                page_number=page,
                chunk_index=chunk_index,
            ),
        )

    @staticmethod
    def retrieved_chunk(
        text: str = "Default retrieved chunk text.",
        score: float = 0.9,
        method: RetrievalMethod = RetrievalMethod.DENSE,
        org_id: UUID | None = None,
    ) -> RetrievedChunk:
        return RetrievedChunk(
            chunk=DataFactory.chunk(text=text, org_id=org_id),
            score=score,
            retrieval_method=method,
        )

    @staticmethod
    def content_plan(
        title: str = "Test Plan",
        section_count: int = 2,
    ) -> ContentPlan:
        sections = [
            SectionPlan(
                heading=f"Section {i + 1}",
                description=f"Cover topic {i + 1}",
                word_budget=200,
                key_points=[f"point {i + 1}a", f"point {i + 1}b"],
            )
            for i in range(section_count)
        ]
        return ContentPlan(
            title=title,
            sections=sections,
            total_word_budget=200 * section_count,
            target_audience="general",
        )

    @staticmethod
    def section_draft(
        heading: str = "Test Section",
        content: str = "This is the content of the test section with enough words.",
    ) -> SectionDraft:
        return SectionDraft(
            heading=heading,
            content=content,
            sources_used=["test.pdf › p.1"],
        )

    @staticmethod
    def token_usage(
        input_tokens: int = 100,
        output_tokens: int = 50,
    ) -> TokenUsage:
        return TokenUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
            estimated_cost_usd=0.001,
        )


@pytest.fixture
def factory() -> DataFactory:
    """Provide the DataFactory to tests."""
    return DataFactory()
