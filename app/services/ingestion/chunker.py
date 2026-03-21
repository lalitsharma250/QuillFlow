"""
app/services/ingestion/chunker.py

Text chunking with overlap and metadata preservation.

This is one of the MOST CRITICAL components in a RAG system.
Chunk size and overlap directly impact retrieval quality.

Strategy:
  1. Respect document structure (prefer splitting at section/paragraph boundaries)
  2. Use recursive splitting: try paragraph → sentence → word boundaries
  3. Maintain overlap between chunks for context continuity
  4. Attach rich metadata to every chunk for filtering and citations
"""

from __future__ import annotations

import re
from uuid import UUID, uuid4

import structlog

from app.models.domain import Chunk, ChunkMetadata
from config import get_settings

logger = structlog.get_logger(__name__)


# ── Sentence splitting regex ──────────────────────────────
# Splits on sentence-ending punctuation followed by whitespace
SENTENCE_PATTERN = re.compile(r"(?<=[.!?])\s+")

# Paragraph boundary (two or more newlines)
PARAGRAPH_PATTERN = re.compile(r"\n\s*\n")


class TextChunker:
    """
    Splits text into overlapping chunks suitable for embedding.

    The chunker uses a recursive strategy:
      1. Try to split on paragraph boundaries
      2. If chunks are still too large, split on sentences
      3. If still too large, split on words
      4. Apply overlap between consecutive chunks

    Usage:
        chunker = TextChunker(chunk_size=512, chunk_overlap=64)
        chunks = chunker.chunk_document(
            text="...",
            doc_id=uuid,
            org_id=uuid,
            filename="paper.pdf",
        )
    """

    def __init__(
        self,
        chunk_size: int | None = None,
        chunk_overlap: int | None = None,
    ) -> None:
        settings = get_settings()
        self.chunk_size = chunk_size or settings.chunk_size
        self.chunk_overlap = chunk_overlap or settings.chunk_overlap

        if self.chunk_overlap >= self.chunk_size:
            raise ValueError(
                f"chunk_overlap ({self.chunk_overlap}) must be less than "
                f"chunk_size ({self.chunk_size})"
            )

    def chunk_document(
        self,
        text: str,
        doc_id: UUID,
        org_id: UUID,
        filename: str,
        document_version: int = 1,
        section_heading: str | None = None,
        page_number: int | None = None,
    ) -> list[Chunk]:
        """
        Split a document (or section) into chunks with metadata.

        Args:
            text: The text to chunk
            doc_id: Source document UUID
            org_id: Organization UUID (for data isolation)
            filename: Source filename (for citations)
            document_version: Version of the source document
            section_heading: Optional heading (if chunking a specific section)
            page_number: Optional page number (if from PDF)

        Returns:
            List of Chunk objects (without embeddings — those come later)
        """
        if not text or not text.strip():
            return []

        # Split text into raw chunks
        raw_chunks = self._recursive_split(text)

        # Apply overlap
        overlapped_chunks = self._apply_overlap(raw_chunks)

        # Build Chunk objects with metadata
        total_chunks = len(overlapped_chunks)
        chunks: list[Chunk] = []

        for idx, chunk_text in enumerate(overlapped_chunks):
            cleaned = chunk_text.strip()
            if not cleaned:
                continue

            metadata = ChunkMetadata(
                org_id=org_id,
                source_doc_id=doc_id,
                source_filename=filename,
                page_number=page_number,
                section_heading=section_heading,
                chunk_index=idx,
                total_chunks=total_chunks,
                document_version=document_version,
            )

            chunks.append(Chunk(
                id=uuid4(),
                text=cleaned,
                metadata=metadata,
                embedding=None,
            ))

        logger.debug(
            "chunking_complete",
            filename=filename,
            input_length=len(text),
            chunk_count=len(chunks),
            chunk_size=self.chunk_size,
            overlap=self.chunk_overlap,
        )

        return chunks

    def chunk_sections(
        self,
        sections: list[dict],
        doc_id: UUID,
        org_id: UUID,
        filename: str,
        document_version: int = 1,
    ) -> list[Chunk]:
        """
        Chunk multiple sections from a parsed document.
        Preserves section headings and page numbers in metadata.

        Args:
            sections: List of dicts with keys: text, heading (optional), page_number (optional)
            doc_id: Source document UUID
            org_id: Organization UUID
            filename: Source filename
            document_version: Version of the source document

        Returns:
            All chunks from all sections, with correct metadata
        """
        all_chunks: list[Chunk] = []

        for section in sections:
            section_chunks = self.chunk_document(
                text=section["text"],
                doc_id=doc_id,
                org_id=org_id,
                filename=filename,
                document_version=document_version,
                section_heading=section.get("heading"),
                page_number=section.get("page_number"),
            )
            all_chunks.extend(section_chunks)

        # Re-index chunk_index across all sections (global ordering)
        total = len(all_chunks)
        for idx, chunk in enumerate(all_chunks):
            # ChunkMetadata is frozen, so we need to create a new one
            new_metadata = ChunkMetadata(
                org_id=chunk.metadata.org_id,
                source_doc_id=chunk.metadata.source_doc_id,
                source_filename=chunk.metadata.source_filename,
                page_number=chunk.metadata.page_number,
                section_heading=chunk.metadata.section_heading,
                chunk_index=idx,
                total_chunks=total,
                document_version=chunk.metadata.document_version,
            )
            # Pydantic models are immutable by default, so create new Chunk
            all_chunks[idx] = Chunk(
                id=chunk.id,
                text=chunk.text,
                metadata=new_metadata,
                embedding=chunk.embedding,
            )

        logger.info(
            "multi_section_chunking_complete",
            filename=filename,
            section_count=len(sections),
            total_chunks=total,
        )

        return all_chunks

    def _recursive_split(self, text: str) -> list[str]:
        """
        Recursively split text, trying larger boundaries first.

        Priority:
          1. Paragraph boundaries (\\n\\n)
          2. Sentence boundaries (.!? followed by space)
          3. Word boundaries (spaces)
        """
        # If text fits in one chunk, return as-is
        if self._estimate_tokens(text) <= self.chunk_size:
            return [text]

        # Try paragraph split first
        paragraphs = PARAGRAPH_PATTERN.split(text)
        if len(paragraphs) > 1:
            return self._merge_splits(paragraphs, self.chunk_size)

        # Try sentence split
        sentences = SENTENCE_PATTERN.split(text)
        if len(sentences) > 1:
            return self._merge_splits(sentences, self.chunk_size)

        # Last resort: word split
        words = text.split()
        return self._merge_splits(words, self.chunk_size)

    def _merge_splits(self, pieces: list[str], max_tokens: int) -> list[str]:
        """
        Merge small pieces into chunks that don't exceed max_tokens.
        Greedy: keep adding pieces until the next one would overflow.
        """
        chunks: list[str] = []
        current_pieces: list[str] = []
        current_tokens = 0

        for piece in pieces:
            piece_tokens = self._estimate_tokens(piece)

            if current_tokens + piece_tokens > max_tokens and current_pieces:
                # Current chunk is full — save it
                chunks.append(" ".join(current_pieces))
                current_pieces = []
                current_tokens = 0

            # Handle single piece larger than max_tokens
            if piece_tokens > max_tokens:
                # Force-split by words
                words = piece.split()
                sub_chunks = self._merge_splits(words, max_tokens)
                chunks.extend(sub_chunks)
            else:
                current_pieces.append(piece)
                current_tokens += piece_tokens

        # Don't forget the last chunk
        if current_pieces:
            chunks.append(" ".join(current_pieces))

        return chunks

    def _apply_overlap(self, chunks: list[str]) -> list[str]:
        """
        Add overlap between consecutive chunks.
        Takes the last N tokens of chunk[i] and prepends to chunk[i+1].
        """
        if self.chunk_overlap == 0 or len(chunks) <= 1:
            return chunks

        overlapped: list[str] = [chunks[0]]

        for i in range(1, len(chunks)):
            # Get overlap text from end of previous chunk
            prev_words = chunks[i - 1].split()
            overlap_words = prev_words[-self.chunk_overlap:]
            overlap_text = " ".join(overlap_words)

            # Prepend overlap to current chunk
            overlapped.append(f"{overlap_text} {chunks[i]}")

        return overlapped

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        """
        Rough token count estimation.
        Rule of thumb: 1 token ≈ 4 characters for English text.
        Good enough for chunking — exact counts aren't needed here.
        """
        return len(text) // 4
