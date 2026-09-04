"""Socket-level integration tests for the NFM-4270 docker gate proxy.

A fake docker daemon (threaded HTTP-over-unix-socket server) stands in
for the real one; the REAL proxy runs in front of it. These tests pin
the properties ADR-013 G2 makes authoritative:

  * denied mutations never reach the daemon (AC-G2),
  * reads flow through unchanged, query strings intact (AC-G2.2),
  * non-prod mutations flow through and get audited,
  * a pipelined second request cannot smuggle past an allowed first one,
  * every denial leaves an attributable JSONL record (AC-G2.6),
  * full (sanctioned) mode passes prod mutations and audits them (AC-G2.3).
"""

from __future__ import annotations

import json
import socket
import sys
import threading
import time
from pathlib import Path

import pytest

GATE_DIR = Path(__file__).resolve().parents[1] / "host-prod-gate"
sys.path.insert(0, str(GATE_DIR))

from nfm_docker_gate.audit import AuditLog  # noqa: E402
from nfm_docker_gate.proxy import DockerGateProxy  # noqa: E402


class FakeDaemon:
    """Just enough docker daemon to make the proxy's decisions visible."""

    def __init__(self, path: str) -> None:
        self.path = path
        self.requests: list[tuple[str, str]] = []  # (method, full target)
        # Optional override for container-inspect responses (NFM-4273
        # review F1: tests of the resolver's volume extraction set this).
        self.inspect_payload: dict | None = None
        # HTTP status for container-inspect responses; a non-200 simulates
        # the daemon hiccup that makes the resolver roundtrip fail.
        self.inspect_status = 200
        self._lock = threading.Lock()
        self._server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server.bind(path)
        self._server.listen(16)
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while True:
            try:
                conn, _ = self._server.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn: socket.socket) -> None:
        try:
            buffer = b""
            while b"\r\n\r\n" not in buffer:
                chunk = conn.recv(65536)
                if not chunk:
                    return
                buffer += chunk
            head, _, rest = buffer.partition(b"\r\n\r\n")
            lines = head.decode().split("\r\n")
            method, target = lines[0].split(" ")[0], lines[0].split(" ")[1]
            length = 0
            for line in lines[1:]:
                name, _, value = line.partition(":")
                if name.strip().lower() == "content-length":
                    length = int(value.strip())
            while len(rest) < length:
                chunk = conn.recv(65536)
                if not chunk:
                    break
                rest += chunk
            with self._lock:
                self.requests.append((method, target))
            path = target.partition("?")[0]
            if path.endswith("/events"):
                self._stream_events(conn)
            else:
                conn.sendall(self._respond(method, target))
            conn.close()
        except OSError:
            pass

    def _stream_events(self, conn: socket.socket) -> None:
        """docker events-style streamed response: headers, then chunks."""
        conn.sendall(
            (
                "HTTP/1.1 200 OK\r\n"
                "Content-Type: application/json\r\n"
                "Transfer-Encoding: chunked\r\n"
                "Connection: close\r\n\r\n"
            ).encode()
        )
        for index in range(3):
            time.sleep(0.05)
            payload = json.dumps({"seq": index}).encode()
            conn.sendall(f"{len(payload):x}\r\n".encode() + payload + b"\r\n")
        conn.sendall(b"0\r\n\r\n")

    def _respond(self, method: str, target: str) -> bytes:
        path = target.partition("?")[0]
        if path.endswith("/containers/json"):
            payload = json.dumps([{"Names": ["nucpot-prod-api"]}]).encode()
        elif "/containers/" in path and path.endswith("/json"):
            if self.inspect_status != 200:
                return (
                    f"HTTP/1.1 {self.inspect_status} Error\r\n"
                    "Content-Type: application/json\r\n"
                    "Connection: close\r\n\r\n"
                ).encode()
            doc = self.inspect_payload if self.inspect_payload is not None else {
                "Name": "/nucpot-prod-api",
                "Config": {"Labels": {"com.docker.compose.project": "nucpot-prod"}},
            }
            payload = json.dumps(doc).encode()
        else:
            payload = json.dumps({"Id": "ok"}).encode()
        return (
            "HTTP/1.1 200 OK\r\n"
            "Content-Type: application/json\r\n"
            f"Content-Length: {len(payload)}\r\n"
            "Connection: close\r\n\r\n"
        ).encode() + payload

    def seen(self, method: str, fragment: str = "") -> bool:
        with self._lock:
            return any(m == method and fragment in t for m, t in self.requests)


class Harness:
    def __init__(self, tmp: Path, mode: str = "ro") -> None:
        # AF_UNIX sun_path is 104 bytes on macOS — pytest tmp dirs under
        # /private/var are far longer, so sockets live in a short /tmp dir.
        import tempfile

        self.sock_dir = tempfile.mkdtemp(prefix="g2t-")
        upstream = self.sock_dir + "/daemon.sock"
        listen = f"{self.sock_dir}/gate-{mode}.sock"
        self.log_path = str(tmp / f"gate-{mode}.log")
        self.daemon = FakeDaemon(upstream)
        self.proxy = DockerGateProxy(
            listen_path=listen,
            upstream_path=upstream,
            audit_log=AuditLog(self.log_path, mode),
            full_mode=(mode == "full"),
        )
        self.gate_path = listen
        threading.Thread(target=self.proxy.serve_forever, daemon=True).start()
        self._wait_listening()

    def close(self) -> None:
        import shutil

        shutil.rmtree(self.sock_dir, ignore_errors=True)

    def _wait_listening(self, timeout: float = 5.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                probe.settimeout(1)
                probe.connect(self.gate_path)
                probe.close()
                return
            except OSError:
                time.sleep(0.05)
        raise RuntimeError("gate socket never came up")

    def request(self, raw: bytes, read_all: bool = True, timeout: float = 5.0) -> bytes:
        conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        conn.settimeout(timeout)
        conn.connect(self.gate_path)
        conn.sendall(raw)
        chunks = []
        if read_all:
            try:
                while True:
                    chunk = conn.recv(65536)
                    if not chunk:
                        break
                    chunks.append(chunk)
            except socket.timeout:
                pass
        conn.close()
        return b"".join(chunks)

    def audit_records(self, event: str) -> list[dict]:
        records = []
        with open(self.log_path, encoding="utf-8") as handle:
            for line in handle:
                record = json.loads(line)
                if record.get("event") == event:
                    records.append(record)
        return records


def http(method: str, target: str, body: bytes = b"", extra: str = "") -> bytes:
    head = (
        f"{method} {target} HTTP/1.1\r\n"
        f"Host: docker\r\n"
        f"Content-Length: {len(body)}\r\n"
        f"{extra}"
        "User-Agent: Docker-Client/test\r\n\r\n"
    ).encode()
    return head + body


@pytest.fixture()
def ro(tmp_path, request):
    harness = Harness(tmp_path, "ro")
    request.addfinalizer(harness.close)
    return harness


@pytest.fixture()
def full(tmp_path, request):
    harness = Harness(tmp_path, "full")
    request.addfinalizer(harness.close)
    return harness


# ---- AC-G2: prod mutations never reach the daemon ---------------------------


def test_delete_prod_container_denied_before_daemon(ro):
    response = ro.request(http("DELETE", "/v1.43/containers/nucpot-prod-api"))
    assert response.startswith(b"HTTP/1.1 403")
    assert not ro.daemon.seen("DELETE")


def test_stop_prod_container_denied(ro):
    response = ro.request(http("POST", "/v1.43/containers/nucpot-prod-api/stop"))
    assert response.startswith(b"HTTP/1.1 403")
    assert not ro.daemon.seen("POST", "/stop")


def test_exec_prod_denied(ro):
    response = ro.request(http("POST", "/v1.43/containers/nucpot-prod-db/exec"))
    assert response.startswith(b"HTTP/1.1 403")


def test_compose_up_denied_at_network_create(ro):
    body = json.dumps({"Name": "nucpot-prod_default"}).encode()
    response = ro.request(http("POST", "/v1.43/networks/create", body))
    assert response.startswith(b"HTTP/1.1 403")


def test_opaque_hex_container_id_resolved_and_denied(ro):
    opaque = "b" * 64
    response = ro.request(http("DELETE", f"/v1.43/containers/{opaque}"))
    assert response.startswith(b"HTTP/1.1 403")
    assert ro.daemon.seen("GET", f"/containers/{opaque}/json")  # resolver consulted
    assert not ro.daemon.seen("DELETE")


def test_short_hex_id_prefix_resolved_and_denied(ro):
    """NFM-4273 review R2: the daemon accepts id prefixes of any unambiguous
    length — an 11-hex prefix must still get a resolver roundtrip instead of
    a free pass as a non-prod 'name'."""
    prefix = "b" * 11
    response = ro.request(http("DELETE", f"/v1.43/containers/{prefix}"))
    assert response.startswith(b"HTTP/1.1 403")
    assert ro.daemon.seen("GET", f"/containers/{prefix}/json")  # resolver consulted
    assert not ro.daemon.seen("DELETE")


def test_opaque_id_with_prod_volume_denied_via_resolver(ro):
    """NFM-4273 review F1 at the proxy layer: the resolver's daemon inspect
    surfaces attached named volumes — a mutation against an innocent-named
    container MOUNTING prod state is denied, and never reaches the daemon."""
    ro.daemon.inspect_payload = {
        "Name": "/innocent-toolbox",
        "Config": {"Labels": {}},
        "NetworkSettings": {"Networks": {"bridge": {}}},
        "Mounts": [
            {"Type": "volume", "Name": "nucpot-prod_pgdata",
             "Source": "/var/lib/docker/volumes/x/_data", "Destination": "/data"},
            {"Type": "bind", "Source": "/Users/x/proj", "Destination": "/p"},
        ],
    }
    opaque = "9" * 64
    response = ro.request(http("POST", f"/v1.43/containers/{opaque}/stop"))
    assert response.startswith(b"HTTP/1.1 403")
    assert ro.daemon.seen("GET", f"/containers/{opaque}/json")  # resolver consulted
    assert not ro.daemon.seen("POST", "/stop")


def test_prune_denied(ro):
    assert ro.request(http("POST", "/v1.43/containers/prune")).startswith(b"HTTP/1.1 403")
    assert not ro.daemon.seen("POST", "prune")


def test_network_connect_body_prod_container_denied(ro):
    """NFM-4273 review E2: ``docker network connect rogue-net
    nucpot-prod-api-1`` — the prod container rides in the connect BODY,
    so the proxy must read it and the daemon must never see the connect."""
    body = json.dumps({"Container": "nucpot-prod-api-1"}).encode()
    response = ro.request(http("POST", "/v1.43/networks/rogue-net/connect", body))
    assert response.startswith(b"HTTP/1.1 403")
    assert not ro.daemon.seen("POST", "/connect")


def test_unresolvable_opaque_id_fails_closed(ro):
    """NFM-4273 review W1: when the resolver's daemon inspect fails
    (timeout / 5xx), the mutation against the opaque id must be denied,
    not waved through as a non-prod 'name'."""
    ro.daemon.inspect_status = 500
    response = ro.request(http("DELETE", f"/v1.43/containers/{'a' * 64}"))
    assert response.startswith(b"HTTP/1.1 403")
    assert not ro.daemon.seen("DELETE")


# ---- AC-G2.2: reads frictionless, semantics preserved -----------------------


def test_containers_ps_flows_through_with_query(ro):
    response = ro.request(http("GET", "/v1.43/containers/json?all=1&size=1"))
    assert response.startswith(b"HTTP/1.1 200")
    assert b"nucpot-prod-api" in response
    assert ro.daemon.seen("GET", "/containers/json?all=1&size=1")


def test_logs_inspect_stats_flows_through(ro):
    for target in ("/v1.43/containers/abc123/json", "/v1.43/containers/abc123/logs?tail=100"):
        assert ro.request(http("GET", target)).startswith(b"HTTP/1.1 200"), target


def test_version_flows_through(ro):
    assert ro.request(http("GET", "/v1.43/version")).startswith(b"HTTP/1.1 200")


# ---- non-prod + image-layer mutations pass and get audited -------------------


def test_staging_stop_flows_through_and_audited(ro):
    response = ro.request(http("POST", "/v1.43/containers/nucpot-staging-api/stop"))
    assert response.startswith(b"HTTP/1.1 200")
    assert ro.daemon.seen("POST", "/nucpot-staging-api/stop")
    allows = ro.audit_records("allow")
    assert any(r["target"] == "/v1.43/containers/nucpot-staging-api/stop" for r in allows)


def test_staging_create_with_body_flows_through(ro):
    body = json.dumps({"Image": "nucpot-staging-api:x", "Labels": {}}).encode()
    response = ro.request(http("POST", "/v1.43/containers/create?name=nucpot-staging-api-1", body))
    assert response.startswith(b"HTTP/1.1 200")
    assert ro.daemon.seen("POST", "containers/create?name=nucpot-staging-api-1")


def test_build_flows_through(ro):
    response = ro.request(http("POST", "/v1.43/build?t=nucpot-prod-api:candidate-1",
                               extra="Transfer-Encoding: chunked\r\n"))
    assert response.startswith(b"HTTP/1.1 200")
    assert ro.daemon.seen("POST", "/build?t=nucpot-prod-api:candidate-1")


# ---- anti-smuggling: one request per connection -------------------------------


def test_pipelined_mutation_after_allowed_get_is_dropped(ro):
    smuggle = (
        http("GET", "/v1.43/containers/json")
        + http("DELETE", "/v1.43/containers/nucpot-prod-api")
    )
    response = ro.request(smuggle)
    assert response.startswith(b"HTTP/1.1 200")  # first request answered
    assert not ro.daemon.seen("DELETE")  # smuggled one never arrived
    assert ro.audit_records("deny") == []  # never even classified as a request


def test_smuggle_behind_declared_body_is_dropped(ro):
    body = json.dumps({"Name": "nucpot-staging-x"}).encode()
    smuggle = (
        http("POST", "/v1.43/networks/create", body)
        + http("DELETE", "/v1.43/containers/nucpot-prod-api")
    )
    response = ro.request(smuggle)
    assert response.startswith(b"HTTP/1.1 200")
    assert ro.daemon.seen("POST", "networks/create")
    assert not ro.daemon.seen("DELETE")


# ---- AC-G2.6: attributable deny records ----------------------------------------


def test_deny_record_carries_identity_verb_target(ro):
    ro.request(http("DELETE", "/v1.43/containers/nucpot-prod-api"))
    denies = ro.audit_records("deny")
    assert len(denies) == 1
    record = denies[0]
    assert record["method"] == "DELETE"
    assert "nucpot-prod-api" in record["target"]
    assert record["scope"] == "prod"
    identity = record["identity"]
    assert identity["known"] is True
    assert identity["pid"] and identity["pid"] > 0
    assert identity["uid"] is not None


# ---- AC-G2.3: sanctioned (full) mode -------------------------------------------


def test_full_mode_allows_prod_stop_and_audits(full):
    response = full.request(http("POST", "/v1.43/containers/nucpot-prod-api/stop"))
    assert response.startswith(b"HTTP/1.1 200")
    assert full.daemon.seen("POST", "/nucpot-prod-api/stop")
    allows = full.audit_records("allow")
    assert any("nucpot-prod-api" in r["target"] for r in allows)


def test_full_mode_audits_identity(full):
    full.request(http("POST", "/v1.43/containers/nucpot-prod-api/stop"))
    allows = full.audit_records("allow")
    assert allows and allows[-1]["identity"].get("pid")


# ---- streaming -----------------------------------------------------------------


def test_streamed_events_response_relayed_intact(ro):
    """docker logs -f / events / stats need multi-write streams to pass."""
    response = ro.request(http("GET", "/v1.43/events"), timeout=10)
    assert response.startswith(b"HTTP/1.1 200")
    assert b"Transfer-Encoding: chunked" in response
    for index in range(3):
        assert json.dumps({"seq": index}).encode() in response
    assert response.endswith(b"0\r\n\r\n")


def test_daemon_unreachable_yields_502_not_silent_pass(tmp_path):
    harness = Harness(tmp_path, "ro")
    import os

    os.unlink(harness.daemon.path)
    response = harness.request(http("GET", "/v1.43/containers/json"))
    assert response.startswith(b"HTTP/1.1 502")
