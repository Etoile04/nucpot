"""Nucpot site customization — auto-loaded by Python at startup.

Three monkey-patches over LightRAG 1.5.4 (pin: docker/lightrag.Dockerfile):

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

3. **NFM-5126 (D-1b) — server-internal ollama hard-timeout envelope.**
   D-1 (NFM-5082, PR #1402) bounds the API-side ``nfm_db`` -> lightrag
   surface with a 90s x 3 jittered hang guard, but the LightRAG
   server-internal ollama calls sit outside it (NFM-5084 canary Finding B:
   the 03:30Z reingest burst wedged 3x with D-1 live). Call-site inventory
   against lightrag-hku 1.5.4:

   - Chat, native binding (``LLM_BINDING=ollama``): the server's
     ``create_llm_model_kwargs`` injects ``timeout=args.llm_timeout`` into
     every ``_ollama_model_if_cache`` call; with the ``LLM_TIMEOUT`` env
     unset that is ``DEFAULT_LLM_TIMEOUT = 240`` (constants.py). The prod
     container sets no ``LLM_TIMEOUT`` -> 240s per call.
   - Native embeddings (``EMBEDDING_BINDING=ollama``): the server's
     ``optimized_embedding_function`` ollama branch passes no ``timeout``,
     so ``ollama.AsyncClient(timeout=None)`` — unbounded.
   - OpenAI-compat embeddings against the host ollama server (the LIVE
     prod path: ``EMBEDDING_BINDING=openai`` +
     ``EMBEDDING_BINDING_HOST=http://host.docker.internal:11434/v1``):
     ``openai_embed`` receives no ``timeout`` kwarg and falls back to the
     OpenAI SDK default (600s) plus a tenacity retry ladder.

   The patch wraps all three with the D-1 envelope shape: per-attempt
   wall-clock budget (<= 90s, ``NFM_D1B_BUDGET_S``, hard-capped at 90 — a
   larger budget is an architect decision, not an env tweak), up to 3
   attempts with 200-800ms jittered backoff
   (``NFM_D1B_JITTER_MIN_S``/``NFM_D1B_JITTER_MAX_S``), a structured
   ``runner_hang_timeout`` audit record on exhaustion (NFM-4742
   vocabulary, logger ``nfm.d1b``), and a clean ``TimeoutError`` so the
   LightRAG pipeline marks the unit failed and continues. Non-timeout
   errors are NOT retried (D-1 semantics — they carry routing
   information). The compat-embed guard applies only to hosts listed in
   ``NFM_D1B_BOUND_EMBED_HOSTS`` (default ``host.docker.internal:11434``),
   so staging against the real OpenAI API is a byte-for-byte passthrough.
   Tighter caller budgets win (an explicit ``timeout=8`` stays 8s);
   ``timeout=0`` — which lightrag maps to "no timeout" — and missing/
   oversized values clamp to the budget. A drift tripwire warns at startup
   when the installed lightrag exposes an ollama call site outside the
   covered inventory (upgrade canary).

References:
- NFM-4525 issue (thinking patch)
- NFM-4521 (Path A — switched prod LLM_MODEL to qwen3.5:4b-nvfp4; thinking
  mode was the latent bug that surfaced under fresh (non-cached) queries)
- NFM-3404 (three-tier RAG latency framework — the thinking patch is what
  brings the post-Path-A cold-query latency budget back inside the gate)
- NFM-4822 issue (rerank pool cap; first attempt PR #1352 bounded the
  wrong things — see docker/lightrag/tests/test_rerank_pool_cap_patch.py)
- NFM-5126 issue (D-1b; see
  docker/lightrag/tests/test_d1b_ollama_timeout_envelope.py for the
  exhaustiveness guard) and NFM-5084 canary Finding B (origin evidence)
"""

import asyncio
import logging
import os
import random
import re
import time

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
    print("[nucmd-patch] LightRAG rerank pool cap armed (RERANK_POOL_CAP, unset/0 = disabled)")


# ---------------------------------------------------------------------------
# NFM-5126 (D-1b): server-internal ollama hard-timeout envelope
# ---------------------------------------------------------------------------
# Mirrors the API-side D-1 helper (apps/api/.../lightrag_client.py
# ``_request_with_hang_guard``): per-attempt ``asyncio.wait_for`` at the
# effective budget, retry on TimeoutError only, jittered backoff between
# attempts, structured audit record + clean ``TimeoutError`` on exhaustion.

_D1B_BUDGET_CAP_S = 90.0
_D1B_DEFAULT_BUDGET_S = 90.0
_D1B_MAX_ATTEMPTS = 3
_D1B_FAILURE_REASON = "runner_hang_timeout"
_D1B_LOGGER = logging.getLogger("nfm.d1b")

_D1B_CHAT_MARKER = "_nfm5126_d1b_chat_timeout_applied"
_D1B_OLLAMA_EMBED_MARKER = "_nfm5126_d1b_ollama_embed_timeout_applied"
_D1B_OPENAI_EMBED_MARKER = "_nfm5126_d1b_openai_embed_timeout_applied"

# Call sites the envelope covers (drift-tripwire baseline). Chat entries:
# ``ollama_model_complete`` is the server-facing entrypoint and delegates
# straight to ``_ollama_model_if_cache``, which is the single chat funnel.
_D1B_COVERED_OLLAMA_CALL_SITES = frozenset(
    {
        "_ollama_model_if_cache",
        "ollama_model_complete",
        "ollama_embed",
    }
)
_D1B_CALL_SITE_NAME_RE = re.compile(r"(_model_if_cache|_model_complete|_embed)$")
_D1B_BOUND_EMBED_HOSTS_DEFAULT = "host.docker.internal:11434"


def _d1b_budget_s() -> float:
    """Effective per-attempt budget ceiling (<= 90s, env-tunable down only).

    ``NFM_D1B_BUDGET_S`` may lower the budget (e.g. tests pin 0.05s); it
    can NEVER raise it past the D-1 90s ceiling — a larger server-internal
    budget is an architect decision (charter NFM-5079/NFM-5081), not an
    env tweak. Garbage / non-positive values fall back to the default.
    """
    raw = os.getenv("NFM_D1B_BUDGET_S", "")
    try:
        value = float(raw) if raw else _D1B_DEFAULT_BUDGET_S
    except ValueError:
        value = _D1B_DEFAULT_BUDGET_S
    if value <= 0:
        value = _D1B_DEFAULT_BUDGET_S
    return min(value, _D1B_BUDGET_CAP_S)


def _d1b_effective_budget_s(requested: object) -> float:
    """Resolve the per-call budget from the caller's ``timeout`` kwarg.

    Tighter explicit budgets win (``timeout=8`` stays 8s — the overlay
    stack's query-latency ceilings keep working). Missing, ``None``,
    ``0`` (lightrag maps 0 to "no timeout"), non-numeric, or above-cap
    values all clamp to the envelope budget — those are exactly the
    hang-relevant shapes observed in NFM-5084 Finding B.
    """
    budget = _d1b_budget_s()
    if isinstance(requested, (int, float)) and not isinstance(requested, bool):
        requested_f = float(requested)
        if 0 < requested_f <= budget:
            return requested_f
    return budget


def _d1b_jitter_s() -> float:
    """Backoff sleep between attempts, in [min, max] (default 0.2-0.8s)."""

    def _bound(name: str, default: float) -> float:
        raw = os.getenv(name, "")
        try:
            return float(raw) if raw else default
        except ValueError:
            return default

    lo = max(0.0, _bound("NFM_D1B_JITTER_MIN_S", 0.2))
    hi = max(lo, _bound("NFM_D1B_JITTER_MAX_S", 0.8))
    return random.uniform(lo, hi)


def _d1b_emit_hang_timeout_audit(
    *,
    call_site: str,
    host: str,
    attempts: int,
    elapsed_s: float,
    budget_s: float,
) -> None:
    """Structured audit record on envelope exhaustion (NFM-4742 vocabulary).

    Same shape as the API-side D-1 emitter: ``failure_reason=
    'runner_hang_timeout'`` rides the log record's ``extra`` so the audit
    collector can correlate without parsing message text, and the calling
    pipeline owns any DB write that consumes the row.
    """
    _D1B_LOGGER.error(
        (
            "lightrag server-internal hang timeout exhausted: "
            "call_site=%s host=%s attempts=%d budget_s=%.3f "
            "elapsed_s=%.3f failure_reason=%s"
        ),
        call_site,
        host,
        attempts,
        budget_s,
        elapsed_s,
        _D1B_FAILURE_REASON,
        extra={
            "failure_reason": _D1B_FAILURE_REASON,
            "audit_vocabulary": "NFM-4742",
            "charter": "NFM-5126",
            "layer": "lightrag-server-internal",
            "call_site": call_site,
            "host": host,
            "attempts": attempts,
            "budget_s": budget_s,
            "elapsed_s": elapsed_s,
        },
    )


def _d1b_make_guarded(
    orig,
    *,
    call_site: str,
    host_of,
    host_gate=None,
    forward_timeout: bool = True,
):
    """Wrap ``orig`` in the D-1b envelope.

    ``host_of(kwargs)`` resolves the target host (for the audit record);
    ``host_gate(host)`` optionally short-circuits the envelope — when it
    returns False the call is a verbatim passthrough (compat-embed guard
    for hosts off the bound list). ``forward_timeout=False`` keeps the
    caller's kwargs byte-for-byte and enforces the budget through
    ``asyncio.wait_for`` alone — for wrapped functions with a closed
    signature that rejects a ``timeout`` kwarg (``openai_embed`` 1.5.4).
    """

    async def _d1b_guarded(*args, **kwargs):
        host = host_of(kwargs)
        if host_gate is not None and not host_gate(host):
            return await orig(*args, **kwargs)

        effective = _d1b_effective_budget_s(kwargs.get("timeout"))
        call_kwargs = dict(kwargs)
        if forward_timeout:
            call_kwargs["timeout"] = effective
        started = time.monotonic()
        for attempt in range(1, _D1B_MAX_ATTEMPTS + 1):
            try:
                return await asyncio.wait_for(orig(*args, **call_kwargs), timeout=effective)
            except TimeoutError:
                _D1B_LOGGER.warning(
                    ("NFM-5126 D-1b: %s attempt %d/%d hung (budget_s=%.3f host=%s)"),
                    call_site,
                    attempt,
                    _D1B_MAX_ATTEMPTS,
                    effective,
                    host,
                )
                if attempt < _D1B_MAX_ATTEMPTS:
                    await asyncio.sleep(_d1b_jitter_s())
                    continue
                break
        elapsed_s = time.monotonic() - started
        _d1b_emit_hang_timeout_audit(
            call_site=call_site,
            host=host,
            attempts=_D1B_MAX_ATTEMPTS,
            elapsed_s=elapsed_s,
            budget_s=effective,
        )
        raise TimeoutError(
            f"LightRAG server-internal {call_site} hung for "
            f"{_D1B_MAX_ATTEMPTS}x{effective:.1f}s (= {elapsed_s:.1f}s "
            f"wall-clock); failure_reason={_D1B_FAILURE_REASON!r} "
            "(NFM-5126 D-1b)"
        )

    return _d1b_guarded


def _d1b_wrap_embed_attr(module, attr_name: str, marker: str, guarded) -> None:
    """Install ``guarded`` over an embed entrypoint in place.

    Handles both shapes the server consumes: an ``EmbeddingFunc`` instance
    (replace ``.func`` — the server's optimized wrapper calls it directly
    and ``__call__`` delegates dynamically) and a bare async callable
    (replace the module attribute). The marker always lands on the module
    so tests/staging can check patch state without touching the object.
    """
    embed_obj = getattr(module, attr_name, None)
    if embed_obj is None:
        return
    inner = getattr(embed_obj, "func", None)
    if inner is not None and callable(inner):
        embed_obj.func = guarded(inner)
    else:
        setattr(module, attr_name, guarded(embed_obj))
    setattr(module, marker, True)


def _d1b_drift_tripwire(module) -> None:
    """Warn when the installed lightrag exposes an uncovered ollama call site.

    The envelope covers a fixed inventory (see
    ``_D1B_COVERED_OLLAMA_CALL_SITES``). A lightrag upgrade that adds a
    new ``*_model_if_cache`` / ``*_model_complete`` / ``*_embed`` callable
    would reintroduce an unbounded call site silently; this tripwire puts
    the drift in the container startup log where the next deploy check
    (and the docker/lightrag test suite) can see it.
    """
    for name in dir(module):
        if name.startswith("_nfm") or name in _D1B_COVERED_OLLAMA_CALL_SITES:
            continue
        if not _D1B_CALL_SITE_NAME_RE.search(name):
            continue
        if not callable(getattr(module, name, None)):
            continue
        _D1B_LOGGER.warning(
            "NFM-5126 D-1b drift tripwire: lightrag.llm.ollama exposes "
            "call site %r outside the covered inventory (%s) — it is NOT "
            "enveloped; extend the patch or pin the lightrag version",
            name,
            sorted(_D1B_COVERED_OLLAMA_CALL_SITES),
        )


def _patch_lightrag_ollama_timeout_envelope() -> None:
    """NFM-5126 D-1b: bound the native-binding chat + embed call sites.

    Gated on an ollama binding being active (LLM or EMBEDDING) so staging
    never imports ``lightrag.llm.ollama`` — the staging image does not
    bake the ``ollama`` client package (NFM-4527) and the import would
    fail exactly like the pipmaster loop it fixed.
    """
    llm_binding = os.environ.get("LLM_BINDING", "").lower()
    embed_binding = os.environ.get("EMBEDDING_BINDING", "").lower()
    if llm_binding != "ollama" and embed_binding != "ollama":
        return

    try:
        from lightrag.llm import ollama as _ollama_mod
    except ImportError:
        return

    def _native_host_of(kwargs):
        return str(kwargs.get("host") or os.getenv("LLM_BINDING_HOST", ""))

    chat_orig = getattr(_ollama_mod, "_ollama_model_if_cache", None)
    if callable(chat_orig) and not getattr(_ollama_mod, _D1B_CHAT_MARKER, False):
        _ollama_mod._ollama_model_if_cache = _d1b_make_guarded(
            chat_orig,
            call_site="lightrag.llm.ollama._ollama_model_if_cache",
            host_of=_native_host_of,
        )
        setattr(_ollama_mod, _D1B_CHAT_MARKER, True)
        print(
            "[nucmd-patch] NFM-5126 D-1b: ollama chat calls enveloped "
            f"(<= {_d1b_budget_s():.0f}s x {_D1B_MAX_ATTEMPTS} jittered; "
            "240s default and 0/None unbounded shapes clamped)"
        )

    if not getattr(_ollama_mod, _D1B_OLLAMA_EMBED_MARKER, False):
        _d1b_wrap_embed_attr(
            _ollama_mod,
            "ollama_embed",
            _D1B_OLLAMA_EMBED_MARKER,
            lambda orig: _d1b_make_guarded(
                orig,
                call_site="lightrag.llm.ollama.ollama_embed",
                host_of=_native_host_of,
            ),
        )
        if getattr(_ollama_mod, _D1B_OLLAMA_EMBED_MARKER, False):
            print(
                "[nucmd-patch] NFM-5126 D-1b: ollama native embeddings "
                "enveloped (missing timeout kwarg injected)"
            )

    _d1b_drift_tripwire(_ollama_mod)


def _patch_lightrag_openai_embed_timeout_envelope() -> None:
    """NFM-5126 D-1b: bound the OpenAI-compat embed path when it targets
    the host ollama server (the live prod embedding configuration).

    Host-gated per call: hosts off ``NFM_D1B_BOUND_EMBED_HOSTS`` are a
    byte-for-byte passthrough, so staging against the real OpenAI API is
    untouched (its 600s SDK default + tenacity ladder stay stock).
    Setting the env to an empty value / ``off`` disables the guard.
    """
    bound_raw = os.getenv("NFM_D1B_BOUND_EMBED_HOSTS", _D1B_BOUND_EMBED_HOSTS_DEFAULT)
    entries = [e.strip() for e in bound_raw.split(",") if e.strip()]
    if not entries or entries[0].lower() in {"none", "off", "disabled"}:
        return

    try:
        from lightrag.llm import openai as _openai_mod
    except ImportError:
        return

    def _bound_hosts(host: str) -> bool:
        return any(entry in (host or "") for entry in entries)

    def _compat_host_of(kwargs):
        return str(kwargs.get("base_url") or os.getenv("EMBEDDING_BINDING_HOST", ""))

    if not getattr(_openai_mod, _D1B_OPENAI_EMBED_MARKER, False):
        _d1b_wrap_embed_attr(
            _openai_mod,
            "openai_embed",
            _D1B_OPENAI_EMBED_MARKER,
            lambda orig: _d1b_make_guarded(
                orig,
                call_site="lightrag.llm.openai.openai_embed",
                host_of=_compat_host_of,
                host_gate=_bound_hosts,
                forward_timeout=False,
            ),
        )
        if getattr(_openai_mod, _D1B_OPENAI_EMBED_MARKER, False):
            print(
                "[nucmd-patch] NFM-5126 D-1b: openai-compat embeddings "
                f"enveloped for bound hosts {entries} (passthrough "
                "elsewhere)"
            )


_patch_lightrag_ollama_thinking()
_patch_lightrag_rerank_pool_cap()
_patch_lightrag_ollama_timeout_envelope()
_patch_lightrag_openai_embed_timeout_envelope()
