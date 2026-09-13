"""Nucpot site customization — auto-loaded by Python at startup.

Two monkey-patches over LightRAG 1.5.4 (pin: docker/lightrag.Dockerfile):

1. **NFM-4525 — ollama thinking-mode default.** Patches LightRAG's Ollama
   LLM binding to default ``think=False`` when the prod lightrag sidecar
   runs with ``LLM_BINDING=ollama``. Without this patch,
   ``qwen3.5:4b-nvfp4`` (and other qwen3.x models with thinking baked into
   the chat template) consume the entire output token budget on internal
   reasoning before producing any user-visible content, so chat-completion
   requests return ``finish_reason=length`` with empty ``content``. The
   wrapper then hangs in the LightRAG query path and hits the
   ``NFM_LIGHTRAG_QUERY_TIMEOUT_S`` read-budget ceiling.

   Background: NFM-4525 (follow-up to NFM-4521 Path A). Ollama's
   OpenAI-compat endpoint (``/v1/chat/completions``) silently ignores
   ``chat_template_kwargs``, ``extra_body.think``, and ``options.think``.
   Only the native ``/api/chat`` endpoint honors the top-level ``think``
   body parameter. So this patch is gated on ``LLM_BINDING=ollama`` (which
   uses the native binding) and is a no-op when ``LLM_BINDING=openai``
   (which is what staging uses against the real OpenAI API, where the model
   has no thinking mode to begin with).

   To re-enable thinking on a per-call basis (not currently exposed through
   env vars — would require wiring through LightRAG's role LLM kwargs),
   override by passing ``think=True`` from the caller; ``setdefault`` keeps
   the per-call value if one is supplied.

2. **NFM-4822 — rerank pool cap.** LightRAG 1.5.4's
   ``process_chunks_unified`` reranks the FULL merged chunk pool BEFORE the
   ``chunk_top_k`` truncation, and the mix path's pool is entity-driven:
   top_k=16 still yielded 83 entity-related chunks (85 after round-robin
   merge with the naive pool) for the canonical UO2 query. ``top_n`` (=
   ``chunk_top_k``) caps what the reranker RETURNS, never what it SCORES —
   85 chunks against the serial llama-server reranker is 8-12s, blowing the
   8s ``NFM_LIGHTRAG_QUERY_TIMEOUT_S`` budget on every uncached mode=mix
   query (100% ILIKE fallback). ``priority_limit_async_func_call`` also
   arms the worker timeout at ``llm_timeout * 2`` (12s for
   RERANK_TIMEOUT=6), so timeout-based containment can never fire inside
   the budget either. This patch wraps
   ``lightrag.utils.apply_rerank_if_enabled`` to pre-truncate the pool to
   ``RERANK_POOL_CAP`` chunks (retrieval order) before scoring; the cap is
   read per call and unset/0 disables it, so staging keeps stock behavior.
   Prod compose pins 24 (measured ≈2.0s per pass @ ~76-92ms/chunk).

References:
- NFM-4525 issue (thinking patch)
- NFM-4521 (Path A — switched prod LLM_MODEL to qwen3.5:4b-nvfp4; thinking
  mode was the latent bug that surfaced under fresh (non-cached) queries)
- NFM-3404 (three-tier RAG latency framework — the thinking patch is what
  brings the post-Path-A cold-query latency budget back inside the gate)
- NFM-4822 issue (rerank pool cap; first attempt PR #1352 bounded the
  wrong things — see docker/lightrag/tests/test_rerank_pool_cap_patch.py)
"""

import os

_PATCH_APPLIED_ATTR = "_nucmd_thinking_disabled_patch_applied"
_RERANK_POOL_CAP_ATTR = "_nfm4822_rerank_pool_cap_applied"


def _patch_lightrag_ollama_thinking() -> None:
    binding = os.environ.get("LLM_BINDING", "").lower()
    if binding != "ollama":
        return

    try:
        from lightrag.llm import ollama as _ollama_mod
    except ImportError:
        return

    if getattr(_ollama_mod, _PATCH_APPLIED_ATTR, False):
        return

    _orig_if_cache = _ollama_mod._ollama_model_if_cache

    async def _patched_if_cache(*args, **kwargs):
        # Disable qwen3.5 thinking mode by default. ``setdefault`` preserves
        # any explicit per-call value (e.g. ``think=True`` from a future
        # LightRAG role-LLM kwargs override).
        kwargs.setdefault("think", False)
        return await _orig_if_cache(*args, **kwargs)

    _ollama_mod._ollama_model_if_cache = _patched_if_cache
    setattr(_ollama_mod, _PATCH_APPLIED_ATTR, True)
    print(
        "[nucmd-patch] LightRAG ollama binding patched: think=False default "
        "(per-call think=... overrides via setdefault)"
    )


def _patch_lightrag_rerank_pool_cap() -> None:
    """NFM-4822: cap the rerank candidate pool BEFORE scoring.

    LightRAG 1.5.4 sends the full merged chunk pool (entity-driven, 85
    chunks for the canonical mix query) to the reranker and only truncates
    to ``chunk_top_k`` afterwards. Pre-truncate to ``RERANK_POOL_CAP``
    chunks in retrieval order so each pass fits the 8s query budget.
    ``RERANK_POOL_CAP`` is read per call; unset/0 disables the cap.
    """
    try:
        from lightrag import utils as _utils_mod
    except ImportError:
        return

    if getattr(_utils_mod, _RERANK_POOL_CAP_ATTR, False):
        return

    _orig_apply_rerank = _utils_mod.apply_rerank_if_enabled

    async def _pool_capped_apply_rerank_if_enabled(
        query, retrieved_docs, global_config, enable_rerank=True, top_n=None
    ):
        try:
            cap = int(os.getenv("RERANK_POOL_CAP", "0") or 0)
        except ValueError:
            cap = 0
        if cap > 0 and enable_rerank and retrieved_docs and len(retrieved_docs) > cap:
            print(
                "[nucmd-patch] NFM-4822: rerank pool "
                f"{len(retrieved_docs)} -> {cap} (RERANK_POOL_CAP, "
                "retrieval order, pre-scoring)"
            )
            retrieved_docs = retrieved_docs[:cap]
            if top_n is not None and top_n > cap:
                top_n = cap
        return await _orig_apply_rerank(
            query=query,
            retrieved_docs=retrieved_docs,
            global_config=global_config,
            enable_rerank=enable_rerank,
            top_n=top_n,
        )

    _utils_mod.apply_rerank_if_enabled = _pool_capped_apply_rerank_if_enabled
    setattr(_utils_mod, _RERANK_POOL_CAP_ATTR, True)
    print(
        "[nucmd-patch] LightRAG rerank pool cap armed "
        "(RERANK_POOL_CAP, unset/0 = disabled)"
    )


_patch_lightrag_ollama_thinking()
_patch_lightrag_rerank_pool_cap()
