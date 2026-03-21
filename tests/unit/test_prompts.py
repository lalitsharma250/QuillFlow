"""
tests/unit/test_prompts.py

Tests for prompt templates.
Validates structure, required content, and edge cases

```python
.
"""

import pytest
from uuid import uuid4

from app.models.domain import (
    Chunk,
    ChunkMetadata,
    ContentPlan,
    RetrievedChunk,
    SectionPlan,
)
from app.services.llm.prompts import (
    _format_context,
    faithfulness_check_prompt,
    planner_prompt,
    reducer_prompt,
    relevancy_check_prompt,
    router_prompt,
    simple_answer_prompt,
    writer_prompt,
)


def _make_chunk(text: str, filename: str = "test.pdf", page: int = 1) -> RetrievedChunk:
    """Helper to create test RetrievedChunk objects."""
    return RetrievedChunk(
        chunk=Chunk(
            text=text,
            metadata=ChunkMetadata(
                org_id=uuid4(),
                source_doc_id=uuid4(),
                source_filename=filename,
                page_number=page,
                chunk_index=0,
            ),
        ),
        score=0.9,
    )


def _make_plan() -> ContentPlan:
    """Helper to create a test ContentPlan."""
    return ContentPlan(
        title="Test Article",
        sections=[
            SectionPlan(
                heading="Introduction",
                description="Cover the basics",
                word_budget=200,
                key_points=["point one", "point two"],
            ),
            SectionPlan(
                heading="Details",
                description="Go deeper",
                word_budget=400,
            ),
        ],
        total_word_budget=600,
        target_audience="technical",
    )


class TestRouterPrompt:
    def test_returns_system_and_user(self):
        system, user = router_prompt("What is RAG?")
        assert isinstance(system, str)
        assert isinstance(user, str)

    def test_system_contains_classification_rules(self):
        system, _ = router_prompt("test")
        assert "SIMPLE" in system
        assert "COMPLEX" in system
        assert "JSON" in system

    def test_user_contains_query(self):
        _, user = router_prompt("Explain transformers in detail")
        assert "Explain transformers in detail" in user

    def test_handles_empty_query(self):
        system, user = router_prompt("")
        assert isinstance(system, str)
        assert isinstance(user, str)


class TestSimpleAnswerPrompt:
    def test_includes_context(self):
        chunks = [_make_chunk("RAG combines retrieval with generation")]
        system, user = simple_answer_prompt("What is RAG?", chunks)
        assert "RAG combines retrieval" in user

    def test_includes_query(self):
        chunks = [_make_chunk("Some context")]
        _, user = simple_answer_prompt("What is attention?", chunks)
        assert "What is attention?" in user

    def test_system_has_citation_rules(self):
        system, _ = simple_answer_prompt("test", [])
        assert "Source" in system
        assert "context" in system.lower()

    def test_empty_context(self):
        system, user = simple_answer_prompt("test", [])
        assert "No context available" in user


class TestPlannerPrompt:
    def test_includes_context_and_query(self):
        chunks = [_make_chunk("Transformers use attention")]
        system, user = planner_prompt("Explain transformers", chunks)
        assert "Transformers use attention" in user
        assert "Explain transformers" in user

    def test_system_has_json_schema(self):
        system, _ = planner_prompt("test", [])
        assert "title" in system
        assert "sections" in system
        assert "word_budget" in system
        assert "key_points" in system

    def test_max_sections_in_system(self):
        system, _ = planner_prompt("test", [], max_sections=3)
        assert "3" in system

    def test_default_max_sections(self):
        system, _ = planner_prompt("test", [])
        assert "5" in system


class TestWriterPrompt:
    def test_includes_section_assignment(self):
        plan = _make_plan()
        section = plan.sections[0]
        chunks = [_make_chunk("Relevant context")]

        system, user = writer_prompt(section, chunks, plan)
        assert "Introduction" in user
        assert "Cover the basics" in user
        assert "200" in user  # word budget

    def test_includes_key_points(self):
        plan = _make_plan()
        section = plan.sections[0]
        chunks = [_make_chunk("Context")]

        _, user = writer_prompt(section, chunks, plan)
        assert "point one" in user
        assert "point two" in user

    def test_includes_plan_overview(self):
        plan = _make_plan()
        section = plan.sections[0]
        chunks = [_make_chunk("Context")]

        _, user = writer_prompt(section, chunks, plan)
        assert "Introduction" in user
        assert "Details" in user

    def test_current_section_marked(self):
        plan = _make_plan()
        section = plan.sections[0]
        chunks = [_make_chunk("Context")]

        _, user = writer_prompt(section, chunks, plan)
        assert "→" in user  # Current section marker

    def test_preceding_sections_included(self):
        plan = _make_plan()
        section = plan.sections[1]
        chunks = [_make_chunk("Context")]

        _, user = writer_prompt(
            section, chunks, plan,
            preceding_sections=["This is the intro content that was written."],
        )
        assert "intro content" in user

    def test_system_has_json_schema(self):
        plan = _make_plan()
        section = plan.sections[0]
        system, _ = writer_prompt(section, [], plan)
        assert "heading" in system
        assert "content" in system
        assert "sources_used" in system

    def test_audience_in_system(self):
        plan = _make_plan()
        section = plan.sections[0]
        system, _ = writer_prompt(section, [], plan)
        assert "technical" in system


class TestReducerPrompt:
    def test_includes_all_drafts(self):
        drafts = [
            {"heading": "Intro", "content": "Introduction content here."},
            {"heading": "Body", "content": "Body content here."},
        ]
        system, user = reducer_prompt("My Article", drafts)
        assert "Introduction content here" in user
        assert "Body content here" in user
        assert "My Article" in user

    def test_system_has_editing_rules(self):
        system, _ = reducer_prompt("Test", [])
        assert "transition" in system.lower()
        assert "redundancy" in system.lower()
        assert "NOT" in system  # Don't add info

    def test_audience_in_system(self):
        system, _ = reducer_prompt("Test", [], target_audience="executive")
        assert "executive" in system


class TestFaithfulnessPrompt:
    def test_includes_all_components(self):
        chunks = [_make_chunk("RAG uses retrieval")]
        system, user = faithfulness_check_prompt(
            query="What is RAG?",
            context_chunks=chunks,
            answer="RAG is a technique that uses retrieval.",
        )
        assert "RAG uses retrieval" in user
        assert "What is RAG?" in user
        assert "RAG is a technique" in user

    def test_system_has_scoring_rubric(self):
        system, _ = faithfulness_check_prompt("q", [], "a")
        assert "1.0" in system
        assert "0.0" in system
        assert "faithfulness_score" in system


class TestRelevancyPrompt:
    def test_includes_query_and_answer(self):
        system, user = relevancy_check_prompt(
            query="What is attention?",
            answer="Attention is a mechanism...",
        )
        assert "What is attention?" in user
        assert "Attention is a mechanism" in user

    def test_system_has_scoring_rubric(self):
        system, _ = relevancy_check_prompt("q", "a")
        assert "relevancy_score" in system


class TestFormatContext:
    def test_formats_chunks(self):
        chunks = [
            _make_chunk("First chunk", "doc1.pdf", page=1),
            _make_chunk("Second chunk", "doc2.pdf", page=5),
        ]
        result = _format_context(chunks)
        assert "Chunk 1" in result
        assert "Chunk 2" in result
        assert "First chunk" in result
        assert "doc1.pdf" in result
        assert "doc2.pdf" in result

    def test_empty_chunks(self):
        result = _format_context([])
        assert "No context available" in result

    def test_max_chunks_respected(self):
        chunks = [_make_chunk(f"Chunk {i}") for i in range(20)]
        result = _format_context(chunks, max_chunks=3)
        assert "Chunk 1" in result
        assert "Chunk 3" in result
        assert "Chunk 4" not in result

    def test_includes_relevance_score(self):
        chunks = [_make_chunk("Text")]
        result = _format_context(chunks)
        assert "0.90" in result  # Score formatted to 2 decimals
