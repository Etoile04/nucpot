"""Regression tests for ADR-023 / NFM-5542 — sentinel egress independence.

Context (CTO-verified ground truth, 2026-10-15, NFM-5540 → parent NFM-5541):
the NFM-5226 on-host sentinel had THREE python egress lanes with inconsistent
proxy posture. Bare ``urlopen``/``urllib.request.urlopen`` rides the macOS
system proxy (v2cloud 127.0.0.1:7892 via scutil), and three episodes in ~20 h
produced false CRITICALs / page-latch cycles while the site was fully up:

- 2026-10-14 x2 — named-check lane blind (SSL EOF through the proxy vantage;
  NFM-5525 later added the confirmation ladder, but the PRIMARY still rode
  the VPN);
- 2026-10-15 02:12-02:26Z — the UNCOVERED chunk-scan lane: log shows
  ``fetches=0/5``, mode=A x3 → CRITICAL, origin=ok. A VPN flap read as
  edge-down because the scan's page GETs and ref HEADs went through
  ``urllib.request.urlopen`` with no bypass and no ladder.

Timeline proof the ladder design was insufficient: the NFM-5525 ladder merged
2026-10-14T16:51Z (``fb098c9e0``) — yet the chunk-scan lane still went blind
that night. Failure-path confirmation cannot fix a lane it does not cover,
and a covered lane's primary still rode the VPN (inverse blindness: a real
outage during a VPN flap stays unclassifiable).

ADR-023 decision: **direct is the PRIMARY lane everywhere; proxy paths cease
to exist for probes.** These tests pin the contract (AC1-AC4 of NFM-5542):

- AC1: ``check_url``'s primary fetch uses the no-proxy opener — a proxy-hostile
  environment (dead ``getproxies`` + a dead default opener installed) cannot
  make the check fail-or-annotate; it must pass CLEAN (no ladder annotation).
- AC2: the chunk-scan heredoc's page GETs AND ref HEADs go through the
  no-proxy opener — the heredoc runs against a local mock edge with env-var
  proxies pinned to a dead address and ``no_proxy`` deliberately ABSENT, and
  still reads green (the wrapper's own D2 export is bypassed by running the
  heredoc python directly, so this pins the OPENER, not the env).
- AC3: the wrapper exports ``no_proxy='*'`` / ``NO_PROXY='*'`` into its python
  invocations (captured via an env-dumping probe stub), and the webhook
  sender uses the no-proxy opener.
- AC4: every sentinel run logs a one-line ``/usr/sbin/scutil --proxy`` summary
  at startup, and every degraded cycle carries it adjacent to the
  ``mode=/fetches=`` evidence — the next flap must adjudicate host-egress vs
  edge in ONE log line.

Records correction pinned here as well (D4): the 10-14 "CN-side ClientHello
DPI" attribution for the site-monitor windows is superseded — local proxy
defects. ADR-013's broker/GitHub DPI rulings are a different subsystem and
stay valid (asserted untouched below).

Hermeticity: local ``ThreadingHTTPServer`` stubs, dead loopback proxies
(``127.0.0.1:1`` — instant refusal), no real network. The monitor-wrapper
tests reuse the fixtures/helpers of ``test_nfm_site_monitor.py`` (same
established env-seam pattern; every helper hard-fails if a seam is stripped).
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import URLError

import pytest

SCRIPT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPT_DIR))

import health_check  # noqa: E402

MONITOR = SCRIPT_DIR / "nfm-site-monitor.sh"
DEAD_PROXY = "http://127.0.0.1:1"  # nothing listens on port 1 — refused instantly

# Established helpers/fixtures from the NFM-5446/5497 wrapper suite: the mock
# edge server, env-seam set, runner and log/state readers.
from test_nfm_site_monitor import (  # noqa: E402
    REF_TIMEOUT,
    _degrade_refs,
    log_text,
    mock_site,  # noqa: F401  (pytest fixture)
    run_script,
)
from test_nfm_site_monitor import (  # noqa: E402
    env as monitor_env,  # noqa: F401  (pytest fixture)
)

# ---------------------------------------------------------------------------
# Local stub site for health_check.check_url (AC1) — plain 200 JSON endpoint.
# ---------------------------------------------------------------------------


class _PlainHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # silence
        pass

    def do_GET(self):
        body = b'{"status":"ok"}'
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture()
def local_site():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _PlainHandler)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


# ---------------------------------------------------------------------------
# AC1 — check_url primary fetch is proxy-independent
# ---------------------------------------------------------------------------


def test_check_url_primary_survives_dead_proxies(local_site, monkeypatch):
    """AC1: with every proxy path poisoned, the primary fetch still passes CLEAN.

    ``getproxies`` is pinned to a dead proxy AND a dead-proxy default opener
    is installed (so even a cached ``urlopen`` default opener cannot slip
    through). A proxy-aware primary would fail here and only survive via the
    ladder's direct confirm — observable as an annotated pass. The assertion
    ``error is None`` is the discriminator: ADR-023 requires the PRIMARY
    itself to be direct.
    """
    dead = {"http": DEAD_PROXY, "https": DEAD_PROXY}
    monkeypatch.setattr(urllib.request, "getproxies", lambda: dead)
    monkeypatch.setattr(urllib.request, "proxy_bypass", lambda host: False)
    dead_default = urllib.request.build_opener(urllib.request.ProxyHandler(dead))
    monkeypatch.setattr(urllib.request, "_opener", dead_default)

    # Kill-harness self-check: a proxy-aware fetch MUST fail in this setup —
    # otherwise this test proves nothing (the poison is not effective).
    with pytest.raises(URLError):
        dead_default.open(local_site, timeout=5)

    target = health_check.CheckTarget(
        name="T", url=local_site, expected_contains='"ok"', severity="P0"
    )
    result = health_check.check_url(target)

    assert result.success is True
    assert result.status_code == 200
    # Clean pass — no "direct-egress confirm OK" ladder annotation: the
    # primary itself ignored the poisoned proxy environment.
    assert result.error is None


def test_check_url_primary_survives_dead_proxies_p1(local_site, monkeypatch):
    """AC1 breadth: the same poison cannot blind a P1 named check either."""
    dead = {"http": DEAD_PROXY, "https": DEAD_PROXY}
    monkeypatch.setattr(urllib.request, "getproxies", lambda: dead)
    monkeypatch.setattr(urllib.request, "proxy_bypass", lambda host: False)
    monkeypatch.setattr(
        urllib.request, "_opener", urllib.request.build_opener(urllib.request.ProxyHandler(dead))
    )

    target = health_check.CheckTarget(name="T", url=local_site, severity="P1")
    result = health_check.check_url(target)

    assert result.success is True
    assert result.error is None


# ---------------------------------------------------------------------------
# AC3 (webhook half) — send_alert POSTs through the no-proxy opener
# ---------------------------------------------------------------------------


class _CaptureOpener:
    """Records open() calls; stands in for NO_PROXY_OPENER."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, bytes]] = []

    def open(self, req, timeout=None):
        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        self.calls.append((req.full_url, req.get_method(), req.data or b""))
        return _Resp()


def test_send_alert_uses_no_proxy_opener(monkeypatch):
    """AC3: the webhook lane (D1c) POSTs via NO_PROXY_OPENER — no bare urlopen."""
    cap = _CaptureOpener()
    monkeypatch.setattr(health_check, "NO_PROXY_OPENER", cap)
    failing = health_check.CheckResult(
        target=health_check.CheckTarget(name="T", url="https://t.example/", severity="P1"),
        success=False,
        error="Connection error: poisoned vantage",
    )

    health_check.send_alert([failing], "https://hook.example/xyz")

    assert len(cap.calls) == 1
    url, method, data = cap.calls[0]
    assert url == "https://hook.example/xyz"
    assert method == "POST"
    assert b"msg_type" in data


def test_send_alert_swallows_opener_failure(monkeypatch, capsys):
    """Webhook failure stays best-effort (unchanged semantics): printed, not raised."""

    class _Dead:
        def open(self, req, timeout=None):
            raise URLError("refused")

    monkeypatch.setattr(health_check, "NO_PROXY_OPENER", _Dead())
    failing = health_check.CheckResult(
        target=health_check.CheckTarget(name="T", url="https://t.example/", severity="P1"),
        success=False,
        error="x",
    )
    health_check.send_alert([failing], "https://hook.example/xyz")  # must not raise
    assert "Failed to send alert" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# AC2 — chunk-scan heredoc proxy-independence (opener, not env)
# ---------------------------------------------------------------------------


def _heredoc_source() -> str:
    """Extract the embedded python scan body from the wrapper (between
    ``<<'PYEOF'`` and the terminating ``PYEOF``)."""
    src = MONITOR.read_text(encoding="utf-8")
    start = src.index("<<'PYEOF'")
    body_start = src.index("\n", start) + 1
    body_end = src.index("\nPYEOF", body_start)
    return src[body_start:body_end]


def _scan_env(base: str) -> dict:
    """Env for running the heredoc python DIRECTLY: dead env-var proxies,
    ``no_proxy``/``NO_PROXY`` deliberately ABSENT — pins the opener (D1b),
    not the wrapper's D2 export (which this path bypasses by construction)."""
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", "/tmp"),
        "NFM_SCAN_BASE": base,
        "NFM_SCAN_DEADLINE": "60",
        "NFM_SCAN_REF_POOL": "4",
        "http_proxy": DEAD_PROXY,
        "https_proxy": DEAD_PROXY,
        "HTTP_PROXY": DEAD_PROXY,
        "HTTPS_PROXY": DEAD_PROXY,
    }


def test_chunk_scan_heredoc_is_proxy_independent(mock_site):  # noqa: F811  (fixture param shadows the registered import above)
    """AC2: with env proxies dead and no_proxy absent, the scan reads GREEN.

    The 2026-10-15 02:12Z false-CRITICAL shape: a bare-urlopen scan lane in
    this environment would route every page GET through the dead proxy and
    report ``fetches=0/5`` degraded. The shared no-proxy opener must fetch
    pages AND verify refs directly.
    """
    base, _config = mock_site
    body = _heredoc_source()
    assert "ProxyHandler" in body, "heredoc extraction failed (no opener present)"

    # Kill-harness self-check: a proxy-aware (default-opener) fetch must fail
    # in exactly this env — otherwise the green below is vacuous.
    probe = (
        "import sys, urllib.request\n"
        "try:\n"
        "    urllib.request.urlopen(sys.argv[1], timeout=5)\n"
        "    print('UNEXPECTED-GREEN')\n"
        "except Exception as exc:\n"
        "    print('DEAD-AS-EXPECTED', type(exc).__name__)\n"
    )
    r0 = subprocess.run(
        [sys.executable, "-", base + "/"],
        input=probe,
        text=True,
        capture_output=True,
        env=_scan_env(base),
        timeout=30,
    )
    assert "DEAD-AS-EXPECTED" in r0.stdout, r0.stdout + r0.stderr

    r = subprocess.run(
        [sys.executable, "-", "/"],
        input=body,
        text=True,
        capture_output=True,
        env=_scan_env(base),
        timeout=60,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert "ok (" in r.stdout and "refs verified" in r.stdout


def test_chunk_scan_heredoc_ref_heads_are_direct_too(mock_site):  # noqa: F811  (fixture param shadows the registered import above)
    """AC2 (ref HEAD half): ref HEAD verdicts also ride the no-proxy opener.

    A page with a 4xx ref must produce the definitive ``->404`` P0 verdict —
    through the dead-proxy environment. A proxied HEAD lane would count the
    ref unverified and degrade instead of reporting the dead ref.
    """
    base, config = mock_site
    config["ref_status"] = {"r0-a1b2c3.js": 404}
    r = subprocess.run(
        [sys.executable, "-", "/"],
        input=_heredoc_source(),
        text=True,
        capture_output=True,
        env=_scan_env(base),
        timeout=60,
    )
    assert r.returncode == 1, r.stdout + r.stderr
    assert "P0 dead refs" in r.stdout
    assert "r0-a1b2c3.js->404" in r.stdout


# ---------------------------------------------------------------------------
# AC3 (wrapper half) — no_proxy/NO_PROXY exported for python invocations
# ---------------------------------------------------------------------------


def test_wrapper_exports_no_proxy_for_python(monitor_env, mock_site, tmp_path):  # noqa: F811  (fixture param shadows the registered import above)
    """AC3/D2: the wrapper pins no_proxy='*' and NO_PROXY='*' into its python
    invocations (defense-in-depth: a future bare urlopen cannot re-couple
    through the env-var proxy class). Captured via an env-dumping probe stub.
    """
    capture = tmp_path / "probe-env.txt"
    probe = tmp_path / "probe_env.py"
    probe.write_text(
        "import os\n"
        f"open({str(capture)!r}, 'w').write(\n"
        "    os.environ.get('no_proxy', '<unset>') + '\\n'\n"
        "    + os.environ.get('NO_PROXY', '<unset>') + '\\n')\n"
        "print('3 check(s) passed')\n",
        encoding="utf-8",
    )
    monitor_env["NFM_MONITOR_PROBE"] = str(probe)

    r = run_script(monitor_env)
    assert r.returncode == 0, r.stdout
    assert capture.exists(), "probe stub never ran"
    no_proxy, no_proxy_upper = capture.read_text(encoding="utf-8").splitlines()
    assert no_proxy == "*"
    assert no_proxy_upper == "*"


# ---------------------------------------------------------------------------
# AC4 — scutil proxy-state observability (startup + degraded cycles)
# ---------------------------------------------------------------------------


def test_startup_proxy_state_line_logged(monitor_env, mock_site):  # noqa: F811  (fixture param shadows the registered import above)
    """AC4/D3: every sentinel run logs a one-line scutil proxy summary at
    startup. On non-macOS hosts scutil is absent and the line reads
    ``scutil-proxy: unknown`` — the line itself must always be there."""
    r = run_script(monitor_env)
    assert r.returncode == 0, r.stdout
    startup = [ln for ln in log_text(monitor_env).splitlines() if "proxy-state" in ln]
    assert startup, "no startup proxy-state line in monitor log"
    assert "scutil-proxy:" in startup[0]


def _scutil_stub(tmp_path, name, body):
    stub = tmp_path / name
    stub.write_text(body, encoding="utf-8")
    stub.chmod(0o755)
    return str(stub)


@pytest.mark.parametrize("mode", ["missing", "silent"])
def test_proxy_state_reads_unknown_when_scutil_fails(monitor_env, mock_site, tmp_path, mode):  # noqa: F811  (fixture param shadows the registered import above)
    """D3 fallback: scutil absent or emitting nothing must read ``unknown`` —
    never a confident ``none``. The runbook adjudicates a mode-A cycle against
    the scutil reading; a mislabeled ``none`` would point the flap at the
    edge on any host where scutil is unavailable."""
    if mode == "silent":
        monitor_env["NFM_MONITOR_SCUTIL"] = _scutil_stub(
            tmp_path, "scutil-silent.sh", "#!/bin/sh\nexit 0\n"
        )
    else:
        monitor_env["NFM_MONITOR_SCUTIL"] = str(tmp_path / "scutil-absent")
    r = run_script(monitor_env)
    assert r.returncode == 0, r.stdout
    startup = next(ln for ln in log_text(monitor_env).splitlines() if "proxy-state" in ln)
    assert "scutil-proxy: unknown" in startup


def test_proxy_state_reports_enabled_proxy_from_scutil(monitor_env, mock_site, tmp_path):  # noqa: F811  (fixture param shadows the registered import above)
    """D3 parse: a scutil report with HTTP enabled must surface the endpoint
    in the one-line summary — the healthy-proxy reading the runbook needs to
    attribute a degraded cycle to the edge."""
    monitor_env["NFM_MONITOR_SCUTIL"] = _scutil_stub(
        tmp_path,
        "scutil-proxy-on.sh",
        "#!/bin/sh\n"
        "cat <<'EOF'\n"
        "<dictionary> {\n"
        "  HTTPEnable : 1\n"
        "  HTTPProxy : 127.0.0.1\n"
        "  HTTPPort : 7892\n"
        "  SOCKSEnable : 0\n"
        "}\n"
        "EOF\n",
    )
    r = run_script(monitor_env)
    assert r.returncode == 0, r.stdout
    startup = next(ln for ln in log_text(monitor_env).splitlines() if "proxy-state" in ln)
    assert "scutil-proxy: http=127.0.0.1:7892" in startup
    assert "unknown" not in startup


def test_degraded_cycle_carries_scutil_next_to_mode_evidence(monitor_env, mock_site):  # noqa: F811  (fixture param shadows the registered import above)
    """AC4/D3: every degraded cycle's evidence token carries the one-line
    scutil summary ADJACENT to mode=/fetches= — same log line, so the next
    flap adjudicates host-egress vs edge in one line."""
    _base, config = mock_site
    _degrade_refs(config, lat=float(REF_TIMEOUT))
    monitor_env["NFM_SCAN_DEADLINE"] = "12"
    r = run_script(monitor_env)
    assert r.returncode == 0, r.stdout
    warn = next(ln for ln in log_text(monitor_env).splitlines() if "chunk-scan DEGRADED" in ln)
    assert "mode=B" in warn
    assert "scutil-proxy:" in warn


def test_mode_a_critical_carries_scutil_evidence(monitor_env, mock_site):  # noqa: F811  (fixture param shadows the registered import above)
    """AC4/D3 on the paging path: the mode-A blind CRITICAL line (the exact
    2026-10-15 02:26Z false-CRITICAL shape) carries the proxy state inline."""
    _base, _config = mock_site
    monitor_env["NFM_SCAN_BASE"] = "http://127.0.0.1:1"  # nothing listens there
    monitor_env["NFM_SCAN_DEADLINE"] = "30"
    for _ in range(2):
        assert run_script(monitor_env).returncode == 0
    r3 = run_script(monitor_env)
    assert r3.returncode == 2, r3.stdout
    crit = [
        ln
        for ln in log_text(monitor_env).splitlines()
        if "CRITICAL — sentinel chunk-scan blind" in ln
    ][-1]
    assert "mode=A" in crit
    assert "scutil-proxy:" in crit


def test_base_lane_critical_carries_scutil_evidence(tmp_path, mock_site):  # noqa: F811  (fixture param shadows the registered import above)
    """AC4/D3 on the base lane: a confirmed base-probe P0 CRITICAL carries
    the proxy state too — a real outage during a healthy-proxy reading is
    edge trouble, not vantage trouble, in one line."""
    probe = tmp_path / "probe_fail.py"
    probe.write_text(
        "print('7 check(s) failed (2 P0 critical)')\n"
        "print('| ❌ Backend API Health | P0 | — | Connection error: poisoned vantage |')\n"
        "import sys; sys.exit(1)\n",
        encoding="utf-8",
    )
    local_env = {
        "NFM_SCAN_BASE": mock_site[0],
        "NFM_SCAN_DEADLINE": "240",
        "NFM_SCAN_REF_POOL": "4",
        "NO_PROXY": "127.0.0.1,localhost",
        "no_proxy": "127.0.0.1,localhost",
        "NFM_MONITOR_LOG": str(tmp_path / "monitor.log"),
        "NFM_MONITOR_STATE": str(tmp_path / "monitor.state"),
        "NFM_MONITOR_PROBE": str(probe),
        "NFM_MONITOR_NOTIFY": "/bin/true",
    }
    run_script(local_env)  # streak 1/2 — WARN
    r2 = run_script(local_env)  # streak 2/2 — CRITICAL
    assert r2.returncode == 2, r2.stdout
    crit = [
        ln for ln in log_text(local_env).splitlines() if "CRITICAL —" in ln and "public-edge" in ln
    ][-1]
    assert "scutil-proxy:" in crit


# ---------------------------------------------------------------------------
# D4 — records correction guards
# ---------------------------------------------------------------------------


def test_adr013_broker_dpi_rulings_untouched():
    """D4: the supersession is site-monitor-scope ONLY. ADR-013's broker/
    GitHub DPI rulings are a different subsystem and must remain intact."""
    adr013 = SCRIPT_DIR.parent / "docs" / "adr" / "ADR-013-NFM-4266-prod-mutation-guardrails.md"
    text = adr013.read_text(encoding="utf-8")
    assert "DPI" in text or "dpi" in text, "ADR-013 DPI record vanished — D4 violation"
