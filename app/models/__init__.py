"""
app/models — Pydantic models for QuillFlow.

Three categories:
  - domain.py:    Core business types (used everywhere internally)
  - requests.py:  API input schemas (used only in app/api/)
  - responses.py: API output schemas (used only in app/api/)
"""
from app.models.domain import AuthContext, IngestionJob, JobStatus, RetrievalMethod, SectionDraft
from app.models.requests import BulkIngestRequest, IngestDocumentItem
from app.models.responses import (
    BulkIngestResponse,
    JobStatusResponse,
    JobDocumentStatus,
    DocumentListResponse,
    HealthResponse,
    ComponentHealth,
    TokenUsage,
    EvalScoreSummary,
    SourceReference,
)