"""
app/services/llm/client.py

Unified LLM client for QuillFlow.

All LLM calls in the application go through this client.
It handles:
  - Model selection (fast vs strong)
  - Request formatting for OpenRouter API
  - Response parsing and validation
  - Token usage tracking and cost estimation
  - Error classification (rate limit, timeout, content filter, etc.)
  - Integration with retry + circuit breaker

Uses the OpenAI SDK pointed at OpenRouter — this gives us
access to Claude models with a familiar API.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from uuid import UUID, uuid4

import structlog
from openai import AsyncOpenAI, APIError, APITimeoutError, RateLimitError

from app.models.responses import TokenUsage
from app.services.llm.retry import (
    CircuitBreaker,
    LLMContentFilterError,
    LLMError,
    LLMProviderError,
    LLMRateLimitError,
    LLMTimeoutError,
    retry_with_backoff,
)
from config import get_settings

logger = structlog.get_logger(__name__)


# ═══════════════════════════════════════════════════════════
# Response Types
# ═══════════════════════════════════════════════════════════


@dataclass
class LLMResponse:
    """
    Structured response from an LLM call.
    Every call returns this — never raw API responses.
    """

    content: str
    model: str
    usage: TokenUsage
    response_id: str
    latency_ms: float
    finish_reason: str | None = None

    @property
    def was_truncated(self) -> bool:
        """Check if response was cut off due to max_tokens."""
        return self.finish_reason == "length"


# ═══════════════════════════════════════════════════════════
# Cost Estimation
# ═══════════════════════════════════════════════════════════

# Approximate pricing per 1M tokens (USD) — update as prices change
_MODEL_PRICING: dict[str, dict[str, float]] = {
    "anthropic/claude-sonnet-4-20250514": {
        "input": 3.0,
        "output": 15.0,
    },
    "anthropic/claude-opus-4-6": {
        "input": 15.0,
        "output": 75.0,
    },
    # Fallback for unknown models
    "default": {
        "input": 5.0,
        "output": 15.0,
    },
}


def _estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Estimate cost in USD for a single LLM call."""
    pricing = _MODEL_PRICING.get(model, _MODEL_PRICING["default"])
    input_cost = (input_tokens / 1_000_000) * pricing["input"]
    output_cost = (output_tokens / 1_000_000) * pricing["output"]
    return round(input_cost + output_cost, 6)


# ═══════════════════════════════════════════════════════════
# LLM Client
# ═══════════════════════════════════════════════════════════


class LLMClient:
    """
    Unified LLM client for all QuillFlow LLM interactions.

    Features:
      - Model selection: 'fast' (Sonnet) vs 'strong' (Opus)
      - Automatic retry with exponential backoff
      - Circuit breaker to avoid hammering a down provider
      - Token usage tracking and cost estimation
      - Structured JSON output support
      - Streaming support (for SSE)

    Usage:
        client = LLMClient()

        # Simple call
        response = await client.generate(
            messages=[{"role": "user", "content": "Hello"}],
            model_tier="fast",
        )

        # Structured JSON output
        response = await client.generate_json(
            messages=[...],
            model_tier="strong",
        )

        # Streaming
        async for chunk in client.stream(messages=[...]):
            print(chunk)
    """

    def __init__(self) -> None:
        settings = get_settings()

        self._openai_client = AsyncOpenAI(
            base_url=settings.llm_provider_base_url,
            api_key=settings.llm_api_key.get_secret_value(),
            timeout=settings.llm_timeout_seconds,
            max_retries=0,  # We handle retries ourselves
        )

        self._model_fast = settings.llm_model_fast
        self._model_strong = settings.llm_model_strong
        self._max_retries = settings.llm_max_retries
        self._max_tokens = settings.llm_max_tokens_per_request

        # One circuit breaker per model tier
        self._breakers = {
            "fast": CircuitBreaker(),
            "strong": CircuitBreaker(),
        }

    def _resolve_model(self, model_tier: str) -> str:
        """Map tier name to actual model identifier."""
        if model_tier == "fast":
            return self._model_fast
        elif model_tier == "strong":
            return self._model_strong
        else:
            return self._model_fast  # Default to fast

    # ═══════════════════════════════════════════════════
    # Standard Generation
    # ═══════════════════════════════════════════════════

    async def generate(
        self,
        messages: list[dict[str, str]],
        model_tier: str = "fast",
        max_tokens: int | None = None,
        temperature: float = 0.7,
        system_prompt: str | None = None,
    ) -> LLMResponse:
        """
        Generate a response from the LLM.

        Args:
            messages: Chat messages [{"role": "user", "content": "..."}]
            model_tier: "fast" (Sonnet) or "strong" (Opus)
            max_tokens: Max tokens in response (default from settings)
            temperature: Sampling temperature (0=deterministic, 1=creative)
            system_prompt: Optional system message prepended to messages

        Returns:
            LLMResponse with content, usage, and metadata

        Raises:
            LLMError subclass on failure (after retries exhausted)
        """
        model = self._resolve_model(model_tier)
        max_tokens = max_tokens or self._max_tokens
        breaker = self._breakers.get(model_tier, self._breakers["fast"])

        # Prepend system prompt if provided
        full_messages = []
        if system_prompt:
            full_messages.append({"role": "system", "content": system_prompt})
        full_messages.extend(messages)

        # Retry wrapper
        response = await retry_with_backoff(
            self._call_api,
            model=model,
            messages=full_messages,
            max_tokens=max_tokens,
            temperature=temperature,
            circuit_breaker=breaker,
            max_retries=self._max_retries,
        )

        return response

    async def _call_api(
        self,
        model: str,
        messages: list[dict[str, str]],
        max_tokens: int,
        temperature: float,
    ) -> LLMResponse:
        """
        Make a single API call (no retry — that's handled by the wrapper).
        Translates OpenAI SDK exceptions to our exception hierarchy.
        """
        start_time = time.monotonic()

        try:
            response = await self._openai_client.chat.completions.create(
                model=model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
            )
        except RateLimitError as e:
            retry_after = None
            if hasattr(e, "response") and e.response is not None:
                retry_after_header = e.response.headers.get("retry-after")
                if retry_after_header:
                    try:
                        retry_after = float(retry_after_header)
                    except ValueError:
                        pass
            raise LLMRateLimitError(retry_after=retry_after) from e

        except APITimeoutError as e:
            raise LLMTimeoutError(f"Request timed out: {e}") from e

        except APIError as e:
            status = getattr(e, "status_code", 500)

            # Content filter / moderation rejection
            if status == 400 and "content" in str(e).lower():
                raise LLMContentFilterError(str(e)) from e

            # Server errors (retriable)
            if status >= 500:
                raise LLMProviderError(status, str(e)) from e

            # Client errors (not retriable)
            raise LLMError(f"API error {status}: {e}") from e

        latency_ms = (time.monotonic() - start_time) * 1000

        # Parse response
        choice = response.choices[0]
        content = choice.message.content or ""

        # Build usage tracking
        usage_data = response.usage
        input_tokens = usage_data.prompt_tokens if usage_data else 0
        output_tokens = usage_data.completion_tokens if usage_data else 0
        total_tokens = usage_data.total_tokens if usage_data else 0

        usage = TokenUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            estimated_cost_usd=_estimate_cost(model, input_tokens, output_tokens),
        )

        llm_response = LLMResponse(
            content=content,
            model=model,
            usage=usage,
            response_id=response.id or str(uuid4()),
            latency_ms=round(latency_ms, 2),
            finish_reason=choice.finish_reason,
        )

        logger.debug(
            "llm_call_complete",
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=usage.estimated_cost_usd,
            latency_ms=llm_response.latency_ms,
            finish_reason=choice.finish_reason,
        )

        return llm_response

    # ═══════════════════════════════════════════════════
    # JSON Generation
    # ═══════════════════════════════════════════════════

    async def generate_json(
        self,
        messages: list[dict[str, str]],
        model_tier: str = "fast",
        max_tokens: int | None = None,
        temperature: float = 0.3,
        system_prompt: str | None = None,
    ) -> LLMResponse:
        """
        Generate a JSON response from the LLM.

        Same as generate() but:
          - Lower default temperature (more deterministic)
          - Appends instruction to return valid JSON
          - Validates that response is parseable JSON

        The caller is responsible for parsing the JSON into
        the expected Pydantic model.
        """
        # Append JSON instruction to the last user message
        json_messages = [m.copy() for m in messages]

        # Add JSON instruction to system prompt
        json_system = system_prompt or ""
        json_system += (
            "\n\nIMPORTANT: Respond with valid JSON only. "
            "No markdown code fences, no explanation, just the JSON object."
        )

        response = await self.generate(
            messages=json_messages,
            model_tier=model_tier,
            max_tokens=max_tokens,
            temperature=temperature,
            system_prompt=json_system.strip(),
        )

        # Clean up common LLM JSON formatting issues
        response.content = self._clean_json_response(response.content)

        # Validate it's parseable JSON
        import json

        try:
            json.loads(response.content)
        except json.JSONDecodeError as e:
            logger.warning(
                "llm_json_parse_failed",
                model=response.model,
                content_preview=response.content[:200],
                error=str(e),
            )
            raise LLMError(
                f"LLM returned invalid JSON: {e}. "
                f"Content preview: {response.content[:200]}"
            ) from e

        return response

    @staticmethod
    def _clean_json_response(content: str) -> str:
        """
        Clean common LLM JSON formatting issues.
        Models often wrap JSON in markdown code fences.
        """
        content = content.strip()

        # Remove markdown code fences
        if content.startswith("```json"):
            content = content[7:]
        elif content.startswith("```"):
            content = content[3:]

        if content.endswith("```"):
            content = content[:-3]

        return content.strip()

    # ═══════════════════════════════════════════════════
    # Streaming
    # ═══════════════════════════════════════════════════

    async def stream(
        self,
        messages: list[dict[str, str]],
        model_tier: str = "fast",
        max_tokens: int | None = None,
        temperature: float = 0.7,
        system_prompt: str | None = None,
    ):
        """
        Stream a response from the LLM token by token.

        Yields StreamChunk objects with incremental content.
        Used by the API layer for SSE streaming.

        Usage:
            accumulated = ""
            async for chunk in client.stream(messages=[...]):
                accumulated += chunk.content
                yield chunk  # Send to client via SSE

        Yields:
            StreamChunk objects with content deltas and metadata
        """
        model = self._resolve_model(model_tier)
        max_tokens = max_tokens or self._max_tokens
        breaker = self._breakers.get(model_tier, self._breakers["fast"])

        full_messages = []
        if system_prompt:
            full_messages.append({"role": "system", "content": system_prompt})
        full_messages.extend(messages)

        start_time = time.monotonic()
        input_tokens = 0
        output_tokens = 0

        try:
            async with breaker:
                stream = await self._openai_client.chat.completions.create(
                    model=model,
                    messages=full_messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    stream=True,
                    stream_options={"include_usage": True},
                )

                async for event in stream:
                    # Usage info comes in the final chunk
                    if event.usage:
                        input_tokens = event.usage.prompt_tokens
                        output_tokens = event.usage.completion_tokens

                    if not event.choices:
                        continue

                    choice = event.choices[0]
                    delta = choice.delta

                    if delta and delta.content:
                        yield StreamChunk(
                            content=delta.content,
                            finish_reason=None,
                        )

                    if choice.finish_reason:
                        latency_ms = (time.monotonic() - start_time) * 1000
                        total_tokens = input_tokens + output_tokens

                        yield StreamChunk(
                            content="",
                            finish_reason=choice.finish_reason,
                            usage=TokenUsage(
                                input_tokens=input_tokens,
                                output_tokens=output_tokens,
                                total_tokens=total_tokens,
                                estimated_cost_usd=_estimate_cost(
                                    model, input_tokens, output_tokens
                                ),
                            ),
                            latency_ms=round(latency_ms, 2),
                        )

        except RateLimitError as e:
            raise LLMRateLimitError() from e
        except APITimeoutError as e:
            raise LLMTimeoutError(str(e)) from e
        except APIError as e:
            status = getattr(e, "status_code", 500)
            if status >= 500:
                raise LLMProviderError(status, str(e)) from e
            raise LLMError(str(e)) from e

    # ═══════════════════════════════════════════════════
    # Health & Status
    # ═══════════════════════════════════════════════════

    async def health_check(self) -> tuple[bool, float]:
        """
        Quick health check — send a minimal prompt and measure latency.

        Returns:
            (is_healthy, latency_ms)
        """
        try:
            response = await self.generate(
                messages=[{"role": "user", "content": "Hi"}],
                model_tier="fast",
                max_tokens=5,
                temperature=0,
            )
            return True, response.latency_ms
        except Exception as e:
            logger.error("llm_health_check_failed", error=str(e))
            return False, 0.0

    def get_circuit_status(self) -> dict[str, str]:
        """Get circuit breaker status for each model tier."""
        return {
            tier: breaker.state.value
            for tier, breaker in self._breakers.items()
        }


@dataclass
class StreamChunk:
    """A single chunk from a streaming LLM response."""

    content: str
    finish_reason: str | None = None
    usage: TokenUsage | None = None
    latency_ms: float | None = None

    @property
    def is_final(self) -> bool:
        return self.finish_reason is not None
