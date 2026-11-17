"""Tests for egress_proxy.py — G1 loopback, G3 fail-closed upstream, G4 allowlist.

Hermetic: upstream TLS endpoints are loopback servers with test CAs.
The production upstream connector (connect_upstream) is exercised directly
against a loopback bogus-cert endpoint for the G3 negative test.
"""

import contextlib
import pathlib
import socket
import ssl
import subprocess
import threading
import time

import egress_proxy
import pytest

HERE = pathlib.Path(__file__).resolve().parent
MINT = HERE / "mint_ca.sh"
DEST_HOST = "broker.actions.githubusercontent.com"
OUT_OF_CLASS = "evil.example.com"
IN_CLASS_NO_LEAF = "foo.github.com"
# Bound for every loopback handshake/IO wait — generous because CI hosts
# run loaded (the pre-fix 10s expired under load; see NFM-5587 de-flake).
HANDSHAKE_TIMEOUT_S = 30


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, check=False, **kw)


@pytest.fixture(scope="module")
def cert_dir(tmp_path_factory):
    install = tmp_path_factory.mktemp("certs")
    r = run(["bash", str(MINT), str(install), "out-of-scope.probe.nfm.invalid"])
    assert r.returncode == 0, r.stderr
    return install


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class TestAllowlist:
    @pytest.mark.parametrize(
        "host",
        [
            "github.com",
            "www.github.com",
            "api.github.com",
            "actions.githubusercontent.com",
            "broker.actions.githubusercontent.com",
            "pipelinesghubeus23.actions.githubusercontent.com",
            "GITHUB.COM",
            "github.com.",
        ],
    )
    def test_allowed(self, host):
        assert egress_proxy.host_allowed(host)

    @pytest.mark.parametrize(
        "host",
        [
            "evil.example.com",
            "github.com.evil.com",
            "notgithub.com",
            "egithub.com",
            "actions.githubusercontent.com.evil.net",
            "",
        ],
    )
    def test_denied(self, host):
        assert not egress_proxy.host_allowed(host)


class TestBindGuard:
    def test_refuses_non_loopback_bind(self, cert_dir, monkeypatch):
        monkeypatch.setenv("NFM_EGRESS_CERT_DIR", str(cert_dir))
        with pytest.raises(SystemExit):
            egress_proxy.serve("0.0.0.0", free_port())

    def test_refuses_link_local_bind(self, cert_dir, monkeypatch):
        monkeypatch.setenv("NFM_EGRESS_CERT_DIR", str(cert_dir))
        with pytest.raises(SystemExit):
            egress_proxy.serve("127.0.0.2", free_port())


class LoopbackUpstream:
    """Loopback TLS server speaking as DEST_HOST under a test CA.

    Two flake defenses (CR finding on NFM-5589, root-caused on NFM-5587):

    1. One handler thread per accepted connection: the module-scoped server
       is shared across the whole module, and a serial accept loop let one
       stalled handshake block the next test's connection until its client
       timed out (`_ssl.c:1063: The handshake operation timed out`). Handler
       threads are bounded by HANDSHAKE_TIMEOUT_S so a client that never
       completes cannot leak one forever.
    2. The loopback upstream is pinned to TLS 1.2. Under TLS 1.3 the tight
       loopback topology hits a rare race where the client's first
       post-handshake record is written successfully but never surfaces in
       the server's kernel receive queue (verified at the stall: empty
       MSG_PEEK + ssl pending 0 at the 30s timeout; ~3%/connection
       standalone, TLS 1.2 forced = 0/200). Test-topology artifact only —
       the production proxy's upstream leg to real github.com endpoints is
       unaffected and stays TLS 1.3.
    """

    def __init__(self, certfile, keyfile):
        self.port = free_port()
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(certfile, keyfile)
        # TLS 1.2 pin — class docstring, defense 2
        ctx.maximum_version = ssl.TLSVersion.TLSv1_2
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", self.port))
        self.sock.listen(16)
        self.ctx = ctx
        self.running = True
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def _loop(self):
        while self.running:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn):
        try:
            conn.settimeout(HANDSHAKE_TIMEOUT_S)
            tls = self.ctx.wrap_socket(conn, server_side=True)
            data = tls.recv(4096)
            tls.sendall(b"ECHO:" + data)
            tls.close()
        except Exception:
            with contextlib.suppress(OSError):
                conn.close()

    def close(self):
        self.running = False
        with contextlib.suppress(OSError):
            self.sock.close()


@pytest.fixture(scope="module")
def test_ca_env(tmp_path_factory):
    """Independent test CA + leaf for the loopback upstream server."""
    d = tmp_path_factory.mktemp("upstream_ca")
    key, crt = d / "u.key", d / "u.crt"
    run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            str(key),
            "-out",
            str(crt),
            "-days",
            "2",
            "-subj",
            "/CN=upstream.test",
            "-addext",
            f"subjectAltName=DNS:{DEST_HOST}",
        ]
    )
    return crt, key, d


@pytest.fixture(scope="module")
def good_upstream(test_ca_env):
    crt, key, _ = test_ca_env
    srv = LoopbackUpstream(str(crt), str(key))
    yield srv
    srv.close()


@pytest.fixture(scope="module")
def bad_upstream(tmp_path_factory):
    """Self-signed cert NOT in the trust bundle -> G3 must fail closed."""
    d = tmp_path_factory.mktemp("bad_ca")
    key, crt = d / "b.key", d / "b.crt"
    run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            str(key),
            "-out",
            str(crt),
            "-days",
            "2",
            "-subj",
            f"/CN={DEST_HOST}",
            "-addext",
            f"subjectAltName=DNS:{DEST_HOST}",
        ]
    )
    srv = LoopbackUpstream(str(crt), str(key))
    yield srv
    srv.close()


@pytest.fixture(scope="module")
def proxy(cert_dir):
    """Running proxy on an ephemeral loopback port."""
    port = free_port()
    server = egress_proxy.make_server("127.0.0.1", port, str(cert_dir))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield ("127.0.0.1", port)
    server.shutdown()
    server.server_close()


def connect_dest(addr, request_line):
    s = socket.create_connection(addr, timeout=HANDSHAKE_TIMEOUT_S)
    s.sendall(request_line.encode() + b"\r\n\r\n")
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = s.recv(4096)
        if not chunk:
            break
        data += chunk
    s.close()
    return data.split(b"\r\n\r\n")[0].decode(errors="replace")


class TestConnectGate:
    def test_out_of_class_denied_403(self, proxy):
        head = connect_dest(proxy, f"CONNECT {OUT_OF_CLASS}:443 HTTP/1.1\r\nHost: {OUT_OF_CLASS}")
        assert head.startswith("HTTP/1.1 403")

    def test_in_class_wrong_port_denied_403(self, proxy):
        head = connect_dest(proxy, "CONNECT github.com:80 HTTP/1.1\r\nHost: github.com")
        assert head.startswith("HTTP/1.1 403")

    def test_plain_http_method_denied_403(self, proxy):
        head = connect_dest(proxy, "GET http://github.com/ HTTP/1.1\r\nHost: github.com")
        assert head.startswith("HTTP/1.1 403")

    def test_in_class_without_leaf_fails_closed_502(self, proxy):
        head = connect_dest(
            proxy, f"CONNECT {IN_CLASS_NO_LEAF}:443 HTTP/1.1\r\nHost: {IN_CLASS_NO_LEAF}"
        )
        assert head.startswith("HTTP/1.1 502")


class TestUpstreamFailClosed:
    def test_bogus_upstream_chain_no_connect_through(self, bad_upstream, monkeypatch):
        """G3 negative: untrusted upstream chain => connect_upstream raises."""
        # no trust bundle -> system roots only -> loopback self-signed must fail
        monkeypatch.delenv("NFM_EGRESS_UPSTREAM_CA_BUNDLE", raising=False)
        with pytest.raises((ssl.SSLCertVerificationError, egress_proxy.UpstreamVerifyFailedError)):
            egress_proxy.connect_upstream(
                DEST_HOST,
                bad_upstream.port,
                timeout=HANDSHAKE_TIMEOUT_S,
                connect_address=("127.0.0.1", bad_upstream.port),
            )

    def test_good_chain_connects(self, good_upstream, test_ca_env, monkeypatch):
        crt, _, _ = test_ca_env
        monkeypatch.setenv("NFM_EGRESS_UPSTREAM_CA_BUNDLE", str(crt))
        sock = egress_proxy.connect_upstream(
            DEST_HOST,
            good_upstream.port,
            timeout=HANDSHAKE_TIMEOUT_S,
            connect_address=("127.0.0.1", good_upstream.port),
        )
        assert sock.gettimeout() is None
        sock.sendall(b"ping")
        assert sock.recv(4096) == b"ECHO:ping"
        sock.close()


class TestTerminatedFlow:
    def test_end_to_end_roundtrip(self, cert_dir, good_upstream, test_ca_env, monkeypatch):
        """Client TLS terminates at proxy, proxy re-originates TLS upstream.

        Hermetic: a connector is constructor-injected that carries the
        production upstream wrap (full verification against the env trust
        bundle) to the loopback upstream. The CLI path never injects one.
        """
        crt, _, _ = test_ca_env
        monkeypatch.setenv("NFM_EGRESS_UPSTREAM_CA_BUNDLE", str(crt))

        def loopback_connector(host, port):
            assert host == DEST_HOST
            return egress_proxy.connect_upstream(
                host,
                good_upstream.port,
                timeout=HANDSHAKE_TIMEOUT_S,
                connect_address=("127.0.0.1", good_upstream.port),
            )

        port = free_port()
        server = egress_proxy.make_server(
            "127.0.0.1", port, str(cert_dir), upstream_connector=loopback_connector
        )
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            client_ctx = ssl.create_default_context(cafile=str(cert_dir / "ca.cert.pem"))
            raw = socket.create_connection(("127.0.0.1", port), timeout=HANDSHAKE_TIMEOUT_S)
            raw.sendall(f"CONNECT {DEST_HOST}:443 HTTP/1.1\r\nHost: {DEST_HOST}\r\n\r\n".encode())
            buf = b""
            while b"\r\n\r\n" not in buf:
                buf += raw.recv(4096)
            assert b" 200 " in buf.split(b"\r\n")[0]
            tls = client_ctx.wrap_socket(raw, server_hostname=DEST_HOST)
            tls.sendall(b"hello-via-proxy")
            assert tls.recv(4096) == b"ECHO:hello-via-proxy"
            tls.close()
        finally:
            server.shutdown()
            server.server_close()

    def test_segmented_connect_headers_handshake_survives(
        self, cert_dir, good_upstream, test_ca_env, monkeypatch
    ):
        """Regression: the request line and remaining headers arrive in
        separate TCP segments — the proxy must drain all headers through the
        blank line before wrapping, or the stray headers are read as the
        ClientHello and the local handshake fails."""
        crt, _, _ = test_ca_env
        monkeypatch.setenv("NFM_EGRESS_UPSTREAM_CA_BUNDLE", str(crt))

        def loopback_connector(host, port):
            return egress_proxy.connect_upstream(
                host,
                good_upstream.port,
                timeout=HANDSHAKE_TIMEOUT_S,
                connect_address=("127.0.0.1", good_upstream.port),
            )

        port = free_port()
        server = egress_proxy.make_server(
            "127.0.0.1", port, str(cert_dir), upstream_connector=loopback_connector
        )
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            client_ctx = ssl.create_default_context(cafile=str(cert_dir / "ca.cert.pem"))
            raw = socket.create_connection(("127.0.0.1", port), timeout=HANDSHAKE_TIMEOUT_S)
            raw.sendall(f"CONNECT {DEST_HOST}:443 HTTP/1.1\r\n".encode())
            time.sleep(0.3)
            raw.sendall(f"Host: {DEST_HOST}\r\n\r\n".encode())
            buf = b""
            while b"\r\n\r\n" not in buf:
                buf += raw.recv(4096)
            assert b" 200 " in buf.split(b"\r\n")[0]
            tls = client_ctx.wrap_socket(raw, server_hostname=DEST_HOST)
            tls.sendall(b"segmented-send")
            assert tls.recv(4096) == b"ECHO:segmented-send"
            tls.close()
        finally:
            server.shutdown()
            server.server_close()
