"""
app/graph/nodes/reducer.py

Merges section drafts into a final cohesive document.
Also handles simple query responses (direct LLM answer).
"""

from __future__ import annotations

import structlog

from app.graph.state import GraphState
from app.models.domain import QueryType
from app.services.llm.client import LLMClient
from app.services.llm.prompts import reducer_prompt, simple_answer_prompt, filter_relevant_chunks

logger = structlog.get_logger(__name__)


async def reducer_node(
    state: GraphState,
    llm_client: LLMClient,
) -> GraphState:
    """
    Produce the final output.

    For SIMPLE queries: Generate a direct answer from context
    For COMPLEX queries: Merge section drafts into a polished document

    Reads: query_type, sanitized_query, retrieved_chunks, section_drafts, plan
    Writes: final_output, total_usage
    """
    query_type = state.get("query_type", QueryType.SIMPLE)
    query = state["sanitized_query"]
    chunks = state.get("retrieved_chunks", [])
    model_preference = state.get("model_preference", "auto")

    if query_type == QueryType.SIMPLE:
        return await _simple_answer(state, llm_client, query, chunks, model_preference)
    else:
        return await _complex_merge(state, llm_client, model_preference)


async def _simple_answer(
    state: GraphState,
    llm_client: LLMClient,
    query: str,
    chunks: list,
    model_preference: str,
) -> GraphState:
    """Generate a direct answer for simple queries."""
    model_tier = "fast" if model_preference in ("auto", "fast") else "strong"
    history = state.get("history", [])

    # Filter chunks BEFORE sending to LLM
    filtered_chunks = filter_relevant_chunks(chunks)

    system, user_msg = simple_answer_prompt(
        query=query,
        context_chunks=filtered_chunks,
        history=history,
    )

    response = await llm_client.generate(
        messages=[{"role": "user", "content": user_msg}],
        system_prompt=system,
        model_tier=model_tier,
        temperature=0.5,
    )

    existing_usage = state.get("total_usage")
    total_usage = (existing_usage + response.usage) if existing_usage else response.usage

    return {
        "final_output": response.content,
        "total_usage": total_usage,
        # Store filtered chunks so sources match citations
        "retrieved_chunks": filtered_chunks,
    }

async def _complex_merge(
    state: GraphState,
    llm_client: LLMClient,
    model_preference: str,
) -> GraphState:
    """Merge section drafts into a final document."""
    plan = state["plan"]
    drafts = state.get("section_drafts", [])

    if not drafts:
        return {
            "final_output": "[No sections were generated]",
            "error": "No section drafts available for merging",
            "error_node": "reducer",
        }

    # For short documents (≤3 sections), concatenate directly
    # No need for expensive LLM merge
    total_words = sum(d.word_count for d in drafts)
    if len(drafts) <= 3 and total_words <= 500:
        merged = f"# {plan.title}\n\n"
        for draft in drafts:
            content = _clean_citations(draft.content)
            merged += f"## {draft.heading}\n\n{content}\n\n"

        logger.info(
            "complex_merge_direct",
            section_count=len(drafts),
            output_words=len(merged.split()),
            method="concatenation",
        )

        # Filter chunks for consistent source numbering
        from app.services.llm.prompts import filter_relevant_chunks
        filtered_chunks = filter_relevant_chunks(state.get("retrieved_chunks", []))

        return {
            "final_output": merged.strip(),
            "total_usage": state.get("total_usage"),
            "retrieved_chunks": filtered_chunks,
        }

    # For longer documents, use LLM to polish
    model_tier = "strong" if model_preference in ("auto", "strong") else "fast"

    # Build section text for the merger
    sections_text = f"# {plan.title}\n\n"
    for draft in drafts:
        content = _clean_citations(draft.content)
        sections_text += f"## {draft.heading}\n\n{content}\n\n---\n\n"

    from app.services.llm.prompts import filter_relevant_chunks
    filtered_chunks = filter_relevant_chunks(state.get("retrieved_chunks", []))
    
    system = (
        "You are an editor. Merge these section drafts into a polished document.\n\n"
        "Rules:\n"
        "1. Preserve the content and structure of each section\n"
        "2. Add smooth transitions between sections\n"
        "3. Remove any redundancy\n"
        "4. Keep numbered citations [1], [2] as they are\n"
        "5. Do NOT use [Source: filename] format\n"
        "6. Do NOT add information that wasn't in the drafts\n"
        "7. Do NOT include raw context or chunk text\n"
        "8. Output clean markdown with headings and formatting\n"
    )

    user = f"Section drafts to merge:\n\n{sections_text}\nMerge into a polished document:"

    response = await llm_client.generate(
        messages=[{"role": "user", "content": user}],
        system_prompt=system,
        model_tier=model_tier,
        max_tokens=2000,
        temperature=0.5,
    )

    existing_usage = state.get("total_usage")
    total_usage = (existing_usage + response.usage) if existing_usage else response.usage

    logger.info(
        "complex_merge_complete",
        section_count=len(drafts),
        output_words=len(response.content.split()),
        method="llm_merge",
    )

    return {
        "final_output": response.content,
        "total_usage": total_usage,
        "retrieved_chunks": filtered_chunks,
    }


def _clean_citations(text: str) -> str:
    """
    Clean up citation formats in text.
    Converts [Source: filename.txt] → removes them (numbered citations should be used instead).
    Also removes [Source: filename › Section] format.
    """
    import re
    # Remove [Source: anything] patterns
    cleaned = re.sub(r'\[Source:\s*[^\]]+\]', '', text)
    # Clean up double spaces left behind
    cleaned = re.sub(r'  +', ' ', cleaned)
    # Clean up space before periods
    cleaned = re.sub(r'\s+\.', '.', cleaned)
    return cleaned.strip()