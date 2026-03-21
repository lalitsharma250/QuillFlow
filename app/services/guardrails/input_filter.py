"""
app/services/guardrails/input_filter.py

Pre-LLM input filtering and sanitization.

Runs BEFORE any LLM call to:
  1. Detect and optionally strip PII from queries
  2. Detect prompt injection attempts
  3. Enforce content policy (block prohibited topics)
  4. Validate query complexity and length

Design:
  - All checks are fast (regex + heuristics, no LLM calls)
  - Returns a structured result with pass/fail and details
  - PII stripping is reversible (tokens can be rehydrated)
  - Configurable strictness via settings
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

import structlog

from config import get_settings

logger = structlog.get_logger(__name__)


# ═══════════════════════════════════════════════════════════
# Result Types
# ═══════════════════════════════════════════════════════════


class FilterAction(str, Enum):
    """What to do with the query after filtering."""
    ALLOW = "allow"           # Query is safe, proceed normally
    SANITIZE = "sanitize"     # Query was modified (PII stripped), proceed with sanitized version
    BLOCK = "block"           # Query is rejected, do not proceed


@dataclass
class PIIEntity:
    """A detected PII entity in the text."""
    entity_type: str          # "email", "phone", "ssn", "credit_card", "person_name"
    value: str                # The actual PII value found
    start: int                # Start position in original text
    end: int                  # End position in original text
    replacement: str          # Token that replaced it (e.g. "[EMAIL_1]")


@dataclass
class InputFilterResult:
    """
    Result of running input filters on a query.

    If action is ALLOW: query is unchanged, safe to proceed
    If action is SANITIZE: use sanitized_query instead of original
    If action is BLOCK: reject the query, show block_reason to user
    """
    action: FilterAction
    original_query: str
    sanitized_query: str                          # Same as original if no sanitization needed
    pii_entities: list[PIIEntity] = field(default_factory=list)
    injection_detected: bool = False
    injection_patterns: list[str] = field(default_factory=list)
    block_reason: str | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def has_pii(self) -> bool:
        return len(self.pii_entities) > 0

    @property
    def pii_map(self) -> dict[str, str]:
        """Map of replacement tokens to original values (for rehydration)."""
        return {entity.replacement: entity.value for entity in self.pii_entities}


# ═══════════════════════════════════════════════════════════
# PII Detection Patterns
# ═══════════════════════════════════════════════════════════

# Email addresses
_EMAIL_PATTERN = re.compile(
    r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"
)

# Phone numbers (US formats)
_PHONE_PATTERN = re.compile(
    r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"
)

_PHONE_PATTERN_INDIA = re.compile(
    r"\b(?:\+91[-.\s]?|91[-.\s]?|0)?[6-9]\d{9}\b"
)

# Social Security Numbers
_SSN_PATTERN = re.compile(
    r"\b\d{3}[-.\s]?\d{2}[-.\s]?\d{4}\b"
)

# Credit card numbers (basic — 13-19 digits with optional separators)
_CREDIT_CARD_PATTERN = re.compile(
    r"\b(?:\d{4}[-.\s]?){3,4}\d{1,4}\b"
)

# IP addresses
_IP_PATTERN = re.compile(
    r"\b(?:\d{1,3}\.){3}\d{1,3}\b"
)

# Dates of birth patterns (MM/DD/YYYY, DD-MM-YYYY, etc.)
_DOB_PATTERN = re.compile(
    r"\b(?:\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4})\b"
)

_PII_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("email", _EMAIL_PATTERN),
    ("phone", _PHONE_PATTERN_INDIA),
    ("ssn", _SSN_PATTERN),
    ("credit_card", _CREDIT_CARD_PATTERN),
    ("ip_address", _IP_PATTERN),
    ("date_of_birth", _DOB_PATTERN),
]


# ═══════════════════════════════════════════════════════════
# Prompt Injection Detection Patterns
# ═══════════════════════════════════════════════════════════

_INJECTION_PATTERNS: list[tuple[str, re.Pattern]] = [
    (
        "role_override",
        re.compile(
            r"(?:ignore|forget|disregard)\s+(?:all\s+)?(?:previous|above|prior)\s+"
            r"(?:instructions|prompts|rules|context)",
            re.IGNORECASE,
        ),
    ),
    (
        "system_prompt_extraction",
        re.compile(
            r"(?:show|reveal|display|print|output|repeat)\s+(?:your\s+)?"
            r"(?:system\s+)?(?:prompt|instructions|rules|configuration)",
            re.IGNORECASE,
        ),
    ),
    (
        "role_play_attack",
        re.compile(
            r"(?:you\s+are\s+now|act\s+as|pretend\s+(?:to\s+be|you(?:'re|\s+are)))\s+"
            r"(?:a\s+)?(?:different|new|evil|unrestricted|jailbroken)",
            re.IGNORECASE,
        ),
    ),
    (
        "encoding_bypass",
        re.compile(
            r"(?:base64|rot13|hex|unicode|encode|decode)\s*[:=]",
            re.IGNORECASE,
        ),
    ),
    (
        "delimiter_injection",
        re.compile(
            r"(?:```|<\|(?:im_start|im_end|system|user|assistant)\|>|</?(?:system|instruction)>)",
            re.IGNORECASE,
        ),
    ),
    (
        "instruction_override",
        re.compile(
            r"(?:new\s+instructions?|override\s+(?:the\s+)?(?:system|rules)|"
            r"from\s+now\s+on|starting\s+now|henceforth)",
            re.IGNORECASE,
        ),
    ),
]


# ═══════════════════════════════════════════════════════════
# Content Policy Patterns
# ═══════════════════════════════════════════════════════════

_BLOCKED_CONTENT_PATTERNS: list[tuple[str, re.Pattern]] = [
    (
        "weapons_instructions",
        re.compile(
            r"(?:how\s+to\s+(?:make|build|create|construct)\s+(?:a\s+)?(?:bomb|weapon|explosive))",
            re.IGNORECASE,
        ),
    ),
    (
        "illegal_activity",
        re.compile(
            r"(?:how\s+to\s+(?:hack|break\s+into|steal|forge|counterfeit))",
            re.IGNORECASE,
        ),
    ),
]


# ═══════════════════════════════════════════════════════════
# Input Filter Service
# ═══════════════════════════════════════════════════════════


class InputFilter:
    """
    Pre-LLM input filtering service.

    Runs a series of checks on the user's query and returns
    a structured result indicating whether to allow, sanitize, or block.

    Usage:
        filter = InputFilter()
        result = filter.check(query="My email is john@example.com, what is RAG?")

        if result.action == FilterAction.BLOCK:
            return error_response(result.block_reason)
        elif result.action == FilterAction.SANITIZE:
            query = result.sanitized_query  # PII stripped
        # else: proceed with original query
    """

    def __init__(self) -> None:
        self._settings = get_settings()

    def check(self, query: str) -> InputFilterResult:
        """
        Run all input filters on a query.

        Checks (in order):
          1. Length validation
          2. Content policy (blocked topics)
          3. Prompt injection detection
          4. PII detection and stripping

        Args:
            query: The raw user query

        Returns:
            InputFilterResult with action and details
        """
        # ── 1. Length validation ───────────────────────
        if len(query) > self._settings.max_input_length:
            return InputFilterResult(
                action=FilterAction.BLOCK,
                original_query=query,
                sanitized_query=query,
                block_reason=(
                    f"Query exceeds maximum length of "
                    f"{self._settings.max_input_length} characters"
                ),
            )

        if len(query.strip()) == 0:
            return InputFilterResult(
                action=FilterAction.BLOCK,
                original_query=query,
                sanitized_query=query,
                block_reason="Query is empty",
            )

        # ── 2. Content policy ─────────────────────────
        content_result = self._check_content_policy(query)
        if content_result is not None:
            return content_result

        # ── 3. Prompt injection ───────────────────────
        injection_detected, injection_patterns = self._check_injection(query)

        # ── 4. PII detection and stripping ────────────
        pii_entities, sanitized = self._detect_and_strip_pii(query)

        # ── Build result ──────────────────────────────
        warnings: list[str] = []

        if injection_detected:
            # We warn but don't block — the LLM's system prompt should handle it
            # Blocking would cause too many false positives
            warnings.append(
                f"Potential prompt injection detected: {', '.join(injection_patterns)}"
            )
            logger.warning(
                "prompt_injection_detected",
                patterns=injection_patterns,
                query_preview=query[:100],
            )

        if pii_entities and self._settings.dlp_strip_pii_before_llm:
            logger.info(
                "pii_detected_and_stripped",
                entity_types=[e.entity_type for e in pii_entities],
                entity_count=len(pii_entities),
            )
            return InputFilterResult(
                action=FilterAction.SANITIZE,
                original_query=query,
                sanitized_query=sanitized,
                pii_entities=pii_entities,
                injection_detected=injection_detected,
                injection_patterns=injection_patterns,
                warnings=warnings,
            )

        if pii_entities and not self._settings.dlp_strip_pii_before_llm:
            # PII found but stripping disabled — warn only
            warnings.append(
                f"PII detected ({len(pii_entities)} entities) but stripping is disabled"
            )

        return InputFilterResult(
            action=FilterAction.ALLOW,
            original_query=query,
            sanitized_query=query,
            pii_entities=pii_entities,
            injection_detected=injection_detected,
            injection_patterns=injection_patterns,
            warnings=warnings,
        )

    def _check_content_policy(self, query: str) -> InputFilterResult | None:
        """Check query against blocked content patterns."""
        for pattern_name, pattern in _BLOCKED_CONTENT_PATTERNS:
            if pattern.search(query):
                logger.warning(
                    "content_policy_violation",
                    pattern=pattern_name,
                    query_preview=query[:100],
                )
                return InputFilterResult(
                    action=FilterAction.BLOCK,
                    original_query=query,
                    sanitized_query=query,
                    block_reason=(
                        "Your query was blocked by our content policy. "
                        "Please rephrase your request."
                    ),
                )
        return None

    def _check_injection(self, query: str) -> tuple[bool, list[str]]:
        """
        Check for prompt injection patterns.
        Returns (detected, list_of_pattern_names).
        """
        detected_patterns = []

        for pattern_name, pattern in _INJECTION_PATTERNS:
            if pattern.search(query):
                detected_patterns.append(pattern_name)

        return len(detected_patterns) > 0, detected_patterns

    def _detect_and_strip_pii(
        self,
        text: str,
    ) -> tuple[list[PIIEntity], str]:
        """
        Detect PII entities and replace them with tokens.

        Replacement tokens are deterministic per entity type:
          First email → [EMAIL_1], second → [EMAIL_2], etc.

        Returns:
            (list of detected entities, sanitized text)
        """
        entities: list[PIIEntity] = []
        sanitized = text
        counters: dict[str, int] = {}

        for entity_type, pattern in _PII_PATTERNS:
            for match in pattern.finditer(text):
                # Generate replacement token
                count = counters.get(entity_type, 0) + 1
                counters[entity_type] = count
                replacement = f"[{entity_type.upper()}_{count}]"

                entity = PIIEntity(
                    entity_type=entity_type,
                    value=match.group(),
                    start=match.start(),
                    end=match.end(),
                    replacement=replacement,
                )
                entities.append(entity)

        # Apply replacements (reverse order to preserve positions)
        if entities:
            sorted_entities = sorted(entities, key=lambda e: e.start, reverse=True)
            for entity in sorted_entities:
                sanitized = (
                    sanitized[:entity.start]
                    + entity.replacement
                    + sanitized[entity.end:]
                )

        return entities, sanitized


def rehydrate_pii(text: str, pii_map: dict[str, str]) -> str:
    """
    Replace PII tokens back with original values.

    Used when the response needs to contain the original PII
    (e.g., internal-only responses, not sent to LLM).

    Args:
        text: Text containing PII tokens like [EMAIL_1]
        pii_map: Mapping from tokens to original values

    Returns:
        Text with PII tokens replaced by original values
    """
    result = text
    for token, original_value in pii_map.items():
        result = result.replace(token, original_value)
    return result
