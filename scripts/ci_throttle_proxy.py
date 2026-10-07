#!/usr/bin/env python3
"""nfmd-ci-throttle — loopback rate-limiting forward proxy (NFM-5333).

Owner-approved mitigation (a) for the NFM-5273 RCA: self-hosted CI jobs on
the prod Mac Studio bulk-download build layers (pip wheels, pnpm packages,
corepack shims) during the three per-deploy ``docker build --no-cache``
runs and saturate the shared broadband, stalling the host↔CF-edge leg past
the sentinel 5s public-edge P0 budget (confirmed P0s 2026-09-28 07:30Z and
2026-09-29 15:59:44Z). This proxy caps the AGGREGATE download throughput
of exactly those fetches:

  * binds 127.0.0.1 only (no LAN exposure — it is an open relay by
    design, so loopback-only is a hard requirement, not a default);
  * one global token bucket shared by every connection — the cap is on
    total egress-to-client bytes, so 20 parallel pip connections cannot
    each get the full rate;
  * egress is DIRECT: no upstream VPN hop. NFM-2502 cleared proxy env on
    the api build precisely because the system v2cloud proxy tunneled CN
    mirrors abroad; this proxy connects straight to the target, so the
    tuna/aliyun/npmmirror fast domestic routes survive, only rate-capped;
  * answers ``GET /__nfmd_ci_throttle_health`` both in proxy form
    (absolute-URI via ``curl -x``) and direct form, so ``deploy_prod.sh``
    can gate its wiring on a live probe and fall back to the exact
    pre-NFM-5333 uncapped behavior when the proxy is down.

Wired in ``scripts/deploy_prod.sh``: build containers reach the host via
``host.docker.internal:7899`` (Docker Desktop resolves it to the host's
loopback). pip/pnpm/corepack honor HTTP(S)_PROXY; the legacy (non-BuildKit)
builder forwards those as predefined build args into every RUN step.

Stdlib only — runs under the host python3 as a LaunchAgent rendered from
``scripts/host/ci-throttle/io.nfmd.ci-throttle.plist.template`` (label
``io.nfmd.ci-throttle``), installed by ``scripts/install_ci_throttle.sh``
to ``~/.nfmd/ci-throttle/``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import os
import resource
import socket
import sys
import time
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlsplit

__version__ = "1.0.0"

DEFAULT_PORT = 7899
DEFAULT_RATE_MBPS = 40.0
DEFAULT_BURST_SECONDS = 2.0
DEFAULT_BIND = "127.0.0.1"
HEALTH_PATH = "/__nfmd_ci_throttle_health"
CHUNK_BYTES = 16 * 1024
# Bound the kernel-side prefetch on upstream sockets: without this the
# kernel drains the WAN into a large receive buffer during each paced
# window and the cap leaks by rcvbuf x connections.
UPSTREAM_RCVBUF = 64 * 1024
IDLE_TIMEOUT_SECONDS = 600.0

LOG = logging.getLogger("nfmd-ci-throttle")


class TokenBucket:
    """Asyncio-friendly token bucket: ``acquire(n)`` waits for n tokens.

    ``clock``/``sleep`` are injectable so tests can drive time by hand.
    Waiters re-check the balance after each sleep interval; with pip's
    handful of connections this is fair enough (no single stream can
    starve the others for longer than one chunk interval).
    """

    def __init__(
        self,
        rate_bps: float,
        burst: float,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], asyncio.Future[None]] = asyncio.sleep,
    ) -> None:
        if rate_bps <= 0:
            raise ValueError(f"rate_bps must be positive, got {rate_bps}")
        if burst <= 0:
            raise ValueError(f"burst must be positive, got {burst}")
        self._rate = float(rate_bps)
        self._burst = float(burst)
        self._tokens = float(burst)
        self._clock = clock
        self._sleep = sleep
        self._updated = clock()

    @property
    def rate_bps(self) -> float:
        return self._rate

    @property
    def burst(self) -> float:
        return self._burst

    @property
    def tokens(self) -> float:
        return self._tokens

    def refill_now(self) -> None:
        now = self._clock()
        elapsed = now - self._updated
        self._tokens = min(self._burst, self._tokens + elapsed * self._rate)
        self._updated = now

    async def acquire(self, amount: int) -> None:
        while True:
            self.refill_now()
            if self._tokens >= amount:
                self._tokens -= amount
                return
            deficit = amount - self._tokens
            await self._sleep(deficit / self._rate)


def parse_rate_mbps(value: str) -> float:
    """Parse a megabits-per-second rate; refuse anything non-positive.

    An unparseable cap must raise, never silently mean "no cap".
    """
    parsed = float(value.strip())
    if not math.isfinite(parsed) or parsed <= 0:
        raise ValueError(f"rate must be a finite positive number, got {parsed!r}")
    return parsed


def rewrite_absolute_uri(_method: str, request_line_url: str) -> tuple[str, str]:
    """Split an absolute-URI request target into (origin-form path, host).

    ``GET http://host:port/path?q HTTP/1.1`` → ``('/path?q', 'host:port')``.
    An origin-form target (``/path``) passes through with an empty host —
    the caller then falls back to the Host header.
    """
    if "://" not in request_line_url:
        return request_line_url, ""
    parts = urlsplit(request_line_url)
    path = parts.path or "/"
    if parts.query:
        path = f"{path}?{parts.query}"
    host = parts.netloc
    return path, host


def is_health_request(method: str, url: str, host_header: str, port: int) -> bool:
    """True when the request targets this proxy's own health endpoint.

    Accepts both the proxy form (``GET http://127.0.0.1:<port>/__health``)
    and the direct origin form (``GET /__health`` + matching Host header),
    so ``curl -x`` probes and plain curls both work.
    """
    if method != "GET":
        return False
    if "://" in url:
        parts = urlsplit(url)
        path = parts.path or "/"
        if path != HEALTH_PATH:
            return False
        authority = parts.netloc
    else:
        if url.split("?", 1)[0] != HEALTH_PATH:
            return False
        authority = host_header
    try:
        host, _, port_str = authority.rpartition(":")
        return host in ("127.0.0.1", "localhost", "::1") and int(port_str) == port
    except ValueError:
        return False


def build_health_response(
    *,
    rate_mbps: float,
    port: int,
    bytes_relayed: int,
    connections: int,
    started_iso: str,
) -> bytes:
    doc = {
        "status": "ok",
        "version": __version__,
        "rate_mbps": rate_mbps,
        "port": port,
        "bytes_relayed": bytes_relayed,
        "connections": connections,
        # Process start, NOT answer time: health telemetry must be literal
        # or restart-vs-wedge diagnosis reads phantom restarts (2026-10-07).
        "started": started_iso,
    }
    body = json.dumps(doc).encode()
    head = (
        "HTTP/1.1 200 OK\r\n"
        "Content-Type: application/json\r\n"
        f"Content-Length: {len(body)}\r\n"
        "Connection: close\r\n\r\n"
    )
    return head.encode() + body


async def _pump(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    bucket: Optional[TokenBucket],
    counter: Callable[[int], None],
    idle_timeout: Optional[float] = None,
) -> None:
    """Relay reader→writer, pacing each chunk through ``bucket`` when set.

    Tokens are acquired BEFORE the read: stopping the reads closes the TCP
    window, so backpressure propagates to the WAN instead of buffering
    unboundedly. Overpaying when a read returns short is conservative —
    it can only under-use the configured rate.

    ``idle_timeout`` bounds how long a single read may block with no data;
    a silently stalled peer must tear the relay down rather than park it
    forever.
    """
    while True:
        if bucket is not None:
            await bucket.acquire(CHUNK_BYTES)
        if idle_timeout is None:
            data = await reader.read(CHUNK_BYTES)
        else:
            data = await asyncio.wait_for(
                reader.read(CHUNK_BYTES), timeout=idle_timeout
            )
        if not data:
            return
        counter(len(data))
        writer.write(data)
        await writer.drain()


class ThrottleProxy:
    """The server. One global bucket; every client connection shares it."""

    def __init__(
        self,
        rate_mbps: float = DEFAULT_RATE_MBPS,
        port: int = DEFAULT_PORT,
        burst_seconds: float = DEFAULT_BURST_SECONDS,
        bind: str = DEFAULT_BIND,
    ) -> None:
        self.rate_mbps = float(rate_mbps)
        self.requested_port = int(port)
        self.port = self.requested_port
        self.bind = bind
        self.bucket = TokenBucket(
            rate_bps=rate_mbps * 1_000_000 / 8,
            burst=max(CHUNK_BYTES, rate_mbps * 1_000_000 / 8 * burst_seconds),
        )
        self.server: Optional[asyncio.AbstractServer] = None
        self.bytes_relayed = 0
        self.connections_total = 0
        self._active = 0
        self._started_iso = datetime.now(timezone.utc).isoformat()

    # -- lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        self.server = await asyncio.start_server(
            self._handle_client, host=self.bind, port=self.requested_port
        )
        sock = self.server.sockets[0]
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.port = sock.getsockname()[1]
        LOG.info(
            "listening on %s:%d rate=%.1fMbit/s burst=%.0fKiB",
            self.bind, self.port, self.rate_mbps, self.bucket.burst / 1024,
        )

    async def serve_forever(self) -> None:
        if self.server is None:
            await self.start()
        assert self.server is not None
        async with self.server:
            await self.server.serve_forever()

    async def close(self) -> None:
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()
            self.server = None

    # -- connection handling ----------------------------------------------

    def _count(self, n: int) -> None:
        self.bytes_relayed += n

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        self.connections_total += 1
        self._active += 1
        peer = writer.get_extra_info("peername")
        try:
            await self._dispatch(reader, writer)
        except (ConnectionError, asyncio.IncompleteReadError, asyncio.TimeoutError):
            pass  # client hung up mid-transfer; nothing to relay left
        except Exception:
            LOG.exception("connection from %s failed", peer)
        finally:
            self._active -= 1
            writer.close()

    async def _dispatch(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        # Guard: loopback-only by design; an accidental non-loopback bind
        # (config typo) must refuse relaying rather than become a LAN-open
        # proxy.
        peer = writer.get_extra_info("peername")
        if peer and peer[0] not in ("127.0.0.1", "::1", "::ffff:127.0.0.1"):
            LOG.error("refusing non-loopback client %s", peer[0])
            return

        line = await asyncio.wait_for(reader.readline(), timeout=IDLE_TIMEOUT_SECONDS)
        if not line:
            return
        request_line = line.decode("latin-1").rstrip("\r\n")
        parts = request_line.split(" ")
        if len(parts) != 3:
            writer.write(b"HTTP/1.1 400 Bad Request\r\nConnection: close\r\n\r\n")
            return
        method, url, _version = parts
        headers: dict[str, str] = {}
        while True:
            hline = await asyncio.wait_for(
                reader.readline(), timeout=IDLE_TIMEOUT_SECONDS
            )
            if hline in (b"\r\n", b"\n", b""):
                break
            name, _, value = hline.decode("latin-1").partition(":")
            headers[name.strip().lower()] = value.strip()

        if method == "CONNECT":
            await self._connect_tunnel(reader, writer, url)
            return

        host_header = headers.get("host", "")
        if is_health_request(method, url, host_header, self.port):
            writer.write(
                build_health_response(
                    rate_mbps=self.rate_mbps,
                    port=self.port,
                    bytes_relayed=self.bytes_relayed,
                    connections=self._active,
                    started_iso=self._started_iso,
                )
            )
            await writer.drain()
            return

        await self._forward_http(reader, writer, method, url, host_header, headers)

    async def _connect_tunnel(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, target: str
    ) -> None:
        host, _, port_str = target.rpartition(":")
        try:
            port = int(port_str)
        except ValueError:
            writer.write(b"HTTP/1.1 400 Bad Request\r\n\r\n")
            return
        upstream_reader, upstream_writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=30.0
        )
        sock = upstream_writer.get_extra_info("socket")
        if sock is not None:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, UPSTREAM_RCVBUF)
        writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
        await writer.drain()
        # Downloads flow upstream→client (capped); requests/ACKs flow the
        # other way and are tiny — uncapped. The request direction gets no
        # idle deadline (a client legitimately stays silent for the whole
        # download), so the pair is raced: whichever pump ends first — EOF,
        # error, or the upstream idle timeout — cancels the other.
        downstream = asyncio.ensure_future(
            _pump(
                upstream_reader, writer, self.bucket, self._count,
                idle_timeout=IDLE_TIMEOUT_SECONDS,
            )
        )
        upstream = asyncio.ensure_future(
            _pump(reader, upstream_writer, None, lambda _n: None)
        )
        try:
            done, _pending = await asyncio.wait(
                {downstream, upstream}, return_when=asyncio.FIRST_COMPLETED
            )
            for task in done:
                task.result()
        finally:
            for task in (downstream, upstream):
                task.cancel()
            await asyncio.gather(downstream, upstream, return_exceptions=True)
            upstream_writer.close()

    async def _forward_http(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        method: str,
        url: str,
        host_header: str,
        headers: dict[str, str],
    ) -> None:
        path, url_host = rewrite_absolute_uri(method, url)
        authority = url_host or host_header
        if not authority:
            writer.write(b"HTTP/1.1 400 Bad Request\r\nConnection: close\r\n\r\n")
            await writer.drain()
            return
        host, _, port_str = authority.rpartition(":")
        port = int(port_str) if port_str.isdigit() else 80

        upstream_reader, upstream_writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=30.0
        )
        sock = upstream_writer.get_extra_info("socket")
        if sock is not None:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, UPSTREAM_RCVBUF)

        out_headers = [
            f"{name}: {value}"
            for name, value in headers.items()
            if name not in ("proxy-connection", "connection", "host")
        ]
        out_headers.append(f"Host: {authority}")
        out_headers.append("Connection: close")
        request = (
            f"{method} {path} HTTP/1.1\r\n" + "\r\n".join(out_headers) + "\r\n\r\n"
        )
        upstream_writer.write(request.encode("latin-1"))

        # Request bodies (pip PUTs, none today) are small; forward uncapped.
        length = int(headers.get("content-length", "0") or 0)
        if length > 0:
            body = await reader.readexactly(length)
            upstream_writer.write(body)
        await upstream_writer.drain()

        await _pump(
            upstream_reader, writer, self.bucket, self._count,
            idle_timeout=IDLE_TIMEOUT_SECONDS,
        )
        upstream_writer.close()


def _rate_from_env() -> float:
    raw = os.environ.get("NFMD_CI_THROTTLE_RATE_MBPS", "")
    if not raw:
        return DEFAULT_RATE_MBPS
    try:
        return parse_rate_mbps(raw)
    except ValueError as exc:
        raise SystemExit(
            f"NFMD_CI_THROTTLE_RATE_MBPS={raw!r} is not a usable rate: {exc}"
        ) from exc


def raise_fd_limit(target_soft: int = 4096) -> tuple[int, int]:
    """Raise this process's soft RLIMIT_NOFILE toward ``target_soft``.

    A pip download burst opens 200+ concurrent upstream sockets; launchd's
    default 256 soft limit EMFILE-killed the relay mid-burst on
    2026-10-07 08:45Z (the proxy stayed alive but wedged — KeepAlive only
    restarts on exit, so the wedge persisted until a manual kickstart).
    Doing this in-process (not via a plist LimitNOFILE reload) keeps the
    fix effective even while gui/501 launchd refuses plist reloads during
    a hung-logout wedge. Never fatal: on refusal the limit stays as-is.
    """
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    new_soft = min(target_soft, hard)
    if new_soft <= soft:
        return soft, soft
    try:
        resource.setrlimit(resource.RLIMIT_NOFILE, (new_soft, hard))
    except OSError:
        LOG.warning("RLIMIT_NOFILE raise to %d refused; staying at %d", new_soft, soft)
        return soft, soft
    return soft, new_soft


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="NFM-5333 CI download throttle proxy")
    parser.add_argument("--port", type=int, default=int(
        os.environ.get("NFMD_CI_THROTTLE_PORT", DEFAULT_PORT)))
    parser.add_argument("--rate-mbps", type=parse_rate_mbps, default=None)
    parser.add_argument("--burst-seconds", type=float, default=DEFAULT_BURST_SECONDS)
    parser.add_argument("--bind", default=DEFAULT_BIND)
    args = parser.parse_args(argv)

    rate = args.rate_mbps if args.rate_mbps is not None else _rate_from_env()
    if args.bind not in ("127.0.0.1", "localhost", "::1"):
        raise SystemExit(
            f"refusing non-loopback bind {args.bind!r} — this proxy is an open "
            "relay by design and must stay loopback-only"
        )

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)sZ %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        stream=sys.stderr,
    )
    proxy = ThrottleProxy(
        rate_mbps=rate, port=args.port, burst_seconds=args.burst_seconds, bind=args.bind
    )
    old_soft, new_soft = raise_fd_limit()
    LOG.info(
        "nfmd-ci-throttle v%s starting — pid=%d rate=%.1fMbit/s started=%s "
        "fd_soft_limit=%d->%d",
        __version__, os.getpid(), rate, proxy._started_iso, old_soft, new_soft,
    )

    async def run() -> None:
        await proxy.start()
        last_bytes = 0
        last_at = time.monotonic()
        try:
            while True:
                await asyncio.sleep(10.0)
                now = time.monotonic()
                moved = proxy.bytes_relayed - last_bytes
                if moved or proxy._active:
                    LOG.info(
                        "conns=%d total_conns=%d window=%.1fs moved=%.2fMiB "
                        "window_rate=%.1fMbit/s cap=%.1fMbit/s total=%.2fMiB",
                        proxy._active, proxy.connections_total, now - last_at,
                        moved / 1048576, moved * 8 / (now - last_at) / 1e6,
                        rate, proxy.bytes_relayed / 1048576,
                    )
                last_bytes = proxy.bytes_relayed
                last_at = now
        finally:
            await proxy.close()

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        LOG.info("shutdown")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
