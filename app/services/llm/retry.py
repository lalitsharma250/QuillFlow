"""
app/services/llm/retry.py

Retry logic with exponential backoff and circuit breaker for LLM API calls.

Why both?
  - Retry: Handles transient failures (network blip, 429 rate limit)
  - Circuit breaker: Prevents hammering a down service
    (if 5 calls fail in a row, stop trying for 60 seconds)

The circuit breaker has three states:
  CLOSED  → Normal operation, calls go through
  OPEN    → Service is down, calls fail immediately (no network call)
  HALF_OPEN → After timeout, allow ONE call through to test recovery
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Coroutine

import structlog

from config.constants import (
    LLM_CIRCUIT_BREAKER_THRESHOLD,
    LLM_CIRCUIT_BREAKER_TIMEOUT,
    LLM_RETRY_BACKOFF_BASE,
    LLM_RETRY_BACKOFF_MAX,
)

logger = structlog.get_logger(__name__)


# ═══════════════════════════════════════════════════════════
# Exceptions
# ═══════════════════════════════════════════════════════════


class LLMError(Exception):
    """Base exception for LLM-related errors."""

    pass


class LLMRateLimitError(LLMError):
    """Raised when the LLM provider returns 429 Too Many Requests."""

    def __init__(self, retry_after: float | None = None):
        self.retry_after = retry_after
        super().__init__(
            f"Rate limited. Retry after {retry_after}s"
            if retry_after
            else "Rate limited"
        )


class LLMTimeoutError(LLMError):
    """Raised when the LLM call exceeds the timeout."""

    pass


class LLMProviderError(LLMError):
    """Raised for 5xx errors from the LLM provider."""

    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        super().__init__(f"Provider error {status_code}: {message}")


class LLMContentFilterError(LLMError):
    """Raised when the LLM refuses to generate due to content policy."""

    pass


class CircuitOpenError(LLMError):
    """Raised when the circuit breaker is open (service assumed down)."""

    def __init__(self, recovery_in: float):
        self.recovery_in = recovery_in
        super().__init__(
            f"Circuit breaker is open. Recovery in {recovery_in:.0f}s"
        )


# ═══════════════════════════════════════════════════════════
# Circuit Breaker
# ═══════════════════════════════════════════════════════════


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreaker:
    """
    Circuit breaker for LLM API calls.

    Usage:
        breaker = CircuitBreaker()

        async with breaker:
            result = await make_llm_call()

    The context manager:
      - Checks if circuit is open (raises CircuitOpenError)
      - On success: resets failure count
      - On failure: increments failure count, opens circuit if threshold hit
    """

    failure_threshold: int = LLM_CIRCUIT_BREAKER_THRESHOLD
    recovery_timeout: float = LLM_CIRCUIT_BREAKER_TIMEOUT

    # Internal state
    _state: CircuitState = field(default=CircuitState.CLOSED)
    _failure_count: int = field(default=0)
    _last_failure_time: float = field(default=0.0)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @property
    def state(self) -> CircuitState:
        """Current circuit state (may transition from OPEN to HALF_OPEN)."""
        if self._state == CircuitState.OPEN:
            elapsed = time.monotonic() - self._last_failure_time
            if elapsed >= self.recovery_timeout:
                return CircuitState.HALF_OPEN
        return self._state

    @property
    def time_until_recovery(self) -> float:
        """Seconds until circuit transitions from OPEN to HALF_OPEN."""
        if self._state != CircuitState.OPEN:
            return 0.0
        elapsed = time.monotonic() - self._last_failure_time
        return max(0.0, self.recovery_timeout - elapsed)

    async def __aenter__(self):
        """Check circuit state before allowing a call through."""
        async with self._lock:
            current_state = self.state

            if current_state == CircuitState.OPEN:
                raise CircuitOpenError(self.time_until_recovery)

            if current_state == CircuitState.HALF_OPEN:
                logger.info("circuit_breaker_half_open_testing")

        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Update circuit state based on call result."""
        async with self._lock:
            if exc_type is None:
                # Success — reset
                self._on_success()
            elif exc_type in (
                LLMRateLimitError,
                LLMTimeoutError,
                LLMProviderError,
            ):
                # Retriable failure — count it
                self._on_failure()
            # Don't suppress the exception
            return False

    def _on_success(self) -> None:
        """Reset circuit on successful call."""
        if self._state != CircuitState.CLOSED:
            logger.info(
                "circuit_breaker_closed",
                previous_state=self._state.value,
                failure_count=self._failure_count,
            )
        self._failure_count = 0
        self._state = CircuitState.CLOSED

    def _on_failure(self) -> None:
        """Record failure and potentially open circuit."""
        self._failure_count += 1
        self._last_failure_time = time.monotonic()

        if self._failure_count >= self.failure_threshold:
            self._state = CircuitState.OPEN
            logger.warning(
                "circuit_breaker_opened",
                failure_count=self._failure_count,
                recovery_timeout=self.recovery_timeout,
            )
        else:
            logger.debug(
                "circuit_breaker_failure_recorded",
                failure_count=self._failure_count,
                threshold=self.failure_threshold,
            )

    def reset(self) -> None:
        """Manually reset the circuit breaker."""
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._last_failure_time = 0.0


# ═══════════════════════════════════════════════════════════
# Retry with Backoff
# ═══════════════════════════════════════════════════════════


async def retry_with_backoff(
    func: Callable[..., Coroutine[Any, Any, Any]],
    *args: Any,
    max_retries: int = 3,
    backoff_base: float = LLM_RETRY_BACKOFF_BASE,
    backoff_max: float = LLM_RETRY_BACKOFF_MAX,
    circuit_breaker: CircuitBreaker | None = None,
    **kwargs: Any,
) -> Any:
    """
    Execute an async function with retry and exponential backoff.

    Retry behavior by exception type:
      - LLMRateLimitError: Retry with provider's retry_after or backoff
      - LLMTimeoutError: Retry with backoff
      - LLMProviderError (5xx): Retry with backoff
      - LLMContentFilterError: Do NOT retry (deterministic failure)
      - CircuitOpenError: Do NOT retry (service is down)
      - Other exceptions: Do NOT retry (unexpected)

    Args:
        func: Async function to call
        *args: Positional arguments for func
        max_retries: Maximum number of retry attempts
        backoff_base: Base for exponential backoff (seconds)
        backoff_max: Maximum backoff duration (seconds)
        circuit_breaker: Optional circuit breaker to use
        **kwargs: Keyword arguments for func

    Returns:
        Result of func

    Raises:
        Last exception if all retries exhausted
    """
    last_exception: Exception | None = None

    for attempt in range(max_retries + 1):
        try:
            if circuit_breaker:
                async with circuit_breaker:
                    return await func(*args, **kwargs)
            else:
                return await func(*args, **kwargs)

        except CircuitOpenError:
            # Don't retry — service is down
            raise

        except LLMContentFilterError:
            # Don't retry — content was rejected
            raise

        except LLMRateLimitError as e:
            last_exception = e
            if attempt == max_retries:
                break

            # Use provider's retry_after if available
            wait_time = e.retry_after or _calculate_backoff(
                attempt, backoff_base, backoff_max
            )

            logger.warning(
                "llm_rate_limited_retrying",
                attempt=attempt + 1,
                max_retries=max_retries,
                wait_seconds=wait_time,
            )
            await asyncio.sleep(wait_time)

        except (LLMTimeoutError, LLMProviderError) as e:
            last_exception = e
            if attempt == max_retries:
                break

            wait_time = _calculate_backoff(attempt, backoff_base, backoff_max)

            logger.warning(
                "llm_call_failed_retrying",
                error_type=type(e).__name__,
                error=str(e),
                attempt=attempt + 1,
                max_retries=max_retries,
                wait_seconds=wait_time,
            )
            await asyncio.sleep(wait_time)

        except Exception:
            # Unexpected error — don't retry
            raise

    # All retries exhausted
    logger.error(
        "llm_call_failed_all_retries_exhausted",
        error=str(last_exception),
        max_retries=max_retries,
    )
    raise last_exception  # type: ignore[misc]


def _calculate_backoff(
    attempt: int,
    base: float,
    maximum: float,
) -> float:
    """
    Calculate exponential backoff with jitter.

    Formula: min(base * 2^attempt + random_jitter, maximum)
    Jitter prevents thundering herd when multiple clients retry simultaneously.
    """
    import random

    exponential = base * (2 ** attempt)
    jitter = random.uniform(0, base)
    return min(exponential + jitter, maximum)
