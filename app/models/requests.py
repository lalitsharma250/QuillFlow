"""
app/models/requests.py

Pydantic models for API request bodies.
These are used ONLY in the API layer (app/api/).
They validate and sanitize user input before it reaches services.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from config import get_settings

class ChatMessage(BaseModel):
    """A single message in conversation history."""
    role: str = Field(description="'user' or 'assistant'")
    content: str = Field(max_length=50000)

class ChatRequest(BaseModel):
    """
    Request body for POST /v1/chat

    Example:
    {
        "query": "Explain the impact of transformer architecture on NLP",
        "conversation_id": "abc-123",
        "stream": true,
        "model_preference": "auto"
    }
    """

    query: str = Field(
        min_length=1,
        max_length=10_000,
        description="The user's question or content request",
        examples=["Explain the impact of transformer architecture on NLP"],
    )
    conversation_id: str | None = Field(
        default=None,
        max_length=100,
        description="Optional conversation ID for multi-turn context",
    )
    stream: bool = Field(
        default=True,
        description="Whether to stream the response via SSE",
    )
    model_preference: str = Field(
        default="auto",
        description=(
            "LLM selection strategy: "
            "'auto' (router decides), 'fast' (Sonnet), 'strong' (Opus)"
        ),
    )
    include_sources: bool = Field(
        default=True,
        description="Whether to include source citations in the response",
    )
    max_sections: int | None = Field(
        default=None,
        ge=1,
        le=8,
        description="Override max sections for complex queries (default: from config)",
    )
    history: list[ChatMessage] = Field(
        default_factory=list,
        max_length=20,
        description="Previous messages for multi-turn context (max 20)",
    )

    @field_validator("query")
    @classmethod
    def strip_and_validate_query(cls, v: str) -> str:
        """Clean up whitespace, reject empty-after-strip queries."""
        v = v.strip()
        if not v:
            raise ValueError("Query cannot be empty or whitespace-only")
        return v

    @field_validator("model_preference")
    @classmethod
    def validate_model_preference(cls, v: str) -> str:
        allowed = {"auto", "fast", "strong"}
        v_lower = v.lower().strip()
        if v_lower not in allowed:
            raise ValueError(f"model_preference must be one of {allowed}")
        return v_lower


class IngestRequest(BaseModel):
    """
    Request body for POST /v1/ingest

    Supports two modes:
      1. Direct text upload (provide content + filename)
      2. File upload (handled separately via UploadFile, not this model)

    Example:
    {
        "content": "The transformer architecture was introduced in...",
        "filename": "transformers_overview.txt",
        "content_type": "text",
        "metadata": {"author": "Research Team", "category": "ML"}
    }
    """

    content: str = Field(
        min_length=1,
        max_length=15_000_000,  # ~500K chars ≈ ~125K tokens ≈ ~200 pages
        description="Raw text content of the document",
    )
    filename: str = Field(
        min_length=1,
        max_length=500,
        description="Original filename (used for metadata and citations)",
        examples=["research_paper.pdf", "api_docs.html"],
    )
    content_type: str = Field(
        default="text",
        description="Document type: 'pdf', 'html', 'text', 'markdown'",
    )
    metadata: dict[str, str] = Field(
        default_factory=dict,
        description="Optional key-value metadata to attach to all chunks from this document",
    )

    @field_validator("content_type")
    @classmethod
    def validate_content_type(cls, v: str) -> str:
        allowed = {"pdf", "html", "text", "markdown"}
        v_lower = v.lower().strip()
        if v_lower not in allowed:
            raise ValueError(f"content_type must be one of {allowed}")
        return v_lower

    @field_validator("metadata")
    @classmethod
    def validate_metadata_size(cls, v: dict[str, str]) -> dict[str, str]:
        """Prevent abuse via massive metadata payloads."""
        if len(v) > 20:
            raise ValueError("metadata cannot have more than 20 keys")
        for key, value in v.items():
            if len(key) > 100 or len(value) > 1000:
                raise ValueError(
                    f"metadata key max 100 chars, value max 1000 chars. "
                    f"Got key='{key[:50]}...' ({len(key)} chars)"
                )
        return v



class IngestDocumentItem(BaseModel):
    """
    A single document within a bulk ingest request.
    Same fields as IngestRequest but without being a top-level request.
    """

    content: str = Field(min_length=1, max_length=15_000_000)  # Match IngestRequest
    filename: str = Field(min_length=1, max_length=500)
    content_type: str = Field(default="text")
    metadata: dict[str, str] = Field(default_factory=dict)

    @field_validator("content_type")
    @classmethod
    def validate_content_type(cls, v: str) -> str:
        allowed = {"pdf", "html", "text", "markdown"}
        v_lower = v.lower().strip()
        if v_lower not in allowed:
            raise ValueError(f"content_type must be one of {allowed}")
        return v_lower
    
class BulkIngestRequest(BaseModel):
    """
    Request body for POST /v1/ingest/bulk

    Example:
    {
        "documents": [
            {"content": "...", "filename": "doc1.txt", "content_type": "text"},
            {"content": "...", "filename": "doc2.md", "content_type": "markdown"}
        ]
    }
    """

    documents: list[IngestDocumentItem] = Field(
        min_length=1,
        max_length=500,
        description="List of documents to ingest (max 500 per batch)",
    )

    @field_validator("documents")
    @classmethod
    def validate_total_size(cls, docs: list[IngestDocumentItem]) -> list[IngestDocumentItem]:
        """Prevent abuse: cap total content size across all docs."""
        total_chars = sum(len(d.content) for d in docs)
        max_total = 15_000_000  # ~15MB of text
        if total_chars > max_total:
            raise ValueError(
                f"Total content size ({total_chars:,} chars) exceeds "
                f"maximum ({max_total:,} chars)"
            )
        return docs

