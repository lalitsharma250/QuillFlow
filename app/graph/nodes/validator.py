"""
app/graph/nodes/validator.py

Post-generation validation: faithfulness, relevancy, PII leak, safety.
"""

from __future__ import annotations

import structlog

from app.graph.state import GraphState
from app.services.guardrails.output_validator import OutputValidator

logger = structlog.get_logger(__name__)


async def validator_node(
    state: GraphState,
    output_validator: OutputValidator,
) -> GraphState:
    """
    Validate the generated output for quality and safety.

    Reads: sanitized_query, final_output, retrieved_chunks
    Writes: validation_result, eval_scores, is_approved
    """
    query = state["sanitized_query"]
    answer = state.get("final_output", "")
    chunks = state.get("retrieved_chunks", [])

    if not answer:
        logger.warning("validator_received_empty_answer")
        return {
            "is_approved": False,
            "error": "Empty answer generated",
            "error_node": "validator",
        }

    result = await output_validator.validate(
        query=query,
        answer=answer,
        context_chunks=chunks,
    )

    logger.info(
        "validation_complete",
        is_approved=result.is_approved,
        faithfulness=result.eval_scores.faithfulness,
        relevancy=result.eval_scores.answer_relevancy,
        pii_leaked=result.pii_leaked,
        rejection_reasons=result.rejection_reasons,
    )

    return {
        "validation_result": result,
        "eval_scores": result.eval_scores,
        "is_approved": result.is_approved,
    }
