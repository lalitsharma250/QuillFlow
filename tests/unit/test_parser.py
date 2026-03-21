"""
tests/unit/test_parser.py

Tests for document parsers.
"""

import pytest

from app.services.ingestion.parser import DocumentParser


@pytest.fixture
def parser() -> DocumentParser:
    return DocumentParser()


class TestTextParser:
    def test_simple_text(self, parser):
        result = parser.parse("Hello world", content_type="text", filename="test.txt")
        assert not result.is_empty
        assert "Hello world" in result.full_text

    def test_empty_text(self, parser):
        result = parser.parse("", content_type="text", filename="empty.txt")
        assert result.is_empty

    def test_paragraphs_become_sections(self, parser):
        text = "First paragraph.\n\nSecond paragraph.\n\nThird paragraph."
        result = parser.parse(text, content_type="text", filename="paras.txt")
        assert len(result.sections) >= 1
        assert "First paragraph" in result.full_text
        assert "Third paragraph" in result.full_text

    def test_metadata_includes_filename(self, parser):
        result = parser.parse("Content", content_type="text", filename="doc.txt")
        assert result.metadata["filename"] == "doc.txt"
        assert result.metadata["content_type"] == "text"


class TestHtmlParser:
    def test_basic_html(self, parser):
        html = """
        <html>
        <head><title>Test Page</title></head>
        <body>
            <h1>Main Heading</h1>
            <p>First paragraph of content.</p>
            <h2>Sub Heading</h2>
            <p>Second paragraph under sub heading.</p>
        </body>
        </html>
        """
        result = parser.parse(html, content_type="html", filename="page.html")

        assert not result.is_empty
        assert result.metadata.get("title") == "Test Page"
        assert "First paragraph" in result.full_text
        assert "Second paragraph" in result.full_text

    def test_html_strips_scripts(self, parser):
        html = """
        <html><body>
            <p>Visible content.</p>
            <script>alert('hidden');</script>
            <style>.hidden { display: none; }</style>
        </body></html>
        """
        result = parser.parse(html, content_type="html", filename="scripts.html")
        assert "alert" not in result.full_text
        assert "hidden" not in result.full_text
        assert "Visible content" in result.full_text

    def test_html_preserves_headings(self, parser):
        html = """
        <html><body>
            <h1>Title</h1>
            <p>Intro text.</p>
            <h2>Section A</h2>
            <p>Section A content.</p>
        </body></html>
        """
        result = parser.parse(html, content_type="html", filename="headings.html")
        headings = [s.heading for s in result.sections if s.heading]
        assert "Section A" in headings


class TestMarkdownParser:
    def test_basic_markdown(self, parser):
        md = """# Main Title

Introduction paragraph here.

## Section One

Content of section one.

## Section Two

Content of section two.
"""
        result = parser.parse(md, content_type="markdown", filename="doc.md")

        assert not result.is_empty
        assert result.metadata.get("title") == "Main Title"
        assert len(result.sections) >= 2

    def test_markdown_without_headings(self, parser):
        md = "Just plain text without any headings."
        result = parser.parse(md, content_type="markdown", filename="plain.md")
        assert not result.is_empty
        assert "plain text" in result.full_text


class TestPdfParser:
    def test_pdf_with_page_markers(self, parser):
        text = "Page one content.\f Page two content.\f Page three content."
        result = parser.parse(text, content_type="pdf", filename="doc.pdf")

        assert not result.is_empty
        assert len(result.sections) >= 2

    def test_pdf_plain_text_fallback(self, parser):
        text = "Just regular extracted PDF text without page markers."
        result = parser.parse(text, content_type="pdf", filename="simple.pdf")
        assert not result.is_empty


class TestParserFallback:
    def test_unknown_type_falls_back_to_text(self, parser):
        """Unknown content types should fall back to text parser."""
        result = parser.parse(
            "Some content",
            content_type="text",  # Would be caught by validator, but parser handles gracefully
            filename="unknown.xyz",
        )
        assert not result.is_empty