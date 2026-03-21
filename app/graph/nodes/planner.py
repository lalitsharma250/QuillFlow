"""
app/graph/nodes/planner.py

Generates a structured content plan for complex queries.
Only runs for complex queries — simple queries skip this.
"""

from __future__ import annotations

import json

import structlog

from app.graph.state import GraphState
from app.models.domain import ContentPlan
from app.services.llm.client import LLMClient
from app.services.llm.prompts import planner_prompt
from config import get_settings

logger = structlog.get_logger(__name__)


async def planner_node(
    state: GraphState,
    llm_client: LLMClient,
) -> GraphState:
    """
    Generate a content plan with sections and word budgets.

    Reads: sanitized_query, retrieved_chunks, model_preference, max_sections_override
    Writes: plan, total_usage
    """
    query = state["sanitized_query"]
    chunks = state.get("retrieved_chunks", [])
    model_preference = state.get("model_preference", "auto")
    max_sections_override = state.get("max_sections_override")

    settings = get_settings()
    max_sections = max_sections_override or settings.max_plan_sections

    # Use strong model for planning (quality matters here)
    model_tier = "fast"

    system, user_msg = planner_prompt(
        query=query,
        context_chunks=chunks,
        max_sections=max_sections,
    )

    response = await llm_client.generate_json(
        messages=[{"role": "user", "content": user_msg}],
        system_prompt=system,
        model_tier=model_tier,
        max_tokens=2000,
        temperature=0.3,
    )

    # Parse into ContentPlan
    raw_plan = json.loads(response.content)

    try:
        plan = ContentPlan(**raw_plan)
    except Exception as e:
        logger.warning(
            "plan_validation_failed_using_fallback",
            error=str(e),
            raw_plan_keys=list(raw_plan.keys()),
        )
        # Fallback: create a simple single-section plan
        plan = ContentPlan(
            title=raw_plan.get("title", query[:100]),
            sections=[
                {
                    "heading": "Response",
                    "description": query,
                    "word_budget": 500,
                    "key_points": [],
                }
            ],
            total_word_budget=500,
        )

    # Track usage
    existing_usage = state.get("total_usage")
    total_usage = (existing_usage + response.usage) if existing_usage else response.usage

    logger.info(
        "plan_generated",
        title=plan.title,
        section_count=len(plan.sections),
        total_word_budget=plan.total_word_budget,
        model=response.model,
        latency_ms=response.latency_ms,
    )

    return {
        "plan": plan,
        "total_usage": total_usage,
    }
