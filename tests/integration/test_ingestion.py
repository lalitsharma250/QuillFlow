"""
tests/integration/test_ingestion.py

Integration tests for the document ingestion pipeline.
Tests parse → chunk → embed flow (without Qdrant storage).
"""

import pytest
from uuid import uuid4

from app.services.ingestion.parser import DocumentParser
from app.services.ingestion.chunker import TextChunker
from app.services.ingestion.pipeline import IngestionPipeline


class TestParserChunkerIntegration:
    """Test parser + chunker working together."""

    def test_text_document_end_to_end(self):
        parser = DocumentParser()
        chunker = TextChunker(chunk_size=100, chunk_overlap=20)
        org_id = uuid4()
        doc_id = uuid4()

        raw_text = (
            "Introduction to Machine Learning\n\n"
            "Machine learning is a subset of artificial intelligence that "
            "enables systems to learn and improve from experience without "
            "being explicitly programmed. It focuses on developing computer "
            "programs that can access data and use it to learn for themselves.\n\n"
            "Types of Machine Learning\n\n"
            "There are three main types of machine learning: supervised learning, "
            "unsupervised learning, and reinforcement learning. Each type has "
            "different use cases and approaches to learning from data."
        )

        # Parse
        parse_result = parser.parse(raw_text, content_type="text", filename="ml_intro.txt")
        assert not parse_result.is_empty
        assert len(parse_result.sections) >= 1

        # Chunk
        sections_data = [
            {"text": s.text, "heading": s.heading, "page_number": s.page_number}
            for s in parse_result.sections
        ]
        chunks = chunker.chunk_sections(
            sections=sections_data,
            doc_id=doc_id,
            org_id=org_id,
            filename="ml_intro.txt",
        )

        assert len(chunks) >= 1
        assert all(c.metadata.org_id == org_id for c in chunks)
        assert all(c.metadata.source_doc_id == doc_id for c in chunks)
        assert all(c.embedding is None for c in chunks)

    def test_html_document_end_to_end(self):
        parser = DocumentParser()
        chunker = TextChunker(chunk_size=100, chunk_overlap=10)
        org_id = uuid4()
        doc_id = uuid4()

        html = """
        <html>
        <head><title>API Documentation</title></head>
        <body>
            <h1>REST API Guide</h1>
            <p>This guide covers the basics of building REST APIs.</p>
            <h2>Authentication</h2>
            <p>All API requests require authentication via API keys.
            Include your key in the Authorization header.</p>
            <h2>Endpoints</h2>
            <p>The API provides several endpoints for managing resources.
            Each endpoint supports standard HTTP methods.</p>
        </body>
        </html>
        """

        parse_result = parser.parse(html, content_type="html", filename="api_docs.html")
        assert not parse_result.is_empty
        assert parse_result.metadata.get("title") == "API Documentation"

        sections_data = [
            {"text": s.text, "heading": s.heading, "page_number": s.page_number}
            for s in parse_result.sections
        ]
        chunks = chunker.chunk_sections(
            sections=sections_data,
            doc_id=doc_id,
            org_id=org_id,
            filename="api_docs.html",
        )

        assert len(chunks) >= 1
        # Check that headings are preserved
        headings = {c.metadata.section_heading for c in chunks if c.metadata.section_heading}
        assert len(headings) >= 1

    def test_markdown_document_end_to_end(self):
        parser = DocumentParser()
        chunker = TextChunker(chunk_size=80, chunk_overlap=10)
        org_id = uuid4()
        doc_id = uuid4()

        markdown = """# Project QuillFlow

## Overview

QuillFlow is a production-grade agentic RAG application that orchestrates
LLM calls via a stateful DAG.

## Architecture

The system uses LangGraph for DAG orchestration, Qdrant for

vector storage, and FastAPI for the API layer.

## Getting Started

Install dependencies and run the development server using the provided Makefile.
"""

        parse_result = parser.parse(markdown, content_type="markdown", filename="readme.md")
        assert not parse_result.is_empty
        assert parse_result.metadata.get("title") == "Project QuillFlow"

        sections_data = [
            {"text": s.text, "heading": s.heading, "page_number": s.page_number}
            for s in parse_result.sections
        ]
        chunks = chunker.chunk_sections(
            sections=sections_data,
            doc_id=doc_id,
            org_id=org_id,
            filename="readme.md",
        )

        assert len(chunks) >= 1
        headings = {c.metadata.section_heading for c in chunks if c.metadata.section_heading}
        assert "Overview" in headings or "Architecture" in headings


class TestIngestionPipelineWithMocks:
    """Test the pipeline orchestration with mocked embedder and vector store."""

    @pytest.fixture
    def mock_embedder(self):
        from unittest.mock import AsyncMock, MagicMock

        embedder = MagicMock()
        embedder.batch_size = 64

        async def _embed_batch(texts):
            return [[0.1] * 1024 for _ in texts]

        embedder.embed_batch = _embed_batch
        return embedder

    @pytest.fixture
    def mock_vector_store(self):
        from unittest.mock import AsyncMock, MagicMock

        store = MagicMock()
        store.upsert_chunks = AsyncMock(return_value=0)
        store.delete_by_document_id = AsyncMock(return_value=0)
        return store

    async def test_process_text_document(self, mock_embedder, mock_vector_store):
        pipeline = IngestionPipeline(
            embedder=mock_embedder,
            vector_store=mock_vector_store,
        )

        result = await pipeline.process_document(
            document_id=uuid4(),
            org_id=uuid4(),
            raw_text="This is a test document with enough content to be chunked properly. " * 10,
            filename="test.txt",
            content_type="text",
        )

        assert result.chunk_count > 0
        assert len(result.chunks) == result.chunk_count
        assert all(c.has_embedding for c in result.chunks)
        mock_vector_store.upsert_chunks.assert_called()

    async def test_process_empty_document(self, mock_embedder, mock_vector_store):
        pipeline = IngestionPipeline(
            embedder=mock_embedder,
            vector_store=mock_vector_store,
        )

        result = await pipeline.process_document(
            document_id=uuid4(),
            org_id=uuid4(),
            raw_text="",
            filename="empty.txt",
            content_type="text",
        )

        assert result.chunk_count == 0
        assert result.chunks == []
        mock_vector_store.upsert_chunks.assert_not_called()

    async def test_delete_before_reingestion(self, mock_embedder, mock_vector_store):
        pipeline = IngestionPipeline(
            embedder=mock_embedder,
            vector_store=mock_vector_store,
        )

        doc_id = uuid4()
        org_id = uuid4()

        deleted = await pipeline.delete_document_chunks(doc_id, org_id)
        mock_vector_store.delete_by_document_id.assert_called_once_with(
            document_id=doc_id,
            org_id=org_id,
        )
