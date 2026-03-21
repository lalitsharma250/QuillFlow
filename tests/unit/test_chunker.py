"""
tests/unit/test_chunker.py

Tests for the text chunking logic.
Chunking quality directly impacts retrieval — these tests are critical.
"""

import pytest
from uuid import uuid4

from app.services.ingestion.chunker import TextChunker


@pytest.fixture
def chunker() -> TextChunker:
    """Standard chunker with small sizes for testing."""
    return TextChunker(chunk_size=100, chunk_overlap=20)


@pytest.fixture
def org_id():
    return uuid4()


@pytest.fixture
def doc_id():
    return uuid4()


class TestTextChunker:
    def test_short_text_single_chunk(self, chunker, doc_id, org_id):
        """Text shorter than chunk_size should produce one chunk."""
        chunks = chunker.chunk_document(
            text="This is a short text.",
            doc_id=doc_id,
            org_id=org_id,
            filename="test.txt",
        )
        assert len(chunks) == 1
        assert chunks[0].text == "This is a short text."
        assert chunks[0].metadata.source_filename == "test.txt"
        assert chunks[0].metadata.org_id == org_id

    def test_empty_text_no_chunks(self, chunker, doc_id, org_id):
        """Empty text should produce no chunks."""
        chunks = chunker.chunk_document(
            text="",
            doc_id=doc_id,
            org_id=org_id,
            filename="empty.txt",
        )
        assert len(chunks) == 0

    def test_whitespace_only_no_chunks(self, chunker, doc_id, org_id):
        """Whitespace-only text should produce no chunks."""
        chunks = chunker.chunk_document(
            text="   \n\n\t  ",
            doc_id=doc_id,
            org_id=org_id,
            filename="blank.txt",
        )
        assert len(chunks) == 0

    def test_long_text_multiple_chunks(self, chunker, doc_id, org_id):
        """Long text should be split into multiple chunks."""
        # Generate text longer than chunk_size (100 tokens ≈ 400 chars)
        long_text = "This is a sentence with several words. " * 50
        chunks = chunker.chunk_document(
            text=long_text,
            doc_id=doc_id,
            org_id=org_id,
            filename="long.txt",
        )
        assert len(chunks) > 1

    def test_chunks_have_correct_metadata(self, chunker, doc_id, org_id):
        """Every chunk should have correct metadata."""
        long_text = "Word " * 500
        chunks = chunker.chunk_document(
            text=long_text,
            doc_id=doc_id,
            org_id=org_id,
            filename="meta_test.txt",
            document_version=3,
            section_heading="Introduction",
            page_number=5,
        )

        for i, chunk in enumerate(chunks):
            assert chunk.metadata.source_doc_id == doc_id
            assert chunk.metadata.org_id == org_id
            assert chunk.metadata.source_filename == "meta_test.txt"
            assert chunk.metadata.document_version == 3
            assert chunk.metadata.section_heading == "Introduction"
            assert chunk.metadata.page_number == 5
            assert chunk.metadata.chunk_index == i
            assert chunk.metadata.total_chunks == len(chunks)

    def test_chunks_have_unique_ids(self, chunker, doc_id, org_id):
        """Every chunk should have a unique UUID."""
        long_text = "Word " * 500
        chunks = chunker.chunk_document(
            text=long_text,
            doc_id=doc_id,
            org_id=org_id,
            filename="ids.txt",
        )
        ids = [chunk.id for chunk in chunks]
        assert len(ids) == len(set(ids))

    def test_overlap_present(self, doc_id, org_id):
        """Consecutive chunks should share overlapping text."""
        chunker = TextChunker(chunk_size=50, chunk_overlap=10)
        long_text = "Word " * 200

        chunks = chunker.chunk_document(
            text=long_text,
            doc_id=doc_id,
            org_id=org_id,
            filename="overlap.txt",
        )

        if len(chunks) >= 2:
            # Last words of chunk 0 should appear at start of chunk 1
            first_chunk_words = chunks[0].text.split()
            second_chunk_words = chunks[1].text.split()
            overlap_words = first_chunk_words[-10:]

            # At least some overlap words should be in the second chunk's start
            second_start = " ".join(second_chunk_words[:15])
            overlap_text = " ".join(overlap_words)
            assert any(
                word in second_start for word in overlap_words
            ), f"Expected overlap between chunks. Overlap: '{overlap_text}', Start: '{second_start}'"

    def test_no_embedding_on_chunks(self, chunker, doc_id, org_id):
        """Chunker should NOT set embeddings — that's the embedder's job."""
        chunks = chunker.chunk_document(
            text="Some text here.",
            doc_id=doc_id,
            org_id=org_id,
            filename="no_embed.txt",
        )
        for chunk in chunks:
            assert chunk.embedding is None
            assert chunk.has_embedding is False

    def test_chunk_sections(self, chunker, doc_id, org_id):
        """Chunking multiple sections should produce correctly indexed chunks."""
        sections = [
            {"text": "First section content. " * 20, "heading": "Intro", "page_number": 1},
            {"text": "Second section content. " * 20, "heading": "Methods", "page_number": 3},
        ]

        chunks = chunker.chunk_sections(
            sections=sections,
            doc_id=doc_id,
            org_id=org_id,
            filename="sections.txt",
        )

        assert len(chunks) > 0

        # Check global indexing
        for i, chunk in enumerate(chunks):
            assert chunk.metadata.chunk_index == i
            assert chunk.metadata.total_chunks == len(chunks)

        # Check section headings are preserved
        headings = {c.metadata.section_heading for c in chunks}
        assert "Intro" in headings
        assert "Methods" in headings

    def test_overlap_cannot_exceed_chunk_size(self):
        """Overlap >= chunk_size should raise ValueError."""
        with pytest.raises(ValueError, match="must be less than"):
            TextChunker(chunk_size=100, chunk_overlap=100)

        with pytest.raises(ValueError, match="must be less than"):
            TextChunker(chunk_size=100, chunk_overlap=150)


class TestParagraphSplitting:
    """Test that the chunker prefers paragraph boundaries."""

    def test_splits_on_paragraphs(self, doc_id, org_id):
        chunker = TextChunker(chunk_size=50, chunk_overlap=0)

        text = (
            "First paragraph with enough words to be meaningful.\n\n"
            "Second paragraph that is also fairly long and detailed.\n\n"
            "Third paragraph concluding the document nicely."
        )

        chunks = chunker.chunk_document(
            text=text,
            doc_id=doc_id,
            org_id=org_id,
            filename="paragraphs.txt",
        )

        # Should have split on paragraph boundaries
        assert len(chunks) >= 2
