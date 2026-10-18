#!/usr/bin/env python3
"""NFM runner-egress terminating-TLS proxy — ADR-024 staged exception lane.

Terminates TLS locally and re-originates it with CPython's ssl stack
(its ClientHello passes the on-path DPI; the .NET broker's does not).
Stdlib only — pip/uv cannot reach pypi from this host.

Hardening bar mapping:
  G1  loopback-only bind, enforced below (non-127.0.0.1 binds are refused)
  G3  upstream full certificate verification, fail-closed
      (ssl.create_default_context; NO verify-off anywhere in this file)
  G4  in-code dest-class allowlist, deny-all-else (403 at CONNECT)

Secrets: none in this file or in the repo. Leaf certs/keys are loaded from
the host-local cert dir (NFM_EGRESS_CERT_DIR, default ~/nfm-egress-proxy).

Armed, not active: the runner stays on DIRECT egress. Activation is SRE's
(W4/W5); this daemon merely listens on loopback until then.
"""

import contextlib
import logging
import os
import socket
import socketserver
import ssl
import sys
import threading

log = logging.getLogger("nfm-egress-proxy")

DEFAULT_BIND_HOST = "127.0.0.1"  # G1: loopback only
DEFAULT_PORT = 8443
DEFAULT_CERT_DIR = os.path.expanduser("~/nfm-egress-proxy")

# G4 dest-class allowlist — GitHub Actions backend classes ONLY.
# Suffix match on dot boundaries; base names included.
ALLOWED_DNS_SUFFIXES = ("github.com", "actions.githubusercontent.com")

UPSTREAM_PORT = 443  # the lane is TLS-only; any other port is denied
MAX_REQUEST_LINE = 8192

RESP_200 = b"HTTP/1.1 200 Connection Established\r\n\r\n"
RESP_403 = b"HTTP/1.1 403 Forbidden (dest not in allowlist)\r\n\r\n"
RESP_502 = b"HTTP/1.1 502 Bad Gateway (fail-closed)\r\n\r\n"


class UpstreamVerifyFailedError(Exception):
    """G3: the upstream certificate chain failed full verification."""


def host_allowed(host):
    """True iff host is inside the G4 dest-class allowlist."""
    h = (host or "").strip().lower()
    if h.endswith("."):
        h = h[:-1]
    return any(h == s or h.endswith("." + s) for s in ALLOWED_DNS_SUFFIXES)


def leaf_paths(cert_dir, host):
    """Host-local static leaf paths for a dest host."""
    return (
        os.path.join(cert_dir, f"leaf.{host}.chain.crt"),
        os.path.join(cert_dir, f"leaf.{host}.key"),
    )


def upstream_ssl_context():
    """G3: full verification context. NEVER disables verification.

    NFM_EGRESS_UPSTREAM_CA_BUNDLE optionally adds a trust anchor on top of
    the already-loaded system roots (test hook — it can only widen trust,
    never narrow it); chain + hostname verification stay fully on.
    """
    context = ssl.create_default_context(purpose=ssl.Purpose.SERVER_AUTH)
    bundle = os.environ.get("NFM_EGRESS_UPSTREAM_CA_BUNDLE")
    if bundle:
        context.load_verify_locations(cafile=bundle)
    return context


def connect_upstream(host, port, timeout=30, connect_address=None):
    """Open the re-originated TLS leg to the real destination.

    This is the DPI-relevant handshake: CPython ssl ClientHello, not .NET.
    Certificate verification ALWAYS uses `host` (SNI + server_hostname).
    `connect_address` (test-only) lets hermetic tests point the TCP leg at a
    loopback endpoint while keeping the real hostname for verification.
    Raises UpstreamVerifyFailedError on any certificate verification failure.
    """
    context = upstream_ssl_context()
    address = connect_address or (host, port)
    try:
        sock = socket.create_connection(address, timeout=timeout)
    except OSError as exc:
        log.info("upstream-connect-fail host=%s port=%s err=%s", host, port, exc)
        raise
    try:
        tls = context.wrap_socket(sock, server_hostname=host)
    except ssl.SSLCertVerificationError as exc:
        # G3 fail-closed: bogus chain => no connect-through
        log.warning("upstream-verify-fail host=%s port=%s err=%s", host, port, exc)
        sock.close()
        raise UpstreamVerifyFailedError(str(exc)) from exc
    except (ssl.SSLError, OSError) as exc:
        log.warning("upstream-handshake-fail host=%s port=%s err=%s", host, port, exc)
        sock.close()
        raise
    tls.settimeout(None)
    return tls


def _read_request_line(sock):
    """Drain the full request headers through the blank line (bounded).

    Returns the request line, or "" if the headers never terminate. All
    remaining CONNECT headers must be consumed before the TLS wrap reads,
    otherwise headers still in flight after a segmented client send get
    parsed as the start of the ClientHello.
    """
    data = b""
    while b"\r\n\r\n" not in data and len(data) < MAX_REQUEST_LINE:
        chunk = sock.recv(MAX_REQUEST_LINE - len(data))
        if not chunk:
            break
        data += chunk
    if b"\r\n\r\n" not in data:
        return ""
    return data.split(b"\r\n", 1)[0].decode("iso-8859-1", errors="replace")


def _pump(src, dst, label):
    try:
        while True:
            data = src.recv(65536)
            if not data:
                break
            dst.sendall(data)
    except OSError:
        pass
    finally:
        with contextlib.suppress(OSError):
            dst.shutdown(socket.SHUT_WR)
        log.debug("pump-end %s", label)


class EgressProxyHandler(socketserver.BaseRequestHandler):
    """One client connection: CONNECT gate -> local TLS -> upstream TLS -> pump."""

    def handle(self):
        peer = self.client_address[0]
        try:
            request_line = _read_request_line(self.request)
        except OSError:
            return
        parts = request_line.split()
        if len(parts) < 2:
            self._deny(f"malformed request line: {request_line!r}")
            return
        method, target = parts[0].upper(), parts[1]

        # G4: TLS-only lane — CONNECT to 443 inside the dest classes only.
        if method != "CONNECT":
            self._deny(f"non-CONNECT method {method} from {peer}")
            return
        if ":" not in target:
            self._deny(f"CONNECT without port from {peer}")
            return
        host, _, port = target.rpartition(":")
        try:
            port_num = int(port)
        except ValueError:
            self._deny(f"bad port {port!r} from {peer}")
            return
        host = host.strip("[]")
        if port_num != UPSTREAM_PORT:
            self._deny(f"port {port_num} not allowed (host={host})")
            return
        if not host_allowed(host):
            self._deny(f"dest {host}:{port_num} not in allowlist (from {peer})")
            return

        chain, key = leaf_paths(self.server.cert_dir, host)
        if not (os.path.isfile(chain) and os.path.isfile(key)):
            # In-class but no static leaf minted: fail closed (ca.key is gone,
            # nothing can mint new leaves), never connect through.
            log.warning("no-leaf host=%s — fail closed", host)
            self.request.sendall(RESP_502)
            return

        # Upstream FIRST (fail-closed before we bless the client with a 200).
        connector = self.server.upstream_connector or _default_connector
        try:
            upstream = connector(host, port_num)
        except Exception as exc:  # any upstream failure closes the tunnel
            log.warning("upstream-refused host=%s err=%s", host, exc)
            self.request.sendall(RESP_502)
            return

        # Reply 200, terminate the local TLS leg with the static leaf.
        self.request.sendall(RESP_200)
        server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        server_ctx.load_cert_chain(chain, key)
        try:
            client_tls = server_ctx.wrap_socket(self.request, server_side=True)
        except ssl.SSLError as exc:
            log.info("local-tls-fail host=%s err=%s", host, exc)
            upstream.close()
            return

        log.info("allow host=%s from=%s", host, peer)
        t1 = threading.Thread(
            target=_pump, args=(client_tls, upstream, f"c->u {host}"), daemon=True
        )
        t2 = threading.Thread(
            target=_pump, args=(upstream, client_tls, f"u->c {host}"), daemon=True
        )
        t1.start()
        t2.start()
        t1.join()
        t2.join()

    def _deny(self, reason):
        log.warning("deny %s", reason)
        with contextlib.suppress(OSError):
            self.request.sendall(RESP_403)


def _default_connector(host, port):
    return connect_upstream(host, port, timeout=30)


class EgressServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, address, cert_dir, upstream_connector=None):
        self.cert_dir = cert_dir
        self.upstream_connector = upstream_connector
        super().__init__(address, EgressProxyHandler)


def make_server(bind_host, bind_port, cert_dir, upstream_connector=None):
    """G1: refuse any bind that is not exactly the loopback address."""
    if bind_host != DEFAULT_BIND_HOST:
        sys.exit(f"refusing non-loopback bind {bind_host!r} (G1: 127.0.0.1 only)")
    if not os.path.isdir(cert_dir):
        sys.exit(f"cert dir not found: {cert_dir}")
    server = EgressServer((bind_host, bind_port), cert_dir, upstream_connector)
    log.info(
        "listening %s:%d cert_dir=%s allowlist=%s (staged: runner stays DIRECT)",
        bind_host,
        bind_port,
        cert_dir,
        ",".join(ALLOWED_DNS_SUFFIXES),
    )
    return server


def serve(bind_host=None, bind_port=None, cert_dir=None):
    """CLI entry. Env overrides: NFM_EGRESS_BIND, NFM_EGRESS_PORT,
    NFM_EGRESS_CERT_DIR."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        stream=sys.stderr,
    )
    host = bind_host or os.environ.get("NFM_EGRESS_BIND", DEFAULT_BIND_HOST)
    port = bind_port or int(os.environ.get("NFM_EGRESS_PORT", DEFAULT_PORT))
    cdir = cert_dir or os.environ.get("NFM_EGRESS_CERT_DIR", DEFAULT_CERT_DIR)
    server = make_server(host, port, cdir)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    serve()
