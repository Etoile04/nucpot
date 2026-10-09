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
    pre-NFM-5333 uncapped behavior when the proxy is down. The health
    ``started`` field is the process start (stable across answers), and
    stderr log timestamps are true UTC — both 2026-10-07 NFM-5348
    hardening so restart-vs-wedge diagnosis reads literal telemetry;
  * raises its own soft RLIMIT_NOFILE at startup (belt) on top of the
    plist's ``LimitNOFILE`` (braces): a ~200-connection pip burst
    EMFILE-killed the relay under launchd's default 256 soft limit, and
    KeepAlive only restarts on exit — the in-process raise stays
    effective even when plist reloads are blocked (2026-10-07 NFM-5348);
  * tunnel teardown is total: the request-direction pump deliberately has
    no idle deadline (a client legitimately stays silent for a whole
    download), so a client-transport watchdog (``_peer_gone``) is raced
    against both pumps and teardown closes both transports before
    awaiting them. The 2026-10-07 Linux-runner race (NFM-5401) where a
    client hangup left that pump parked forever turned every Batch1 run
    since NFM-5333 into a 20-minute CANCELLED — and the same shape on the
    LaunchAgent host would leak one handler task + fds per occurrence
    (2026-10-09 NFM-5401);
  * per-edge health (NFM-5425): pip pools its CONNECT tunnels for the
    whole install, so a tunnel pinned at connect time (getaddrinfo) to a
    degraded CDN edge that TRICKLES — 0.2-0.5 Mbps single-connection
    observed on 2026-10-09 aliyun/alikunlun edges while sibling edges of
    the same mirror ran 1.6-2.4 MB/s — never errors and never idles out,
    and the Dockerfile mirror ladder only advances on leg FAILURE, so
    the build crawls for as long as the edge stays sick (4 occurrences
    2026-10-09, 40-60 min each). A supervisor now tracks each tunnel's
    delivered bytes and kills tunnels that crept continuously through a
    full window yet stayed under EDGE_TRICKLE_MAX_BYTES while the global
    bucket had spare capacity (the shared cap must never be mistaken for
    a sick edge). A killed tunnel costs pip one of its --retries; the
    reconnect re-resolves DNS and lands on a different edge — the
    automated, surgical form of the manual ``launchctl kickstart``
    mitigation proven 3x on 2026-10-09.

Wired in ``scripts/deploy_prod.sh``: build containers reach the host via
``host.docker.internal:7899`` (Docker Desktop resolves it to the host's
loopback). pip/pnpm/corepack honor HTTP(S)_PROXY, and the cap reaches RUN
steps only as explicit predefined ``--build-arg`` forms (NFM-5389
correction, 2026-10-08: the legacy non-BuildKit builder does NOT forward
env-prefix proxy exports into RUN containers on this docker CLI — the
historical capped traffic rode Docker Desktop's daemon-side proxy-env
injection, which the 2026-10-07 manual-proxy clearance killed). The gate
probes the health endpoint from a container through the build URL before
claiming a cap, so a broken ``host.docker.internal`` path builds uncapped
with a loud NFM-5389 log line instead of a false-green cap.

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
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlsplit

__version__ = "1.1.0"

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
# How often the client-transport watchdog wakes to re-check peer state
# (see _peer_gone): frequent enough that a lost-wakeup teardown costs one
# interval, rare enough that ~200 concurrent tunnels add trivial load.
PEER_GONE_POLL_SECONDS = 1.0
# Bound on Server.wait_closed() at shutdown: Python 3.12's implementation
# never returns when a handler task is still active at close() time (fixed
# in 3.13; repro'd 2026-10-09: 3.12.12 hangs in both close/handler
# orderings) — without this bound, shutting the LaunchAgent down with a
# live tunnel wedges the process forever (NFM-5401).
CLOSE_DRAIN_TIMEOUT_SECONDS = 5.0
# --- NFM-5425: sick-CDN-edge watchdog ---------------------------------------
# 2026-10-09: pip's pooled CONNECT tunnels pin to whatever CDN edge
# getaddrinfo returned at connect time and stay there for the life of the
# connection. A degraded aliyun/alikunlun edge that TRICKLES (observed
# 0.2-0.5 Mbps single-connection while sibling edges of the same mirror
# ran 1.6-2.4 MB/s, and fastly direct ~180 MB at 1.6-2.4 MB/s) never
# fails the tunnel and never trips the 600s idle timeout, so the `||`
# mirror ladder in docker/prod-api.Dockerfile — which advances only on
# leg FAILURE — crawls indefinitely. The supervisor kills verified
# trickling tunnels; pip spends one --retries and reconnects onto a
# freshly resolved (healthy) edge.
EDGE_SUPERVISOR_TICK_SECONDS = 30.0
EDGE_TRICKLE_WINDOW_SECONDS = 180.0
# <24 MiB per 180s ⇒ sustained < ~1.1 Mbit/s. Healthy single connections
# observed 13-20 Mbit/s; even fair-shared under the 40 Mbit/s cap, 20
# saturated tunnels each get 2 Mbit/s = 45 MiB/180s. The sick (≤11 MiB)
# and healthy-shared (≥45 MiB) bands are an order of magnitude apart —
# 24 MiB sits in the empty middle.
EDGE_TRICKLE_MAX_BYTES = 24 * 1024 * 1024
# A tunnel must prove itself for a full window before it is judged.
EDGE_MIN_AGE_SECONDS = 180.0
# Judge edges only while the global bucket is NOT the limiter: when the
# aggregate window rate is at (or near) cap, per-tunnel slowness is fair
# sharing, not a sick edge, and kills would churn healthy tunnels.
EDGE_SPARE_CAPACITY_FRACTION = 0.25
# A sick edge delivers continuously (TCP keeps feeding the in-flight
# response); request/response metadata traffic moves in bursts with
# quiet gaps between requests. Require ~all window ticks to have moved
# bytes so pip's resolution phase is never mistaken for a trickle.
EDGE_CREEP_TICK_FRACTION = 0.9
# Recent sick edges kept in the health payload for deploy-watch.
EDGE_SICK_LOG_MAX = 8

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
    edge_kills: int = 0,
    sick_edges: Optional[list] = None,
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
        # NFM-5425: sick-edge supervisor telemetry — cumulative kills plus
        # the recent edges killed, so a slow deploy window can be
        # reconciled against the exact edge IPs without reading the log.
        "edge_kills": edge_kills,
        "sick_edges": list(sick_edges or []),
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
            data = await asyncio.wait_for(reader.read(CHUNK_BYTES), timeout=idle_timeout)
        if not data:
            return
        counter(len(data))
        writer.write(data)
        await writer.drain()


async def _peer_gone(
    writer: asyncio.StreamWriter,
    reader: Optional[asyncio.StreamReader] = None,
    poll_seconds: float = PEER_GONE_POLL_SECONDS,
) -> None:
    """Resolve once the peer is gone: transport closed (RST/abort), or —
    when ``reader`` is given — the peer closed its sending side.

    This polls loop STATE (``at_eof``/``is_closing``), not events. The
    NFM-5401 Linux-runner race was a lost wakeup: the client's FIN was
    processed into the reader's EOF state, but the request pump's pending
    ``read()`` never resolved, leaving the loop idle in
    ``selectors.select`` for 9+ minutes with no fd event and no timer
    pending — the handler (and, on the LaunchAgent host, a handler task +
    fds) leaked until the process died. A timer-driven state poll
    sidesteps event delivery entirely: either flag is observable within
    one interval of the peer dying no matter which wakeup was lost.

    Note the FIN asymmetry: ``is_closing()`` does NOT flip on a graceful
    peer FIN — ``StreamReaderProtocol.eof_received()`` returns True for
    plain TCP (half-close support) and the transport stays open — only
    ``reader.at_eof()`` reflects it. ``reader`` is therefore passed for
    CONNECT tunnels, where a client FIN already means teardown (the pump
    race treats request-side EOF exactly so). The plain-HTTP response leg
    passes no reader: a client that half-closes its request side still
    deserves its full response, and a fully-closed peer surfaces as a
    write-side RST → ``is_closing()`` anyway.
    """
    while not (writer.is_closing() or (reader is not None and reader.at_eof())):
        await asyncio.sleep(poll_seconds)


@dataclass
class _TunnelRecord:
    """Live accounting for one relayed download tunnel (NFM-5425).

    ``bytes_down`` counts only the capped upstream→client direction — the
    direction the sick-edge symptom lives in. ``dead`` is raced against the
    tunnel's pumps exactly like ``_peer_gone``: setting it is enough to tear
    the whole tunnel down through the handler's existing finally-path, so
    the supervisor never touches sockets itself. ``samples`` is a sliding
    ``(monotonic, bytes_down)`` series appended by the supervisor.

    Constructed only inside a running event loop (the ``asyncio.Event``
    binds to the current loop on py3.9).
    """

    created: float
    upstream_peer: Optional[tuple] = None
    target: str = ""
    bytes_down: int = 0
    finished: bool = False
    dead: asyncio.Event = field(default_factory=asyncio.Event)
    samples: deque = field(default_factory=deque)


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
        # NFM-5425 sick-edge watchdog state: live tunnels keyed by record
        # identity, cumulative kill count, and the recent-kill ring the
        # health endpoint serves. `_rate_samples` is the global
        # (monotonic, bytes_relayed) series behind the spare-capacity guard.
        self._tunnels: dict = {}
        self.edge_kills = 0
        self.sick_edges: list = []
        self._rate_samples: deque = deque()

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
            self.bind,
            self.port,
            self.rate_mbps,
            self.bucket.burst / 1024,
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
            # Bounded drain, not a bare wait_closed(): py3.12's
            # wait_closed() hangs forever when a handler is still active
            # at close() (see CLOSE_DRAIN_TIMEOUT_SECONDS). The listeners
            # are already down; handlers have their own total teardown,
            # so proceeding past a wedged drain leaks nothing permanent.
            try:
                await asyncio.wait_for(
                    self.server.wait_closed(), timeout=CLOSE_DRAIN_TIMEOUT_SECONDS
                )
            except asyncio.TimeoutError:
                # asyncio.TimeoutError, not builtin TimeoutError: the
                # LaunchAgent host runs py3.9, where the two are distinct
                # (aliased only in 3.11+) — the builtin spelling let the
                # bounded-drain guard itself raise on 3.9.
                LOG.warning(
                    "server drain did not settle in %.1fs; closing anyway",
                    CLOSE_DRAIN_TIMEOUT_SECONDS,
                )
            self.server = None

    # -- connection handling ----------------------------------------------

    def _count(self, n: int) -> None:
        self.bytes_relayed += n

    def _tunnel_counter(self, record: _TunnelRecord) -> Callable[[int], None]:
        """Counter that feeds both the global total and one tunnel's record."""

        def count(n: int) -> None:
            self._count(n)
            record.bytes_down += n

        return count

    @staticmethod
    async def _settle(
        tasks: set[asyncio.Task[None]], writers: tuple[asyncio.StreamWriter, ...]
    ) -> None:
        """Cancel ``tasks`` and close ``writers`` so every pump resolves.

        Transport close comes BEFORE the gather: closing a transport feeds
        EOF (or a connection-lost error) into its paired reader and fails
        any pending ``drain()``, so even a pump that ignored or swallowed
        its cancellation has a bounded exit. Awaiting the bare tasks first
        (the pre-NFM-5401 shape) let one wedged pump park the whole
        handler forever — and ``Server.wait_closed()`` then hung the test
        lane for the job's 20-minute timeout on the Linux runners.
        """
        for task in tasks:
            task.cancel()
        for stream_writer in writers:
            stream_writer.close()
        await asyncio.gather(*tasks, return_exceptions=True)

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

    async def _dispatch(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
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
            hline = await asyncio.wait_for(reader.readline(), timeout=IDLE_TIMEOUT_SECONDS)
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
                    edge_kills=self.edge_kills,
                    sick_edges=self.sick_edges,
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
        # download), so the race is four-way: whichever ends first — a
        # pump hitting EOF, an error, or the upstream idle timeout, the
        # client transport closing (NFM-5401: the request pump's own EOF
        # wake cannot be the tunnel's only other exit — on the Linux
        # runners it never fired), or the sick-edge supervisor killing
        # this tunnel (NFM-5425: `record.dead` — the pin to a degraded
        # CDN edge is only breakable from outside the pumps) — tears down
        # the whole tunnel.
        record = _TunnelRecord(
            created=time.monotonic(),
            upstream_peer=upstream_writer.get_extra_info("peername"),
            target=target,
        )
        self._tunnels[id(record)] = record
        downstream = asyncio.ensure_future(
            _pump(
                upstream_reader,
                writer,
                self.bucket,
                self._tunnel_counter(record),
                idle_timeout=IDLE_TIMEOUT_SECONDS,
            )
        )
        upstream = asyncio.ensure_future(_pump(reader, upstream_writer, None, lambda _n: None))
        client_gone = asyncio.ensure_future(_peer_gone(writer, reader))
        sick_edge_kill = asyncio.ensure_future(record.dead.wait())
        try:
            done, _pending = await asyncio.wait(
                {downstream, upstream, client_gone, sick_edge_kill},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in done:
                task.result()
        finally:
            record.finished = True
            self._tunnels.pop(id(record), None)
            await self._settle(
                {downstream, upstream, client_gone, sick_edge_kill}, (writer, upstream_writer)
            )

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
        request = f"{method} {path} HTTP/1.1\r\n" + "\r\n".join(out_headers) + "\r\n\r\n"
        upstream_writer.write(request.encode("latin-1"))

        # Request bodies (pip PUTs, none today) are small; forward uncapped.
        length = int(headers.get("content-length", "0") or 0)
        if length > 0:
            body = await reader.readexactly(length)
            upstream_writer.write(body)
        await upstream_writer.drain()

        # The response leg races the same client-transport watchdog as the
        # CONNECT tunnel: a client that hangs up mid-download must not park
        # the handler until the upstream idle timeout (NFM-5401 invariant —
        # a relay must never outlive either peer), and the sick-edge
        # supervisor can kill the relay the same way (NFM-5425).
        record = _TunnelRecord(
            created=time.monotonic(),
            upstream_peer=upstream_writer.get_extra_info("peername"),
            target=authority,
        )
        self._tunnels[id(record)] = record
        response = asyncio.ensure_future(
            _pump(
                upstream_reader,
                writer,
                self.bucket,
                self._tunnel_counter(record),
                idle_timeout=IDLE_TIMEOUT_SECONDS,
            )
        )
        client_gone = asyncio.ensure_future(_peer_gone(writer))
        sick_edge_kill = asyncio.ensure_future(record.dead.wait())
        try:
            done, _pending = await asyncio.wait(
                {response, client_gone, sick_edge_kill}, return_when=asyncio.FIRST_COMPLETED
            )
            for task in done:
                task.result()
        finally:
            record.finished = True
            self._tunnels.pop(id(record), None)
            await self._settle({response, client_gone, sick_edge_kill}, (writer, upstream_writer))

    # -- sick-edge supervision (NFM-5425) ----------------------------------

    async def supervise_edges(self) -> None:
        """Watch relays for trickle-serving CDN edges; kill the verified ones.

        Started alongside the stats loop in ``main()`` (and explicitly by
        tests). A tick that raises must not take the loop down — the
        watchdog optimizes sick-edge days, it is not a load-bearing relay
        component — so each tick is individually fenced.
        """
        while True:
            await asyncio.sleep(EDGE_SUPERVISOR_TICK_SECONDS)
            try:
                self._edge_supervise_once()
            except Exception:
                LOG.exception("edge supervisor tick failed (continuing)")

    def _edge_supervise_once(self, now: Optional[float] = None) -> None:
        """One synchronous supervision pass over the live tunnels.

        Reads module-level EDGE_* constants at call time so ops (and tests)
        can retune them without re-instantiating the proxy.
        """
        now = time.monotonic() if now is None else now
        window = EDGE_TRICKLE_WINDOW_SECONDS

        # Global spare-capacity sample: if the aggregate window rate is at
        # or near cap, per-tunnel slowness is the bucket fair-sharing, not
        # a sick edge — killing would churn healthy tunnels for nothing.
        self._rate_samples.append((now, self.bytes_relayed))
        while self._rate_samples and now - self._rate_samples[0][0] > window:
            self._rate_samples.popleft()
        global_span = now - self._rate_samples[0][0]
        global_bps = (
            (self.bytes_relayed - self._rate_samples[0][1]) * 8 / global_span
            if global_span > 0
            else 0.0
        )
        # The bucket's ``rate_bps`` is BYTES per second (its tokens are
        # bytes); the guard compares bit rates, so derive the cap in bits.
        # (Unit trap caught in review: comparing global bits/s against the
        # bytes/s cap number made the guard 8x too strict and mislabeled
        # the log's cap — "5.0Mbit/s" for a 40 Mbit/s cap.)
        cap_bps = self.rate_mbps * 1_000_000
        spare_capacity = global_bps < EDGE_SPARE_CAPACITY_FRACTION * cap_bps

        for record in list(self._tunnels.values()):
            record.samples.append((now, record.bytes_down))
            while record.samples and now - record.samples[0][0] > window:
                record.samples.popleft()
            samples = list(record.samples)
            span = now - samples[0][0]
            moved = record.bytes_down - samples[0][1]
            if (
                record.finished
                or record.dead.is_set()
                or not spare_capacity
                or now - record.created < EDGE_MIN_AGE_SECONDS
                or span < window * 0.9  # judge only on a (near-)full window
                or moved <= 0  # idle pools and hard stalls are not trickles
                or moved >= EDGE_TRICKLE_MAX_BYTES
            ):
                continue
            # Creep discriminator: a sick edge feeds the in-flight response
            # continuously, so ~every inter-tick delta is nonzero. Bursts
            # with quiet gaps (pip's request/response metadata phase) are
            # legitimate tunnels even when their window total is small.
            # (A plain loop, not itertools.pairwise: the LaunchAgent host
            # interpreter is py3.9.)
            deltas = []
            prev_bytes = samples[0][1]
            for _t, current_bytes in samples[1:]:
                deltas.append(current_bytes - prev_bytes)
                prev_bytes = current_bytes
            creeping = sum(1 for delta in deltas if delta > 0)
            if not deltas or creeping < EDGE_CREEP_TICK_FRACTION * len(deltas):
                continue
            record.dead.set()
            self.edge_kills += 1
            edge_ip = record.upstream_peer[0] if record.upstream_peer else "unknown"
            self.sick_edges.append(
                {
                    "edge": edge_ip,
                    "target": record.target,
                    "moved_bytes": moved,
                    "window_s": round(span, 1),
                    "at": datetime.now(timezone.utc).isoformat(),
                }
            )
            del self.sick_edges[:-EDGE_SICK_LOG_MAX]
            LOG.warning(
                "NFM-5425: killing sick-edge tunnel edge=%s target=%s moved=%.2fMiB "
                "in %.0fs (~%.2fMbit/s, kill floor %.2fMbit/s; global %.2fMbit/s "
                "vs cap %.1fMbit/s) — pip's retry re-resolves onto a fresh edge",
                edge_ip,
                record.target,
                moved / 1048576,
                span,
                moved * 8 / max(span, 1e-9) / 1e6,
                EDGE_TRICKLE_MAX_BYTES * 8 / window / 1e6,
                global_bps / 1e6,
                cap_bps / 1e6,
            )


def _rate_from_env() -> float:
    raw = os.environ.get("NFMD_CI_THROTTLE_RATE_MBPS", "")
    if not raw:
        return DEFAULT_RATE_MBPS
    try:
        return parse_rate_mbps(raw)
    except ValueError as exc:
        raise SystemExit(f"NFMD_CI_THROTTLE_RATE_MBPS={raw!r} is not a usable rate: {exc}") from exc


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


def utc_log_formatter() -> logging.Formatter:
    """Stderr log formatter whose ``%(asctime)s`` is true UTC.

    2026-10-07: the previous ``format="%(asctime)sZ ..."`` rendered LOCAL
    time with a literal Z suffix — on this +0800 host the log claimed
    ``17:31:48Z`` for true ``09:31:48Z`` and nearly misdirected the
    EMFILE/reboot-window diagnosis (same trap class as NFM-5346's
    local-time DiagnosticReports filenames). ``converter = time.gmtime``
    is the stdlib's supported way to make a Formatter render UTC.
    """
    fmt = logging.Formatter(
        "%(asctime)sZ %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    fmt.converter = time.gmtime  # type: ignore[method-assign]
    return fmt


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="NFM-5333 CI download throttle proxy")
    parser.add_argument(
        "--port", type=int, default=int(os.environ.get("NFMD_CI_THROTTLE_PORT", DEFAULT_PORT))
    )
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

    _stderr_handler = logging.StreamHandler(sys.stderr)
    _stderr_handler.setFormatter(utc_log_formatter())
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)sZ %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        handlers=[_stderr_handler],
    )
    proxy = ThrottleProxy(
        rate_mbps=rate, port=args.port, burst_seconds=args.burst_seconds, bind=args.bind
    )
    old_soft, new_soft = raise_fd_limit()
    LOG.info(
        "nfmd-ci-throttle v%s starting — pid=%d rate=%.1fMbit/s started=%s fd_soft_limit=%d->%d "
        "edge_watchdog=window%.0fs/floor%.1fMbit/s",
        __version__,
        os.getpid(),
        rate,
        proxy._started_iso,
        old_soft,
        new_soft,
        EDGE_TRICKLE_WINDOW_SECONDS,
        EDGE_TRICKLE_MAX_BYTES * 8 / EDGE_TRICKLE_WINDOW_SECONDS / 1e6,
    )

    async def run() -> None:
        await proxy.start()
        supervisor = asyncio.ensure_future(proxy.supervise_edges())
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
                        "window_rate=%.1fMbit/s cap=%.1fMbit/s total=%.2fMiB "
                        "edge_kills=%d",
                        proxy._active,
                        proxy.connections_total,
                        now - last_at,
                        moved / 1048576,
                        moved * 8 / (now - last_at) / 1e6,
                        rate,
                        proxy.bytes_relayed / 1048576,
                        proxy.edge_kills,
                    )
                last_bytes = proxy.bytes_relayed
                last_at = now
        finally:
            supervisor.cancel()
            await asyncio.gather(supervisor, return_exceptions=True)
            await proxy.close()

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        LOG.info("shutdown")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
