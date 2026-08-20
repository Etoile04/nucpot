"""AC-3: a dead LLM must make the next RAG query fail fast, not stall (NFM-3427).

ADR-NFM-3404 aligns four timeout layers so that an unhealthy LightRAG sidecar
surfaces an error inside the browser's budget instead of hanging until the old
90 s ceiling:

    layer 1  LIGHTRAG_LLM_TIMEOUT_S        sidecar -> upstream LLM
    layer 2  NFM_LIGHTRAG_QUERY_CONNECT_S  backend -> sidecar, TCP handshake
    layer 3  NFM_LIGHTRAG_QUERY_TIMEOUT_S  backend -> sidecar, read budget
    layer 4  NEXT_PUBLIC_RAG_QUERY_TIMEOUT_MS  browser AbortController

Layer 4 is the ceiling AC-3 measures against: 14 s. `.env.lightrag` ships
8 + 3 + 1 <= 12 <= 14 <= 15, so every inner layer must fire before the browser
gives up.

Sibling [A] (NFM-3425) already unit-tests that each env var reaches the httpx
transport. Those tests mock the transport, so they can prove the *wiring* but
not the *wall-clock*. These tests close that gap with real containers, real
TCP and a real `docker stop`, timing the actual `LightRAGClient`.

Two layers are pinned, because each alone is satisfiable by an unrelated
accident:

* `test_query_fails_fast_after_llm_container_stopped` — the AC-3 script.
  Pins layer 1: the sidecar must convert a dead LLM into a bounded 5xx.
  Fails if the sidecar stalls instead of giving up.
* `test_backend_read_budget_caps_a_stalled_sidecar` — pins layer 3, the
  backstop for when layer 1 is misconfigured or the sidecar wedges. Fails if
  `NFM_LIGHTRAG_QUERY_TIMEOUT_S` is raised past the layer-4 ceiling, which is
  exactly the ADR invariant this chain exists to hold.

Run with:  uv run pytest tests/integration/test_lightrag_kill_llm_fast_fail.py -m integration
(`-m integration` is required: pyproject's addopts deselect the marker by default.)
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest

from nfm_db.services.lightrag_client import LightRAGClient, LightRAGClientError

_COMPOSE_FILE = Path(__file__).parent / "lightrag_fastfail" / "docker-compose.fastfail.yml"

# --- The budget under test (ADR-NFM-3404 §2.1 / .env.lightrag) --------------
# Layer 1 is deliberately tighter than the shipped 8 s so the sidecar gives up
# quickly and the test stays short; the assertion is against layer 4 either way.
_LLM_TIMEOUT_S = "2"
_QUERY_CONNECT_S = "3"
_QUERY_TIMEOUT_S = "12"

# Layer 4 — the browser's AbortController budget, and the AC-3 ceiling.
_FRONTEND_BUDGET_S = 14.0

# The pre-ADR behaviour this exists to prevent. Not an assertion threshold —
# quoted in failure messages so a regression reads as "we are back to 2026-08".
_LEGACY_CEILING_S = 90.0

# How long a wedged sidecar pretends to work. Comfortably past layer 4, so only
# the backend read budget can end the request.
_STALL_S = 60.0


def _docker_available() -> bool:
    """True when a docker daemon is reachable, not merely installed."""
    if shutil.which("docker") is None:
        return False
    try:
        probe = subprocess.run(["docker", "info"], capture_output=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError):
        return False
    return probe.returncode == 0


# AC-3: "Test must skip cleanly when Docker is not on PATH (CI without a daemon
# should not be a hard fail)". `skipif` rather than `xfail` because the daemon
# is a fixture-time dependency: xfail only converts failures raised in a test's
# *call* phase, so a missing daemon would still surface as a setup ERROR and
# redden CI — the outcome the AC asks us to avoid.
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not _docker_available(), reason="no docker daemon"),
]


class _ComposeStack:
    """Thin wrapper over `docker compose` scoped to one throwaway project."""

    def __init__(self, project: str) -> None:
        self._argv = ["docker", "compose", "-p", project, "-f", str(_COMPOSE_FILE)]
        self.sidecar_port = 0

    def _compose(
        self, *args: str, check: bool = True, timeout: int = 180
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [*self._argv, *args],
            capture_output=True,
            text=True,
            check=check,
            timeout=timeout,
            # AC-3 step 1: the sidecar's layer-1 budget is interpolated into
            # docker-compose.fastfail.yml from this variable, exactly as
            # docker-compose.lightrag.yml reads it from .env.lightrag.
            env={**os.environ, "LIGHTRAG_LLM_TIMEOUT_S": _LLM_TIMEOUT_S},
        )

    def up(self) -> None:
        self._compose("up", "-d")
        # Container port -> ephemeral host port, e.g. "0.0.0.0:32771".
        mapping = self._compose("port", "mock-lightrag", "8001").stdout.strip()
        if not mapping:
            raise RuntimeError("compose did not publish mock-lightrag:8001")
        self.sidecar_port = int(mapping.rsplit(":", 1)[1])

    def down(self) -> None:
        self._compose("down", "-v", "--remove-orphans", check=False, timeout=120)

    def stop(self, service: str) -> None:
        self._compose("stop", "-t", "2", service)

    def start(self, service: str) -> None:
        self._compose("start", service)

    # -- sidecar helpers ----------------------------------------------------

    def _url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.sidecar_port}{path}"

    def await_healthy(self, timeout_s: float = 60.0) -> None:
        """Block until the sidecar answers GET /health (ADR §2.5)."""
        deadline = time.monotonic() + timeout_s
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(self._url("/health"), timeout=2) as response:
                    if response.status == 200:
                        return
            except (urllib.error.URLError, OSError) as exc:
                last_error = exc
            time.sleep(0.5)
        raise RuntimeError(f"sidecar never became healthy: {last_error}")

    def set_stall(self, seconds: float) -> None:
        """Make subsequent /query calls stall for ``seconds`` before replying."""
        request = urllib.request.Request(
            self._url("/__control__"),
            data=json.dumps({"hang_s": seconds}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            response.read()


@pytest.fixture(scope="module")
def stack() -> Iterator[_ComposeStack]:
    """Bring the harness up for the module; always tear it down (AC-3 step 5)."""
    # Unique project name so concurrent runs and leftovers cannot collide.
    compose = _ComposeStack(project=f"nfm3427-{uuid4().hex[:8]}")
    try:
        compose.up()
        compose.await_healthy()
        yield compose
    finally:
        compose.down()


@pytest.fixture
def healthy_stack(stack: _ComposeStack) -> _ComposeStack:
    """Restore the stack to 'everything works' before each test."""
    stack.start("mock-llm")
    stack.await_healthy()
    stack.set_stall(0)
    return stack


def _client(monkeypatch: pytest.MonkeyPatch, port: int) -> LightRAGClient:
    """A LightRAGClient pointed at the harness, configured per .env.lightrag."""
    monkeypatch.setenv("NFM_LIGHTRAG_HOST", "127.0.0.1")
    monkeypatch.setenv("NFM_LIGHTRAG_PORT", str(port))
    monkeypatch.setenv("NFM_LIGHTRAG_QUERY_CONNECT_S", _QUERY_CONNECT_S)
    monkeypatch.setenv("NFM_LIGHTRAG_QUERY_TIMEOUT_S", _QUERY_TIMEOUT_S)
    return LightRAGClient()


async def test_query_fails_fast_after_llm_container_stopped(
    healthy_stack: _ComposeStack,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-3: stop the LLM container; the next query errors within 14 s.

    Pins layer 1. With the LLM gone the sidecar burns its LIGHTRAG_LLM_TIMEOUT_S
    budget and answers 500, which the backend surfaces as LightRAGClientError.
    A sidecar that stalled instead would blow the layer-4 ceiling and fail here.
    """
    client = _client(monkeypatch, healthy_stack.sidecar_port)
    try:
        # Step 2 — baseline query succeeds while the whole stack is healthy.
        baseline = await client.query(query="baseline: is the stack up?")
        assert "response" in baseline, f"unexpected baseline payload: {baseline}"

        # Step 3 — stop the upstream mock LLM container.
        healthy_stack.stop("mock-llm")

        # Step 4 — the next query must fail fast.
        started = time.monotonic()
        with pytest.raises(LightRAGClientError) as caught:
            await client.query(query="does a dead LLM fail fast?")
        elapsed = time.monotonic() - started
    finally:
        await client.close()

    assert elapsed <= _FRONTEND_BUDGET_S, (
        f"query took {elapsed:.1f}s, past the {_FRONTEND_BUDGET_S}s browser budget "
        f"(layer 4). The pre-ADR-NFM-3404 behaviour was ~{_LEGACY_CEILING_S}s — "
        "check LIGHTRAG_LLM_TIMEOUT_S still bounds the sidecar."
    )
    # NFM-3357 asked for a usable message, not a bare timeout: the sidecar's
    # own explanation has to survive the hop into LightRAGClientError.
    assert "LLM unreachable" in str(caught.value), (
        f"sidecar detail was dropped from the error: {caught.value!r}"
    )


async def test_backend_read_budget_caps_a_stalled_sidecar(
    healthy_stack: _ComposeStack,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Layer 3 backstop: a wedged sidecar is cut off by the backend, not the user.

    Layer 1 cannot help here — the sidecar never even reaches its LLM. Only
    NFM_LIGHTRAG_QUERY_TIMEOUT_S stands between the browser and a 60 s stall,
    so this fails the moment that budget is raised past the layer-4 ceiling.
    """
    healthy_stack.set_stall(_STALL_S)
    client = _client(monkeypatch, healthy_stack.sidecar_port)

    started = time.monotonic()
    try:
        with pytest.raises(LightRAGClientError):
            await client.query(query="does a wedged sidecar get cut off?")
        elapsed = time.monotonic() - started
    finally:
        await client.close()

    assert elapsed <= _FRONTEND_BUDGET_S, (
        f"stalled sidecar held the request for {elapsed:.1f}s, past the "
        f"{_FRONTEND_BUDGET_S}s browser budget (layer 4). "
        f"NFM_LIGHTRAG_QUERY_TIMEOUT_S={_QUERY_TIMEOUT_S}s must stay under it."
    )
    assert elapsed < _STALL_S, (
        "the request outlasted the stall, so the backend read budget never fired"
    )
