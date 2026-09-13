"""Static + unit guard for the NFM-4822 rerank pool cap patch.

Background
----------
NFM-4822 first attempted to bound rerank latency with LightRAG's own knobs
(CHUNK_TOP_K=6 / TOP_K=16 / RERANK_TIMEOUT=6, PR #1352). Post-deploy
verification (2026-09-13T18:40Z) proved that premise wrong twice:

1. **The pool is entity-driven, not knob-driven.** LightRAG 1.5.4's
   ``process_chunks_unified`` runs ``apply_rerank_if_enabled`` on the FULL
   merged chunk pool BEFORE the ``chunk_top_k`` truncation step, and the
   mix path's pool is built from entity-related chunks (36 entities → 83
   chunks → 85 after round-robin merge with the naive pool). ``top_n`` (=
   ``chunk_top_k``) only caps what the reranker RETURNS, never what it
   SCORES: 85 chunks x ~76-92ms/chunk against the serial llama-server
   ≈ 8-12s per pass — every uncached mode=mix query blew the 8s
   ``NFM_LIGHTRAG_QUERY_TIMEOUT_S`` budget and degraded to ILIKE.
2. **The wrapper timeout has a 2x buffer.**
   ``priority_limit_async_func_call`` sets
   ``max_execution_timeout = llm_timeout * 2``, so RERANK_TIMEOUT=6 arms a
   12s worker cap — containment cannot fire inside an 8s budget by
   construction.

The fix extends the NFM-4525 precedent: ``docker/lightrag/sitecustomize.py``
additionally wraps ``lightrag.utils.apply_rerank_if_enabled`` to pre-truncate
the pool to ``RERANK_POOL_CAP`` chunks (by retrieval order) before scoring.
The cap is env-gated per call — ``RERANK_POOL_CAP`` unset/0 disables it, so
staging (and any non-prod deployment) keeps stock behavior; prod compose
pins 24 (measured: 24x600c ≈ 2.0s, 30x600c = 2.51s, 16x600c = 1.22s).

What this test enforces
-----------------------
1. Loading ``sitecustomize.py`` installs an async wrapper over
   ``lightrag.utils.apply_rerank_if_enabled`` (idempotent, stamped).
2. The wrapper truncates oversized pools to the cap and clamps ``top_n``.
3. Pools at/below the cap, ``RERANK_POOL_CAP=0``/unset, and
   ``enable_rerank=False`` all pass through untouched.
4. ``docker-compose.prod.yml`` still wires ``RERANK_POOL_CAP`` with the
   prod default (dropping the env line would silently re-ship the bug).

Failure modes
-------------
- Patch deleted / never loads → 85-chunk pools return, every uncached mix
  query falls back to ILIKE (the exact NFM-4822 regression).
- Cap read at import time instead of call time → env changes require a
  container restart and the wrapper cannot be disabled per deployment.
- Compose env line dropped → prod runs with the cap disabled.
"""

from __future__ import annotations

import importlib.util
import inspect
import os
import re
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SITECUSTOMIZE_PATH = REPO_ROOT / "docker" / "lightrag" / "sitecustomize.py"
COMPOSE_PROD_PATH = REPO_ROOT / "docker-compose.prod.yml"

FAKE_UTILS_ATTR = "_nfm4822_rerank_pool_cap_applied"

_LOAD_COUNT = 0


def _install_wrapper(orig_mock: mock.AsyncMock) -> SimpleNamespace:
    """Load sitecustomize.py against a fresh fake ``lightrag.utils``.

    Returns the fake utils module; after loading,
    ``fake.apply_rerank_if_enabled`` is the wrapper installed around
    ``orig_mock``. ``RERANK_POOL_CAP`` is cleared at load time so module
    import is deterministic (the wrapper re-reads it on every call).
    """
    global _LOAD_COUNT
    _LOAD_COUNT += 1

    fake_utils = SimpleNamespace()
    fake_utils.apply_rerank_if_enabled = orig_mock
    fake_lightrag = SimpleNamespace(utils=fake_utils)

    saved = {
        name: sys.modules.get(name)
        for name in ("lightrag", "lightrag.utils")
    }
    sys.modules["lightrag"] = fake_lightrag  # type: ignore[assignment]
    sys.modules["lightrag.utils"] = fake_utils  # type: ignore[assignment]

    spec = importlib.util.spec_from_file_location(
        f"_nucmd_test_sitecustomize_rerank_{_LOAD_COUNT}", SITECUSTOMIZE_PATH
    )
    assert spec and spec.loader, "sitecustomize.py failed to load as a module spec"
    module = importlib.util.module_from_spec(spec)
    try:
        env = {k: v for k, v in os.environ.items() if k != "RERANK_POOL_CAP"}
        with mock.patch.dict(os.environ, env, clear=True):
            spec.loader.exec_module(module)
    finally:
        for name, original in saved.items():
            if original is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original

    return fake_utils


def _docs(count: int) -> list[dict]:
    return [{"content": f"chunk-{i}"} for i in range(count)]


# ---------------------------------------------------------------------------
# 1. Patch installation
# ---------------------------------------------------------------------------
def test_patch_installs_wrapper_and_stamps_utils():
    """Loading sitecustomize swaps apply_rerank_if_enabled for a wrapper."""
    fake_utils = _install_wrapper(mock.AsyncMock(return_value=[]))

    assert getattr(fake_utils, FAKE_UTILS_ATTR, False) is True, (
        "sitecustomize.py did not stamp lightrag.utils — the NFM-4822 pool "
        "cap patch is a silent no-op."
    )
    assert inspect.iscoroutinefunction(fake_utils.apply_rerank_if_enabled), (
        "patched apply_rerank_if_enabled must be an async wrapper"
    )


def test_patch_is_idempotent():
    """Reloading sitecustomize must not double-wrap the function."""
    fake_utils = _install_wrapper(mock.AsyncMock(return_value=[]))
    wrapper_once = fake_utils.apply_rerank_if_enabled

    # Second load against the same fake module: the stamp short-circuits.
    fake_lightrag = SimpleNamespace(utils=fake_utils)
    saved = {
        name: sys.modules.get(name)
        for name in ("lightrag", "lightrag.utils")
    }
    sys.modules["lightrag"] = fake_lightrag  # type: ignore[assignment]
    sys.modules["lightrag.utils"] = fake_utils  # type: ignore[assignment]
    spec = importlib.util.spec_from_file_location(
        "_nucmd_test_sitecustomize_rerank_idem", SITECUSTOMIZE_PATH
    )
    module = importlib.util.module_from_spec(spec)
    try:
        env = {k: v for k, v in os.environ.items() if k != "RERANK_POOL_CAP"}
        with mock.patch.dict(os.environ, env, clear=True):
            spec.loader.exec_module(module)
    finally:
        for name, original in saved.items():
            if original is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original

    assert fake_utils.apply_rerank_if_enabled is wrapper_once, (
        "second sitecustomize load must not wrap the wrapper again"
    )


# ---------------------------------------------------------------------------
# 2. Truncation behavior
# ---------------------------------------------------------------------------
async def _run_wrapper(fake_utils, env, **call_kwargs):
    wrapper = fake_utils.apply_rerank_if_enabled
    with mock.patch.dict(os.environ, env, clear=False):
        if "RERANK_POOL_CAP" not in env:
            os.environ.pop("RERANK_POOL_CAP", None)
        return await wrapper(**call_kwargs)


@pytest.mark.asyncio
async def test_pool_over_cap_is_truncated_in_retrieval_order():
    """85-chunk pool with RERANK_POOL_CAP=24 → reranker scores 24 only."""
    orig_mock = mock.AsyncMock(return_value=[])
    fake_utils = _install_wrapper(orig_mock)

    await _run_wrapper(
        fake_utils,
        {"RERANK_POOL_CAP": "24"},
        query="UO2 热导率",
        retrieved_docs=_docs(85),
        global_config={},
        enable_rerank=True,
        top_n=6,
    )

    orig_mock.assert_awaited_once()
    kwargs = orig_mock.await_args.kwargs
    forwarded = kwargs["retrieved_docs"]
    assert len(forwarded) == 24, (
        f"rerank pool must be truncated to RERANK_POOL_CAP=24, got {len(forwarded)}"
    )
    # Truncation keeps retrieval order (prefix), not a re-shuffled subset.
    assert [d["content"] for d in forwarded] == [f"chunk-{i}" for i in range(24)]
    assert kwargs["top_n"] == 6, "top_n below the cap must pass through unchanged"
    assert kwargs["enable_rerank"] is True


@pytest.mark.asyncio
async def test_top_n_above_cap_is_clamped():
    """cap=8 with top_n=16 must clamp top_n to the capped pool size."""
    orig_mock = mock.AsyncMock(return_value=[])
    fake_utils = _install_wrapper(orig_mock)

    await _run_wrapper(
        fake_utils,
        {"RERANK_POOL_CAP": "8"},
        query="q", retrieved_docs=_docs(20), global_config={},
        enable_rerank=True, top_n=16,
    )
    kwargs = orig_mock.await_args.kwargs
    assert len(kwargs["retrieved_docs"]) == 8
    assert kwargs["top_n"] == 8, "top_n above the cap must clamp to the cap"


@pytest.mark.asyncio
async def test_pool_at_or_below_cap_untouched():
    """6-chunk naive pool with cap=24 → forwarded whole, same object."""
    orig_mock = mock.AsyncMock(return_value=[])
    fake_utils = _install_wrapper(orig_mock)
    pool = _docs(6)

    await _run_wrapper(
        fake_utils,
        {"RERANK_POOL_CAP": "24"},
        query="q", retrieved_docs=pool, global_config={},
        enable_rerank=True, top_n=6,
    )
    assert orig_mock.await_args.kwargs["retrieved_docs"] is pool


@pytest.mark.asyncio
async def test_cap_zero_or_unset_disables_truncation():
    """RERANK_POOL_CAP unset or 0 → stock behavior (staging parity path)."""
    orig_mock = mock.AsyncMock(return_value=[])
    fake_utils = _install_wrapper(orig_mock)
    pool = _docs(85)

    await _run_wrapper(
        fake_utils,
        {},
        query="q", retrieved_docs=pool, global_config={},
        enable_rerank=True, top_n=6,
    )
    await _run_wrapper(
        fake_utils,
        {"RERANK_POOL_CAP": "0"},
        query="q", retrieved_docs=pool, global_config={},
        enable_rerank=True, top_n=6,
    )
    assert orig_mock.await_count == 2
    for call in orig_mock.await_args_list:
        assert len(call.kwargs["retrieved_docs"]) == 85


@pytest.mark.asyncio
async def test_rerank_disabled_skips_truncation():
    """enable_rerank=False → docs forwarded whole (rerank is a no-op anyway)."""
    orig_mock = mock.AsyncMock(return_value=[])
    fake_utils = _install_wrapper(orig_mock)

    await _run_wrapper(
        fake_utils,
        {"RERANK_POOL_CAP": "4"},
        query="q", retrieved_docs=_docs(10), global_config={},
        enable_rerank=False, top_n=6,
    )
    assert len(orig_mock.await_args.kwargs["retrieved_docs"]) == 10


# ---------------------------------------------------------------------------
# 3. Compose wiring
# ---------------------------------------------------------------------------
def test_prod_compose_sets_rerank_pool_cap_default():
    """docker-compose.prod.yml must keep RERANK_POOL_CAP wired (default 24)."""
    contents = COMPOSE_PROD_PATH.read_text()
    pattern = re.compile(
        r"^\s*RERANK_POOL_CAP:\s*\$\{PROD_LIGHTRAG_RERANK_POOL_CAP:-24\}\s*$",
        re.MULTILINE,
    )
    assert pattern.search(contents), (
        "docker-compose.prod.yml must set "
        "`RERANK_POOL_CAP: ${PROD_LIGHTRAG_RERANK_POOL_CAP:-24}` on the "
        "lightrag service — dropping it re-ships the NFM-4822 regression "
        "(85-chunk rerank pools vs the 8s query budget)."
    )
