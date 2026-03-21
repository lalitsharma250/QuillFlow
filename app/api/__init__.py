"""
app/api — HTTP API layer for QuillFlow.

Components:
  - router.py:          Top-level router mounting all sub-routers
  - v1/chat.py:         POST /v1/chat — main query endpoint (graph invocation + SSE)
  - v1/ingest.py:       POST /v1/ingest, /v1/ingest/bulk, GET /v1/ingest/jobs/{id}
  - v1/documents.py:    GET /v1/documents, GET /v1/documents/{id}
  - v1/health.py:       GET /v1/health — deep health check
  - middleware/auth.py:  API key authentication
  - middleware/rbac.py:  Role-based access control
  - middleware/rate_limit.py: Per-user rate limiting
"""
