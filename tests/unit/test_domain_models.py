"""
tests/unit/test_domain_models.py

Tests for core domain models.
Validates that Pydantic constraints catch bad data early.
"""

import pytest
from uuid import uuid4

from app.models.domain import (
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


# ═══════════════════════════════════════════════════════════
# ChunkMetadata
# ═══════════════════════════════════════════════════════════


class TestChunkMetadata:
    def test_valid_metadata(self):
        meta = ChunkMetadata(
            source_doc_id=uuid4(),
            source_filename="test.pdf",
            page_number=5,
            section_heading="Introduction",
            chunk_index=0,
            total_chunks=10,
        )
        assert meta.chunk_index == 0
        assert meta.document_version == 1  # default

    def test_metadata_is_frozen(self):
        meta = ChunkMetadata(
            source_doc_id=uuid4(),
            source_filename="test.pdf",
            chunk_index=0,
        )
        with pytest.raises(Exception):  # ValidationError for frozen model
            meta.chunk_index = 5

    def test_negative_chunk_index_rejected(self):
        with pytest.raises(ValueError):
            ChunkMetadata(
                source_doc_id=uuid4(),
                source_filename="test.pdf",
                chunk_index=-1,
            )


# ═══════════════════════════════════════════════════════════
# Chunk
# ═══════════════════════════════════════════════════════════


class TestChunk:
    def _make_metadata(self) -> ChunkMetadata:
        return ChunkMetadata(
            source_doc_id=uuid4(),
            source_filename="test.pdf",
            chunk_index=0,
        )

    def test_valid_chunk(self):
        chunk = Chunk(text="Hello world", metadata=self._make_metadata())
        assert chunk.has_embedding is False
        assert chunk.id is not None

    def test_chunk_with_embedding(self):
        chunk = Chunk(
            text="Hello world",
            metadata=self._make_metadata(),
            embedding=[0.1, 0.2, 0.3],
        )
        assert chunk.has_embedding is True

    def test_empty_text_rejected(self):
        with pytest.raises(ValueError):
            Chunk(text="", metadata=self._make_metadata())


# ═══════════════════════════════════════════════════════════
# Document
# ═══════════════════════════════════════════════════════════


class TestDocument:
    def test_valid_document(self):
        doc = Document(
            filename="paper.pdf",
            content_type="pdf",
            raw_text="Some content here",
        )
        assert doc.status == DocumentStatus.PENDING
        assert doc.version == 1

    def test_invalid_content_type_rejected(self):
        with pytest.raises(ValueError, match="content_type must be one of"):
            Document(
                filename="file.xyz",
                content_type="docx",
                raw_text="content",
            )

    def test_content_type_normalized_to_lowercase(self):
        doc = Document(
            filename="file.pdf",
            content_type="PDF",
            raw_text="content",
        )
        assert doc.content_type == "pdf"


# ═══════════════════════════════════════════════════════════
# ContentPlan
# ═══════════════════════════════════════════════════════════


class TestContentPlan:
    def _make_section(self, heading: str = "Intro", budget: int = 200) -> SectionPlan:
        return SectionPlan(
            heading=heading,
            description="Cover the basics",
            word_budget=budget,
        )

    def test_valid_plan(self):
        plan = ContentPlan(
            title="Test Article",
            sections=[self._make_section()],
            total_word_budget=500,
        )
        assert len(plan.sections) == 1

    def test_too_many_sections_rejected(self):
        sections = [self._make_section(f"Section {i}") for i in range(10)]
        with pytest.raises(ValueError):
            ContentPlan(
                title="Test",
                sections=sections,
                total_word_budget=5000,
            )

    def test_empty_sections_rejected(self):
        with pytest.raises(ValueError):
            ContentPlan(
                title="Test",
                sections=[],
                total_word_budget=500,
            )

    def test_excessive_combined_budget_rejected(self):
        # 8 sections × 2000 words = 16000 > 12000 limit
        sections = [self._make_section(f"S{i}", budget=2000) for i in range(8)]
        with pytest.raises(ValueError, match="exceed maximum"):
            ContentPlan(
                title="Test",
                sections=sections,
                total_word_budget=10000,
            )


# ═══════════════════════════════════════════════════════════
# RetrievedChunk
# ═══════════════════════════════════════════════════════════


class TestRetrievedChunk:
    def test_source_formatting(self):
        meta = ChunkMetadata(
            source_doc_id=uuid4(),
            source_filename="paper.pdf",
            page_number=3,
            section_heading="Methods",
            chunk_index=0,
        )
        chunk = Chunk(text="Some text", metadata=meta)
        retrieved = RetrievedChunk(chunk=chunk, score=0.85)

        assert retrieved.text == "Some text"
        assert retrieved.source == "paper.pdf › p.3 › Methods"

    def test_source_without_optional_fields(self):
        meta = ChunkMetadata(
            source_doc_id=uuid4(),
            source_filename="notes.txt",
            chunk_index=0,
        )
        chunk = Chunk(text="Some text", metadata=meta)
        retrieved = RetrievedChunk(chunk=chunk, score=0.9)

        assert retrieved.source == "notes.txt"

    def test_score_out_of_range_rejected(self):
        meta = ChunkMetadata(
            source_doc_id=uuid4(),
            source_filename="test.pdf",
            chunk_index=0,
        )
        chunk = Chunk(text="text", metadata=meta)
        with pytest.raises(ValueError):
            RetrievedChunk(chunk=chunk, score=1.5)


# ═══════════════════════════════════════════════════════════
# EvalScores
# ═══════════════════════════════════════════════════════════


class TestEvalScores:
    def test_all_none_is_acceptable(self):
        scores = EvalScores()
        assert scores.is_acceptable is True

    def test_good_scores_acceptable(self):
        scores = EvalScores(
            faithfulness=0.9,
            answer_relevancy=0.85,
            context_precision=0.8,
        )
        assert scores.is_acceptable is True

    def test_low_faithfulness_not_acceptable(self):
        scores = EvalScores(faithfulness=0.3)
        assert scores.is_acceptable is False

    def test_score_out_of_range_rejected(self):
        with pytest.raises(ValueError):
            EvalScores(faithfulness=1.5)
