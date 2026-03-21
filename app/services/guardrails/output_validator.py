"""
app/services/guardrails/output_validator.py

Post-LLM output validation and quality checks.

Runs AFTER LLM generation to:
  1. Score faithfulness (is answer grounded in retrieved context?)
  2. Score relevancy (does answer address the query?)
  3. Detect PII leaks (did the LLM output sensitive data?)
  4. Check for safety issues (toxic, harmful content)

Design:
  - Faithfulness and relevancy use LLM-as-judge (calls the fast model)
  - PII leak detection uses the same regex patterns as input filter
  - Results are structured for the Validator graph node to act on
  - All checks are optional and configurable
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import structlog

from app.models.domain import EvalScores, RetrievedChunk
from app.services.guardrails.input_filter import _PII_PATTERNS
from app.services.llm.client import LLMClient
from app.services.llm.prompts import faithfulness_check_prompt, relevancy_check_prompt
from app.services.llm.retry import LLMError
from config import get_settings

logger = structlog.get_logger(__name__)


# ═══════════════════════════════════════════════════════════
# Result Types
# ═══════════════════════════════════════════════════════════


@dataclass
class ValidationResult:
    """
    Result of output validation.

    The Validator graph node uses this to decide whether to:
      - Approve the response (send to user)
      - Flag for review (send with warning)
      - Reject and retry (regenerate with different prompt)
    """
    is_approved: bool
    eval_scores: EvalScores
    pii_leaked: bool = False
    pii_types_leaked: list[str] = field(default_factory=list)
    safety_issues: list[str] = field(default_factory=list)
    rejection_reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# ═══════════════════════════════════════════════════════════
# Output Validator Service
# ═══════════════════════════════════════════════════════════


class OutputValidator:
    """
    Post-LLM output validation service.

    Runs quality and safety checks on generated responses.

    Usage:
        validator = OutputValidator(llm_client=client)

        result = await validator.validate(
            query="What is RAG?",
            answer="RAG is a technique...",
            context_chunks=retrieved_chunks,
        )

        if not result.is_approved:
            # Handle rejection
            for reason in result.rejection_reasons:
                log.warning(reason)
    """

    def __init__(self, llm_client: LLMClient) -> None:
        self._llm = llm_client
        self._settings = get_settings()

    async def validate(
        self,
        query: str,
        answer: str,
        context_chunks: list[RetrievedChunk] | None = None,
        check_faithfulness: bool = True,
        check_relevancy: bool = True,
        check_pii_leak: bool = True,
    ) -> ValidationResult:
        """
        Run all configured validation checks on a generated answer.

        Args:
            query: The original user query
            answer: The generated answer to validate
            context_chunks: Retrieved chunks used for generation
            check_faithfulness: Whether to run faithfulness check
            check_relevancy: Whether to run relevancy check
            check_pii_leak: Whether to scan for PII in output

        Returns:
            ValidationResult with scores and approval status
        """
        settings = self._settings
        eval_scores = EvalScores()
        rejection_reasons: list[str] = []
        warnings: list[str] = []

        # ── 1. PII Leak Detection ─────────────────────
        pii_leaked = False
        pii_types: list[str] = []

        if check_pii_leak and settings.dlp_scan_output:
            pii_leaked, pii_types = self._check_pii_leak(answer)
            if pii_leaked:
                warnings.append(
                    f"PII detected in output: {', '.join(pii_types)}"
                )
                logger.warning(
                    "pii_leak_detected_in_output",
                    pii_types=pii_types,
                    answer_preview=answer[:100],
                )

        # ── 2. Faithfulness Check ─────────────────────
        if check_faithfulness and context_chunks:
            faithfulness = await self._check_faithfulness(
                query=query,
                answer=answer,
                context_chunks=context_chunks,
            )
            eval_scores.faithfulness = faithfulness

            if faithfulness is not None and faithfulness < settings.eval_faithfulness_threshold:
                rejection_reasons.append(
                    f"Faithfulness score ({faithfulness:.2f}) below threshold "
                    f"({settings.eval_faithfulness_threshold})"
                )

        # ── 3. Relevancy Check ────────────────────────
        if check_relevancy:
            relevancy = await self._check_relevancy(
                query=query,
                answer=answer,
            )
            eval_scores.answer_relevancy = relevancy

            if relevancy is not None and relevancy < settings.eval_relevancy_threshold:
                rejection_reasons.append(
                    f"Relevancy score ({relevancy:.2f}) below threshold "
                    f"({settings.eval_relevancy_threshold})"
                )

        # ── 4. Safety Check (basic) ───────────────────
        safety_issues = self._check_safety(answer)
        if safety_issues:
            warnings.extend(safety_issues)

        # ── Determine approval ────────────────────────
        is_approved = len(rejection_reasons) == 0

        result = ValidationResult(
            is_approved=is_approved,
            eval_scores=eval_scores,
            pii_leaked=pii_leaked,
            pii_types_leaked=pii_types,
            safety_issues=safety_issues,
            rejection_reasons=rejection_reasons,
            warnings=warnings,
        )

        logger.info(
            "output_validation_complete",
            is_approved=is_approved,
            faithfulness=eval_scores.faithfulness,
            relevancy=eval_scores.answer_relevancy,
            pii_leaked=pii_leaked,
            rejection_reasons=rejection_reasons,
        )

        return result

    async def _check_faithfulness(
        self,
        query: str,
        answer: str,
        context_chunks: list[RetrievedChunk],
    ) -> float | None:
        """
        Score faithfulness using LLM-as-judge.
        Returns score 0.0-1.0 or None if check fails.
        """
        try:
            system, user = faithfulness_check_prompt(
                query=query,
                context_chunks=context_chunks,
                answer=answer,
            )

            response = await self._llm.generate_json(
                messages=[{"role": "user", "content": user}],
                system_prompt=system,
                model_tier="fast",  # Use fast model for evaluation (cost efficiency)
                max_tokens=500,
                temperature=0.1,  # Low temperature for consistent scoring
            )

            result = json.loads(response.content)
            score = float(result.get("faithfulness_score", 0.0))

            # Log unsupported claims for debugging
            unsupported = result.get("unsupported_claims", [])
            if unsupported:
                logger.debug(
                    "faithfulness_unsupported_claims",
                    claims=unsupported,
                    score=score,
                )

            return max(0.0, min(1.0, score))  # Clamp to [0, 1]

        except (LLMError, json.JSONDecodeError, ValueError, KeyError) as e:
            logger.warning(
                "faithfulness_check_failed",
                error=str(e),
            )
            return None

    async def _check_relevancy(
        self,
        query: str,
        answer: str,
    ) -> float | None:
        """
        Score relevancy using LLM-as-judge.
        Returns score 0.0-1.0 or None if check fails.
        """
        try:
            system, user = relevancy_check_prompt(
                query=query,
                answer=answer,
            )

            response = await self._llm.generate_json(
                messages=[{"role": "user", "content": user}],
                system_prompt=system,
                model_tier="fast",
                max_tokens=500,
                temperature=0.1,
            )

            result = json.loads(response.content)
            score = float(result.get("relevancy_score", 0.0))

            return max(0.0, min(1.0, score))

        except (LLMError, json.JSONDecodeError, ValueError, KeyError) as e:
            logger.warning(
                "relevancy_check_failed",
                error=str(e),
            )
            return None

    def _check_pii_leak(self, text: str) -> tuple[bool, list[str]]:
        """
        Scan output text for PII patterns.
        Returns (has_pii, list_of_pii_types_found).
        """
        found_types: list[str] = []

        for entity_type, pattern in _PII_PATTERNS:
            if pattern.search(text):
                found_types.append(entity_type)

        return len(found_types) > 0, found_types

    def _check_safety(self, text: str) -> list[str]:
        """
        Basic safety checks on output text.
        Returns list of safety issue descriptions.

        This is a lightweight check — for production, integrate
        a dedicated content moderation API (e.g., OpenAI moderation,
        Perspective API, or a fine-tuned classifier).
        """
        issues: list[str] = []

        # Check for common refusal patterns that indicate the LLM
        # detected something problematic in its own output
        refusal_patterns = [
            r"I (?:cannot|can't|won't|will not) (?:help|assist|provide)",
            r"(?:as an AI|as a language model),?\s+I",
            r"I (?:must|need to) (?:decline|refuse)",
        ]

        import re

        for pattern in refusal_patterns:
            if re.search(pattern, text, re.IGNORECASE):
                issues.append(
                    "Response contains LLM refusal language — "
                    "may indicate content policy conflict"
                )
                break

        # Check for extremely short responses (possible generation failure)
        if len(text.strip()) < 20:
            issues.append(
                "Response is suspiciously short — may indicate generation failure"
            )

        # Check for repetitive text (degenerate output)
        words = text.split()
        if len(words) > 50:
            # Check if any 5-word sequence repeats more than 3 times
            ngrams: dict[str, int] = {}
            for i in range(len(words) - 4):
                ngram = " ".join(words[i:i + 5])
                ngrams[ngram] = ngrams.get(ngram, 0) + 1

            max_repeat = max(ngrams.values()) if ngrams else 0
            if max_repeat > 3:
                issues.append(
                    f"Response contains repetitive text (5-gram repeated {max_repeat} times)"
                )

        return issues
