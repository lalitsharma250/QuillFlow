# ══════════════════════════════════════════════════════════
# QuillFlow API — Multi-stage Docker Build
# ══════════════════════════════════════════════════════════
#
# Multi-stage means we use multiple FROM statements.
# Each stage is a separate build environment.
# Only the final stage becomes the actual image.
#
# Why multi-stage?
#   Stage 1 (builder): Install ALL dependencies (including build tools)
#   Stage 2 (runtime): Copy only what's needed to RUN the app
#   Result: Smaller image (no compilers, no build artifacts)
#
# Typical sizes:
#   Single stage: ~2.5 GB
#   Multi-stage:  ~1.2 GB (50% smaller)
# ══════════════════════════════════════════════════════════


# ── Stage 1: Builder ──────────────────────────────────────
# This stage installs dependencies and builds wheels.
# It's thrown away after — only the installed packages are copied.

# ── Stage 1: Builder ──────────────────────────────────
# ── Stage 1: Builder ──────────────────────────────────
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY pyproject.toml ./
COPY README.md ./
COPY app/__init__.py ./app/
COPY config/__init__.py ./config/

RUN pip install .

# ── Stage 2: Runtime ──────────────────────────────────
FROM python:3.12-slim AS runtime

LABEL maintainer="QuillFlow Team" \
      description="QuillFlow API Server" \
      version="0.1.0"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && rm -rf /tmp/*

COPY --from=builder /opt/venv /opt/venv

RUN groupadd --gid 1000 quillflow \
    && useradd --uid 1000 --gid quillflow --shell /bin/bash --create-home quillflow

WORKDIR /app

# Copy application code AND alembic
COPY --chown=quillflow:quillflow . .

USER quillflow

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["python", "/app/docker/entrypoint.py"]