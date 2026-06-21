"""
scripts/check_voyage.py

Diagnostic: probe Voyage AI embed + rerank endpoints SEPARATELY and report
which (if any) are blocked. Run this FROM RENDER (shell or one-off job) to
determine whether the Cloudflare IP block affects embeddings only, reranking
only, or both.

Usage:
    python scripts/check_voyage.py

Exit code 0 if both succeed, non-zero if either fails.
"""

from __future__ import annotations

import asyncio
import sys

from config import get_settings


async def _probe_embed(api_key: str, model: str) -> tuple[bool, str]:
    try:
        import voyageai

        client = voyageai.AsyncClient(api_key=api_key)
        result = await client.embed(texts=["ping"], model=model, input_type="query")
        dims = len(result.embeddings[0])
        return True, f"OK (dims={dims})"
    except Exception as e:  # noqa: BLE001 - diagnostic wants the raw error
        return False, f"{type(e).__name__}: {str(e)[:300]}"


async def _probe_rerank(api_key: str, model: str) -> tuple[bool, str]:
    try:
        import voyageai

        client = voyageai.AsyncClient(api_key=api_key)
        result = await client.rerank(
            query="ping",
            documents=["alpha", "beta"],
            model=model,
            top_k=1,
        )
        return True, f"OK (top_index={result.results[0].index})"
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {str(e)[:300]}"


async def main() -> int:
    settings = get_settings()
    if not settings.voyage_api_key or not settings.voyage_api_key.get_secret_value():
        print("VOYAGE_API_KEY not configured — cannot probe.")
        return 2

    api_key = settings.voyage_api_key.get_secret_value()

    embed_ok, embed_msg = await _probe_embed(api_key, settings.embedding_model_name)
    rerank_ok, rerank_msg = await _probe_rerank(api_key, settings.reranker_model_name)

    print("== Voyage AI connectivity probe =================")
    print(f"  embed  ({settings.embedding_model_name:<10}): {'PASS' if embed_ok else 'FAIL'}  {embed_msg}")
    print(f"  rerank ({settings.reranker_model_name:<10}): {'PASS' if rerank_ok else 'FAIL'}  {rerank_msg}")
    print("=================================================")

    if embed_ok and rerank_ok:
        print("Both reachable — no IP block detected from this host.")
    elif not embed_ok and not rerank_ok:
        print("Both blocked — switch embeddings to OpenAI AND disable/replace rerank.")
    elif embed_ok and not rerank_ok:
        print("Only rerank blocked — keep Voyage embeddings, disable/replace rerank.")
    else:
        print("Only embed blocked — switch embeddings to OpenAI, keep Voyage rerank.")

    return 0 if (embed_ok and rerank_ok) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
