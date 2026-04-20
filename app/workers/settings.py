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
_worker_redis = settings.worker_redis_settings


class WorkerSettings:
    """
    ARQ worker settings.

    ARQ discovers this class and uses it to configure the worker.
    Docs: https://arq-docs.helpmanual.io/
    """

    # ── Redis connection (supports TLS for Upstash/cloud) ──
    redis_settings = RedisSettings(
        host=_worker_redis["host"],
        port=_worker_redis["port"],
        database=_worker_redis["database"],
        password=_worker_redis.get("password"),
        username=_worker_redis.get("username"),
        ssl=_worker_redis.get("ssl", False),
        ssl_cert_reqs=_worker_redis.get("ssl_cert_reqs"),
    )

    # ── Task functions ─────────────────────────────────
    functions = [
        process_bulk_ingestion_job,
        process_single_document,
    ]

    # ── Worker behavior ────────────────────────────────
    max_jobs = settings.worker_max_jobs
    job_timeout = settings.worker_job_timeout_seconds
    health_check_interval = settings.worker_health_check_interval

    # ── Lifecycle hooks ────────────────────────────────
    on_startup = on_worker_startup
    on_shutdown = on_worker_shutdown