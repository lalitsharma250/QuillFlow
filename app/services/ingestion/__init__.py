"""
app/services/ingestion — Document ingestion pipeline.

Responsible for:
  1. Parsing raw documents (PDF, HTML, Markdown, plain text)
  2. Splitting text into overlapping chunks with metadata
  3. Coordinating embedding + storage (delegates to retrieval services)

Components:
  - parser.py:   Extract text from various document formats
  - chunker.py:  Split text into chunks with overlap and metadata
  - pipeline.py: Orchestrates the full ingestion flow for a single document
"""

