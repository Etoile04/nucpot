"""D-1b guard: hard-timeout envelope over LightRAG server-internal ollama calls.

Background (NFM-5126, follow-up to NFM-5084 canary Finding B)
------------------------------------------------------------
D-1 (NFM-5082, PR #1402) bounds the **API-side** ``nfm_db`` -> lightrag
call surface with a 90s x 3 jittered hang envelope
(``apps/api/src/nfm_db/services/lightrag_client.py``). The LightRAG
**server-internal** ollama calls sit outside that envelope:

1. LLM chat (``LLM_BINDING=ollama``, native binding):
   ``lightrag/llm/ollama.py::_ollama_model_if_cache`` receives
   ``kwargs['timeout']`` from the server's ``create_llm_model_kwargs``
   (lightrag_server.py), which injects ``args.llm_timeout`` —
   ``DEFAULT_LLM_TIMEOUT = 240`` (constants.py:284) when the ``LLM_TIMEOUT``
   env is unset. The live prod container
   (``docker inspect nucpot-prod-lightrag``) sets no ``LLM_TIMEOUT``, so
   every entity-extraction / summary / keyword / query-generation call
   runs at ``timeout=240``.
2. Native ollama embeddings (``EMBEDDING_BINDING=ollama``):
   ``ollama_embed`` pops ``timeout`` from kwargs; the server's
   ``optimized_embedding_function`` ollama branch never passes one, so
   ``ollama.AsyncClient(timeout=None)`` — an unbounded HTTP wait.
3. OpenAI-compat embeddings against the host ollama server (the LIVE prod
   embedding path: ``EMBEDDING_BINDING=openai`` +
   ``EMBEDDING_BINDING_HOST=http://host.docker.internal:11434/v1``):
   ``lightrag/llm/openai.py::openai_embed`` gets no ``timeout`` kwarg and
   falls back to the OpenAI SDK default (600s), with its own tenacity
   retry ladder on top.

``docker/lightrag/sitecustomize.py`` patch #3 (NFM-5126) wraps all three
call sites with the same envelope shape as D-1: per-attempt wall-clock
budget (<= 90s, ``NFM_D1B_BUDGET_S``), up to 3 attempts with jittered
backoff, structured ``runner_hang_timeout`` audit record on exhaustion
(NFM-4742 vocabulary), and a clean ``TimeoutError`` so the LightRAG
pipeline marks the unit failed and continues instead of hanging.

What this test enforces
-----------------------
1. All three inventory call sites are wrapped when the relevant bindings
   are in play, and left untouched otherwise (staging must stay stock).
2. The clamp: ``timeout`` kwargs of 240 / None / 0 / missing become the
   90s budget; tighter caller budgets (e.g. 8s) win.
3. The exhaustion path: 3 attempts, jittered backoff, structured audit
   record with ``failure_reason='runner_hang_timeout'``, then a clean
   ``TimeoutError``; a subsequent call still succeeds (pipeline
   continues). Non-timeout errors propagate unretried (D-1 semantics).
4. The compat-embed guard applies ONLY to hosts listed in
   ``NFM_D1B_BOUND_EMBED_HOSTS`` — real-OpenAI staging is a passthrough.
5. Exhaustiveness: the prod compose pins explicit bounded env
   (``LLM_TIMEOUT`` <= 90, ``NFM_D1B_BOUND_EMBED_HOSTS``) so the library
   240s default cannot silently return, and a drift tripwire warns when
   the installed lightrag exposes an ollama call site outside the
   covered inventory (upgrade canary).
"""

from __future__ import annotations

import asyncio
import importlib.util
import logging
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

AUDIT_LOGGER_NAME = "nfm.d1b"

_CHAT_MARKER = "_nfm5126_d1b_chat_timeout_applied"
_OLLAMA_EMBED_MARKER = "_nfm5126_d1b_ollama_embed_timeout_applied"
_OPENAI_EMBED_MARKER = "_nfm5126_d1b_openai_embed_timeout_applied"


async def _hang(*args, **kwargs):
    """Stand-in for a wedged runner: never produces a first byte."""
    await asyncio.sleep(30)


def _faithful_openai_embed(mock_obj):
    """Adapt an AsyncMock into the real ``openai_embed`` call shape.

    The production module-level ``openai_embed`` (lightrag-hku 1.5.4,
    lightrag/llm/openai.py) is an ``EmbeddingFunc`` whose ``.func`` is a
    plain tenacity-wrapped async function with a CLOSED parameter list:
    no ``timeout`` parameter and no ``**kwargs``. A wrapper that forwards
    a ``timeout`` kwarg raises ``TypeError`` on every call — so the fake
    must mirror that closed signature instead of accepting ``**kwargs``
    (an open adapter would silently swallow kwarg-injection bugs the real
    library rejects). Tests assert on the underlying mock's kwargs.
    """

    async def _impl(
        texts,
        model="text-embedding-3-small",
        base_url=None,
        api_key=None,
        embedding_dim=None,
        max_token_size=None,
        client_configs=None,
        token_tracker=None,
        use_azure=False,
        azure_deployment=None,
        api_version=None,
        context="document",
        query_prefix=None,
        document_prefix=None,
    ):
        return await mock_obj(
            texts=texts,
            model=model,
            base_url=base_url,
            api_key=api_key,
            embedding_dim=embedding_dim,
            max_token_size=max_token_size,
            client_configs=client_configs,
            token_tracker=token_tracker,
            use_azure=use_azure,
            azure_deployment=azure_deployment,
            api_version=api_version,
            context=context,
            query_prefix=query_prefix,
            document_prefix=document_prefix,
        )

    return _impl


class _FakeEmbeddingFunc:
    """Minimal stand-in for ``lightrag.utils.EmbeddingFunc``.

    Mirrors the real shape that matters here: metadata attrs live on the
    instance, ``__call__`` delegates dynamically to ``self.func`` (so a
    wrapper that replaces ``.func`` covers both call shapes), and the
    server's optimized embedding path calls ``.func`` directly.
    """

    embedding_dim = 1024
    max_token_size = 8192
    model_name = "bge-m3:latest"
    send_dimensions = False
    supports_asymmetric = True

    def __init__(self, result=None, func_impl=None) -> None:
        self.func = func_impl or mock.AsyncMock(
            return_value=result if result is not None else [[0.1]]
        )

    async def __call__(self, *args, **kwargs):
        return await self.func(*args, **kwargs)


def _build_fake_modules(chat_impl=None, openai_embed_impl=None, embed_func_impl=None):
    """Fake ``lightrag`` package exposing the llm.ollama / llm.openai /
    utils surfaces the sitecustomize patches touch.

    Tests that need to assert on call args keep their own reference to the
    impl they pass in (the patch replaces the module attributes, so the
    only stable handle on the original is the caller's own).
    """
    fake_ollama = SimpleNamespace()
    fake_ollama._ollama_model_if_cache = chat_impl or mock.AsyncMock(return_value="chat-ok")
    fake_ollama.ollama_model_complete = mock.AsyncMock(return_value="chat-ok")
    fake_ollama.ollama_embed = _FakeEmbeddingFunc(result=None, func_impl=embed_func_impl)

    fake_openai = SimpleNamespace()
    fake_openai.openai_embed = openai_embed_impl or mock.AsyncMock(return_value=[[0.75, 0.9]])

    fake_utils = SimpleNamespace()
    fake_utils.apply_rerank_if_enabled = mock.AsyncMock(return_value=None)

    fake_pkg = SimpleNamespace()
    fake_pkg.llm = SimpleNamespace(ollama=fake_ollama, openai=fake_openai)
    fake_pkg.utils = fake_utils
    return fake_pkg, fake_ollama, fake_openai


_SWAP_MODULES = (
    "docker.lightrag.sitecustomize",
    "lightrag",
    "lightrag.llm",
    "lightrag.llm.ollama",
    "lightrag.llm.openai",
    "lightrag.utils",
)

_CONTROLLED_ENV_KEYS = (
    "LLM_BINDING",
    "EMBEDDING_BINDING",
    "EMBEDDING_BINDING_HOST",
    "NFM_D1B_BUDGET_S",
    "NFM_D1B_JITTER_MIN_S",
    "NFM_D1B_JITTER_MAX_S",
    "NFM_D1B_BOUND_EMBED_HOSTS",
)


def _load_sitecustomize(
    env_overrides: dict[str, str | None],
    chat_impl=None,
    openai_embed_impl=None,
    embed_func_impl=None,
):
    """Load ``docker/lightrag/sitecustomize.py`` against fake lightrag modules.

    ``env_overrides`` maps env var names to values (``None`` deletes the
    var). Every key in ``_CONTROLLED_ENV_KEYS`` not overridden is removed
    so tests are hermetic against the host environment.
    """
    fake_pkg, fake_ollama, fake_openai = _build_fake_modules(
        chat_impl=chat_impl,
        openai_embed_impl=openai_embed_impl,
        embed_func_impl=embed_func_impl,
    )

    saved = {name: sys.modules.get(name) for name in _SWAP_MODULES}
    sys.modules["lightrag"] = fake_pkg
    sys.modules["lightrag.llm"] = fake_pkg.llm
    sys.modules["lightrag.llm.ollama"] = fake_ollama
    sys.modules["lightrag.llm.openai"] = fake_openai
    sys.modules["lightrag.utils"] = fake_pkg.utils

    spec = importlib.util.spec_from_file_location("_nfm5126_test_sitecustomize", SITECUSTOMIZE_PATH)
    assert spec and spec.loader, "sitecustomize.py failed to load as a module spec"
    module = importlib.util.module_from_spec(spec)

    env = {k: v for k, v in os.environ.items() if k not in _CONTROLLED_ENV_KEYS}
    for key, value in env_overrides.items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value

    try:
        with mock.patch.dict(os.environ, env, clear=True):
            spec.loader.exec_module(module)
    finally:
        for name, original in saved.items():
            if original is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original

    return module, fake_ollama, fake_openai


def _prod_env(**extra) -> dict[str, str | None]:
    """Env mirroring the live prod container for the D-1b patch."""
    env: dict[str, str | None] = {
        "LLM_BINDING": "ollama",
        "EMBEDDING_BINDING": "openai",
        "EMBEDDING_BINDING_HOST": "http://host.docker.internal:11434/v1",
        "NFM_D1B_JITTER_MIN_S": "0",
        "NFM_D1B_JITTER_MAX_S": "0",
    }
    env.update(extra)
    return env


# ---------------------------------------------------------------------------
# 1. Wrap installation (inventory call sites)
# ---------------------------------------------------------------------------
def test_chat_call_site_wrapped_when_llm_binding_is_ollama():
    _, fake_ollama, _ = _load_sitecustomize(_prod_env())
    assert getattr(fake_ollama, _CHAT_MARKER, False) is True, (
        "sitecustomize.py did not stamp the D-1b chat patch marker on "
        "lightrag.llm.ollama — the timeout=240 call site is unguarded."
    )


def test_ollama_embed_call_site_wrapped_when_llm_binding_is_ollama():
    _, fake_ollama, _ = _load_sitecustomize(_prod_env())
    assert getattr(fake_ollama, _OLLAMA_EMBED_MARKER, False) is True, (
        "sitecustomize.py did not stamp the D-1b ollama_embed patch marker "
        "— the unbounded native-embedding call site is unguarded."
    )


def test_ollama_embed_wraps_inner_func_preserving_metadata():
    _, fake_ollama, _ = _load_sitecustomize(_prod_env())
    embed = fake_ollama.ollama_embed
    assert getattr(embed, "embedding_dim", None) == 1024, (
        "D-1b must wrap the EmbeddingFunc in place (replace .func) so the "
        "decorator metadata (embedding_dim etc.) survives."
    )


def test_openai_embed_call_site_wrapped_with_default_bound_hosts():
    _, _, fake_openai = _load_sitecustomize(_prod_env())
    assert getattr(fake_openai, _OPENAI_EMBED_MARKER, False) is True, (
        "sitecustomize.py did not stamp the D-1b openai_embed patch marker "
        "— the OpenAI-compat-against-ollama embedding call site (live prod "
        "path, 600s SDK default) is unguarded."
    )


def test_native_module_patch_is_noop_when_no_ollama_binding():
    """Staging (LLM_BINDING=openai, EMBEDDING_BINDING=openai) must not
    even import lightrag.llm.ollama (the staging image has no ``ollama``
    package — NFM-4527); patch markers must stay unset."""
    _, fake_ollama, _ = _load_sitecustomize(
        {
            "LLM_BINDING": "openai",
            "EMBEDDING_BINDING": "openai",
            "NFM_D1B_JITTER_MIN_S": "0",
            "NFM_D1B_JITTER_MAX_S": "0",
        }
    )
    assert getattr(fake_ollama, _CHAT_MARKER, False) is False
    assert getattr(fake_ollama, _OLLAMA_EMBED_MARKER, False) is False


# ---------------------------------------------------------------------------
# 2. The clamp (240 / None / 0 / missing -> budget; tighter wins)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("requested", "expected"),
    [
        pytest.param(240, 90, id="finding-B-240-clamped-to-90"),
        pytest.param(None, 90, id="missing-defaults-to-90"),
        pytest.param(0, 90, id="zero-infinite-trap-defaults-to-90"),
        pytest.param(120, 90, id="above-cap-clamped"),
        pytest.param(90, 90, id="at-cap-kept"),
        pytest.param(8, 8, id="tighter-caller-budget-wins"),
    ],
)
@pytest.mark.asyncio
async def test_chat_timeout_clamp(requested, expected):
    orig = mock.AsyncMock(return_value="chat-ok")
    _, fake_ollama, _ = _load_sitecustomize(_prod_env(), chat_impl=orig)
    wrapped = fake_ollama._ollama_model_if_cache
    kwargs = {} if requested is None else {"timeout": requested}
    result = await wrapped("qwen3.5:4b-nvfp4", "say hi", **kwargs)
    assert result == "chat-ok"
    forwarded = orig.await_args.kwargs
    assert forwarded.get("timeout") == expected, (
        f"timeout={requested!r} must clamp to {expected}s; forwarded: {forwarded!r}"
    )


@pytest.mark.asyncio
async def test_ollama_embed_timeout_injected_when_missing():
    """The server's ollama-embed branch never passes ``timeout`` — the
    wrapper must inject the budget so the AsyncClient is bounded."""
    orig = mock.AsyncMock(return_value=[[0.25, 0.5]])
    _, fake_ollama, _ = _load_sitecustomize(_prod_env(), embed_func_impl=orig)
    embed = fake_ollama.ollama_embed
    await embed(["hello"])
    forwarded = orig.await_args.kwargs
    assert forwarded.get("timeout") == 90, (
        f"ollama_embed must inject timeout=90 when absent; forwarded: {forwarded!r}"
    )


@pytest.mark.asyncio
async def test_chat_success_passthrough():
    orig = mock.AsyncMock(return_value="chat-ok")
    _, fake_ollama, _ = _load_sitecustomize(_prod_env(), chat_impl=orig)
    wrapped = fake_ollama._ollama_model_if_cache
    assert await wrapped("m", "p", timeout=30) == "chat-ok"
    assert await wrapped("m", "p2", timeout=30) == "chat-ok"
    assert orig.await_count == 2


# ---------------------------------------------------------------------------
# 3. Exhaustion: 3 attempts, audit row, clean raise, pipeline continues
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_chat_hang_exhaustion_audits_and_raises_cleanly(caplog):
    env = _prod_env(NFM_D1B_BUDGET_S="0.05")
    _, fake_ollama, _ = _load_sitecustomize(env, chat_impl=_hang)
    wrapped = fake_ollama._ollama_model_if_cache

    # The wrapper resolves the budget from the environment per call
    # (mirroring RERANK_POOL_CAP), so the call must run under the same
    # patched env as the load.
    with (
        mock.patch.dict(os.environ, env, clear=True),
        caplog.at_level(logging.ERROR, logger=AUDIT_LOGGER_NAME),
        pytest.raises(TimeoutError) as excinfo,
    ):
        await wrapped("m", "p", timeout=240)

    assert "runner_hang_timeout" in str(excinfo.value), (
        "exhaustion must carry the NFM-4742 failure_reason in the message"
    )
    audit = [
        r for r in caplog.records if getattr(r, "failure_reason", None) == "runner_hang_timeout"
    ]
    assert audit, "no runner_hang_timeout audit record was emitted on exhaustion"
    record = audit[-1]
    assert getattr(record, "audit_vocabulary", None) == "NFM-4742"
    assert getattr(record, "charter", None) == "NFM-5126"
    assert getattr(record, "attempts", None) == 3
    assert getattr(record, "budget_s", None) == pytest.approx(0.05)


@pytest.mark.asyncio
async def test_chat_hang_retries_then_pipeline_continues():
    """After exhaustion the wrapper is reusable — the next (healthy) call
    succeeds without re-raising (pipeline continues)."""
    calls = {"n": 0}

    async def hang_first_three(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] <= 3:
            await asyncio.sleep(30)
        return "recovered"

    _, fake_ollama, _ = _load_sitecustomize(
        _prod_env(NFM_D1B_BUDGET_S="0.05"), chat_impl=hang_first_three
    )
    wrapped = fake_ollama._ollama_model_if_cache
    env = _prod_env(NFM_D1B_BUDGET_S="0.05")
    with mock.patch.dict(os.environ, env, clear=True):
        with pytest.raises(TimeoutError):
            await wrapped("m", "p", timeout=0.05)
        assert calls["n"] == 3, f"expected 3 timed-out attempts, saw {calls['n']}"
        # Pipeline continues: the next invocation runs fresh attempts and
        # the 4th underlying call (healthy) succeeds within the budget.
        assert await wrapped("m", "p", timeout=0.05) == "recovered"


@pytest.mark.asyncio
async def test_chat_non_timeout_error_propagates_unretried():
    """D-1 semantics: only wall-clock timeouts are retried; definitive
    failures surface immediately so LightRAG's own error routing keeps
    working."""

    async def boom(*args, **kwargs):
        raise ValueError("definitive-failure")

    _, fake_ollama, _ = _load_sitecustomize(_prod_env(), chat_impl=boom)
    wrapped = fake_ollama._ollama_model_if_cache
    with pytest.raises(ValueError, match="definitive-failure"):
        await wrapped("m", "p", timeout=0.05)


# ---------------------------------------------------------------------------
# 4. Compat-embed host gating (staging passthrough)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_openai_embed_hang_audits_for_bound_host(caplog):
    env = _prod_env(NFM_D1B_BUDGET_S="0.05")
    _, _, fake_openai = _load_sitecustomize(env, openai_embed_impl=_hang)
    wrapped = fake_openai.openai_embed

    with (
        mock.patch.dict(os.environ, env, clear=True),
        caplog.at_level(logging.ERROR, logger=AUDIT_LOGGER_NAME),
        pytest.raises(TimeoutError),
    ):
        await wrapped(["t"], base_url="http://host.docker.internal:11434/v1")

    audit = [
        r for r in caplog.records if getattr(r, "failure_reason", None) == "runner_hang_timeout"
    ]
    assert audit, (
        "a hang against a BOUND host (host ollama) must produce a runner_hang_timeout audit record"
    )
    assert getattr(audit[-1], "call_site", "").endswith("openai_embed")


@pytest.mark.asyncio
async def test_openai_embed_clamps_timeout_for_bound_host():
    orig = mock.AsyncMock(return_value=[[0.75, 0.9]])
    _, _, fake_openai = _load_sitecustomize(
        _prod_env(), openai_embed_impl=_faithful_openai_embed(orig)
    )
    wrapped = fake_openai.openai_embed
    result = await wrapped(["t"], base_url="http://host.docker.internal:11434/v1")
    assert result == [[0.75, 0.9]]
    forwarded = orig.await_args.kwargs
    assert "timeout" not in forwarded, (
        "openai_embed 1.5.4 has a closed signature (no ``timeout`` param, "
        "no ``**kwargs``) — forwarding one raises TypeError on every "
        "bound-host call; the budget is enforced by the wrapper's "
        f"asyncio.wait_for. forwarded: {forwarded!r}"
    )
    assert forwarded.get("base_url") == "http://host.docker.internal:11434/v1"


@pytest.mark.asyncio
async def test_openai_embed_passthrough_for_real_openai_host():
    orig = mock.AsyncMock(return_value=[[0.75, 0.9]])
    _, _, fake_openai = _load_sitecustomize(
        _prod_env(), openai_embed_impl=_faithful_openai_embed(orig)
    )
    wrapped = fake_openai.openai_embed
    result = await wrapped(["t"], base_url="https://api.openai.com/v1")
    assert result == [[0.75, 0.9]]
    forwarded = orig.await_args.kwargs
    assert "timeout" not in forwarded, (
        "staging (real OpenAI) must be a byte-for-byte passthrough — no "
        f"timeout injection; forwarded: {forwarded!r}"
    )


@pytest.mark.asyncio
async def test_openai_embed_env_host_used_when_no_base_url_kwarg():
    env = _prod_env(NFM_D1B_BUDGET_S="0.05")
    _, _, fake_openai = _load_sitecustomize(env, openai_embed_impl=_hang)
    wrapped = fake_openai.openai_embed
    # The host fallback resolves from EMBEDDING_BINDING_HOST at call time
    # (the container env is stable in production) — keep it patched. With
    # no base_url kwarg and the env host on the bound list, the envelope
    # must apply: the hang dies at the 0.05s budget, not 30s.
    with (
        mock.patch.dict(os.environ, env, clear=True),
        pytest.raises(TimeoutError),
    ):
        await wrapped(["t"])


@pytest.mark.asyncio
async def test_openai_embed_hang_passthrough_for_real_openai_host():
    """A hanging real-OpenAI call must NOT be enveloped — staging semantics
    stay stock. Distinguished by elapsed time: a wrongly-applied 0.05s
    budget would raise almost immediately; the true passthrough hangs
    until this test's own 1s watchdog fires."""
    import time

    _, _, fake_openai = _load_sitecustomize(
        _prod_env(NFM_D1B_BUDGET_S="0.05"), openai_embed_impl=_hang
    )
    wrapped = fake_openai.openai_embed

    started = time.monotonic()
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(wrapped(["t"], base_url="https://api.openai.com/v1"), timeout=1.0)
    elapsed = time.monotonic() - started
    assert elapsed >= 0.9, (
        f"real-OpenAI host was enveloped (raised after {elapsed:.3f}s, not "
        "~1s) — the D-1b guard must be a passthrough off the bound-host list"
    )


# ---------------------------------------------------------------------------
# 5. Exhaustiveness: compose pins + drift tripwire
# ---------------------------------------------------------------------------
def _lightrag_service_environment() -> dict[str, str]:
    """Parse the prod compose and return the lightrag service's environment
    mapping (docker-compose consumers resolve this exact structure — a raw
    text regex would also match commented-out lines or the same key under a
    different service)."""
    import yaml  # nucpot api env has pyyaml; tests run under uv

    data = yaml.safe_load(COMPOSE_PROD_PATH.read_text())
    environment = data["services"]["lightrag"]["environment"]
    assert isinstance(environment, dict), (
        "docker-compose.prod.yml lightrag service must declare a mapping-style `environment:` block"
    )
    return environment


def test_compose_pins_explicit_llm_timeout_le_90():
    """The prod compose must pin an explicit bounded ``LLM_TIMEOUT`` so the
    LightRAG 240s default (constants.py DEFAULT_LLM_TIMEOUT) cannot silently
    return even if sitecustomize fails to load."""
    raw = _lightrag_service_environment().get("LLM_TIMEOUT")
    assert isinstance(raw, str), (
        "docker-compose.prod.yml lightrag service must set "
        "LLM_TIMEOUT: ${PROD_LIGHTRAG_LLM_TIMEOUT_S:-<n>} with n <= 90"
    )
    match = re.fullmatch(r"\$\{PROD_LIGHTRAG_LLM_TIMEOUT_S:-(\d+)\}", raw)
    assert match, (
        f"lightrag LLM_TIMEOUT pin must be the explicit env-substitution "
        f"form ${{PROD_LIGHTRAG_LLM_TIMEOUT_S:-<n>}}; got {raw!r}"
    )
    assert int(match.group(1)) <= 90, (
        f"compose LLM_TIMEOUT default {match.group(1)}s exceeds the D-1b 90s budget"
    )


def test_compose_pins_d1b_bound_embed_hosts():
    raw = _lightrag_service_environment().get("NFM_D1B_BOUND_EMBED_HOSTS")
    assert isinstance(raw, str), (
        "docker-compose.prod.yml must pin NFM_D1B_BOUND_EMBED_HOSTS so the "
        "compat-embed guard's host list is explicit and reviewable"
    )
    match = re.fullmatch(r"\$\{PROD_LIGHTRAG_D1B_BOUND_EMBED_HOSTS:-(\S+)\}", raw)
    assert match and match.group(1), (
        f"NFM_D1B_BOUND_EMBED_HOSTS pin must be the explicit env-substitution "
        f"form ${{PROD_LIGHTRAG_D1B_BOUND_EMBED_HOSTS:-<hosts>}} with a "
        f"non-empty default; got {raw!r}"
    )


def test_drift_tripwire_warns_on_uncovered_call_site(caplog):
    """Upgrade canary: if a future lightrag adds an ollama call site
    outside the covered inventory, the patch must say so at startup."""
    fake_pkg, fake_ollama, fake_openai = _build_fake_modules()
    fake_ollama._future_model_if_cache = mock.AsyncMock(return_value="drift")

    saved = {name: sys.modules.get(name) for name in _SWAP_MODULES}
    sys.modules["lightrag"] = fake_pkg
    sys.modules["lightrag.llm"] = fake_pkg.llm
    sys.modules["lightrag.llm.ollama"] = fake_ollama
    sys.modules["lightrag.llm.openai"] = fake_openai
    sys.modules["lightrag.utils"] = fake_pkg.utils

    spec = importlib.util.spec_from_file_location("_nfm5126_test_drift", SITECUSTOMIZE_PATH)
    module = importlib.util.module_from_spec(spec)
    env = {k: v for k, v in os.environ.items() if k not in _CONTROLLED_ENV_KEYS}
    env.update(_prod_env())
    try:
        with (
            caplog.at_level(logging.WARNING, logger=AUDIT_LOGGER_NAME),
            mock.patch.dict(os.environ, env, clear=True),
        ):
            spec.loader.exec_module(module)
    finally:
        for name, original in saved.items():
            if original is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original

    drift = [r for r in caplog.records if "_future_model_if_cache" in r.getMessage()]
    assert drift, (
        "an uncovered ollama call site must produce a startup warning "
        "(drift tripwire) — otherwise a lightrag upgrade can silently "
        "reintroduce an unbounded call site"
    )
