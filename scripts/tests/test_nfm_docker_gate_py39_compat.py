"""Py3.9 compatibility regression guards for the NFM-4270 host gate package (NFM-4320).

The launchd proxies execute with the system interpreter
``/usr/bin/python3`` = 3.9.6, but the package was authored against 3.10+
constructs and shipped to main in NFM-4273/#1159/#1161's blind spot:

  * ``policy.py`` evaluated a PEP 604 union at RUNTIME (module-level type
    alias ``Resolver = Callable[[str], TargetInfo | None]``) — ``from
    __future__ import annotations`` does not defer aliases, only
    annotations, so the full proxy died at IMPORT with ``TypeError``.
  * ``audit.py`` called ``datetime.datetime.now(datetime.UTC)`` — the
    ``datetime.UTC`` alias is 3.11+, so the ro proxy died on its FIRST
    AUDIT WRITE with ``AttributeError``.
  * ``proxy.py`` caught ``TimeoutError`` around socket reads — on 3.9
    ``socket.timeout is TimeoutError`` is False (they only became aliases
    in 3.10), so an idle ``accept()`` timeout fell through to the outer
    ``except OSError: raise`` and killed ``serve_forever``.

Each defect class is pinned by executing the real package under the real
3.9 interpreter via subprocess: the functional smoke below imports every
module, exercises the audit write and policy classification, and proves an
idle ``accept()`` timeout does not kill ``serve_forever``; the entry-script
test proves the launchd entry point parses args. On hosts whose
``/usr/bin/python3`` is not 3.9 both tests skip; the style constraint
(Optional over PEP 604, timezone.utc over UTC) is additionally pinned in
the lint gate via the ``UP017``/``UP045``/``UP041`` per-file ignores in
``pyproject.toml``.
"""

from __future__ import annotations

import subprocess
from functools import lru_cache
from pathlib import Path

import pytest

GATE_DIR = Path(__file__).resolve().parents[1] / "host-prod-gate"
PY39 = "/usr/bin/python3"


@lru_cache(maxsize=1)
def _py39_available() -> bool:
    try:
        probe = subprocess.run(
            [PY39, "-c", "import sys; assert sys.version_info[:2] == (3, 9)"],
            capture_output=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return probe.returncode == 0


SMOKE = r"""
import json, os, socket, sys, tempfile, threading, time

sys.path.insert(0, os.getcwd())

import nfm_docker_gate.audit as audit
import nfm_docker_gate.peercred as peercred
import nfm_docker_gate.policy as policy
import nfm_docker_gate.proxy as proxy
import nfm_docker_gate.watchdog as watchdog

# defect 1: the module-level Resolver alias must evaluate on 3.9
assert policy.Resolver is not None

# defect 2: an audit record must carry a timestamp on 3.9
path = os.path.join(tempfile.mkdtemp(), "audit.jsonl")
log = audit.AuditLog(path, "ro")
log.write("startup", None, extra="x")
record = json.loads(open(path, encoding="utf-8").read().splitlines()[0])
assert record["ts"], record

# policy still classifies: reads pass, prod mutations denied
cfg = policy.ScopeConfig()
assert policy.classify("GET", "/v1.43/_ping", "", None, None, cfg).allowed
denied = policy.classify(
    "POST", "/v1.43/containers/nucpot-prod-api-1/stop", "", None, None, cfg
)
assert not denied.allowed and denied.scope == "prod", denied

# defect 3: an idle accept() timeout must not kill serve_forever on 3.9,
# where socket.timeout is NOT the built-in TimeoutError
tmp = tempfile.mkdtemp()
upstream_path = os.path.join(tmp, "up.sock")
listen_path = os.path.join(tmp, "gate.sock")
server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
server.bind(upstream_path)
server.listen(4)

def daemon():
    try:
        while True:
            conn, _ = server.accept()
            conn.recv(65536)
            conn.sendall(
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                b"Content-Length: 2\r\nConnection: close\r\n\r\n{}"
            )
            conn.close()
    except OSError:
        pass

upstream_thread = threading.Thread(target=daemon, daemon=True)
upstream_thread.start()
gate = proxy.DockerGateProxy(
    listen_path=listen_path,
    upstream_path=upstream_path,
    audit_log=audit.AuditLog(os.path.join(tmp, "gate.jsonl"), "ro"),
)
worker = threading.Thread(target=gate.serve_forever, daemon=True)
worker.start()
for _ in range(200):  # bind must precede the idle window, else the test
    if os.path.exists(listen_path):
        break
    time.sleep(0.05)
time.sleep(1.6)  # straddle the proxy's fixed 1.0s accept timeout
assert worker.is_alive(), (
    "serve_forever died on an accept-idle timeout "
    "(py3.9 socket.timeout is not TimeoutError)"
)
client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
client.settimeout(15)
client.connect(listen_path)
client.sendall(
    b"GET /v1.43/_ping HTTP/1.1\r\nHost: docker\r\n"
    b"Content-Length: 0\r\nConnection: close\r\n\r\n"
)
buf = b""
while b"\r\n\r\n" not in buf:
    chunk = client.recv(4096)
    if not chunk:
        break
    buf += chunk
status = buf.split(b"\r\n", 1)[0]
assert b" 200 " in status, status
gate.shutdown()
# Clean teardown before interpreter exit (NFM-4320 round-1 review finding):
# exiting with threads still blocked in accept() on open Unix sockets
# segfaults py3.9 at Py_Finalize intermittently — a false red that fired
# exclusively on-host, in the verification path RE/SRE rely on.
worker.join(2.0)  # serve_forever exits at its next <=1.0s accept-timeout
                  # tick and closes its listening socket
assert not worker.is_alive(), "serve_forever ignored shutdown() for 2s"
server.close()  # the mock daemon's accept() now raises OSError and its
                # existing handler exits the loop
upstream_thread.join(2.0)
assert not upstream_thread.is_alive(), "mock upstream daemon did not exit"
print("PY39-SMOKE-OK")
"""


@pytest.mark.skipif(not _py39_available(), reason="no /usr/bin/python3 == 3.9 on this host")
def test_package_smoke_under_py39() -> None:
    """Import + audit write + classify + proxy idle-survival on real 3.9."""
    result = subprocess.run(
        [PY39, "-c", SMOKE],
        cwd=str(GATE_DIR),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, f"3.9 smoke failed:\n{result.stdout}\n{result.stderr}"
    assert "PY39-SMOKE-OK" in result.stdout


@pytest.mark.skipif(not _py39_available(), reason="no /usr/bin/python3 == 3.9 on this host")
def test_entry_script_help_under_py39() -> None:
    """The launchd entry script must at least parse args under 3.9."""
    result = subprocess.run(
        [PY39, str(GATE_DIR / "nfm_docker_gate_proxy.py"), "--help"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert "docker gate proxy" in result.stdout.lower()
