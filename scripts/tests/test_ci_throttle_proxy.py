"""Unit + behavioral tests for the NFM-5333 CI download throttle proxy.

``scripts/ci_throttle_proxy.py`` is a loopback-only, user-space forward
proxy whose single job is to cap the AGGREGATE download throughput of CI
build-time fetches (pip/pnpm/corepack inside the deploy host's
``--no-cache`` docker builds) so deploy windows stop saturating the shared
broadband and breaking the sentinel 5s public-edge budget (NFM-5273 RCA,
owner-approved mitigation (a) via NFM-5262 card ``a109200b``).

These tests pin the contract the deploy script relies on:

* the token bucket refills at the configured rate, allows the configured
  burst, and makes callers WAIT when exhausted — a capped transfer must
  take at least ``bytes / rate`` seconds (AC-1's demonstrable cap);
* the health endpoint answers both proxy-form (absolute-URI) and direct
  (origin-form) requests, so ``deploy_prod.sh``'s readiness probe and a
  plain ``curl 127.0.0.1:<port>/...`` both work;
* absolute-URI request lines are rewritten to origin-form with the
  authority preserved in the Host header (plain-HTTP mirrors, apt);
* rate config parsing rejects non-positive garbage instead of silently
  running uncapped — an unparseable cap must never mean "no cap" silently;
* an end-to-end CONNECT tunnel relays bytes and honors the pace;
* health `started` reports the proxy's actual start (not answer time) and
  the proxy raises its own soft RLIMIT_NOFILE so a ~200-connection pip
  burst cannot EMFILE-wedge the relay (2026-10-07 postmortem hardening).
"""

from __future__ import annotations

import asyncio
import json
import logging
import pathlib
import time

import pytest
from scripts.ci_throttle_proxy import (
    DEFAULT_PORT,
    HEALTH_PATH,
    TokenBucket,
    build_health_response,
    is_health_request,
    parse_rate_mbps,
    rewrite_absolute_uri,
)


class FakeClock:
    """Monotonic clock the tests advance by hand."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class FakeSleeper:
    """Records awaited durations; advancing the clock is the test's job.

    Must yield to the event loop (a bare no-await coroutine would never
    give control back and acquire() would monopolize the loop).
    """

    def __init__(self) -> None:
        self.waited: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.waited.append(seconds)
        await asyncio.sleep(0)


class TestTokenBucket:
    def test_burst_is_immediately_available(self) -> None:
        clock = FakeClock()
        bucket = TokenBucket(rate_bps=1000, burst=100, clock=clock)

        asyncio.run(bucket.acquire(100))
        assert bucket.tokens == 0

    def test_refill_accrues_at_configured_rate(self) -> None:
        clock = FakeClock()
        bucket = TokenBucket(rate_bps=1000, burst=100, clock=clock)
        asyncio.run(bucket.acquire(100))

        clock.now += 0.05  # 1000 B/s * 0.05s = 50 tokens, below the cap
        bucket.refill_now()
        assert bucket.tokens == pytest.approx(50)

        clock.now += 10
        bucket.refill_now()
        assert bucket.tokens == 100  # capped at burst, never grows past it

    def test_acquire_waits_when_exhausted(self) -> None:
        clock = FakeClock()
        sleeper = FakeSleeper()

        async def scenario() -> None:
            bucket = TokenBucket(rate_bps=1000, burst=100, clock=clock, sleep=sleeper)
            await bucket.acquire(100)

            pending = asyncio.ensure_future(bucket.acquire(50))
            for _ in range(5):
                await asyncio.sleep(0)  # give acquire turns to park
            assert not pending.done(), "acquire must not return while tokens are missing"
            assert sleeper.waited, "acquire must sleep for the deficit interval"
            pending.cancel()

        asyncio.run(scenario())

    def test_acquire_completes_after_clock_advances(self) -> None:
        clock = FakeClock()
        sleeper = FakeSleeper()

        async def scenario() -> None:
            bucket = TokenBucket(rate_bps=1000, burst=100, clock=clock, sleep=sleeper)
            await bucket.acquire(100)

            acquired = asyncio.ensure_future(bucket.acquire(50))
            await asyncio.sleep(0)  # let acquire park
            clock.now += 0.06  # >50 bytes of refill (float-margin safe)
            for _ in range(5):
                await asyncio.sleep(0)
            assert acquired.done()
            await acquired

        asyncio.run(scenario())


class TestRateParsing:
    def test_valid_rate(self) -> None:
        assert parse_rate_mbps("40") == 40.0
        assert parse_rate_mbps("12.5") == 12.5

    def test_env_style_blank_falls_back_to_caller(self) -> None:
        with pytest.raises(ValueError):
            parse_rate_mbps("")

    def test_non_positive_rejected(self) -> None:
        with pytest.raises(ValueError):
            parse_rate_mbps("0")
        with pytest.raises(ValueError):
            parse_rate_mbps("-5")

    def test_garbage_rejected(self) -> None:
        with pytest.raises(ValueError):
            parse_rate_mbps("fast")

    def test_non_finite_rejected(self) -> None:
        # "inf" would silently uncap the proxy; "nan" would park every
        # acquire() forever — both must raise, not degrade the cap.
        for bad in ("inf", "-inf", "infinity", "nan"):
            with pytest.raises(ValueError):
                parse_rate_mbps(bad)


class TestRequestRewrite:
    def test_absolute_uri_becomes_origin_form(self) -> None:
        path, host = rewrite_absolute_uri(
            "GET", "http://pypi.tuna.tsinghua.edu.cn/simple/nfm-db/"
        )
        assert path == "/simple/nfm-db/"
        assert host == "pypi.tuna.tsinghua.edu.cn"

    def test_port_preserved_in_host_header(self) -> None:
        path, host = rewrite_absolute_uri(
            "GET", "http://mirrors.aliyun.com:8080/pypi/simple/"
        )
        assert path == "/pypi/simple/"
        assert host == "mirrors.aliyun.com:8080"

    def test_query_string_kept(self) -> None:
        path, _ = rewrite_absolute_uri(
            "GET", "http://registry.npmmirror.com/pkg?tab=dist"
        )
        assert path == "/pkg?tab=dist"

    def test_bare_path_has_no_host(self) -> None:
        path, host = rewrite_absolute_uri("GET", "/healthz")
        assert path == "/healthz"
        assert host == ""


class TestHealthDetection:
    def test_proxy_form_to_self(self) -> None:
        url = f"http://127.0.0.1:{DEFAULT_PORT}{HEALTH_PATH}"
        assert is_health_request("GET", url, f"127.0.0.1:{DEFAULT_PORT}", DEFAULT_PORT)

    def test_origin_form_with_matching_host_header(self) -> None:
        assert is_health_request("GET", HEALTH_PATH, f"localhost:{DEFAULT_PORT}", DEFAULT_PORT)

    def test_other_host_is_not_health(self) -> None:
        url = f"http://pypi.tuna.tsinghua.edu.cn{HEALTH_PATH}"
        assert not is_health_request("GET", url, "pypi.tuna.tsinghua.edu.cn", DEFAULT_PORT)

    def test_other_path_is_not_health(self) -> None:
        assert not is_health_request("GET", "/nope", f"127.0.0.1:{DEFAULT_PORT}", DEFAULT_PORT)


class TestHealthResponse:
    def test_shape(self) -> None:
        payload = build_health_response(
            rate_mbps=40.0, port=7899, bytes_relayed=5, connections=1,
            started_iso="2026-10-07T09:31:48.300575+00:00",
        )
        head, _, body = payload.partition(b"\r\n\r\n")
        assert head.startswith(b"HTTP/1.1 200 OK")
        assert b"connection: close" in head.lower()
        doc = json.loads(body)
        assert doc["status"] == "ok"
        assert doc["rate_mbps"] == 40.0
        assert doc["port"] == 7899

    def test_started_reports_proxy_start_not_response_time(self) -> None:
        """`started` must be the process start, not `now()` at answer time.

        2026-10-07: the first EMFILE diagnosis burned a cycle misreading a
        health `started` of "now" as evidence of a mystery restart while
        launchd showed pid never exited. Health telemetry must be literal.
        """
        boot = "2026-10-07T09:31:48.300575+00:00"
        later = build_health_response(
            rate_mbps=40.0, port=7899, bytes_relayed=987, connections=3,
            started_iso=boot,
        )
        doc = json.loads(later.partition(b"\r\n\r\n")[2])
        assert doc["started"] == boot


class TestUtcLogTimestamps:
    """Log prefixes must be true UTC, not local-time-with-a-Z-suffix.

    2026-10-07: the proxy's stderr log printed `17:31:48Z` for true
    09:31:48Z (host tz +0800) — the `%(asctime)s` default is local time
    and the format string appended a literal "Z". The mislabel nearly
    misdirected the EMFILE/reboot-window diagnosis (same trap class as
    the NFM-5346 RCA's local-time DiagnosticReports filenames).
    """

    def test_formatter_renders_utc_timestamp(self) -> None:
        import time as time_mod

        from scripts.ci_throttle_proxy import utc_log_formatter

        fmt = utc_log_formatter()
        assert fmt.converter is time_mod.gmtime, (
            "log formatter must convert via time.gmtime, not local time"
        )
        # 2026-10-07T09:31:48Z — the first post-reboot proxy start.
        fixed_epoch = 1791365508.0
        record = logging.LogRecord(
            name="nfmd-ci-throttle", level=logging.INFO, pathname=__file__,
            lineno=1, msg="starting", args=(), exc_info=None,
        )
        record.created = fixed_epoch
        record.msecs = 0.0
        rendered = fmt.format(record)
        assert rendered.startswith("2026-10-07T09:31:48Z "), (
            f"log prefix must be true UTC even on a +0800 host, got {rendered!r}"
        )


class TestRaiseFdLimit:
    """A pip download burst opens 200+ concurrent upstream sockets; launchd's
    default 256 soft RLIMIT_NOFILE EMFILE-killed the relay mid-burst on
    2026-10-07 08:45Z. The proxy must raise its own soft limit at startup,
    wedge-proof (works regardless of launchd plist reload state)."""

    def test_soft_limit_raised_toward_target(self, monkeypatch) -> None:
        import scripts.ci_throttle_proxy as proxy_mod

        monkeypatch.setattr(proxy_mod.resource, "getrlimit",
                            lambda _which: (256, 10240))
        set_calls: list[tuple[int, int]] = []

        def fake_setrlimit(_which, limits):
            set_calls.append(limits)

        monkeypatch.setattr(proxy_mod.resource, "setrlimit", fake_setrlimit)
        old, new = proxy_mod.raise_fd_limit(target_soft=4096)
        assert (old, new) == (256, 4096)
        assert set_calls == [(4096, 10240)]  # (new_soft, hard)

    def test_target_capped_at_hard_limit(self, monkeypatch) -> None:
        import scripts.ci_throttle_proxy as proxy_mod

        monkeypatch.setattr(proxy_mod.resource, "getrlimit",
                            lambda _which: (256, 1024))
        monkeypatch.setattr(proxy_mod.resource, "setrlimit",
                            lambda _which, limits: None)
        old, new = proxy_mod.raise_fd_limit(target_soft=4096)
        assert (old, new) == (256, 1024)

    def test_setrlimit_failure_is_nonfatal(self, monkeypatch) -> None:
        import scripts.ci_throttle_proxy as proxy_mod

        monkeypatch.setattr(proxy_mod.resource, "getrlimit",
                            lambda _which: (256, 10240))

        def boom(_which, _limits):
            raise OSError("not permitted")

        monkeypatch.setattr(proxy_mod.resource, "setrlimit", boom)
        old, new = proxy_mod.raise_fd_limit(target_soft=4096)
        assert (old, new) == (256, 256)

    def test_already_high_is_noop(self, monkeypatch) -> None:
        import scripts.ci_throttle_proxy as proxy_mod

        monkeypatch.setattr(proxy_mod.resource, "getrlimit",
                            lambda _which: (8192, 65536))
        monkeypatch.setattr(proxy_mod.resource, "setrlimit",
                            lambda _which, limits: (_ for _ in ()).throw(
                                AssertionError("must not call setrlimit")))
        old, new = proxy_mod.raise_fd_limit(target_soft=4096)
        assert (old, new) == (8192, 8192)


class TestEndToEnd:
    """Live socket tests: relay works AND the pace is enforced (AC-1 in miniature)."""

    PAYLOAD = b"x" * (256 * 1024)  # 256 KiB

    @staticmethod
    async def _origin(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        # Consume the proxied request like a real origin, and wait_closed()
        # before returning: a handler that exits with buffered bytes gets its
        # transport aborted and the client sees a short read (a test-origin
        # artifact, not proxy behavior).
        await reader.read(4096)
        writer.write(
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: application/octet-stream\r\n"
            b"Content-Length: " + str(len(TestEndToEnd.PAYLOAD)).encode() + b"\r\n"
            b"Connection: close\r\n\r\n"
        )
        writer.write(TestEndToEnd.PAYLOAD)
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    async def _run_fetch(self, rate_bytes_per_sec: int) -> tuple[bytes, float]:
        from scripts.ci_throttle_proxy import ThrottleProxy

        origin = await asyncio.start_server(self._origin, "127.0.0.1", 0)
        origin_port = origin.sockets[0].getsockname()[1]

        proxy = ThrottleProxy(
            rate_mbps=rate_bytes_per_sec * 8 / 1_000_000,
            port=0,
            burst_seconds=8 / rate_bytes_per_sec,
        )
        await proxy.start()
        started = time.monotonic()

        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", proxy.port)
            request = (
                f"GET http://127.0.0.1:{origin_port}/big.bin HTTP/1.1\r\n"
                f"Host: 127.0.0.1:{origin_port}\r\n"
                f"Proxy-Connection: close\r\n\r\n"
            )
            writer.write(request.encode())
            await writer.drain()
            await reader.readuntil(b"\r\n\r\n")  # response head first
            body = await reader.readexactly(len(self.PAYLOAD))
            elapsed = time.monotonic() - started
        finally:
            writer.close()
            await proxy.close()
            origin.close()
            await origin.wait_closed()
        return body, elapsed

    def test_http_fetch_relayed_and_capped(self) -> None:
        # 256 KiB at 256 KiB/s (2 Mbit/s) with a one-chunk burst must take
        # at least ~0.4s; the same fetch through a 8x faster proxy serves
        # as the uncapped-ish control so the assertion cannot pass because
        # relay is merely slow for unrelated reasons.
        body, elapsed = asyncio.run(self._run_fetch(rate_bytes_per_sec=256 * 1024))
        assert body == self.PAYLOAD
        assert elapsed >= 0.4, f"cap not enforced: {len(body)} bytes in {elapsed:.3f}s"

        _, control = asyncio.run(self._run_fetch(rate_bytes_per_sec=2 * 1024 * 1024))
        assert elapsed > control, "capped fetch should be slower than the 8x-faster control"


class TestRelayTeardown:
    """A relay must never outlive both of its peers (NFM-5333 review).

    A long-running KeepAlive LaunchAgent accumulates leaked handler tasks
    and file descriptors if a stalled peer parks a pump forever: the health
    gauge's ``connections`` climbs permanently and fds run out. These tests
    reproduce the two stall shapes against live sockets.
    """

    STALL_SECONDS = 30.0

    @staticmethod
    async def _stalled_origin(_reader: asyncio.StreamReader, _writer: asyncio.StreamWriter) -> None:
        # Accept the TCP connection, then never send a byte.
        await asyncio.sleep(TestRelayTeardown.STALL_SECONDS)

    def test_client_abort_frees_the_tunnel(self) -> None:
        async def scenario() -> None:
            from scripts.ci_throttle_proxy import ThrottleProxy

            origin = await asyncio.start_server(self._stalled_origin, "127.0.0.1", 0)
            origin_port = origin.sockets[0].getsockname()[1]
            proxy = ThrottleProxy(rate_mbps=40.0, port=0)
            await proxy.start()
            try:
                reader, writer = await asyncio.open_connection("127.0.0.1", proxy.port)
                writer.write(
                    f"CONNECT 127.0.0.1:{origin_port} HTTP/1.1\r\n\r\n".encode()
                )
                await writer.drain()
                await reader.readuntil(b"\r\n\r\n")
                writer.close()
                await writer.wait_closed()

                deadline = time.monotonic() + 5.0
                while proxy._active > 0 and time.monotonic() < deadline:
                    await asyncio.sleep(0.05)
                assert proxy._active == 0, (
                    "client hangup must tear down the whole tunnel, not leave "
                    "the upstream pump parked on the stalled origin"
                )
            finally:
                await proxy.close()
                origin.close()
                await origin.wait_closed()

        asyncio.run(scenario())

    def test_stalled_upstream_hits_idle_timeout(self, monkeypatch) -> None:
        from scripts import ci_throttle_proxy

        monkeypatch.setattr(ci_throttle_proxy, "IDLE_TIMEOUT_SECONDS", 0.5)

        async def scenario() -> None:
            origin = await asyncio.start_server(
                self._stalled_origin, "127.0.0.1", 0
            )
            origin_port = origin.sockets[0].getsockname()[1]
            proxy = ci_throttle_proxy.ThrottleProxy(rate_mbps=40.0, port=0)
            await proxy.start()
            try:
                reader, writer = await asyncio.open_connection("127.0.0.1", proxy.port)
                writer.write(
                    f"CONNECT 127.0.0.1:{origin_port} HTTP/1.1\r\n\r\n".encode()
                )
                await writer.drain()
                await reader.readuntil(b"\r\n\r\n")

                closed = await asyncio.wait_for(reader.read(1), timeout=5.0)
                assert closed == b"", (
                    "a stalled upstream must hit the relay idle timeout and "
                    "close the client connection"
                )
                deadline = time.monotonic() + 5.0
                while proxy._active > 0 and time.monotonic() < deadline:
                    await asyncio.sleep(0.05)
                assert proxy._active == 0, "timed-out relay must release its handler"
            finally:
                writer.close()
                await proxy.close()
                origin.close()
                await origin.wait_closed()

        asyncio.run(scenario())


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


class TestBuildPathWiring:
    """The cap only protects the P0 budget if every bulk-download build
    path on the prod host actually routes through the proxy. These pin
    the wiring so a refactor cannot silently drop a leg back to line
    rate (the exact gap the NFM-5273 RCA documented)."""

    def test_deploy_prod_builds_are_health_gated(self) -> None:
        script = (REPO_ROOT / "scripts" / "deploy_prod.sh").read_text()
        assert "nfmd_ci_throttle_ready" in script
        assert "host.docker.internal:7899" in script
        # capped leg sets the proxy env for the three builds; fallback leg
        # reproduces the exact pre-NFM-5333 (NFM-2502) behavior.
        assert "HTTP_PROXY=" in script and "HTTPS_PROXY=" in script
        assert "pre-NFM-5333 / NFM-2502 behavior" in script
        # the builds must live INSIDE the health-gated subshell
        gated = script[script.index("nfmd_ci_throttle_ready()"):]
        for image in ("prod-api.Dockerfile", "lightrag.Dockerfile", "web.Dockerfile"):
            assert image in gated, f"{image} build must be inside the gated subshell"

    def test_workflow_candidate_build_is_health_gated(self) -> None:
        wf = (
            REPO_ROOT / ".github" / "workflows" / "production-deployment.yml"
        ).read_text()
        # candidate build caps through the same loopback proxy, with the
        # uncapped NFM-2502 leg as the fallback when the proxy is down.
        assert "__nfmd_ci_throttle_health" in wf
        assert "host.docker.internal:7899" in wf
        assert "NFM-2502 behavior" in wf

    def test_dockerfiles_keep_direct_final_legs(self) -> None:
        for name in ("prod-api", "web", "lightrag"):
            dockerfile = (REPO_ROOT / "docker" / f"{name}.Dockerfile").read_text()
            assert "env -u HTTP_PROXY" in dockerfile, (
                f"docker/{name}.Dockerfile must strip proxy env on its final "
                "download leg — a dead proxy may cost speed, never the build"
            )
