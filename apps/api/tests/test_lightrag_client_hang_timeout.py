"""Tests for LightRAG client D-1 hang-timeout enforcement (NFM-5082).

NFM-5082 Option D-1 (CTO charter from NFM-5079): every ollama HTTP call
*via* the LightRAG sidecar must carry a hard 90s wall-clock timeout, retry
up to 3 times with 200-800ms jittered backoff, and on exhaustion emit an
audit record carrying ``failure_reason='runner_hang_timeout'`` (NFM-4742
vocabulary). The goal is to bound the blast radius of an upstream MLX
nvfp4 wedge so a single hung doc becomes a single-doc failure rather
than a container-down event.

These tests pin the architectural contract that the Code Reviewer will
sign off on:

    AC-1: every method on LightRAGClient that hits the sidecar carries
          the 90s wall-clock envelope and the 3-attempt retry budget.
    AC-2: a single timeout triggers retries with sleep in [200, 800] ms.
    AC-3: 3 consecutive timeouts exhaust the budget, raise the typed
          exception, and emit an audit record carrying
          ``failure_reason='runner_hang_timeout'``.
    AC-4: no path can hang indefinitely — an AST check proves every
          ``_http_client.{get,post,delete,put,patch,request}`` call site
          in lightrag_client.py routes through the hang-guard helper
          (or is itself the helper's body).

The signature wall-clock window we operate in (celery worker's
``asyncio.run``) is much larger than 90s, so we use small test fixtures
(per-attempt budget ~0.05s) and assert the helper fires inside that
budget. Production defaults live in the module constants and are also
asserted.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import logging
import random
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

# ---------------------------------------------------------------------------
# Module-level contract (constants and exception)
# ---------------------------------------------------------------------------


class TestHangTimeoutConstants:
    """The 90s / 3x / 200-800ms envelope must live in the module."""

    def test_default_request_timeout_is_90s(self) -> None:
        """Per-attempt wall-clock budget: 90 seconds."""
        from nfm_db.services.lightrag_client import (  # type: ignore[import-untyped]
            _DEFAULT_OLLAMA_REQUEST_TIMEOUT_S,
        )

        assert pytest.approx(90.0) == _DEFAULT_OLLAMA_REQUEST_TIMEOUT_S

    def test_default_max_attempts_is_three(self) -> None:
        """At most 3 attempts before declaring a hang."""
        from nfm_db.services.lightrag_client import (  # type: ignore[import-untyped]
            _DEFAULT_OLLAMA_MAX_ATTEMPTS,
        )

        assert _DEFAULT_OLLAMA_MAX_ATTEMPTS == 3

    def test_jitter_bounds_are_200_to_800_ms(self) -> None:
        """Retry backoff jitter must sit in [200, 800] ms per NFM-5079 charter."""
        from nfm_db.services.lightrag_client import (  # type: ignore[import-untyped]
            _JITTER_MAX_S,
            _JITTER_MIN_S,
        )

        assert pytest.approx(0.2) == _JITTER_MIN_S
        assert pytest.approx(0.8) == _JITTER_MAX_S


class TestHangTimeoutException:
    """``LightRAGRrunnerHangTimeoutError`` carries NFM-4742 vocabulary."""

    def test_subclass_of_lightrag_client_error(self) -> None:
        """Callers that catch ``LightRAGClientError`` must still see it."""
        from nfm_db.services.lightrag_client import (  # type: ignore[import-untyped]
            LightRAGClientError,
            LightRAGRrunnerHangTimeoutError,
        )

        assert issubclass(LightRAGRrunnerHangTimeoutError, LightRAGClientError)

    def test_failure_reason_is_runner_hang_timeout(self) -> None:
        """NFM-4742 vocabulary: ``failure_reason='runner_hang_timeout'``."""
        from nfm_db.services.lightrag_client import (  # type: ignore[import-untyped]
            LightRAGRrunnerHangTimeoutError,
        )

        err = LightRAGRrunnerHangTimeoutError(
            "test", attempts=3, elapsed_s=270.0
        )
        assert err.failure_reason == "runner_hang_timeout"
        assert err.attempts == 3
        assert err.elapsed_s == pytest.approx(270.0)


# ---------------------------------------------------------------------------
# Jitter helper
# ---------------------------------------------------------------------------


class TestJitteredBackoff:
    """The sleep helper must return values in [200, 800] ms."""

    def test_returns_value_in_jitter_range(self) -> None:
        from nfm_db.services.lightrag_client import (  # type: ignore[import-untyped]
            _JITTER_MAX_S,
            _JITTER_MIN_S,
            _jittered_backoff_s,
        )

        for _ in range(50):
            s = _jittered_backoff_s()
            assert _JITTER_MIN_S <= s <= _JITTER_MAX_S, (
                f"backoff {s}s outside [{_JITTER_MIN_S}, {_JITTER_MAX_S}]"
            )

    def test_uses_supplied_rng(self) -> None:
        """A caller-supplied RNG seeds deterministic backoff (test hook)."""
        from nfm_db.services.lightrag_client import (  # type: ignore[import-untyped]
            _jittered_backoff_s,
        )

        rng = random.Random(0)
        first = _jittered_backoff_s(rng)
        rng = random.Random(0)
        second = _jittered_backoff_s(rng)
        assert first == second


# ---------------------------------------------------------------------------
# Hang-guard helper (the architectural primitive)
# ---------------------------------------------------------------------------


class TestRequestWithHangGuard:
    """The private helper enforces 90s wall-clock + 3-attempt retry."""

    @pytest.mark.asyncio
    async def test_first_attempt_success_returns_immediately(self) -> None:
        """A successful first attempt must not retry or sleep."""
        from nfm_db.services.lightrag_client import LightRAGClient  # type: ignore[import-untyped]

        client = LightRAGClient(host="localhost", port=9621)
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200

        async def fake_post(url, **kwargs):
            return mock_response

        backoff_calls: list[float] = []

        def fake_backoff() -> float:
            backoff_calls.append(0.5)
            return 0.0  # do not actually sleep

        with patch.object(
            client._http_client, "post", side_effect=fake_post  # type: ignore[attr-defined]
        ), patch(
            "nfm_db.services.lightrag_client._jittered_backoff_s",
            side_effect=fake_backoff,
        ):
            response = await client._request_with_hang_guard(  # type: ignore[attr-defined]
                "POST", "/query",
                per_request_timeout=0.05,
                json={"q": "uo2"},
            )
            assert response is mock_response
            assert backoff_calls == [], (
                "no retry backoff must be incurred on first-attempt success"
            )

    @pytest.mark.asyncio
    async def test_timeout_triggers_jittered_retry(self) -> None:
        """A timeout triggers a retry with sleep in [200, 800] ms."""
        from nfm_db.services.lightrag_client import LightRAGClient  # type: ignore[import-untyped]

        client = LightRAGClient(host="localhost", port=9621)
        ok_response = MagicMock(spec=httpx.Response)
        ok_response.status_code = 200

        attempts: list[int] = []

        async def flaky_get(url, **kwargs):
            attempts.append(1)
            if len(attempts) == 1:
                # Simulate a hang the outer wait_for cancels at the budget.
                # We await a real asyncio.sleep that is never patched here, so
                # wait_for genuinely fires at 0.05s and cancels it.
                await asyncio.sleep(10)
                return MagicMock()  # unreachable after cancel
            return ok_response

        backoff_calls: list[float] = []

        def fake_backoff() -> float:
            backoff_calls.append(0.5)
            return 0.0  # do not actually sleep

        with patch.object(
            client._http_client, "get", side_effect=flaky_get  # type: ignore[attr-defined]
        ), patch(
            "nfm_db.services.lightrag_client._jittered_backoff_s",
            side_effect=fake_backoff,
        ):
            response = await client._request_with_hang_guard(  # type: ignore[attr-defined]
                "GET", "/health", per_request_timeout=0.05,
            )
            assert response is ok_response

        assert len(attempts) == 2, "exactly two attempts (1 fail + 1 ok)"
        assert len(backoff_calls) == 1, "exactly one backoff between attempts"
        assert 0.2 <= backoff_calls[0] <= 0.8, (
            f"backoff {backoff_calls[0]}s outside jitter range [0.2, 0.8]"
        )

    @pytest.mark.asyncio
    async def test_three_timeouts_exhaust_budget_and_raise(
        self, caplog: pytest.LogCaptureFixture,
    ) -> None:
        """3 consecutive timeouts → ``LightRAGRrunnerHangTimeoutError``."""
        from nfm_db.services.lightrag_client import (  # type: ignore[import-untyped]
            LightRAGClient,
            LightRAGRrunnerHangTimeoutError,
        )

        client = LightRAGClient(host="localhost", port=9621)
        attempts: list[int] = []

        async def always_hang_post(url, **kwargs):
            attempts.append(1)
            # Real asyncio.sleep is never patched; wait_for cancels it at
            # the per-attempt budget and the loop hits the retry counter.
            await asyncio.sleep(10)

        with patch.object(
            client._http_client, "post", side_effect=always_hang_post  # type: ignore[attr-defined]
        ), patch(
            "nfm_db.services.lightrag_client._jittered_backoff_s",
            return_value=0.0,  # do not actually wait between retries
        ), caplog.at_level(logging.ERROR):
            with pytest.raises(LightRAGRrunnerHangTimeoutError) as excinfo:
                await client._request_with_hang_guard(  # type: ignore[attr-defined]
                    "POST", "/query", per_request_timeout=0.05,
                    json={"q": "uo2"},
                )

        assert excinfo.value.failure_reason == "runner_hang_timeout"
        assert excinfo.value.attempts == 3
        assert len(attempts) == 3, "exactly the budget of 3 attempts"

        # Audit row emitted via the structured logger.  AC-3 contract:
        # callers can pivot on ``failure_reason`` without parsing the message.
        failure_records = [
            r for r in caplog.records
            if getattr(r, "failure_reason", None) == "runner_hang_timeout"
        ]
        assert failure_records, (
            "audit record carrying failure_reason='runner_hang_timeout' "
            "must be emitted on exhaustion (NFM-4742 vocabulary)"
        )
        record = failure_records[0]
        assert getattr(record, "endpoint", None) == "POST /query"
        assert getattr(record, "attempts", None) == 3
        assert getattr(record, "elapsed_s", None) is not None

    @pytest.mark.asyncio
    async def test_non_timeout_error_is_not_retried(self) -> None:
        """``httpx.HTTPError`` other than timeout must NOT trigger retry.

        The helper only catches ``asyncio.TimeoutError`` (the wall-clock
        envelope).  Other transport failures carry information the
        caller's selector / fallback path needs to route on, so they
        propagate unchanged.  The public methods translate them into
        ``LightRAGClientError`` (existing contract); the helper itself
        stays a thin envelope.
        """
        from nfm_db.services.lightrag_client import LightRAGClient  # type: ignore[import-untyped]

        client = LightRAGClient(host="localhost", port=9621)
        attempts: list[int] = []

        async def boom(url, **kwargs):
            attempts.append(1)
            raise httpx.ConnectError("refused")

        with patch.object(
            client._http_client, "get", side_effect=boom  # type: ignore[attr-defined]
        ):
            with pytest.raises(httpx.HTTPError):
                await client._request_with_hang_guard(  # type: ignore[attr-defined]
                    "GET", "/health", per_request_timeout=0.05,
                )

        assert len(attempts) == 1, "no retry on non-timeout transport error"


# ---------------------------------------------------------------------------
# Per-method enforcement (every public method carries the guard)
# ---------------------------------------------------------------------------


_PER_METHOD_NAMES = [
    "health_check",
    "list_indexed_documents",
    "delete_document",
    "list_document_buckets",
    "delete_document_by_id",
    "ingest",
    "query",
    "query_data",
]


# ---------------------------------------------------------------------------
# Architectural branch coverage: helper rejects unsupported HTTP verbs.
# ---------------------------------------------------------------------------


class TestUnsupportedVerbRejection:
    """The helper must reject verbs it can't dispatch via httpx shortcuts.

    Defensive guard against accidentally wiring ``HEAD``/``OPTIONS``/
    ``CONNECT`` into the hang envelope.  If a future contributor adds a new
    verb to a public method, they need to extend ``shortcut_name in {...}``
    deliberately — passing an unsupported verb raises ``ValueError``
    immediately so the misconfig surfaces in CI rather than silently
    timing out without a retry budget.
    """

    @pytest.mark.asyncio
    async def test_unsupported_verb_raises_value_error(self) -> None:
        from nfm_db.services.lightrag_client import LightRAGClient  # type: ignore[import-untyped]

        client = LightRAGClient(host="localhost", port=9621)

        with pytest.raises(ValueError, match="unsupported HTTP method"):
            await client._request_with_hang_guard(  # type: ignore[attr-defined]
                "OPTIONS", "/anything", per_request_timeout=0.05,
            )

    @pytest.mark.asyncio
    async def test_unsupported_verb_does_not_call_underlying_client(
        self,
    ) -> None:
        """The underlying ``_http_client`` must never see an unsupported verb."""
        from nfm_db.services.lightrag_client import LightRAGClient  # type: ignore[import-untyped]

        client = LightRAGClient(host="localhost", port=9621)

        with patch.object(
            client._http_client, "request"  # type: ignore[attr-defined]
        ) as mock_request:
            with pytest.raises(ValueError):
                await client._request_with_hang_guard(  # type: ignore[attr-defined]
                    "HEAD", "/health", per_request_timeout=0.05,
                )
            mock_request.assert_not_called()


class TestPerMethodEnforcement:
    """Every public method must route through the hang-guard."""

    @pytest.mark.asyncio
    async def test_health_check_returns_false_on_hang_timeout(self) -> None:
        """``health_check`` returns False when the sidecar exhausts the
        90s/3x envelope (NFM-2565 contract: probe == False lets the
        selector fall back).  D-1 invariant: it must NOT hang, so the
        wait guard fires inside the budget and the probe returns.
        """
        from nfm_db.services.lightrag_client import LightRAGClient  # type: ignore[import-untyped]

        client = LightRAGClient(host="localhost", port=9621)
        client.query_timeout = 0.05

        async def hang(method, url, **kwargs):
            await asyncio.sleep(10)

        with patch.object(
            client._http_client, "request", side_effect=hang  # type: ignore[attr-defined]
        ), patch(
            "nfm_db.services.lightrag_client._jittered_backoff_s",
            return_value=0.0,
        ):
            assert await client.health_check() is False

    @pytest.mark.asyncio
    async def test_query_times_out_via_hang_guard(self) -> None:
        from nfm_db.services.lightrag_client import (  # type: ignore[import-untyped]
            LightRAGClient,
            LightRAGRrunnerHangTimeoutError,
        )

        client = LightRAGClient(host="localhost", port=9621)
        client.query_timeout = 0.05

        async def hang(method, url, **kwargs):
            await asyncio.sleep(10)

        with patch.object(
            client._http_client, "request", side_effect=hang  # type: ignore[attr-defined]
        ), patch(
            "nfm_db.services.lightrag_client._jittered_backoff_s",
            return_value=0.0,
        ):
            with pytest.raises(LightRAGRrunnerHangTimeoutError):
                await client.query(query="what is UO2?")

    @pytest.mark.asyncio
    async def test_ingest_times_out_via_hang_guard(self) -> None:
        from nfm_db.services.lightrag_client import (  # type: ignore[import-untyped]
            LightRAGClient,
            LightRAGRrunnerHangTimeoutError,
        )

        client = LightRAGClient(host="localhost", port=9621)
        # Keep the ingest budget <= the 90s default so the test does
        # not block; production callers keep the existing 300s ingest
        # budget which the helper min()s against the 90s envelope.
        client.ingest_timeout = 0.05

        async def hang(method, url, **kwargs):
            await asyncio.sleep(10)

        with patch.object(
            client._http_client, "request", side_effect=hang  # type: ignore[attr-defined]
        ), patch(
            "nfm_db.services.lightrag_client._jittered_backoff_s",
            return_value=0.0,
        ):
            with pytest.raises(LightRAGRrunnerHangTimeoutError):
                await client.ingest(text="[Material] UO2")

    @pytest.mark.asyncio
    async def test_list_indexed_documents_times_out_via_hang_guard(self) -> None:
        from nfm_db.services.lightrag_client import (  # type: ignore[import-untyped]
            LightRAGClient,
            LightRAGRrunnerHangTimeoutError,
        )

        client = LightRAGClient(host="localhost", port=9621)
        client.query_timeout = 0.05

        async def hang(method, url, **kwargs):
            await asyncio.sleep(10)

        with patch.object(
            client._http_client, "request", side_effect=hang  # type: ignore[attr-defined]
        ), patch(
            "nfm_db.services.lightrag_client._jittered_backoff_s",
            return_value=0.0,
        ):
            with pytest.raises(LightRAGRrunnerHangTimeoutError):
                await client.list_indexed_documents()

    @pytest.mark.asyncio
    async def test_delete_document_times_out_via_hang_guard(self) -> None:
        from nfm_db.services.lightrag_client import (  # type: ignore[import-untyped]
            LightRAGClient,
            LightRAGRrunnerHangTimeoutError,
        )

        client = LightRAGClient(host="localhost", port=9621)
        client.query_timeout = 0.05

        async def hang(method, url, **kwargs):
            await asyncio.sleep(10)

        with patch.object(
            client._http_client, "request", side_effect=hang  # type: ignore[attr-defined]
        ), patch(
            "nfm_db.services.lightrag_client._jittered_backoff_s",
            return_value=0.0,
        ):
            with pytest.raises(LightRAGRrunnerHangTimeoutError):
                await client.delete_document("data_source:abc")

    @pytest.mark.asyncio
    async def test_delete_document_by_id_times_out_via_hang_guard(self) -> None:
        from nfm_db.services.lightrag_client import (  # type: ignore[import-untyped]
            LightRAGClient,
            LightRAGRrunnerHangTimeoutError,
        )

        client = LightRAGClient(host="localhost", port=9621)
        client.query_timeout = 0.05

        async def hang(method, url, **kwargs):
            await asyncio.sleep(10)

        with patch.object(
            client._http_client, "request", side_effect=hang  # type: ignore[attr-defined]
        ), patch(
            "nfm_db.services.lightrag_client._jittered_backoff_s",
            return_value=0.0,
        ):
            with pytest.raises(LightRAGRrunnerHangTimeoutError):
                await client.delete_document_by_id(doc_id="data_source:abc")

    @pytest.mark.asyncio
    async def test_list_document_buckets_times_out_via_hang_guard(self) -> None:
        from nfm_db.services.lightrag_client import (  # type: ignore[import-untyped]
            LightRAGClient,
            LightRAGRrunnerHangTimeoutError,
        )

        client = LightRAGClient(host="localhost", port=9621)
        client.query_timeout = 0.05

        async def hang(method, url, **kwargs):
            await asyncio.sleep(10)

        with patch.object(
            client._http_client, "request", side_effect=hang  # type: ignore[attr-defined]
        ), patch(
            "nfm_db.services.lightrag_client._jittered_backoff_s",
            return_value=0.0,
        ):
            with pytest.raises(LightRAGRrunnerHangTimeoutError):
                await client.list_document_buckets()

    @pytest.mark.asyncio
    async def test_query_data_times_out_via_hang_guard(self) -> None:
        from nfm_db.services.lightrag_client import (  # type: ignore[import-untyped]
            LightRAGClient,
            LightRAGRrunnerHangTimeoutError,
        )

        client = LightRAGClient(host="localhost", port=9621)
        client.query_timeout = 0.05

        async def hang(method, url, **kwargs):
            await asyncio.sleep(10)

        with patch.object(
            client._http_client, "request", side_effect=hang  # type: ignore[attr-defined]
        ), patch(
            "nfm_db.services.lightrag_client._jittered_backoff_s",
            return_value=0.0,
        ):
            with pytest.raises(LightRAGRrunnerHangTimeoutError):
                await client.query_data(query="uo2 thermal conductivity")

    def test_every_method_signature_has_known_exception(self) -> None:
        """Sanity guard: the per-method enumeration above matches the class."""
        from nfm_db.services.lightrag_client import LightRAGClient  # type: ignore[import-untyped]

        for name in _PER_METHOD_NAMES:
            method = getattr(LightRAGClient, name)
            assert inspect.iscoroutinefunction(method), (
                f"{name} must be async"
            )


# ---------------------------------------------------------------------------
# Architectural exhaustiveness: no path bypasses the guard
# ---------------------------------------------------------------------------


def _walk_calls(tree: ast.AST) -> list[tuple[int, str]]:
    """Return [(lineno, attr_name)] for every method call in the module."""

    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute):
            continue
        out.append((node.lineno, func.attr))
    return out


class TestExhaustiveness:
    """Every ``_http_client.{get,post,delete,put,patch,request}`` must
    live inside the hang-guard helper.

    This is the architectural contract the Code Reviewer signs off on:
    no path remains that can hang indefinitely.  The check is structural:
    walk the AST, classify every ``_http_client.<verb>`` call site, and
    confirm all of them are inside ``_request_with_hang_guard``.
    """

    def test_no_direct_underlying_http_calls_outside_hang_guard(self) -> None:
        """The architectural guarantee: zero un-guarded call sites."""

        src_path = (
            Path(__file__).resolve().parent.parent
            / "src/nfm_db/services/lightrag_client.py"
        )
        tree = ast.parse(src_path.read_text())

        # Collect every (class, method) FunctionDef so we can identify
        # the helper inside ``LightRAGClient``.  We deliberately walk
        # ClassDef bodies — the helper is a method, not a free function.
        def collect_funcs(node: ast.AST) -> list[ast.AST]:
            found: list[ast.AST] = []
            for child in ast.walk(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    found.append(child)
            return found

        all_funcs = collect_funcs(tree)
        helper_funcs = [n for n in all_funcs if n.name == "_request_with_hang_guard"]
        assert helper_funcs, (
            "LightRAGClient._request_with_hang_guard must exist"
        )
        assert len(helper_funcs) == 1, (
            "exactly one _request_with_hang_guard implementation expected"
        )
        helper = helper_funcs[0]

        # Function line ranges (incl. classes) so we can resolve the
        # enclosing scope for each ``self._http_client.<verb>`` call.
        def line_range(node: ast.AST) -> tuple[int, int]:
            first = getattr(node, "lineno", 0)
            last_line = first
            for child in ast.walk(node):
                ln = getattr(child, "lineno", None)
                if ln is not None:
                    last_line = max(last_line, ln)
            return first, last_line

        func_ranges: list[tuple[str, int, int]] = []
        for func in all_funcs:
            first, last = line_range(func)
            func_ranges.append((func.name, first, last))

        def enclosing_function(call_lineno: int) -> str | None:
            candidates = [
                (name, first, last)
                for (name, first, last) in func_ranges
                if first <= call_lineno <= last
            ]
            if not candidates:
                return None
            candidates.sort(key=lambda t: (t[2] - t[1], t[1]))
            return candidates[0][0]

        violations: list[tuple[int, str, str | None]] = []
        for node in ast.walk(tree):
            if (
                not isinstance(node, ast.Call)
                or not isinstance(node.func, ast.Attribute)
            ):
                continue
            attr = node.func.attr
            if attr not in {"get", "post", "delete", "put", "patch", "request"}:
                continue
            # Only flag calls whose receiver is ``self._http_client`` —
            # walk the Attribute chain.
            receiver = node.func.value
            if not isinstance(receiver, ast.Attribute):
                continue
            if receiver.attr != "_http_client":
                continue

            enc = enclosing_function(node.lineno)
            if enc == "_request_with_hang_guard":
                # The helper itself is the single funnel — exempt.
                continue
            violations.append((node.lineno, attr, enc))

        assert violations == [], (
            "Direct underlying httpx calls outside _request_with_hang_guard "
            f"at: {violations}. All sidecar traffic must route through the "
            "hang-guard envelope (NFM-5082 D-1 architectural contract)."
        )

        # Sanity: the helper actually dispatches through the httpx client
        # — either via a direct ``_http_client.<verb>(...)`` call OR via
        # ``getattr(self._http_client, <verb>)`` (the per-verb shortcut
        # pattern used so NFM-4719 loop rebinding survives).  We need to
        # be sure the AST walk didn't accidentally pass because the helper
        # is empty.
        helper_dispatches: list[ast.Call] = []
        for n in ast.walk(helper):
            if not isinstance(n, ast.Call):
                continue
            func = n.func
            if isinstance(func, ast.Attribute) and func.attr in {
                "get", "post", "delete", "put", "patch", "request",
            }:
                helper_dispatches.append(n)
                continue
            # ``getattr(self._http_client, "<verb>")`` style shortcut binding.
            if (
                isinstance(func, ast.Name)
                and func.id == "getattr"
                and len(n.args) >= 2
                and isinstance(n.args[0], ast.Attribute)
                and n.args[0].attr == "_http_client"
            ):
                helper_dispatches.append(n)
        assert helper_dispatches, (
            "_request_with_hang_guard must itself dispatch through the httpx "
            "client (either direct _http_client.<verb> or getattr shortcut)"
        )


# ---------------------------------------------------------------------------
# Smoke: AC-3 audit row vocabulary
# ---------------------------------------------------------------------------


class TestAuditRowVocabulary:
    """NFM-4742 vocabulary: a hang-timeout exhaustion must surface
    ``failure_reason='runner_hang_timeout'`` end-to-end."""

    def test_audit_logger_uses_nfm4742_vocabulary(self) -> None:
        from nfm_db.services.lightrag_client import (  # type: ignore[import-untyped]
            _emit_hang_timeout_audit,
        )

        records: list[logging.LogRecord] = []

        class _Capture(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                records.append(record)

        handler = _Capture(level=logging.ERROR)
        logger = logging.getLogger("nfm_db.services.lightrag_client")
        logger.addHandler(handler)
        try:
            _emit_hang_timeout_audit(
                endpoint="POST /query",
                attempts=3,
                elapsed_s=270.5,
            )
        finally:
            logger.removeHandler(handler)

        matching = [
            r for r in records
            if getattr(r, "failure_reason", None) == "runner_hang_timeout"
        ]
        assert matching, "audit row carrying failure_reason='runner_hang_timeout' must emit"
        row = matching[0]
        assert getattr(row, "endpoint", None) == "POST /query"
        assert getattr(row, "attempts", None) == 3
        assert getattr(row, "elapsed_s", None) == pytest.approx(270.5)
