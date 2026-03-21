"""
app/workers/settings.py

ARQ worker configuration.
This file is the entry point for the worker process:
    arq app.workers.settings.WorkerSettings
"""

from arq import cron
from arq.connections import RedisSettings

from app.workers.tasks import (
    process_bulk_ingestion_job,
    process_single_document,
    on_worker_startup,
    on_worker_shutdown,
)

from config import get_settings


settings = get_settings()


class WorkerSettings:
    """
    ARQ worker settings.

    ARQ discovers this class and uses it to configure the worker.
    Docs: https://arq-docs.helpmanual.io/
    """

    # ── Redis connection ───────────────────────────────
    redis_settings = RedisSettings(
        host=settings.worker_redis_settings["host"],
        port=settings.worker_redis_settings["port"],
        database=settings.worker_redis_settings["database"],
    )

    # ── Task functions (imported at worker startup) ────
    # ARQ needs the actual function references here.
    # We use a string import pattern to avoid circular imports.
    functions = [
        process_bulk_ingestion_job,
        process_single_document,
    ]
    # ── Worker behavior ────────────────────────────────
    max_jobs = settings.worker_max_jobs
    job_timeout = settings.worker_job_timeout_seconds
    health_check_interval = settings.worker_health_check_interval

    # ── Optional: scheduled tasks ──────────────────────
    # Example: re-index stale documents every hour
    # cron_jobs = [
    #     cron(reindex_stale_documents, hour=None, minute=0),  # Every hour
    # ]

    # ── Lifecycle hooks ────────────────────────────────
    on_startup = on_worker_startup
    on_shutdown = on_worker_shutdown