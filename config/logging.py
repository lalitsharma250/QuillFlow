"""
config/logging.py

Structured logging configuration using structlog.
Outputs JSON in production, pretty-printed in development.
"""

import logging
import sys

import structlog

from config.settings import get_settings


def setup_logging() -> None:
    """
    Configure structlog + stdlib logging.
    Call this once at app startup (before any log statements).
    """
    settings = get_settings()
    is_debug = settings.debug

    # ── Shared processors (run on every log event) ─────
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]

    if is_debug:
        # Pretty console output for local dev
        renderer = structlog.dev.ConsoleRenderer(colors=True)
    else:
        # JSON output for production (parseable by Grafana/Loki/etc.)
        renderer = structlog.processors.JSONRenderer()

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # ── Configure stdlib logging to use structlog formatting ──
    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(settings.log_level.upper())

    # Quiet noisy third-party loggers
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)