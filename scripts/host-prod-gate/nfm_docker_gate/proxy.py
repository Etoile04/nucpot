"""Filtering unix-socket HTTP proxy for the NFM-4270 (ADR-013 G2) gate.

Listens on a unix socket in front of the real Docker daemon socket:

    docker CLI ──> gate socket ──(policy)──> real daemon socket

One request per connection. The request head is parsed, classified by
nfm_docker_gate.policy, and either denied with a 403 + audit record or
forwarded upstream with ``Connection: close`` and then blindly relayed
in both directions (streaming-safe: ``docker logs -f`` / ``stats`` /
chunked builds work unchanged — the pump runs fully blocking, no
timeouts). Forcing one request per connection is also the anti-smuggling
wall: a pipelined second request on the same connection is never treated
as "already allowed" — the connection closes after the first exchange.

Requests whose JSON body participates in classification (container /
network / volume create, network connect/disconnect) have it read
up-front (bounded, Content-Length framed — any other body framing on
those endpoints fails closed); everything else streams untouched.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import socket
import sys
import threading
from dataclasses import dataclass, field
from typing import Any

from .audit import AuditLog
from .peercred import peer_identity
from .policy import REFUSAL_HINT, Decision, ScopeConfig, TargetInfo, classify, parse_json_object

_HEAD_LIMIT = 64 * 1024
_BODY_LIMIT = 2 * 1024 * 1024
_HEAD_TIMEOUT = 120.0
_HEX = set("0123456789abcdef")

# Endpoints whose JSON body participates in classification.
_BODY_ENDPOINTS = {"containers/create", "networks/create", "volumes/create"}
_BODY_ENDPOINT_RE = re.compile(r"^networks/[^/]+/(connect|disconnect)$")


_API_VERSION_PREFIX = re.compile(r"^/v[0-9]+\.[0-9]+/")


def _strip_version(path: str) -> str:
    return _API_VERSION_PREFIX.sub("", path, count=1)


@dataclass
class _ConnCtx:
    """Per-connection parse state (the proxy itself is shared by threads)."""

    method: str = ""
    target: str = ""  # original request target, query included
    path: str = ""
    query: str = ""
    headers: list[tuple[str, str]] = field(default_factory=list)
    rest: bytes = b""  # bytes already read beyond the head
    body: bytes = b""  # classified body bytes (only for _BODY_ENDPOINTS)
    # Set when the request head carries CR/LF inside a header name/value
    # or the request line: the proxy would re-serialize it as a REAL
    # header upstream (request smuggling) — denied before classification
    # (NFM-4273 CR R1).
    smuggle: str | None = None


class UpstreamResolver:
    """Resolves opaque container ids by asking the daemon (read-only)."""

    def __init__(self, upstream_path: str) -> None:
        self._upstream = upstream_path

    def __call__(self, ident: str) -> TargetInfo | None:
        # Names / short refs classify directly; opaque hex refs need a
        # daemon roundtrip to learn name + labels + networks. The daemon
        # accepts id PREFIXES of any unambiguous length, so any pure-hex
        # ident (even a few chars) is treated as opaque (NFM-4273 review R2).
        if not ident or not set(ident.lower()) <= _HEX:
            return TargetInfo(name=ident)
        raw = self._daemon_request(f"/containers/{ident}/json")
        if raw is None:
            return None
        try:
            doc = json.loads(raw)
        except ValueError:
            return None
        config = doc.get("Config") or {}
        labels = config.get("Labels") or {}
        networks = tuple(((doc.get("NetworkSettings") or {}).get("Networks") or {}).keys())
        # Named volumes attached to the container (inspect Mounts entries
        # carry the volume name in "Name"; bind mounts have none) — a
        # rogue container MOUNTING prod state is a prod mutation too.
        volumes = tuple(
            str(mount["Name"])
            for mount in doc.get("Mounts") or []
            if isinstance(mount, dict) and mount.get("Type") == "volume" and mount.get("Name")
        )
        return TargetInfo(
            name=(doc.get("Name") or "").lstrip("/") or ident,
            project=labels.get("com.docker.compose.project"),
            networks=tuple(networks),
            volumes=volumes,
        )

    def _daemon_request(self, api_path: str) -> bytes | None:
        sock = None
        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(10)
            sock.connect(self._upstream)
            request = (
                f"GET {api_path} HTTP/1.1\r\n"
                "Host: docker\r\n"
                "User-Agent: nfm-g2-resolver\r\n"
                "Content-Length: 0\r\n"
                "Connection: close\r\n\r\n"
            )
            sock.sendall(request.encode())
            chunks = []
            while True:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                chunks.append(chunk)
        except OSError:
            return None
        finally:
            if sock is not None:
                with contextlib.suppress(OSError):
                    sock.close()
        raw = b"".join(chunks)
        header, _, body = raw.partition(b"\r\n\r\n")
        status_line = header.split(b"\r\n", 1)[0]
        if b" 200" not in status_line:
            return None
        if b"chunked" in header.lower():
            body = _dechunk(body)
        return body


def _dechunk(body: bytes) -> bytes:
    out = []
    view = memoryview(body)
    while True:
        nl = view.find(b"\r\n")
        if nl < 0:
            break
        try:
            size = int(bytes(view[:nl]).split(b";")[0], 16)
        except ValueError:
            break
        if size == 0:
            break
        start = nl + 2
        out.append(bytes(view[start : start + size]))
        view = view[start + size + 2 :]
    return b"".join(out)


class DockerGateProxy:
    def __init__(
        self,
        listen_path: str,
        upstream_path: str,
        audit_log: AuditLog,
        scope: ScopeConfig | None = None,
        full_mode: bool = False,
        socket_mode: int = 0o666,
        socket_group: str | None = None,
    ) -> None:
        self.listen_path = listen_path
        self.upstream_path = upstream_path
        self.audit = audit_log
        self.scope = scope or ScopeConfig()
        self.full_mode = full_mode
        self.socket_mode = socket_mode
        self.socket_group = socket_group
        self.resolver = UpstreamResolver(upstream_path)
        self._shutdown = threading.Event()

    # ---- lifecycle -------------------------------------------------------

    def serve_forever(self) -> None:
        if os.path.exists(self.listen_path):
            try:  # a live listener means another instance owns this socket
                probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                probe.settimeout(2)
                probe.connect(self.listen_path)
                probe.close()
                raise SystemExit(f"nfm-g2: another instance is listening on {self.listen_path}")
            except (TimeoutError, ConnectionRefusedError, FileNotFoundError):
                os.unlink(self.listen_path)

        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(self.listen_path)
        os.chmod(self.listen_path, self.socket_mode)
        if self.socket_group:
            import grp

            group_id = grp.getgrnam(self.socket_group).gr_gid
            os.chown(self.listen_path, -1, group_id)
        server.listen(64)
        server.settimeout(1.0)
        self.audit.write(
            "startup",
            None,
            listen=self.listen_path,
            upstream=self.upstream_path,
            mode="full" if self.full_mode else "ro",
        )
        while not self._shutdown.is_set():
            try:
                conn, _ = server.accept()
            except TimeoutError:
                continue
            except OSError:
                if self._shutdown.is_set():
                    break
                raise
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()
        server.close()

    def shutdown(self) -> None:
        self._shutdown.set()

    # ---- per-connection handling -------------------------------------------

    def _handle(self, conn: socket.socket) -> None:
        try:
            conn.settimeout(_HEAD_TIMEOUT)
            ctx = self._read_head(conn)
            if ctx is None:
                return
            identity = peer_identity(conn)
            if ctx.smuggle:
                # R1: refuse before classification — the classification
                # would read a different request than the daemon.
                self._deny(conn, Decision(False, ctx.smuggle, audit=True), identity, ctx)
                return
            decision = self._decide(conn, ctx)
            if not decision.allowed:
                self._deny(conn, decision, identity, ctx)
                return
            if decision.audit:
                # F5: never forward a mutation the audit trail failed to
                # record — fail loud, fail closed (AC-G2.6).
                try:
                    self.audit.write(
                        "allow",
                        identity,
                        method=ctx.method,
                        target=ctx.target,
                        **decision.to_log_fields(),
                    )
                except OSError as error:
                    print(
                        f"nfm-g2: AUDIT WRITE FAILED, aborting allow: {error}",
                        file=sys.stderr,
                        flush=True,
                    )
                    self._bad_gateway(conn, "nfm-g2: audit log unavailable (fail-closed)")
                    return
            # Streaming from here on: no timeouts, pump until EOF.
            conn.settimeout(None)
            self._forward(conn, ctx)
        except OSError:
            pass  # client vanished mid-head; nothing to attribute yet
        finally:
            with contextlib.suppress(OSError):
                conn.close()

    def _read_head(self, conn: socket.socket) -> _ConnCtx | None:
        buffer = b""
        while b"\r\n\r\n" not in buffer:
            if len(buffer) > _HEAD_LIMIT:
                return None
            try:
                chunk = conn.recv(4096)
            except TimeoutError:
                return None
            if not chunk:
                return None
            buffer += chunk
        head_bytes, _, rest = buffer.partition(b"\r\n\r\n")
        lines = head_bytes.decode("iso-8859-1").split("\r\n")
        parts = lines[0].split(" ")
        if len(parts) < 3:
            return None
        ctx = _ConnCtx(method=parts[0].upper(), target=parts[1], rest=rest)
        ctx.path, _, ctx.query = parts[1].partition("?")
        # R1: the head is split on CRLF only, so a bare LF/CR inside a
        # request-line token or header name/value stays embedded in ONE
        # parsed line here — but _forward re-serializes it as a SEPARATE
        # line upstream. `X-A: foo\nTransfer-Encoding: chunked` is one
        # header to the gate and two to the daemon: the daemon dechunks a
        # body the gate classified under Content-Length. Any CR/LF before
        # the wire boundary is smuggling — deny, never forward.
        if not parts[0].isalpha() or any(c in ctx.target for c in "\r\n"):
            ctx.smuggle = "request line contains CR/LF or non-alpha method (request smuggling)"
        for line in lines[1:]:
            name, _, value = line.partition(":")
            name_s, value_s = name.strip().lower(), value.strip()
            if any(c in name_s + value_s for c in "\r\n"):
                if ctx.smuggle is None:
                    ctx.smuggle = "header name/value contains CR/LF (request smuggling)"
                continue  # keep the head parseable for the audit record only
            ctx.headers.append((name_s, value_s))
        return ctx

    def _decide(self, conn: socket.socket, ctx: _ConnCtx) -> Decision:
        stripped = _strip_version(ctx.path).lstrip("/")
        if ctx.method == "POST" and (
            stripped in _BODY_ENDPOINTS or _BODY_ENDPOINT_RE.match(stripped)
        ):
            body = self._read_body(conn, ctx)
            if body is None:
                return Decision(
                    False, "oversized or unreadable JSON body (fail-closed)", audit=True
                )
            ctx.body = body
        return classify(
            ctx.method,
            ctx.path,
            ctx.query,
            parse_json_object(ctx.body) if ctx.body else None,
            self.resolver,
            self.scope,
            full_mode=self.full_mode,
        )

    def _read_body(self, conn: socket.socket, ctx: _ConnCtx) -> bytes | None:
        length = -1
        for name, value in ctx.headers:
            if name == "content-length":
                try:
                    declared = int(value)
                except ValueError:
                    return None
                if length >= 0 and declared != length:
                    return None
                length = declared
            elif name == "transfer-encoding":
                return None
        if length < 0 or length > _BODY_LIMIT:
            return None
        body = bytearray(ctx.rest)
        ctx.rest = b""
        while len(body) < length:
            try:
                chunk = conn.recv(min(65536, length - len(body)))
            except (TimeoutError, OSError):
                return None
            if not chunk:
                return None
            body += chunk
        return bytes(body[:length])

    def _deny(
        self,
        conn: socket.socket,
        decision: Decision,
        identity: dict[str, Any] | None,
        ctx: _ConnCtx,
    ) -> None:
        # F5: the 403 is the security boundary — an audit-write failure
        # must never suppress it. Record loudly and refuse anyway.
        try:
            self.audit.write(
                "deny", identity, method=ctx.method, target=ctx.target, **decision.to_log_fields()
            )
        except OSError as error:
            print(
                f"nfm-g2: AUDIT WRITE FAILED on deny (still refusing): {error}",
                file=sys.stderr,
                flush=True,
            )
        message = f"{decision.reason}. {REFUSAL_HINT}"
        payload = json.dumps({"message": message}).encode()
        response = (
            "HTTP/1.1 403 Forbidden\r\n"
            "Content-Type: application/json\r\n"
            f"Content-Length: {len(payload)}\r\n"
            "Connection: close\r\n\r\n"
        ).encode() + payload
        with contextlib.suppress(OSError):
            conn.sendall(response)

    def _forward(self, conn: socket.socket, ctx: _ConnCtx) -> None:
        lines = [f"{ctx.method} {ctx.target} HTTP/1.1"]
        for name, value in ctx.headers:
            if name in ("connection", "proxy-connection"):
                continue
            lines.append(f"{name}: {value}")
        lines.append("Connection: close")
        head = ("\r\n".join(lines) + "\r\n\r\n").encode("iso-8859-1")

        try:
            upstream = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            upstream.connect(self.upstream_path)
        except OSError as error:
            self._bad_gateway(conn, f"nfm-g2: upstream daemon unreachable: {error}")
            return
        upstream.settimeout(None)

        try:
            # Anti-smuggling: bytes that arrived beyond the head are only
            # forwarded when the request actually declares a body (length or
            # chunked framing). Otherwise they are a pipelined second
            # request riding an already-allowed first one — dropped.
            declared_body = any(
                (name == "content-length" and value not in ("", "0")) or name == "transfer-encoding"
                for name, value in ctx.headers
            )
            prefix = ctx.body + (ctx.rest if declared_body else b"")
            upstream.sendall(head + prefix)
        except OSError:
            upstream.close()
            return

        self._pump(conn, upstream)

    def _bad_gateway(self, conn: socket.socket, message: str) -> None:
        payload = json.dumps({"message": message}).encode()
        response = (
            "HTTP/1.1 502 Bad Gateway\r\n"
            "Content-Type: application/json\r\n"
            f"Content-Length: {len(payload)}\r\n"
            "Connection: close\r\n\r\n"
        ).encode() + payload
        with contextlib.suppress(OSError):
            conn.sendall(response)

    def _pump(self, client: socket.socket, upstream: socket.socket) -> None:
        """Relay both directions until either side reaches EOF."""

        def pipe(source: socket.socket, destination: socket.socket) -> None:
            try:
                while True:
                    chunk = source.recv(65536)
                    if not chunk:
                        break
                    destination.sendall(chunk)
            except OSError:
                pass
            finally:
                with contextlib.suppress(OSError):
                    destination.shutdown(socket.SHUT_WR)

        up_thread = threading.Thread(target=pipe, args=(upstream, client), daemon=True)
        down_thread = threading.Thread(target=pipe, args=(client, upstream), daemon=True)
        up_thread.start()
        down_thread.start()
        up_thread.join()
        down_thread.join()
        for sock in (client, upstream):
            with contextlib.suppress(OSError):
                sock.close()
