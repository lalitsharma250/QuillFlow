"""
app/services/llm/prompts.py

All prompt templates used by QuillFlow graph nodes.

Design principles:
  1. Every prompt is a function that returns (system_prompt, user_message)
  2. No f-strings with complex logic — use clear template formatting
  3. Each prompt function documents its expected LLM output format
  4. Prompts are testable — you can assert on the generated strings

Prompt functions are named after the graph node that uses them:
  - router_prompt()   → Router node
  - planner_prompt()  → Planner node
  - writer_prompt()   → Writer node
  - reducer_prompt()  → Reducer node
  - evaluator_prompt() → Validator node
"""

from __future__ import annotations

from app.models.domain import ContentPlan, RetrievedChunk, SectionPlan


# ═══════════════════════════════════════════════════════════
# Router Node Prompt
# ═══════════════════════════════════════════════════════════


def router_prompt(query: str) -> tuple[str, str]:
    """
    Classify a query as 'simple' or 'complex'.

    Simple: Direct factual question, short answer expected
    Complex: Requires structured content, multiple sections, analysis

    Returns:
        (system_prompt, user_message)

    Expected LLM output (JSON):
        {"query_type": "simple"} or {"query_type": "complex"}
    """
    system = (
        "You are a query classifier. Determine whether a query needs a "
        "simple direct answer or a complex multi-section document.\n\n"
        "SIMPLE:\n"
        "- Questions: 'What is X?', 'How does X work?', 'Explain X'\n"
        "- Definitions, explanations, comparisons\n"
        "- Any query that can be answered in 1-3 paragraphs\n"
        "- Questions starting with How, What, Why, When, Where\n\n"
        "COMPLEX(structured multi-section output):\n"
        "- 'Write a guide/tutorial/article about...'\n"
        "- 'Create a comprehensive overview of...'\n"
        "- 'Write a detailed report on...'\n"
        "- Must contain explicit words like: guide, tutorial, article, "
        "report, document, write, create, draft\n\n"
        "When in doubt, choose SIMPLE.\n\n"
        "- 'What are the best practices for X?' → COMPLEX\n"
        "- Multi-part questions ('A and B and C') → COMPLEX\n"
        "- Questions needing examples, steps, or analysis → COMPLEX\n\n"
        "Respond with JSON only: {\"query_type\": \"simple\"} or "
        "{\"query_type\": \"complex\"}\n\n"
        "IMPORTANT: Respond with valid JSON only. No markdown, no explanation."
    )

    user = f"Classify this query:\n\n{query}"

    return system, user


# ═══════════════════════════════════════════════════════════
# Simple Answer Prompt (for simple queries — skip planner/writer)
# ═══════════════════════════════════════════════════════════


def simple_answer_prompt(
    query: str,
    context_chunks: list,
    history: list | None = None,
) -> tuple[str, str]:
    system = (
        "You are a knowledgeable assistant. Answer the user's question using "
        "ONLY the provided context. Be concise and accurate.\n\n"
        "Rules:\n"
        "1. Only use information from the provided context\n"
        "2. Use numbered citations like [1], [2] to reference sources\n"
        "   - [1] refers to the first context chunk, [2] to the second, etc.\n"
        "3. If the context doesn't contain relevant information, say so clearly\n"
        "4. Do NOT make up information not in the context\n"
        "5. Do NOT reference citation numbers that don't exist in the context\n"
        "6. Be concise — aim for 100-300 words\n"
        "7. Use markdown formatting where appropriate\n"
        "8. If the user's message is a follow-up, use conversation history for context\n"
    )

    # Filter chunks BEFORE formatting — only relevant ones get numbers
    filtered = filter_relevant_chunks(context_chunks)
    context_text = _format_context_numbered(filtered)

    # Build conversation history
    history_text = ""
    if history and len(history) > 0:
        history_parts = []
        for msg in history[-10:]:
            role_label = "User" if msg.get("role") == "user" else "Assistant"
            content = msg.get("content", "")
            if len(content) > 500:
                content = content[:500] + "..."
            history_parts.append(f"{role_label}: {content}")
        history_text = "\n\nConversation history:\n" + "\n".join(history_parts)

    user = (
        f"Context:\n{context_text}\n"
        f"{history_text}\n\n"
        f"---\n\n"
        f"Question: {query}"
    )

    return system, user

def _format_context_numbered(chunks: list) -> str:
    """Format pre-filtered context chunks with numbered references."""
    if not chunks:
        return "No relevant context available."

    parts = []
    for i, rc in enumerate(chunks, 1):
        chunk = rc.chunk if hasattr(rc, 'chunk') else rc
        text = chunk.text if hasattr(chunk, 'text') else str(chunk)
        filename = ''
        if hasattr(chunk, 'metadata') and hasattr(chunk.metadata, 'source_filename'):
            filename = f" (from: {chunk.metadata.source_filename})"

        parts.append(f"[{i}]{filename}:\n{text}")

    return "\n\n".join(parts)


# ═══════════════════════════════════════════════════════════
# Planner Node Prompt
# ═══════════════════════════════════════════════════════════


def planner_prompt(
    query: str,
    context_chunks: list,
    max_sections: int = 3,
) -> tuple[str, str]:
    system = (
        "You are a concise content planner. Given a user's request and relevant context, "
        "create a focused, structured content plan.\n\n"
        "Rules:\n"
        f"1. Create between 2 and {max_sections} sections (prefer fewer, more focused sections)\n"
        "2. Each section needs a clear heading, description, word budget, and key points\n"
        "3. Word budgets should be CONCISE:\n"
        "   - Introduction: 50-80 words\n"
        "   - Body sections: 80-150 words each\n"
        "   - Conclusion/summary: 50-80 words (optional — skip if not needed)\n"
        "4. Total word budget should be 200-400 words for most requests\n"
        "5. Sections should flow logically\n"
        "6. Key points should reference specific information from the context\n"
        "7. DO NOT pad content — every sentence should add value\n"
        "8. DO NOT include a conclusion section unless explicitly requested\n"
        "9. Writers will use numbered citations [1], [2] — NOT [Source: filename]\n"
        "10. target_audience should be one of: 'general', 'technical', 'executive'\n\n"
        "Respond with JSON only matching this schema:\n"
        "{\n"
        '  "title": "string",\n'
        '  "sections": [\n'
        "    {\n"
        '      "heading": "string",\n'
        '      "description": "string (instructions for the writer)",\n'
        '      "word_budget": number,\n'
        '      "key_points": ["string", ...]\n'
        "    }\n"
        "  ],\n"
        '  "total_word_budget": number,\n'
        '  "target_audience": "general|technical|executive"\n'
        "}"
    )

    filtered = filter_relevant_chunks(context_chunks)
    context_text = _format_context_numbered(filtered)

    user = (
        f"Context from knowledge base:\n{context_text}\n\n"
        f"---\n\n"
        f"User request: {query}\n\n"
        f"Create a concise content plan (aim for 200-400 words total, 2-3 sections):"
    )

    return system, user


# ═══════════════════════════════════════════════════════════
# Writer Node Prompt
# ═══════════════════════════════════════════════════════════


def writer_prompt(
    section,
    context_chunks: list,
    full_plan=None,
    preceding_sections: list | None = None,
) -> tuple[str, str]:
    """Generate a section of a complex document."""

    # Extract fields from section object
    heading = getattr(section, 'heading', str(section))
    description = getattr(section, 'description', '')
    key_points = getattr(section, 'key_points', [])
    word_budget = getattr(section, 'word_budget', 200)

    # Get plan metadata
    title = getattr(full_plan, 'title', '') if full_plan else ''
    target_audience = getattr(full_plan, 'target_audience', 'technical') if full_plan else 'technical'

    # Filter and format chunks with numbers
    filtered = filter_relevant_chunks(context_chunks)
    context_text = _format_context_numbered(filtered)

    system = (
        f"You are a technical writer. Write a section for a document.\n\n"
        f"Rules:\n"
        f"1. Write approximately {word_budget} words\n"
        f"2. Use numbered citations [1], [2] to reference sources from the context\n"
        f"3. Do NOT use [Source: filename] format — only use [1], [2] etc.\n"
        f"4. Only cite sources that exist in the provided context\n"
        f"5. Target audience: {target_audience}\n"
        f"6. Use markdown formatting (bold, lists) where appropriate\n"
        f"7. Do NOT include the section heading — it will be added automatically\n"
        f"8. Be concise and factual — every sentence should add value\n\n"
        f"Respond with JSON: {{\"heading\": \"{heading}\", \"content\": \"your content here\"}}"
    )

    key_points_text = "\n".join(f"- {kp}" for kp in key_points) if key_points else "None specified"

    # Build preceding sections context for coherence
    preceding_text = ""
    if preceding_sections:
        preceding_text = "\n\nPreceding sections (for context, do not repeat):\n"
        for prev in preceding_sections[-2:]:
            truncated = prev[:300] + "..." if len(prev) > 300 else prev
            preceding_text += f"---\n{truncated}\n"

    user = (
        f"Context:\n{context_text}\n\n"
        f"---\n\n"
        f"Document title: {title}\n"
        f"Section heading: {heading}\n"
        f"Description: {description}\n"
        f"Key points to cover:\n{key_points_text}\n"
        f"Word budget: {word_budget} words\n"
        f"{preceding_text}\n\n"
        f"Write this section:"
    )

    return system, user

# ═══════════════════════════════════════════════════════════
# Reducer Node Prompt
# ═══════════════════════════════════════════════════════════


def reducer_prompt(
    title: str,
    section_drafts: list[dict[str, str]],
    target_audience: str = "general",
) -> tuple[str, str]:
    """
    Merge and polish section drafts into a final cohesive document.

    The reducer:
      - Ensures smooth transitions between sections
      - Removes redundancy across sections
      - Adds an executive summary if appropriate
      - Standardizes citation format
      - Does NOT rewrite sections — just polishes and connects

    Returns:
        (system_prompt, user_message)

    Expected LLM output:
        Plain text — the final merged document
    """
    system = (
        "You are an editor. You receive section drafts of a document and must "
        "merge them into a polished, cohesive final document.\n\n"
        "Rules:\n"
        "1. Preserve the content and structure of each section\n"
        "2. Add smooth transitions between sections\n"
        "3. Remove any redundancy (if two sections say the same thing)\n"
        "4. Standardize citation format: [Source: filename, page]\n"
        "5. Ensure consistent tone throughout\n"
        "6. Add a brief introduction if the first section jumps in too abruptly\n"
        "7. Do NOT add information that wasn't in the drafts\n"
        "8. Do NOT significantly rewrite sections — polish, don't replace\n"
        f"9. Target audience: {target_audience}\n\n"
        "Output the final document as plain text with markdown formatting "
        "(headings, bold, lists) where appropriate."
    )

    # Build the drafts text
    drafts_text = f"# {title}\n\n"
    for draft in section_drafts:
        drafts_text += f"## {draft['heading']}\n\n"
        drafts_text += f"{draft['content']}\n\n"
        drafts_text += "---\n\n"

    user = (
        f"Here are the section drafts to merge:\n\n"
        f"{drafts_text}\n"
        f"Merge these into a polished final document:"
    )

    return system, user


# ═══════════════════════════════════════════════════════════
# Evaluator Prompt (for Validator node)
# ═══════════════════════════════════════════════════════════


def faithfulness_check_prompt(
    query: str,
    context_chunks: list[RetrievedChunk],
    answer: str,
) -> tuple[str, str]:
    """
    Evaluate whether an answer is faithful to the provided context.

    Returns:
        (system_prompt, user_message)

    Expected LLM output (JSON):
        {
            "faithfulness_score": 0.85,
            "reasoning": "The answer accurately reflects...",
            "unsupported_claims": ["claim1", ...]
        }
    """
    system = (
        "You are a factual accuracy evaluator. Given a question, context, and "
        "an answer, evaluate whether the answer is faithful to the context.\n\n"
        "Scoring:\n"
        "- 1.0: Every claim in the answer is supported by the context\n"
        "- 0.7-0.9: Most claims supported, minor unsupported details\n"
        "- 0.4-0.6: Mix of supported and unsupported claims\n"
        "- 0.0-0.3: Mostly fabricated or contradicts the context\n\n"
        "Respond with JSON only:\n"
        "{\n"
        '  "faithfulness_score": number (0.0-1.0),\n'
        '  "reasoning": "string explaining your evaluation",\n'
        '  "unsupported_claims": ["list of claims not in context"]\n'
        "}"
    )

    context_text = _format_context(context_chunks)

    user = (
        f"Context:\n{context_text}\n\n"
        f"---\n\n"
        f"Question: {query}\n\n"
        f"Answer to evaluate:\n{answer}\n\n"
        f"Evaluate the faithfulness of this answer:"
    )

    return system, user


def relevancy_check_prompt(
    query: str,
    answer: str,
) -> tuple[str, str]:
    """
    Evaluate whether an answer is relevant to the query.

    Returns:
        (system_prompt, user_message)

    Expected LLM output (JSON):
        {
            "relevancy_score": 0.9,
            "reasoning": "The answer directly addresses..."
        }
    """
    system = (
        "You are a relevancy evaluator. Given a question and an answer, "
        "evaluate whether the answer actually addresses the question.\n\n"
        "Scoring:\n"
        "- 1.0: Answer directly and completely addresses the question\n"
        "- 0.7-0.9: Answer mostly addresses the question with minor gaps\n"
        "- 0.4-0.6: Answer partially addresses the question\n"
        "- 0.0-0.3: Answer is off-topic or doesn't address the question\n\n"
        "Respond with JSON only:\n"
        "{\n"
        '  "relevancy_score": number (0.0-1.0),\n'
        '  "reasoning": "string explaining your evaluation"\n'
        "}"
    )

    user = (
        f"Question: {query}\n\n"
        f"Answer to evaluate:\n{answer}\n\n"
        f"Evaluate the relevancy of this answer:"
    )

    return system, user


# ═══════════════════════════════════════════════════════════
# Shared Helpers
# ═══════════════════════════════════════════════════════════


def _format_context(chunks: list[RetrievedChunk], max_chunks: int = 10) -> str:
    """
    Format retrieved chunks into a readable context block for prompts.

    Each chunk is labeled with its source for citation.
    Chunks are ordered by relevance (highest first).
    """
    if not chunks:
        return "(No context available)"

    parts = []
    for i, rc in enumerate(chunks[:max_chunks], 1):
        source = rc.source
        score = f"{rc.score:.2f}"
        parts.append(
            f"[Chunk {i}] (Source: {source}, Relevance: {score})\n"
            f"{rc.chunk.text}"
        )

    return "\n\n".join(parts)

def query_rewrite_prompt(
    query: str,
    history: list[dict],
) -> tuple[str, str]:
    """
    Rewrite a vague follow-up query into a standalone search query
    using conversation history for context.
    
    Returns:
        (system_prompt, user_message)
    """
    system = (
        "You are a query rewriter. Your job is to rewrite a user's follow-up message "
        "into a standalone search query that can be used to search a knowledge base.\n\n"
        "Rules:\n"
        "1. If the query is already specific and standalone, return it unchanged\n"
        "2. If the query is vague (like 'yes', 'tell me more', 'explain that'), "
        "use the conversation history to understand what the user is referring to\n"
        "3. The rewritten query should be a clear, specific search query\n"
        "4. Keep it concise — under 50 words\n"
        "5. Do NOT answer the question — just rewrite it for search\n"
        "6. Return ONLY the rewritten query, nothing else\n"
    )

    # Build conversation context
    history_text = ""
    for msg in history[-6:]:
        role = "User" if msg.get("role") == "user" else "Assistant"
        content = msg.get("content", "")[:300]
        history_text += f"{role}: {content}\n"

    user = (
        f"Conversation history:\n{history_text}\n"
        f"---\n"
        f"Latest user message: {query}\n\n"
        f"Rewrite this into a standalone search query:"
    )

    return system, user

RELEVANCE_THRESHOLD = 0.1


def filter_relevant_chunks(chunks: list, min_score: float = RELEVANCE_THRESHOLD) -> list:
    """
    Filter chunks by relevance score.
    Used BEFORE sending to LLM and BEFORE building source references.
    Ensures citation numbers [1], [2] match displayed sources.
    """
    filtered = [rc for rc in chunks if getattr(rc, 'score', 1.0) >= min_score]
    # Always keep at least 1 chunk
    if not filtered and chunks:
        filtered = [chunks[0]]
    return filtered