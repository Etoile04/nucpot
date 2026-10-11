"""Tests for scripts/nfm-site-monitor.sh — NFM-5446 chunk-scan fail-open fix.

Context: the NFM-5418 chunk-ref scan inside the sentinel used to run its
5 page GETs and up-to-150 ref HEADs fully SERIALLY under a 240 s SIGALRM.
During diurnal edge-interference windows (NFM-5262, 5-10.4 s per request)
the serial scan cannot fit the deadline, and the alarm handler printed
``0 0 scan-deadline-exceeded (fail-open)`` with rc 0 — the sentinel logged
GREEN while BOTH lanes it exists to watch (P1 stale 1y-pins, P0 dead chunk
refs) went unverified. Observed live 2026-10-11: ~30 consecutive green
probes over 3h46m while four watched pages stayed 1y-pinned (NFM-5446).

These tests pin the NFM-5446 contract so the scan cannot silently regress
back to fail-open:

- the scan is PARALLEL (pages + bounded ref pool) so latency cannot
  serially exhaust the deadline;
- an exhausted budget reports ``scan-degraded`` with rc 3 — never green;
- TTL spot-check (page s-maxage/age from response headers) is captured
  BEFORE the body read, so stale-pin P1s stay visible during degradation;
- a skip streak of >= 3 consecutive degraded scans escalates to a
  sentinel-blind CRITICAL page; a completed scan resets the streak;
- env seams (NFM_SCAN_BASE, NFM_SCAN_DEADLINE, NFM_MONITOR_LOG/STATE/
  PROBE/NOTIFY) keep the suite hermetic — no real network, no real
  LaunchAgent state. Every helper hard-fails if the seams are missing so
  a stripped seam can never fall through to production.
"""

from __future__ import annotations

import os
import subprocess
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "nfm-site-monitor.sh")
SCAN_PAGES = ["/", "/datasets", "/browse", "/potentials", "/materials"]
REF_TIMEOUT = 8  # must mirror the script's per-HEAD socket timeout
SUBPROCESS_TIMEOUT = 40

# ------------------------------------------------------------------ helpers


def _require_seams() -> None:
    """Fail fast (without executing anything) if the env seams are gone.

    Without this guard a seam-stripped script would silently fall back to
    the real site / real ~/.nfm-site-monitor.state inside tests.
    """
    with open(SCRIPT, "r", encoding="utf-8") as fh:
        src = fh.read()
    for seam in ("NFM_SCAN_BASE", "NFM_SCAN_DEADLINE", "NFM_MONITOR_LOG",
                 "NFM_MONITOR_STATE", "NFM_MONITOR_PROBE", "NFM_MONITOR_NOTIFY"):
        if seam not in src:
            pytest.fail("script lost its %s env seam — refusing to run against production" % seam)


def _ref(i: int) -> str:
    return "/_next/static/chunks/r%d-a1b2c3.js" % i


def _page_body(n_refs: int) -> str:
    return "<html><head>" + "".join(
        '<script src="%s"></script>' % _ref(i) for i in range(n_refs)
    ) + "</head><body>ok</body></html>"


class _MockEdge(BaseHTTPRequestHandler):
    """Mock CDN edge: configurable per-page latency + cache-control, and
    per-request latency/status for /_next/static HEADs."""

    def log_message(self, *args):  # silence
        pass

    def _page_spec(self, path):
        return self.server.config["pages"].get(path)

    def do_GET(self):
        cfg = self.server.config
        if self.path.startswith("/_next/static/"):
            self._serve_ref()
            return
        spec = self._page_spec(self.path)
        if spec is None:
            self.send_response(404)
            self.end_headers()
            return
        time.sleep(spec.get("lat", 0))
        body = spec["body"].encode()
        self.send_response(200)
        self.send_header("Cache-Control", spec["cc"])
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_HEAD(self):
        if self.path.startswith("/_next/static/"):
            self._serve_ref()
            return
        spec = self._page_spec(self.path)
        if spec is None:
            self.send_response(404)
            self.end_headers()
            return
        time.sleep(spec.get("lat", 0))
        self.send_response(200)
        self.send_header("Cache-Control", spec["cc"])
        self.end_headers()

    def _serve_ref(self):
        cfg = self.server.config
        time.sleep(cfg.get("ref_lat", 0))
        code = cfg.get("ref_status", {}).get(self.path.rsplit("/", 1)[-1], 200)
        self.send_response(code)
        self.send_header("Content-Length", "0")
        self.end_headers()


@pytest.fixture()
def mock_site(tmp_path):
    """Yield (base_url, config). Default: fast healthy site."""
    config = {
        "pages": {
            p: {"lat": 0.02, "cc": "public, s-maxage=300, stale-while-revalidate",
                "body": _page_body(2)}
            for p in SCAN_PAGES
        },
        "ref_lat": 0.0,
        "ref_status": {},
    }
    class _Srv(ThreadingHTTPServer):
        request_queue_size = 128  # absorb the parallel-scan connection burst

    srv = _Srv(("127.0.0.1", 0), _MockEdge)
    srv.config = config
    import threading
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    yield "http://127.0.0.1:%d" % srv.server_address[1], config
    srv.shutdown()
    srv.server_close()


@pytest.fixture()
def env(tmp_path, mock_site):
    """Full env seam set pointing at the mock edge + isolated state."""
    base, _config = mock_site
    probe = tmp_path / "probe_ok.py"
    probe.write_text('print("3 check(s) passed")\n')
    e = {
        "NFM_SCAN_BASE": base,
        "NFM_SCAN_DEADLINE": "240",
        "NFM_SCAN_REF_POOL": "4",  # gentle burst: sandboxed shells can 502
                                   # under 12-way loopback bursts; production
                                   # (LaunchAgent, direct egress) runs 12
        "NFM_MONITOR_LOG": str(tmp_path / "monitor.log"),
        "NFM_MONITOR_STATE": str(tmp_path / "monitor.state"),
        "NFM_MONITOR_PROBE": str(probe),
        "NFM_MONITOR_NOTIFY": "/bin/true",
    }
    yield e


def run_script(env, *args, timeout=SUBPROCESS_TIMEOUT):
    _require_seams()
    try:
        return subprocess.run(
            ["bash", SCRIPT, *args], env={**os.environ, **env},
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        pytest.fail("script did not finish in %ss (args=%r) — likely hit the "
                    "real network: stdout=%r" % (timeout, args, exc.stdout))


def log_text(env):
    with open(env["NFM_MONITOR_LOG"], "r", encoding="utf-8") as fh:
        return fh.read()


def state_dict(env):
    out = {}
    try:
        with open(env["NFM_MONITOR_STATE"], "r", encoding="utf-8") as fh:
            for line in fh:
                if "=" in line:
                    k, v = line.rstrip("\n").split("=", 1)
                    out[k] = v
    except FileNotFoundError:
        pass
    return out


def _degrade_refs(config, lat, n_refs=30, page="/"):
    """Make the ref-verification stage unable to fit its budget."""
    config["pages"][page] = {
        "lat": 0.02, "cc": "public, s-maxage=300, stale-while-revalidate",
        "body": _page_body(n_refs),
    }
    config["ref_lat"] = lat


# ------------------------------------------------------------- --scan-once


def test_scan_once_green(env, mock_site):
    r = run_script(env, "--scan-once")
    assert r.returncode == 0, r.stdout
    assert "ok (" in r.stdout and "refs verified" in r.stdout


def test_scan_once_reports_stale_pin_p1(env, mock_site):
    _base, config = mock_site
    for p in ["/datasets", "/browse", "/potentials", "/materials"]:
        config["pages"][p]["cc"] = "public, s-maxage=31536000"
    r = run_script(env, "--scan-once")
    assert r.returncode == 1, r.stdout
    assert "P1 stale 1y-pin" in r.stdout
    assert "s-maxage=31536000" in r.stdout
    # all four pinned pages named, / is clean
    for p in ["/datasets", "/browse", "/potentials", "/materials"]:
        assert p in r.stdout


def test_scan_once_reports_dead_ref_p0(env, mock_site):
    _base, config = mock_site
    config["ref_status"] = {"r0-a1b2c3.js": 404}
    r = run_script(env, "--scan-once")
    assert r.returncode == 1, r.stdout
    assert "P0 dead refs" in r.stdout
    assert "r0-a1b2c3.js->404" in r.stdout


def test_scan_once_deadline_is_degraded_not_green(env, mock_site):
    """THE NFM-5446 regression: budget exhaustion must NOT read green."""
    _base, config = mock_site
    _degrade_refs(config, lat=float(REF_TIMEOUT))  # every HEAD takes ~8s
    env["NFM_SCAN_DEADLINE"] = "12"
    r = run_script(env, "--scan-once")
    assert r.returncode == 3, r.stdout
    assert "scan-degraded" in r.stdout
    assert "refs-unverified" in r.stdout


def test_scan_once_partial_verification_is_degraded(env, mock_site):
    """Some refs verified, some skipped => unknown, not green."""
    _base, config = mock_site
    _degrade_refs(config, lat=4.0)
    env["NFM_SCAN_DEADLINE"] = "12"
    r = run_script(env, "--scan-once")
    assert r.returncode == 3, r.stdout
    assert "scan-degraded" in r.stdout


def test_scan_once_ttl_lane_survives_degradation(env, mock_site):
    """Stale pins stay visible even when ref verification blows the budget."""
    _base, config = mock_site
    for p in SCAN_PAGES[1:]:
        config["pages"][p]["cc"] = "public, s-maxage=31536000"
    _degrade_refs(config, lat=float(REF_TIMEOUT))
    env["NFM_SCAN_DEADLINE"] = "12"
    r = run_script(env, "--scan-once")
    assert r.returncode == 3, r.stdout
    assert "scan-degraded" in r.stdout
    assert "P1 stale 1y-pin" in r.stdout  # TTL lane carried the finding out


def test_scan_once_parallel_pages_beat_serial_latency(env, mock_site):
    """5 pages at 2s each complete in << 5x2s: pages run concurrently."""
    _base, config = mock_site
    for p in SCAN_PAGES:
        config["pages"][p]["lat"] = 2.0
    t0 = time.monotonic()
    r = run_script(env, "--scan-once")
    assert r.returncode == 0, r.stdout
    assert time.monotonic() - t0 < 10.0  # serial would be >= 10s pages alone


def test_scan_once_all_pages_down_is_degraded(env, mock_site):
    base, _config = mock_site
    env["NFM_SCAN_BASE"] = base  # same shape, dead port below
    env["NFM_SCAN_BASE"] = "http://127.0.0.1:1"  # nothing listens there
    env["NFM_SCAN_DEADLINE"] = "30"
    r = run_script(env, "--scan-once")
    assert r.returncode == 3, r.stdout
    assert "scan-degraded" in r.stdout
    assert "all page fetches failed" in r.stdout


# ------------------------------------------------- real-probe integration


def test_integration_green_run(env, mock_site):
    r = run_script(env)
    assert r.returncode == 0, r.stdout
    assert "OK — all checks passed" in log_text(env)
    st = state_dict(env)
    assert st["level"] == "OK"
    assert st["scan_skip_streak"] == "0"


def test_integration_degraded_pins_surface_as_warn(env, mock_site):
    """The live masking case: degraded scan + real pins => WARN, not OK."""
    _base, config = mock_site
    for p in SCAN_PAGES[1:]:
        config["pages"][p]["cc"] = "public, s-maxage=31536000"
    _degrade_refs(config, lat=float(REF_TIMEOUT))
    env["NFM_SCAN_DEADLINE"] = "12"
    r = run_script(env)
    assert r.returncode == 1, r.stdout
    lt = log_text(env)
    assert "WARN — chunk-scan DEGRADED" in lt
    assert "P1 stale 1y-pin" in lt
    st = state_dict(env)
    assert st["level"] == "WARN"
    assert st["scan_skip_streak"] == "1"


def test_integration_skip_streak_escalates_critical(env, mock_site):
    """>=3 consecutive degraded scans => sentinel-blind CRITICAL + latch."""
    _base, config = mock_site
    _degrade_refs(config, lat=float(REF_TIMEOUT))
    env["NFM_SCAN_DEADLINE"] = "12"
    r1 = run_script(env)
    r2 = run_script(env)
    assert r1.returncode == 0 and r2.returncode == 0, (r1.stdout, r2.stdout)
    assert state_dict(env)["scan_skip_streak"] == "2"
    r3 = run_script(env)
    assert r3.returncode == 2, r3.stdout
    lt = log_text(env)
    assert "CRITICAL — sentinel chunk-scan blind" in lt
    assert "NFM-5446" in lt
    st = state_dict(env)
    assert st["level"] == "CRITICAL"
    assert st["scan_skip_streak"] == "3"
    assert st["pending_page"] not in ("-", "")


def test_integration_skip_streak_resets_on_completed_scan(env, mock_site):
    _base, config = mock_site
    degrade = dict(lat=float(REF_TIMEOUT))
    # two degraded cycles
    _degrade_refs(config, **degrade)
    env["NFM_SCAN_DEADLINE"] = "12"
    assert run_script(env).returncode == 0
    assert run_script(env).returncode == 0
    assert state_dict(env)["scan_skip_streak"] == "2"
    # a completing (green) scan resets the streak...
    config["ref_lat"] = 0.0
    config["pages"]["/"] = {
        "lat": 0.02, "cc": "public, s-maxage=300", "body": _page_body(2)}
    env["NFM_SCAN_DEADLINE"] = "240"
    assert run_script(env).returncode == 0
    assert state_dict(env)["scan_skip_streak"] == "0"
    # ...so two more degraded cycles stay sub-critical
    _degrade_refs(config, **degrade)
    env["NFM_SCAN_DEADLINE"] = "12"
    assert run_script(env).returncode == 0
    assert run_script(env).returncode == 0
    st = state_dict(env)
    assert st["scan_skip_streak"] == "2"
    assert st["level"] != "CRITICAL"
    assert "CRITICAL" not in log_text(env)
