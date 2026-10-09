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
  burst cannot EMFILE-wedge the relay (2026-10-07 postmortem hardening);
* the NFM-5425 sick-edge supervisor kills a tunnel only when it has crept
  continuously under the trickle floor for a (near-)full window while the
  global bucket had spare capacity — healthy throughput, bursty metadata
  traffic, a saturated bucket, and idle pools are all left alone, and a
  kill surfaces in the log and the health payload with the edge IP.
"""

from __future__ import annotations

import asyncio
import json
import logging
import pathlib
import time
from collections.abc import Callable

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
        path, host = rewrite_absolute_uri("GET", "http://pypi.tuna.tsinghua.edu.cn/simple/nfm-db/")
        assert path == "/simple/nfm-db/"
        assert host == "pypi.tuna.tsinghua.edu.cn"

    def test_port_preserved_in_host_header(self) -> None:
        path, host = rewrite_absolute_uri("GET", "http://mirrors.aliyun.com:8080/pypi/simple/")
        assert path == "/pypi/simple/"
        assert host == "mirrors.aliyun.com:8080"

    def test_query_string_kept(self) -> None:
        path, _ = rewrite_absolute_uri("GET", "http://registry.npmmirror.com/pkg?tab=dist")
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
            rate_mbps=40.0,
            port=7899,
            bytes_relayed=5,
            connections=1,
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
            rate_mbps=40.0,
            port=7899,
            bytes_relayed=987,
            connections=3,
            started_iso=boot,
        )
        doc = json.loads(later.partition(b"\r\n\r\n")[2])
        assert doc["started"] == boot

    def test_edge_telemetry_fields(self) -> None:
        """NFM-5425: sick-edge kills surface in the health doc with the
        edge IP, so deploy-watch can reconcile a slow window without
        reading the launchd log."""
        payload = build_health_response(
            rate_mbps=40.0,
            port=7899,
            bytes_relayed=1,
            connections=0,
            started_iso="2026-10-09T00:00:00+00:00",
            edge_kills=2,
            sick_edges=[
                {
                    "edge": "222.84.158.10",
                    "target": "mirrors.aliyun.com:443",
                    "moved_bytes": 9_000_000,
                    "window_s": 180.0,
                    "at": "2026-10-09T04:00:00+00:00",
                }
            ],
        )
        doc = json.loads(payload.partition(b"\r\n\r\n")[2])
        assert doc["edge_kills"] == 2
        assert doc["sick_edges"][0]["edge"] == "222.84.158.10"

    def test_edge_telemetry_defaults_to_empty(self) -> None:
        payload = build_health_response(
            rate_mbps=40.0,
            port=7899,
            bytes_relayed=0,
            connections=0,
            started_iso="2026-10-09T00:00:00+00:00",
        )
        doc = json.loads(payload.partition(b"\r\n\r\n")[2])
        assert doc["edge_kills"] == 0
        assert doc["sick_edges"] == []


class TestHealthStartedStability:
    """`started` must be byte-stable across separate live health answers.

    The pre-hardening proxy stamped answer-time into `started`, so two
    GETs a quarter-second apart disagreed — which reads as mystery
    restarts during an incident (2026-10-07 EMFILE diagnosis). NFM-5348
    deliverable #2's live form, complementing the pass-through unit test.
    """

    def test_started_identical_across_two_live_gets(self) -> None:
        from scripts.ci_throttle_proxy import ThrottleProxy

        async def scenario() -> tuple[str, str, str]:
            proxy = ThrottleProxy(rate_mbps=40.0, port=0)
            await proxy.start()
            try:
                docs: list[str] = []
                for _ in range(2):
                    reader, writer = await asyncio.open_connection("127.0.0.1", proxy.port)
                    writer.write(
                        f"GET {HEALTH_PATH} HTTP/1.1\r\n"
                        f"Host: 127.0.0.1:{proxy.port}\r\n"
                        "Connection: close\r\n\r\n".encode()
                    )
                    await writer.drain()
                    raw = await reader.read()
                    writer.close()
                    docs.append(json.loads(raw.partition(b"\r\n\r\n")[2])["started"])
                    await asyncio.sleep(0.25)
                return docs[0], docs[1], proxy._started_iso
            finally:
                await asyncio.wait_for(proxy.close(), timeout=10.0)

        first, second, boot = asyncio.run(asyncio.wait_for(scenario(), timeout=15.0))
        assert first == second == boot, (
            f"health `started` drifted across answers: {first!r} vs {second!r} "
            f"(proxy boot {boot!r})"
        )


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
            name="nfmd-ci-throttle",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="starting",
            args=(),
            exc_info=None,
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

        monkeypatch.setattr(proxy_mod.resource, "getrlimit", lambda _which: (256, 10240))
        set_calls: list[tuple[int, int]] = []

        def fake_setrlimit(_which, limits):
            set_calls.append(limits)

        monkeypatch.setattr(proxy_mod.resource, "setrlimit", fake_setrlimit)
        old, new = proxy_mod.raise_fd_limit(target_soft=4096)
        assert (old, new) == (256, 4096)
        assert set_calls == [(4096, 10240)]  # (new_soft, hard)

    def test_target_capped_at_hard_limit(self, monkeypatch) -> None:
        import scripts.ci_throttle_proxy as proxy_mod

        monkeypatch.setattr(proxy_mod.resource, "getrlimit", lambda _which: (256, 1024))
        monkeypatch.setattr(proxy_mod.resource, "setrlimit", lambda _which, limits: None)
        old, new = proxy_mod.raise_fd_limit(target_soft=4096)
        assert (old, new) == (256, 1024)

    def test_setrlimit_failure_is_nonfatal(self, monkeypatch) -> None:
        import scripts.ci_throttle_proxy as proxy_mod

        monkeypatch.setattr(proxy_mod.resource, "getrlimit", lambda _which: (256, 10240))

        def boom(_which, _limits):
            raise OSError("not permitted")

        monkeypatch.setattr(proxy_mod.resource, "setrlimit", boom)
        old, new = proxy_mod.raise_fd_limit(target_soft=4096)
        assert (old, new) == (256, 256)

    def test_already_high_is_noop(self, monkeypatch) -> None:
        import scripts.ci_throttle_proxy as proxy_mod

        monkeypatch.setattr(proxy_mod.resource, "getrlimit", lambda _which: (8192, 65536))
        monkeypatch.setattr(
            proxy_mod.resource,
            "setrlimit",
            lambda _which, limits: (_ for _ in ()).throw(AssertionError("must not call setrlimit")),
        )
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
            await asyncio.wait_for(proxy.close(), timeout=10.0)
            origin.close()
            await asyncio.wait_for(origin.wait_closed(), timeout=10.0)
        return body, elapsed

    def test_http_fetch_relayed_and_capped(self) -> None:
        # 256 KiB at 256 KiB/s (2 Mbit/s) with a one-chunk burst must take
        # at least ~0.4s; the same fetch through a 8x faster proxy serves
        # as the uncapped-ish control so the assertion cannot pass because
        # relay is merely slow for unrelated reasons.
        body, elapsed = asyncio.run(
            asyncio.wait_for(self._run_fetch(rate_bytes_per_sec=256 * 1024), timeout=60.0)
        )
        assert body == self.PAYLOAD
        assert elapsed >= 0.4, f"cap not enforced: {len(body)} bytes in {elapsed:.3f}s"

        _, control = asyncio.run(
            asyncio.wait_for(self._run_fetch(rate_bytes_per_sec=2 * 1024 * 1024), timeout=60.0)
        )
        assert elapsed > control, "capped fetch should be slower than the 8x-faster control"


class TestRelayTeardown:
    """A relay must never outlive both of its peers (NFM-5333 review).

    A long-running KeepAlive LaunchAgent accumulates leaked handler tasks
    and file descriptors if a stalled peer parks a pump forever: the health
    gauge's ``connections`` climbs permanently and fds run out. These tests
    reproduce the stall shapes against live sockets.

    NFM-5401 hardening (2026-10-09): two stacked 3.12-only traps took the
    whole Batch1 lane down for the job's 20-minute timeout on every PR
    since NFM-5333. (1) A client hangup left the request-direction pump
    parked forever — the handler never finished (fixed in the proxy:
    state-polled peer watchdog + transport-close teardown). (2) Python
    3.12's ``Server.wait_closed()`` then never returned even once handlers
    finished: it hangs whenever a handler task was active at ``close()``
    (repro'd 2026-10-09: 3.12.12 hangs in both close/handler orderings,
    3.13+ returns — the authors' macOS interpreters never saw it). Every
    scenario here is therefore wall-clock bounded end to end (outer
    ``wait_for`` + bounded teardowns) and origin teardown releases
    handlers by Event instead of awaiting ``wait_closed()`` — a
    regression of either shape now FAILS in seconds, not one 20-minute
    CANCELLED per PR.
    """

    @staticmethod
    async def _start_stalled_origin() -> tuple[asyncio.AbstractServer, asyncio.Event]:
        """Origin that accepts, then never reads or writes until released.

        The stall is Event-driven, not ``sleep(30)``: Python 3.12's
        ``Server.wait_closed()`` waits for handler tasks, so a wall-clock
        stall added up to 30s of dead lane time to every one of these
        teardowns even when the proxy behaved correctly.
        """
        release = asyncio.Event()

        async def stalled(_reader: asyncio.StreamReader, _writer: asyncio.StreamWriter) -> None:
            await release.wait()

        server = await asyncio.start_server(stalled, "127.0.0.1", 0)
        return server, release

    def test_client_abort_frees_the_tunnel(self) -> None:
        async def scenario() -> None:
            from scripts.ci_throttle_proxy import ThrottleProxy

            origin, release = await self._start_stalled_origin()
            origin_port = origin.sockets[0].getsockname()[1]
            proxy = ThrottleProxy(rate_mbps=40.0, port=0)
            await proxy.start()
            try:
                reader, writer = await asyncio.open_connection("127.0.0.1", proxy.port)
                writer.write(f"CONNECT 127.0.0.1:{origin_port} HTTP/1.1\r\n\r\n".encode())
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
                await asyncio.wait_for(proxy.close(), timeout=10.0)
                # Release the origin handler by Event and DO NOT await
                # origin.wait_closed(): py3.12's Server.wait_closed()
                # hangs forever when a handler was active at close()
                # (fixed in 3.13; repro'd 2026-10-09 both orderings).
                # The released handler drains during loop shutdown.
                release.set()
                origin.close()

        asyncio.run(asyncio.wait_for(scenario(), timeout=30.0))

    def test_client_hangup_tears_down_even_if_request_pump_never_wakes(self, monkeypatch) -> None:
        """NFM-5401 regression pin: teardown must not need the request pump.

        The Linux-runner race cancelled every Batch1 since NFM-5333: after
        the client hung up, the ONLY thing that could end the handler was
        the request-direction pump's ``read()`` resolving — and on the
        Linux runners it never did (9-minute faulthandler dump: loop idle
        in ``selectors.select``, handler parked, no fd event, no timer).
        This test deletes that path outright — the request pump parks on
        an Event that never completes — so the tunnel can only come down
        if the proxy observes the client transport closing by some OTHER
        means and tears both pumps down.
        """
        from scripts import ci_throttle_proxy

        real_pump = ci_throttle_proxy._pump

        async def pump_that_never_wakes_on_the_request_side(
            reader: asyncio.StreamReader,
            writer: asyncio.StreamWriter,
            bucket: TokenBucket | None,
            counter: Callable[[int], None],
            idle_timeout: float | None = None,
        ) -> None:
            if bucket is None:  # the request direction: uncapped by design
                await asyncio.Event().wait()  # never completes inside this test
            await real_pump(reader, writer, bucket, counter, idle_timeout=idle_timeout)

        monkeypatch.setattr(ci_throttle_proxy, "_pump", pump_that_never_wakes_on_the_request_side)

        async def scenario() -> None:
            origin, release = await self._start_stalled_origin()
            origin_port = origin.sockets[0].getsockname()[1]
            proxy = ci_throttle_proxy.ThrottleProxy(rate_mbps=40.0, port=0)
            await proxy.start()
            try:
                reader, writer = await asyncio.open_connection("127.0.0.1", proxy.port)
                writer.write(f"CONNECT 127.0.0.1:{origin_port} HTTP/1.1\r\n\r\n".encode())
                await writer.drain()
                await reader.readuntil(b"\r\n\r\n")
                writer.close()
                await writer.wait_closed()

                deadline = time.monotonic() + 5.0
                while proxy._active > 0 and time.monotonic() < deadline:
                    await asyncio.sleep(0.05)
                assert proxy._active == 0, (
                    "client hangup must tear the tunnel down even when the "
                    "request-direction pump never wakes — a relay must never "
                    "outlive its client"
                )
            finally:
                await asyncio.wait_for(proxy.close(), timeout=10.0)
                # Release the origin handler by Event and DO NOT await
                # origin.wait_closed(): py3.12's Server.wait_closed()
                # hangs forever when a handler was active at close()
                # (fixed in 3.13; repro'd 2026-10-09 both orderings).
                # The released handler drains during loop shutdown.
                release.set()
                origin.close()

        asyncio.run(asyncio.wait_for(scenario(), timeout=30.0))

    def test_stalled_upstream_hits_idle_timeout(self, monkeypatch) -> None:
        from scripts import ci_throttle_proxy

        monkeypatch.setattr(ci_throttle_proxy, "IDLE_TIMEOUT_SECONDS", 0.5)

        async def scenario() -> None:
            origin, release = await self._start_stalled_origin()
            origin_port = origin.sockets[0].getsockname()[1]
            proxy = ci_throttle_proxy.ThrottleProxy(rate_mbps=40.0, port=0)
            await proxy.start()
            try:
                reader, writer = await asyncio.open_connection("127.0.0.1", proxy.port)
                writer.write(f"CONNECT 127.0.0.1:{origin_port} HTTP/1.1\r\n\r\n".encode())
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
                await asyncio.wait_for(proxy.close(), timeout=10.0)
                # Release the origin handler by Event and DO NOT await
                # origin.wait_closed(): py3.12's Server.wait_closed()
                # hangs forever when a handler was active at close()
                # (fixed in 3.13; repro'd 2026-10-09 both orderings).
                # The released handler drains during loop shutdown.
                release.set()
                origin.close()

        asyncio.run(asyncio.wait_for(scenario(), timeout=30.0))


class TestSickEdgeWatchdog:
    """NFM-5425: a trickle-serving CDN edge never fails and never idles out.

    pip pools its CONNECT tunnels for the whole install, and each tunnel
    stays pinned to whatever CDN edge getaddrinfo returned at connect
    time — so a degraded edge serving 0.2-0.5 Mbps (2026-10-09:
    222.84.158.10-.41 and 125.73.210.105, while sibling edges of the same
    mirror ran 1.6-2.4 MB/s) makes the Dockerfile mirror ladder crawl
    indefinitely: the `||` advances only on leg FAILURE, and a trickle is
    neither. The proxy must detect the trickle itself and kill the
    tunnel — pip spends one of its --retries and reconnects onto a
    freshly resolved edge. The manual `launchctl kickstart -k` mitigation
    proven 3x that day killed every tunnel at once; the supervisor is the
    surgical version, one verified-sick tunnel at a time.

    Thresholds are monkeypatched to seconds/KiB (shipped defaults:
    180s window / 24MiB floor); the "not killed" scenarios pin the
    false-kill guards — the shared cap must never be mistaken for a sick
    edge (that kill would churn healthy tunnels on every capped build).
    """

    @staticmethod
    def _retune(monkeypatch, *, max_bytes: int = 128 * 1024):
        """Shrink the EDGE_* constants to test scale. Returns the module."""
        from scripts import ci_throttle_proxy as mod

        monkeypatch.setattr(mod, "EDGE_SUPERVISOR_TICK_SECONDS", 0.05)
        monkeypatch.setattr(mod, "EDGE_TRICKLE_WINDOW_SECONDS", 1.0)
        monkeypatch.setattr(mod, "EDGE_MIN_AGE_SECONDS", 1.0)
        monkeypatch.setattr(mod, "EDGE_TRICKLE_MAX_BYTES", max_bytes)
        return mod

    @staticmethod
    async def _start_feeding_origin(chunk: bytes, interval: float):
        """Origin that writes ``chunk`` every ``interval`` forever, then
        never EOFs — a CDN edge in miniature. Small chunk + short interval
        models the trickle; large chunk + long interval models pip's
        request/response metadata phase (bursts with quiet gaps)."""
        stop = asyncio.Event()

        async def feed(_reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            try:
                while not stop.is_set():
                    writer.write(chunk)
                    await writer.drain()
                    await asyncio.sleep(interval)
            except (ConnectionError, asyncio.CancelledError):
                pass  # the proxy tore the tunnel down; that is the point

        server = await asyncio.start_server(feed, "127.0.0.1", 0)
        return server, stop

    @staticmethod
    async def _start_finite_stream(total: int, chunk: int, interval: float):
        """Origin that streams ``total`` bytes at a healthy pace, then EOFs."""
        stop = asyncio.Event()

        async def stream(_reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            try:
                sent = 0
                while sent < total and not stop.is_set():
                    n = min(chunk, total - sent)
                    writer.write(b"x" * n)
                    await writer.drain()
                    sent += n
                    await asyncio.sleep(interval)
            except (ConnectionError, asyncio.CancelledError):
                pass
            # returning closes the transport: the tunnel sees a clean EOF

        server = await asyncio.start_server(stream, "127.0.0.1", 0)
        return server, stop

    @staticmethod
    async def _get_health(port: int) -> dict:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(
            f"GET {HEALTH_PATH} HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{port}\r\nConnection: close\r\n\r\n".encode()
        )
        await writer.drain()
        raw = await reader.read()
        writer.close()
        return json.loads(raw.partition(b"\r\n\r\n")[2])

    def test_trickling_connect_tunnel_is_killed(self, monkeypatch) -> None:
        mod = self._retune(monkeypatch)

        async def scenario() -> None:
            origin, stop = await self._start_feeding_origin(b"x" * 2048, 0.02)  # ~100KiB/s
            origin_port = origin.sockets[0].getsockname()[1]
            proxy = mod.ThrottleProxy(rate_mbps=40.0, port=0)
            await proxy.start()
            supervisor = asyncio.ensure_future(proxy.supervise_edges())
            try:
                reader, writer = await asyncio.open_connection("127.0.0.1", proxy.port)
                writer.write(f"CONNECT 127.0.0.1:{origin_port} HTTP/1.1\r\n\r\n".encode())
                await writer.drain()
                await reader.readuntil(b"\r\n\r\n")

                # read(-1) only resolves at EOF: an unkilled trickle hangs
                # here until wait_for times out — that is the failure mode
                # under test (the origin feeds forever).
                received = await asyncio.wait_for(reader.read(-1), timeout=8.0)
                assert received, "the trickle must have delivered bytes on the way out"
                assert proxy.edge_kills >= 1
                assert proxy.sick_edges, "the kill must land in health telemetry"
                assert proxy.sick_edges[-1]["edge"] == "127.0.0.1"
                assert proxy.sick_edges[-1]["target"] == f"127.0.0.1:{origin_port}"

                doc = await self._get_health(proxy.port)
                assert doc["edge_kills"] >= 1
                assert doc["sick_edges"][-1]["edge"] == "127.0.0.1"

                deadline = time.monotonic() + 5.0
                while proxy._active > 0 and time.monotonic() < deadline:
                    await asyncio.sleep(0.05)
                assert proxy._active == 0, "killed tunnel must release its handler"
            finally:
                supervisor.cancel()
                await asyncio.gather(supervisor, return_exceptions=True)
                writer.close()
                await asyncio.wait_for(proxy.close(), timeout=10.0)
                stop.set()
                origin.close()

        asyncio.run(asyncio.wait_for(scenario(), timeout=30.0))

    def test_trickling_plain_http_tunnel_is_killed(self, monkeypatch) -> None:
        """Same kill through the _forward_http leg (plain-HTTP mirrors)."""
        mod = self._retune(monkeypatch)

        async def scenario() -> None:
            origin, stop = await self._start_feeding_origin(b"x" * 2048, 0.02)
            origin_port = origin.sockets[0].getsockname()[1]
            proxy = mod.ThrottleProxy(rate_mbps=40.0, port=0)
            await proxy.start()
            supervisor = asyncio.ensure_future(proxy.supervise_edges())
            try:
                reader, writer = await asyncio.open_connection("127.0.0.1", proxy.port)
                writer.write(
                    f"GET http://127.0.0.1:{origin_port}/big.bin HTTP/1.1\r\n"
                    f"Host: 127.0.0.1:{origin_port}\r\n"
                    "Proxy-Connection: close\r\n\r\n".encode()
                )
                await writer.drain()

                received = await asyncio.wait_for(reader.read(-1), timeout=8.0)
                assert received
                assert proxy.edge_kills >= 1
            finally:
                supervisor.cancel()
                await asyncio.gather(supervisor, return_exceptions=True)
                writer.close()
                await asyncio.wait_for(proxy.close(), timeout=10.0)
                stop.set()
                origin.close()

        asyncio.run(asyncio.wait_for(scenario(), timeout=30.0))

    def test_healthy_throughput_is_not_killed(self, monkeypatch) -> None:
        mod = self._retune(monkeypatch)
        total = 4 * 1024 * 1024  # ~4s of ~1MiB/s: outlives window + min age

        async def scenario() -> None:
            origin, stop = await self._start_finite_stream(total, 50 * 1024, 0.05)
            origin_port = origin.sockets[0].getsockname()[1]
            proxy = mod.ThrottleProxy(rate_mbps=40.0, port=0)
            await proxy.start()
            supervisor = asyncio.ensure_future(proxy.supervise_edges())
            try:
                reader, writer = await asyncio.open_connection("127.0.0.1", proxy.port)
                writer.write(f"CONNECT 127.0.0.1:{origin_port} HTTP/1.1\r\n\r\n".encode())
                await writer.drain()
                await reader.readuntil(b"\r\n\r\n")

                body = await asyncio.wait_for(reader.readexactly(total), timeout=20.0)
                assert len(body) == total
                assert proxy.edge_kills == 0, "a healthy fast tunnel must never be killed"
            finally:
                supervisor.cancel()
                await asyncio.gather(supervisor, return_exceptions=True)
                writer.close()
                await asyncio.wait_for(proxy.close(), timeout=10.0)
                stop.set()
                origin.close()

        asyncio.run(asyncio.wait_for(scenario(), timeout=40.0))

    def test_bursty_metadata_traffic_is_not_killed(self, monkeypatch) -> None:
        """The creep discriminator: bursts with quiet gaps (pip resolving
        index metadata over one pooled tunnel) move few bytes per window,
        but not CONTINUOUSLY — that is not a sick edge and must survive."""
        mod = self._retune(monkeypatch)  # floor 128KiB/window; bursts are 60KiB

        async def scenario() -> None:
            origin, stop = await self._start_feeding_origin(b"x" * (60 * 1024), 1.2)
            origin_port = origin.sockets[0].getsockname()[1]
            proxy = mod.ThrottleProxy(rate_mbps=40.0, port=0)
            await proxy.start()
            supervisor = asyncio.ensure_future(proxy.supervise_edges())
            try:
                reader, writer = await asyncio.open_connection("127.0.0.1", proxy.port)
                writer.write(f"CONNECT 127.0.0.1:{origin_port} HTTP/1.1\r\n\r\n".encode())
                await writer.drain()
                await reader.readuntil(b"\r\n\r\n")

                # ~2 windows of burst traffic; without the creep rule every
                # judged window (60KiB < 128KiB floor) would kill this.
                await asyncio.wait_for(reader.readexactly(120 * 1024), timeout=15.0)
                assert proxy.edge_kills == 0
                assert proxy._active >= 1, "bursty tunnel must still be alive"
            finally:
                supervisor.cancel()
                await asyncio.gather(supervisor, return_exceptions=True)
                writer.close()
                await asyncio.wait_for(proxy.close(), timeout=10.0)
                stop.set()
                origin.close()

        asyncio.run(asyncio.wait_for(scenario(), timeout=30.0))

    def test_saturated_bucket_is_not_mistaken_for_sick_edge(self, monkeypatch) -> None:
        """The spare-capacity guard: with the cap itself the limiter,
        per-tunnel throughput sits under the floor through fair sharing —
        killing those tunnels would churn every capped build."""
        mod = self._retune(monkeypatch)

        async def scenario() -> None:
            origin, stop = await self._start_feeding_origin(b"x" * 2048, 0.02)
            origin_port = origin.sockets[0].getsockname()[1]
            # cap 0.05 Mbit/s = 6.25KiB/s: the feeder offers ~100KiB/s, so
            # the bucket — not the origin — is the bottleneck, and the
            # global window rate sits AT cap (not spare).
            proxy = mod.ThrottleProxy(rate_mbps=0.05, port=0, burst_seconds=2.0)
            await proxy.start()
            supervisor = asyncio.ensure_future(proxy.supervise_edges())
            try:
                reader, writer = await asyncio.open_connection("127.0.0.1", proxy.port)
                writer.write(f"CONNECT 127.0.0.1:{origin_port} HTTP/1.1\r\n\r\n".encode())
                await writer.drain()
                await reader.readuntil(b"\r\n\r\n")

                # ~2.2s of capped transfer: well past window + min age, the
                # tunnel sits far under the floor the whole time.
                await asyncio.wait_for(reader.readexactly(14 * 1024), timeout=20.0)
                assert proxy.edge_kills == 0, (
                    "fair-shared slowness under a saturated cap must not be killed"
                )
                assert proxy._active >= 1
            finally:
                supervisor.cancel()
                await asyncio.gather(supervisor, return_exceptions=True)
                writer.close()
                await asyncio.wait_for(proxy.close(), timeout=10.0)
                stop.set()
                origin.close()

        asyncio.run(asyncio.wait_for(scenario(), timeout=30.0))

    def test_kill_fires_alongside_healthy_companion_traffic(self, monkeypatch) -> None:
        """Pins the spare-capacity guard's units. The 2026-10-09 incidents
        all had healthy sibling tunnels moving a few Mbps alongside the
        trickle; the aggregate (~3Mbit/s here) is well under 25% of the
        40Mbit/s cap, so the trickle MUST still be killed. Comparing the
        global bit rate against the bucket's bytes/s rate number (8x off)
        blocks exactly this kill — the incident shape itself."""
        mod = self._retune(monkeypatch)

        async def scenario() -> None:
            stream_total = 768 * 1024  # ~300KiB/s for ~2.6s
            stream_origin, stream_stop = await self._start_finite_stream(
                stream_total, 15 * 1024, 0.05
            )
            trickle_origin, trickle_stop = await self._start_feeding_origin(b"x" * 2048, 0.02)
            stream_port = stream_origin.sockets[0].getsockname()[1]
            trickle_port = trickle_origin.sockets[0].getsockname()[1]
            proxy = mod.ThrottleProxy(rate_mbps=40.0, port=0)
            await proxy.start()
            supervisor = asyncio.ensure_future(proxy.supervise_edges())
            try:
                stream_reader, stream_writer = await asyncio.open_connection(
                    "127.0.0.1", proxy.port
                )
                stream_writer.write(f"CONNECT 127.0.0.1:{stream_port} HTTP/1.1\r\n\r\n".encode())
                await stream_writer.drain()
                await stream_reader.readuntil(b"\r\n\r\n")
                trickle_reader, trickle_writer = await asyncio.open_connection(
                    "127.0.0.1", proxy.port
                )
                trickle_writer.write(f"CONNECT 127.0.0.1:{trickle_port} HTTP/1.1\r\n\r\n".encode())
                await trickle_writer.drain()
                await trickle_reader.readuntil(b"\r\n\r\n")

                # The healthy companion delivers in full (~300KiB/s >= the
                # 128KiB window floor, so it is never a kill candidate).
                body = await asyncio.wait_for(stream_reader.readexactly(stream_total), timeout=20.0)
                assert len(body) == stream_total
                # The trickle is torn down despite the companion traffic.
                received = await asyncio.wait_for(trickle_reader.read(-1), timeout=10.0)
                assert received
                assert proxy.edge_kills == 1, (
                    "a trickle must be killed while healthy siblings use only a "
                    "few Mbit/s of a 40Mbit/s cap (spare-capacity guard units)"
                )
            finally:
                supervisor.cancel()
                await asyncio.gather(supervisor, return_exceptions=True)
                stream_writer.close()
                trickle_writer.close()
                await asyncio.wait_for(proxy.close(), timeout=10.0)
                stream_stop.set()
                trickle_stop.set()
                stream_origin.close()
                trickle_origin.close()

        asyncio.run(asyncio.wait_for(scenario(), timeout=40.0))

    def test_idle_tunnel_is_not_killed(self, monkeypatch) -> None:
        """moved == 0 (a pooled, silent tunnel) is not a trickle: true
        stalls belong to the idle timeout, idle pools to nobody."""
        mod = self._retune(monkeypatch)

        async def scenario() -> None:
            origin, release = await TestRelayTeardown._start_stalled_origin()
            origin_port = origin.sockets[0].getsockname()[1]
            proxy = mod.ThrottleProxy(rate_mbps=40.0, port=0)
            await proxy.start()
            supervisor = asyncio.ensure_future(proxy.supervise_edges())
            try:
                reader, writer = await asyncio.open_connection("127.0.0.1", proxy.port)
                writer.write(f"CONNECT 127.0.0.1:{origin_port} HTTP/1.1\r\n\r\n".encode())
                await writer.drain()
                await reader.readuntil(b"\r\n\r\n")

                await asyncio.sleep(2.0)  # > window + min age at test scale
                assert proxy.edge_kills == 0
                assert proxy._active >= 1, "silent idle tunnel must be left alone"
            finally:
                supervisor.cancel()
                await asyncio.gather(supervisor, return_exceptions=True)
                writer.close()
                await asyncio.wait_for(proxy.close(), timeout=10.0)
                release.set()
                origin.close()

        asyncio.run(asyncio.wait_for(scenario(), timeout=30.0))


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
        gated = script[script.index("nfmd_ci_throttle_ready()") :]
        for image in ("prod-api.Dockerfile", "lightrag.Dockerfile", "web.Dockerfile"):
            assert image in gated, f"{image} build must be inside the gated subshell"

    def test_workflow_candidate_build_is_health_gated(self) -> None:
        wf = (REPO_ROOT / ".github" / "workflows" / "production-deployment.yml").read_text()
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
