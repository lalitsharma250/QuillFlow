"""
app/services/ingestion/parser.py

Document parsers for extracting text from various formats.

Design:
  - Each format has its own parser function
  - All parsers return a ParseResult with extracted text + structural metadata
  - Parsers are stateless — no side effects, easy to test
  - If a format-specific parser fails, we fall back to plain text
"""

from __future__ import annotations

from dataclasses import dataclass, field
from bs4 import BeautifulSoup

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class ParsedSection:
    """
    A section of extracted text with optional structural info.
    Parsers that understand document structure (HTML headings, PDF pages)
    produce multiple sections. Plain text produces one section.
    """

    text: str
    heading: str | None = None
    page_number: int | None = None


@dataclass
class ParseResult:
    """
    Output of a document parser.

    Attributes:
        sections: Extracted text broken into structural sections
        full_text: All sections concatenated (convenience)
        metadata: Any extra info the parser discovered (title, author, etc.)
    """

    sections: list[ParsedSection] = field(default_factory=list)
    metadata: dict[str, str] = field(default_factory=dict)

    @property
    def full_text(self) -> str:
        """All sections joined with double newlines."""
        return "\n\n".join(s.text for s in self.sections if s.text.strip())

    @property
    def is_empty(self) -> bool:
        return not self.full_text.strip()


class DocumentParser:
    """
    Parses documents of various formats into structured text.

    Usage:
        parser = DocumentParser()
        result = parser.parse(raw_text, content_type="pdf", filename="paper.pdf")
    """

    def parse(
        self,
        raw_text: str,
        content_type: str,
        filename: str = "",
    ) -> ParseResult:
        """
        Parse a document based on its content type.

        Args:
            raw_text: The raw content (already extracted text for PDFs,
                      raw HTML for html, etc.)
            content_type: One of "pdf", "html", "markdown", "text"
            filename: Original filename (for metadata)

        Returns:
            ParseResult with extracted sections and metadata
        """
        parser_map = {
            "pdf": self._parse_pdf,
            "html": self._parse_html,
            "markdown": self._parse_markdown,
            "text": self._parse_text,
        }

        parser_fn = parser_map.get(content_type, self._parse_text)

        try:
            result = parser_fn(raw_text)
        except Exception as e:
            logger.warning(
                "parser_failed_falling_back_to_text",
                content_type=content_type,
                filename=filename,
                error=str(e),
            )
            result = self._parse_text(raw_text)

        # Add filename to metadata
        result.metadata["filename"] = filename
        result.metadata["content_type"] = content_type

        if result.is_empty:
            logger.warning(
                "parser_produced_empty_result",
                filename=filename,
                content_type=content_type,
            )

        return result

    def _parse_text(self, raw_text: str) -> ParseResult:
        """
        Plain text parser. Splits on double newlines to find natural sections.
        """
        # Split on multiple newlines to find paragraph boundaries
        paragraphs = [p.strip() for p in raw_text.split("\n\n") if p.strip()]

        if not paragraphs:
            return ParseResult(sections=[ParsedSection(text=raw_text.strip())])

        # Group small paragraphs together, treat large blocks as sections
        sections = []
        current_text = []

        for para in paragraphs:
            current_text.append(para)
            # If accumulated text is substantial, make it a section
            if len(" ".join(current_text)) > 500:
                sections.append(ParsedSection(text="\n\n".join(current_text)))
                current_text = []

        # Don't forget remaining text
        if current_text:
            sections.append(ParsedSection(text="\n\n".join(current_text)))

        return ParseResult(sections=sections)

    def _parse_html(self, raw_html: str) -> ParseResult:
        """
        HTML parser. Extracts text while preserving heading structure.
        Uses BeautifulSoup for robust HTML handling.
        """

        soup = BeautifulSoup(raw_html, "html.parser")

        # Remove script, style, and nav elements
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()

        metadata: dict[str, str] = {}

        # Extract title if present
        title_tag = soup.find("title")
        if title_tag and title_tag.string:
            metadata["title"] = title_tag.string.strip()

        # Extract meta description
        meta_desc = soup.find("meta", attrs={"name": "description"})
        if meta_desc and meta_desc.get("content"):
            metadata["description"] = str(meta_desc["content"]).strip()

        # Walk through the document, splitting on headings
        sections: list[ParsedSection] = []
        current_heading: str | None = None
        current_text: list[str] = []

        heading_tags = {"h1", "h2", "h3", "h4", "h5", "h6"}

        for element in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "td"]):
            if element.name in heading_tags:
                # Save previous section
                if current_text:
                    sections.append(ParsedSection(
                        text="\n".join(current_text),
                        heading=current_heading,
                    ))
                    current_text = []
                current_heading = element.get_text(strip=True)
            else:
                text = element.get_text(strip=True)
                if text:
                    current_text.append(text)

        # Save last section
        if current_text:
            sections.append(ParsedSection(
                text="\n".join(current_text),
                heading=current_heading,
            ))

        # Fallback: if no sections found, get all text
        if not sections:
            all_text = soup.get_text(separator="\n", strip=True)
            if all_text:
                sections.append(ParsedSection(text=all_text))

        return ParseResult(sections=sections, metadata=metadata)

    def _parse_markdown(self, raw_md: str) -> ParseResult:
        """
        Markdown parser. Splits on headings (# lines).
        Lightweight — no external markdown library needed.
        """
        lines = raw_md.split("\n")
        sections: list[ParsedSection] = []
        current_heading: str | None = None
        current_lines: list[str] = []
        metadata: dict[str, str] = {}

        for line in lines:
            stripped = line.strip()

            # Detect headings (# Heading)
            if stripped.startswith("#"):
                # Save previous section
                if current_lines:
                    text = "\n".join(current_lines).strip()
                    if text:
                        sections.append(ParsedSection(
                            text=text,
                            heading=current_heading,
                        ))
                    current_lines = []

                # Extract heading text (remove # symbols)
                heading_text = stripped.lstrip("#").strip()
                current_heading = heading_text

                # First h1 becomes the title
                if "title" not in metadata and stripped.startswith("# ") and not stripped.startswith("##"):
                    metadata["title"] = heading_text
            else:
                current_lines.append(line)

        # Save last section
        if current_lines:
            text = "\n".join(current_lines).strip()
            if text:
                sections.append(ParsedSection(
                    text=text,
                    heading=current_heading,
                ))

        # Fallback
        if not sections:
            sections.append(ParsedSection(text=raw_md.strip()))

        return ParseResult(sections=sections, metadata=metadata)

    def _parse_pdf(self, raw_text: str) -> ParseResult:
        """
        PDF parser.

        Note: By the time text reaches here, it's already been extracted
        from the PDF (either client-side or via pypdf at upload time).
        This parser handles the extracted text, splitting on page markers
        or structural cues.

        For actual PDF binary parsing, see the upload endpoint which uses
        pypdf to extract text before calling this.
        """
        # Check for page markers (common in extracted PDF text)
        # Pattern: "--- Page N ---" or form feed characters
        import re

        page_pattern = re.compile(r"(?:---\s*Page\s+(\d+)\s*---|\f)")
        parts = page_pattern.split(raw_text)

        sections: list[ParsedSection] = []
        page_num = 1

        for i, part in enumerate(parts):
            text = part.strip()
            if not text:
                continue

            # Check if this part is a page number
            if text.isdigit():
                page_num = int(text)
                continue

            sections.append(ParsedSection(
                text=text,
                page_number=page_num,
            ))
            page_num += 1

        # Fallback: treat as single section
        if not sections:
            sections.append(ParsedSection(text=raw_text.strip(), page_number=1))

        return ParseResult(sections=sections)
