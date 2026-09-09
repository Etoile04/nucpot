"""Nucpot site customization — auto-loaded by Python at startup.

Patches LightRAG's Ollama LLM binding to default ``think=False`` when the
prod lightrag sidecar runs with ``LLM_BINDING=ollama``. Without this patch,
``qwen3.5:4b-nvfp4`` (and other qwen3.x models with thinking baked into the
chat template) consume the entire output token budget on internal reasoning
before producing any user-visible content, so chat-completion requests
return ``finish_reason=length`` with empty ``content``. The wrapper then
hangs in the LightRAG query path and hits the 30 s ``NFM_LIGHTRAG_QUERY_TIMEOUT_S``
ceiling.

Background: NFM-4525 (follow-up to NFM-4521 Path A). Ollama's OpenAI-compat
endpoint (``/v1/chat/completions``) silently ignores ``chat_template_kwargs``,
``extra_body.think``, and ``options.think``. Only the native ``/api/chat``
endpoint honors the top-level ``think`` body parameter. So this patch is
gated on ``LLM_BINDING=ollama`` (which uses the native binding) and is a
no-op when ``LLM_BINDING=openai`` (which is what staging uses against the
real OpenAI API, where the model has no thinking mode to begin with).

To re-enable thinking on a per-call basis (not currently exposed through
env vars — would require wiring through LightRAG's role LLM kwargs),
override by passing ``think=True`` from the caller; ``setdefault`` keeps
the per-call value if one is supplied.

References:
- NFM-4525 issue (this fix)
- NFM-4521 (Path A — switched prod LLM_MODEL to qwen3.5:4b-nvfp4; thinking
  mode was the latent bug that surfaced under fresh (non-cached) queries)
- NFM-3404 (three-tier RAG latency framework — this patch is what brings
  the post-Path-A cold-query latency budget back inside the gate)
"""

import os

_PATCH_APPLIED_ATTR = "_nucmd_thinking_disabled_patch_applied"


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


_patch_lightrag_ollama_thinking()