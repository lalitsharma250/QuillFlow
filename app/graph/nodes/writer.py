"""
app/graph/nodes/writer.py

Writes individual sections of the content plan.
Multiple writer instances run IN PARALLEL for different sections.
"""

from __future__ import annotations

import asyncio
import json

import structlog

from app.graph.state import GraphState
from app.models.domain import ContentPlan, SectionDraft, SectionPlan
from app.models.responses import TokenUsage
from app.services.llm.client import LLMClient
from app.services.llm.prompts import writer_prompt

logger = structlog.get_logger(__name__)


async def writer_node(
    state: GraphState,
    llm_client: LLMClient,
) -> GraphState:
    """
    Write all sections of the content plan in parallel.

    Each section gets its own LLM call. Calls run concurrently
    with a semaphore to control parallelism.

    Reads: plan, retrieved_chunks, model_preference
    Writes: section_drafts, total_usage
    """
    plan = state["plan"]
    chunks = state.get("retrieved_chunks", [])
    model_preference = state.get("model_preference", "auto")

    # Use strong model for writing if preference allows
    model_tier = "strong" if model_preference in ("auto", "strong") else "fast"

    # Parallel writing with bounded concurrency
    max_concurrent = min(len(plan.sections), 4)  # Max 4 parallel LLM calls
    semaphore = asyncio.Semaphore(max_concurrent)

    # Track completed sections for preceding context
    completed_contents: dict[int, str] = {}

    async def _write_section(
        index: int,
        section: SectionPlan,
    ) -> tuple[SectionDraft, TokenUsage]:
        """Write a single section."""
        async with semaphore:
            # Build preceding sections context
            preceding = []
            for prev_idx in range(index):
                if prev_idx in completed_contents:
                    preceding.append(completed_contents[prev_idx])

            system, user_msg = writer_prompt(
                section=section,
                context_chunks=chunks,
                full_plan=plan,
                preceding_sections=preceding if preceding else None,
            )

            response = await llm_client.generate_json(
                messages=[{"role": "user", "content": user_msg}],
                system_prompt=system,
                model_tier=model_tier,
                max_tokens=max(section.word_budget * 3, 500),  # Allow some overflow
                temperature=0.7,
            )

            # Parse response
            try:
                raw = json.loads(response.content)
                draft = SectionDraft(
                    heading=raw.get("heading", section.heading),
                    content=raw.get("content", response.content),
                    sources_used=raw.get("sources_used", []),
                )
            except (json.JSONDecodeError, Exception):
                # Fallback: use raw content
                draft = SectionDraft(
                    heading=section.heading,
                    content=response.content,
                )

            # Store for preceding context
            completed_contents[index] = draft.content

            logger.debug(
                "section_written",
                heading=draft.heading,
                word_count=draft.word_count,
                model=response.model,
                latency_ms=response.latency_ms,
            )

            return draft, response.usage

    # Launch all section writes
    tasks = [
        _write_section(i, section)
        for i, section in enumerate(plan.sections)
    ]

    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Collect results
    drafts: list[SectionDraft] = []
    accumulated_usage = state.get("total_usage") or TokenUsage()

    for i, result in enumerate(results):
        if isinstance(result, Exception):
            logger.error(
                "section_write_failed",
                section_index=i,
                heading=plan.sections[i].heading,
                error=str(result),
            )
            # Create a fallback draft
            drafts.append(SectionDraft(
                heading=plan.sections[i].heading,
                content=f"[Section generation failed: {str(result)[:100]}]",
            ))
        else:
            draft, usage = result
            drafts.append(draft)
            accumulated_usage = accumulated_usage + usage

    logger.info(
        "all_sections_written",
        section_count=len(drafts),
        successful=sum(1 for r in results if not isinstance(r, Exception)),
        failed=sum(1 for r in results if isinstance(r, Exception)),
    )

    return {
        "section_drafts": drafts,
        "total_usage": accumulated_usage,
    }
