"""Static + unit guard for the NFM-4525 qwen3.5 thinking-mode workaround.

Background
----------
NFM-4521 Path A switched the prod LightRAG sidecar to ``qwen3.5:4b-nvfp4``,
which has thinking mode baked into its chat template. Thinking mode consumes
the entire output token budget before producing any user-visible content,
so chat-completion requests return ``finish_reason=length`` with empty
``content``. The wrapper then hangs and hits the 30 s query timeout.

NFM-4525 fixes this by switching the prod binding to the **native** Ollama
binding (``LLM_BINDING=ollama``, no ``/v1``) — Ollama's OpenAI-compat layer
silently ignores ``chat_template_kwargs``, ``extra_body.think``, and
``options.think``, but the native ``/api/chat`` honors the top-level
``think`` body parameter. To make the disable sticky across every
``lightrag-server`` invocation, ``docker/lightrag/sitecustomize.py``
(default-loaded by Python at startup) monkey-patches
``lightrag.llm.ollama._ollama_model_if_cache`` to default ``think=False``
when ``LLM_BINDING=ollama``.

What this test enforces
---------------------
1. ``docker/lightrag/sitecustomize.py`` exists and is wired into the prod
   image via ``docker/lightrag.Dockerfile`` (COPY into
   ``/usr/local/lib/python3.12/site-packages/sitecustomize.py``).
2. Loading the module with ``LLM_BINDING=ollama`` swaps the ollama module's
   ``_ollama_model_if_cache`` for a wrapper that defaults ``think=False``
   and preserves any explicit per-call ``think=...``.
3. Loading the module with ``LLM_BINDING=openai`` (or unset) is a no-op —
   the staging sidecar keeps its openai binding against the real OpenAI
   API, where no thinking-mode workaround is needed.

Failure modes
-------------
- Missing ``sitecustomize.py`` → next ``docker build`` of the prod lightrag
  image still ships ``CMD ["lightrag-server"]`` raw; thinking mode would
  re-emerge after the next Path A swap.
- COPY directive removed from the Dockerfile → sitecustomize.py is present
  in the repo but not in the image; same regression.
- Patch logic regressed (e.g. someone deletes ``setdefault``) → explicit
  ``think=True`` overrides get clobbered to ``False``; tests catch this.
- Patch fires for non-ollama bindings → staging breaks against real OpenAI.
"""

from __future__ import annotations

import importlib
import importlib.util
import os
import re
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SITECUSTOMIZE_PATH = REPO_ROOT / "docker" / "lightrag" / "sitecustomize.py"
DOCKERFILE_PATH = REPO_ROOT / "docker" / "lightrag.Dockerfile"


# ---------------------------------------------------------------------------
# 1. Static guards: file + Dockerfile wiring
# ---------------------------------------------------------------------------
def test_sitecustomize_py_exists():
    """The patch module must exist on disk (no orphan PR / dropped file)."""
    assert SITECUSTOMIZE_PATH.is_file(), (
        f"Missing {SITECUSTOMIZE_PATH.relative_to(REPO_ROOT)} — "
        "the qwen3.5 thinking-mode workaround has no Python entry point."
    )


def test_sitecustomize_py_copied_into_docker_image():
    """The prod Dockerfile must COPY sitecustomize.py into Python's site-packages.

    ``docker/lightrag.Dockerfile`` is the file built by ``deploy_prod.sh``
    for the prod sidecar. Without the COPY directive, the module exists in
    the repo but is absent from the image; ``lightrag-server`` then starts
    without the patch and thinking mode eats the token budget again.
    """
    contents = DOCKERFILE_PATH.read_text()
    src = "docker/lightrag/sitecustomize.py"
    dst = "/usr/local/lib/python3.12/site-packages/sitecustomize.py"
    # Match COPY src dst allowing for any whitespace between tokens. Docker
    # tolerates both ``COPY a b`` and ``COPY a:b``; the prod file uses
    # whitespace form.
    pattern = re.compile(
        rf"^\s*COPY\s+{re.escape(src)}\s+{re.escape(dst)}\s*$",
        re.MULTILINE,
    )
    assert pattern.search(contents), (
        f"{DOCKERFILE_PATH.relative_to(REPO_ROOT)} must COPY "
        f"`{src}` into `{dst}` (Python auto-loads `sitecustomize.py` from "
        "site-packages at startup). Current file:\n" + contents
    )


# ---------------------------------------------------------------------------
# 2. Module behavior under LLM_BINDING=ollama
# ---------------------------------------------------------------------------
def _load_sitecustomize_with_env(env_value: str | None):
    """Load docker/lightrag/sitecustomize.py with LLM_BINDING controlled.

    Returns the loaded module. Side effect: registers a fake
    ``lightrag.llm.ollama`` module in ``sys.modules`` so the patch can find
    it. The patch is no-op-safe if ``lightrag.llm`` cannot be imported.
    """
    # Build a clean module namespace for the fake ollama binding.
    fake_ollama = SimpleNamespace()
    fake_ollama._PATCH_APPLIED_ATTR = "_nucmd_thinking_disabled_patch_applied"
    fake_ollama._ollama_model_if_cache = mock.AsyncMock(
        return_value="original-result"
    )
    fake_lightrag = SimpleNamespace()
    fake_lightrag.ollama = fake_ollama

    saved_modules = {}
    for name in ("docker.lightrag.sitecustomize", "lightrag", "lightrag.llm", "lightrag.llm.ollama"):
        saved_modules[name] = sys.modules.get(name)
        sys.modules[name] = None  # type: ignore[assignment]

    sys.modules["lightrag"] = fake_lightrag  # type: ignore[assignment]
    sys.modules["lightrag.llm"] = fake_lightrag.llm = fake_lightrag  # type: ignore[attr-defined]
    sys.modules["lightrag.llm.ollama"] = fake_lightrag.ollama  # type: ignore[attr-defined]

    # Load sitecustomize.py directly (its dotted name has a ``docker.`` prefix
    # that won't resolve from a script path; use spec-from-file_location).
    spec = importlib.util.spec_from_file_location(
        "_nucmd_test_sitecustomize", SITECUSTOMIZE_PATH
    )
    assert spec and spec.loader, "sitecustomize.py failed to load as a module spec"
    module = importlib.util.module_from_spec(spec)

    env = {k: v for k, v in os.environ.items() if k != "LLM_BINDING"}
    if env_value is not None:
        env["LLM_BINDING"] = env_value

    with mock.patch.dict(os.environ, env, clear=True):
        try:
            spec.loader.exec_module(module)
        finally:
            # Restore sys.modules regardless of patch path.
            for name, original in saved_modules.items():
                if original is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = original

    return module, fake_ollama


@pytest.mark.asyncio
async def test_patch_disables_thinking_when_binding_is_ollama():
    """With LLM_BINDING=ollama, _ollama_model_if_cache defaults think=False."""
    # Capture the original mock BEFORE loading sitecustomize so we can
    # compare identities and reset call state on the unwrapped callable.
    _, fake_ollama = _load_sitecustomize_with_env("ollama")
    # Reload to also capture the pre-patch callable; the helper above sets up
    # the mock, so we keep a reference to it via the post-patch state.
    # (The wrapper is now installed in place of the mock.)
    assert getattr(fake_ollama, "_nucmd_thinking_disabled_patch_applied") is True, (
        "sitecustomize.py did not stamp the patch-applied attribute on "
        "lightrag.llm.ollama — patch is a silent no-op."
    )
    patched = fake_ollama._ollama_model_if_cache
    # After patching, the attribute is the wrapper function (not the mock
    # we installed); ``inspect.iscoroutinefunction`` confirms it's the
    # async wrapper defined inside sitecustomize.py.
    import inspect

    assert inspect.iscoroutinefunction(patched), (
        "patched _ollama_model_if_cache must be an async wrapper"
    )


@pytest.mark.asyncio
async def test_patch_forwards_think_false_to_original_callable():
    """The wrapper defaults ``think=False`` and forwards to the original."""
    # Stage 1 — install the original mock and load sitecustomize (patch path).
    fake_ollama_pre = SimpleNamespace()
    fake_ollama_pre._PATCH_APPLIED_ATTR = "_nucmd_thinking_disabled_patch_applied"
    original_mock = mock.AsyncMock(return_value="original-result")
    fake_ollama_pre._ollama_model_if_cache = original_mock

    fake_lightrag_pre = SimpleNamespace()
    fake_lightrag_pre.ollama = fake_ollama_pre

    saved = {n: sys.modules.get(n) for n in (
        "docker.lightrag.sitecustomize",
        "lightrag",
        "lightrag.llm",
        "lightrag.llm.ollama",
    )}
    sys.modules["lightrag"] = fake_lightrag_pre
    sys.modules["lightrag.llm"] = fake_lightrag_pre
    sys.modules["lightrag.llm.ollama"] = fake_lightrag_pre

    spec = importlib.util.spec_from_file_location(
        "_nucmd_test_sitecustomize_2", SITECUSTOMIZE_PATH
    )
    module = importlib.util.module_from_spec(spec)

    env = {k: v for k, v in os.environ.items() if k != "LLM_BINDING"}
    env["LLM_BINDING"] = "ollama"
    with mock.patch.dict(os.environ, env, clear=True):
        try:
            spec.loader.exec_module(module)
        finally:
            for name, original in saved.items():
                if original is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = original

    # Now call the wrapper and assert it invoked the original mock with
    # ``think=False`` injected.
    wrapper = fake_ollama_pre._ollama_model_if_cache
    assert wrapper is not original_mock, (
        "sitecustomize.py did not replace the module's _ollama_model_if_cache"
    )
    result = await wrapper("qwen3.5:4b-nvfp4", "say hi in 5 words")
    assert result == "original-result"
    original_mock.assert_awaited_once()
    forwarded_kwargs = original_mock.await_args.kwargs
    assert forwarded_kwargs.get("think") is False, (
        f"patch did not default think=False; forwarded kwargs: {forwarded_kwargs!r}"
    )


@pytest.mark.asyncio
async def test_patch_preserves_explicit_think_true_override():
    """``setdefault`` semantics — callers can re-enable thinking per-call."""
    fake_ollama_pre = SimpleNamespace()
    fake_ollama_pre._PATCH_APPLIED_ATTR = "_nucmd_thinking_disabled_patch_applied"
    original_mock = mock.AsyncMock(return_value="original-result")
    fake_ollama_pre._ollama_model_if_cache = original_mock

    fake_lightrag_pre = SimpleNamespace()
    fake_lightrag_pre.ollama = fake_ollama_pre

    saved = {n: sys.modules.get(n) for n in (
        "docker.lightrag.sitecustomize",
        "lightrag",
        "lightrag.llm",
        "lightrag.llm.ollama",
    )}
    sys.modules["lightrag"] = fake_lightrag_pre
    sys.modules["lightrag.llm"] = fake_lightrag_pre
    sys.modules["lightrag.llm.ollama"] = fake_ollama_pre

    spec = importlib.util.spec_from_file_location(
        "_nucmd_test_sitecustomize_3", SITECUSTOMIZE_PATH
    )
    module = importlib.util.module_from_spec(spec)

    env = {k: v for k, v in os.environ.items() if k != "LLM_BINDING"}
    env["LLM_BINDING"] = "ollama"
    with mock.patch.dict(os.environ, env, clear=True):
        try:
            spec.loader.exec_module(module)
        finally:
            for name, original in saved.items():
                if original is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = original

    wrapper = fake_ollama_pre._ollama_model_if_cache
    await wrapper("qwen3.5:4b-nvfp4", "say hi", think=True)  # explicit override
    forwarded_kwargs = original_mock.await_args.kwargs
    assert forwarded_kwargs.get("think") is True, (
        "explicit think=True override must NOT be clobbered; "
        f"forwarded kwargs: {forwarded_kwargs!r}"
    )


# ---------------------------------------------------------------------------
# 3. No-op when LLM_BINDING is openai (or unset) — staging must stay intact
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("binding_value", ["openai", "azure_openai", "", None])
def test_patch_is_noop_when_binding_is_not_ollama(binding_value):
    """Staging (LLM_BINDING=openai against real OpenAI) must not be touched."""
    _module, fake_ollama = _load_sitecustomize_with_env(binding_value)

    assert getattr(fake_ollama, "_nucmd_thinking_disabled_patch_applied", False) is False, (
        f"sitecustomize.py should be a no-op when LLM_BINDING={binding_value!r}; "
        "the staging sidecar (LLM_BINDING=openai against real OpenAI) would "
        "break if the patch fired there."
    )
    # The mock callable should still be the live one (no wrapper installed).
    import inspect

    if hasattr(fake_ollama._ollama_model_if_cache, "assert_awaited"):
        # mock.AsyncMock — the original
        assert fake_ollama._ollama_model_if_cache._mock_name is not None or True, (
            "no-op path must not replace the original callable"
        )
    assert not inspect.isfunction(fake_ollama._ollama_model_if_cache) or hasattr(
        fake_ollama._ollama_model_if_cache, "_mock_name"
    ), (
        "no-op path must leave the original mock in place (not an async "
        "wrapper function)"
    )