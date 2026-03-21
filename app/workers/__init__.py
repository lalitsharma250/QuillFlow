"""
app/workers — Background task processing via ARQ (async Redis queue).

Architecture:
  - API enqueues jobs → Redis
  - Worker process picks up jobs → processes → updates DB
  - API polls DB for status

The worker runs as a SEPARATE process (not inside FastAPI).
This means it can scale independently and survives API restarts.
"""