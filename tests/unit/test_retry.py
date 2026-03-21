"""
tests/unit/test_retry.py

Tests for retry logic and circuit breaker.
"""

import pytest
import asyncio

from app.services.llm.retry import (
    CircuitBreaker,
    CircuitOpenError,
    CircuitState,
    LLMContentFilterError,
    LLMProviderError,
    LLMRateLimitError,
    LLMTimeoutError,
    retry_with_backoff,
    _calculate_backoff,
)


# ═══════════════════════════════════════════════════════════
# Circuit Breaker Tests
# ═══════════════════════════════════════════════════════════


class TestCircuitBreaker:
    @pytest.fixture
    def breaker(self):
        return CircuitBreaker(failure_threshold=3, recovery_timeout=0.5)

    async def test_starts_closed(self, breaker):
        assert breaker.state == CircuitState.CLOSED

    async def test_stays_closed_on_success(self, breaker):
        async with breaker:
            pass  # Success
        assert breaker.state == CircuitState.CLOSED

    async def test_opens_after_threshold_failures(self, breaker):
        for _ in range(3):
            try:
                async with breaker:
                    raise LLMProviderError(500, "Server error")
            except LLMProviderError:
                pass

        assert breaker.state == CircuitState.OPEN

    async def test_open_circuit_raises_immediately(self, breaker):
        # Force open
        for _ in range(3):
            try:
                async with breaker:
                    raise LLMProviderError(500, "error")
            except LLMProviderError:
                pass

        with pytest.raises(CircuitOpenError):
            async with breaker:
                pass  # Should never reach here

    async def test_half_open_after_timeout(self, breaker):
        # Force open
        for _ in range(3):
            try:
                async with breaker:
                    raise LLMProviderError(500, "error")
            except LLMProviderError:
                pass

        assert breaker.state == CircuitState.OPEN

        # Wait for recovery timeout
        await asyncio.sleep(0.6)

        assert breaker.state == CircuitState.HALF_OPEN

    async def test_closes_on_success_after_half_open(self, breaker):
        # Force open
        for _ in range(3):
            try:
                async with breaker:
                    raise LLMProviderError(500, "error")
            except LLMProviderError:
                pass

        # Wait for half-open
        await asyncio.sleep(0.6)

        # Successful call should close circuit
        async with breaker:
            pass

        assert breaker.state == CircuitState.CLOSED

    async def test_reset(self, breaker):
        # Force open
        for _ in range(3):
            try:
                async with breaker:
                    raise LLMProviderError(500, "error")
            except LLMProviderError:
                pass

        assert breaker.state == CircuitState.OPEN

        breaker.reset()
        assert breaker.state == CircuitState.CLOSED

    async def test_content_filter_doesnt_count(self, breaker):
        """Content filter errors should NOT trip the circuit breaker."""
        for _ in range(5):
            try:
                async with breaker:
                    raise LLMContentFilterError("blocked")
            except LLMContentFilterError:
                pass

        # Circuit should still be closed — content filter isn't a provider issue
        assert breaker.state == CircuitState.CLOSED


# ═══════════════════════════════════════════════════════════
# Retry Tests
# ═══════════════════════════════════════════════════════════


class TestRetryWithBackoff:
    async def test_succeeds_first_try(self):
        call_count = 0

        async def succeed():
            nonlocal call_count
            call_count += 1
            return "ok"

        result = await retry_with_backoff(succeed, max_retries=3)
        assert result == "ok"
        assert call_count == 1

    async def test_retries_on_rate_limit(self):
        call_count = 0

        async def fail_then_succeed():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise LLMRateLimitError(retry_after=0.01)
            return "ok"

        result = await retry_with_backoff(
            fail_then_succeed,
            max_retries=3,
            backoff_base=0.01,
        )
        assert result == "ok"
        assert call_count == 3

    async def test_retries_on_timeout(self):
        call_count = 0

        async def fail_then_succeed():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise LLMTimeoutError("timeout")
            return "ok"

        result = await retry_with_backoff(
            fail_then_succeed,
            max_retries=3,
            backoff_base=0.01,
        )
        assert result == "ok"
        assert call_count == 2

    async def test_retries_on_provider_error(self):
        call_count = 0

        async def fail_then_succeed():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise LLMProviderError(503, "service unavailable")
            return "ok"

        result = await retry_with_backoff(
            fail_then_succeed,
            max_retries=3,
            backoff_base=0.01,
        )
        assert result == "ok"

    async def test_does_not_retry_content_filter(self):
        call_count = 0

        async def always_filtered():
            nonlocal call_count
            call_count += 1
            raise LLMContentFilterError("blocked")

        with pytest.raises(LLMContentFilterError):
            await retry_with_backoff(
                always_filtered,
                max_retries=3,
                backoff_base=0.01,
            )

        assert call_count == 1  # No retries

    async def test_does_not_retry_unexpected_errors(self):
        call_count = 0

        async def unexpected():
            nonlocal call_count
            call_count += 1
            raise ValueError("unexpected")

        with pytest.raises(ValueError):
            await retry_with_backoff(
                unexpected,
                max_retries=3,
                backoff_base=0.01,
            )

        assert call_count == 1

    async def test_exhausts_retries(self):
        call_count = 0

        async def always_fails():
            nonlocal call_count
            call_count += 1
            raise LLMProviderError(500, "always fails")

        with pytest.raises(LLMProviderError):
            await retry_with_backoff(
                always_fails,
                max_retries=2,
                backoff_base=0.01,
            )

        assert call_count == 3  # Initial + 2 retries

    async def test_uses_rate_limit_retry_after(self):
        """Should respect the provider's retry-after header."""
        import time

        call_count = 0

        async def rate_limited():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise LLMRateLimitError(retry_after=0.05)
            return "ok"

        start = time.monotonic()
        await retry_with_backoff(
            rate_limited,
            max_retries=3,
            backoff_base=0.01,
        )
        elapsed = time.monotonic() - start

        # Should have waited at least 0.05s (the retry_after value)
        assert elapsed >= 0.04  # Small tolerance


class TestBackoffCalculation:
    def test_exponential_growth(self):
        b0 = _calculate_backoff(0, base=1.0, maximum=100.0)
        b1 = _calculate_backoff(1, base=1.0, maximum=100.0)
        b2 = _calculate_backoff(2, base=1.0, maximum=100.0)

        # Each should roughly double (with jitter)
        assert b1 > b0
        assert b2 > b1

    def test_respects_maximum(self):
        result = _calculate_backoff(10, base=2.0, maximum=30.0)
        assert result <= 30.0

    def test_includes_jitter(self):
        """Multiple calls should produce different values (jitter)."""
        results = [_calculate_backoff(1, base=1.0, maximum=100.0) for _ in range(10)]
        # With jitter, not all values should be identical
        assert len(set(results)) > 1