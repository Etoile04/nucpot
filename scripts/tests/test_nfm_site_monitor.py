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
- NFM-5454 flake threshold: refs-unverified degrades only at >= 2 — a
  single errored ref reads green with a NOTE (drift watch), while
  over-budget truncation (refs-truncated) still degrades at ANY count
  and definitive 4xx dead refs stay immediate P0;
- NFM-5490 latency hardening: a transport-failed ref HEAD (SSL
  UNEXPECTED_EOF / connection reset / tail-latency timeout) gets ONE
  budget-gated retry before counting as unverified, and REF_TIMEOUT
  covers the observed 5-10.4 s diurnal-edge tail. A persistent transport
  failure STILL fails closed — the retry hardens verification, it never
  manufactures a false green (4xx stays immediate P0, 5xx stays unknown).
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
import test_nfm_site_monitor as this_module

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "nfm-site-monitor.sh")
SCAN_PAGES = ["/", "/datasets", "/browse", "/potentials", "/materials"]
REF_TIMEOUT = 15  # must mirror the script's per-HEAD socket timeout (NFM-5490: 8→15, diurnal tail)
SUBPROCESS_TIMEOUT = 40

# ------------------------------------------------------------------ helpers


def _require_seams() -> None:
    """Fail fast (without executing anything) if the env seams are gone.

    Without this guard a seam-stripped script would silently fall back to
    the real site / real ~/.nfm-site-monitor.state inside tests. The check
    matches the READ expressions (``${SEAM:-…}`` in bash,
    ``os.environ.get("SEAM"`` in python), not bare name presence — a
    comment name-dropping a seam must not pass (gate review: the bare
    source-grep was too weak to prove the seam is actually consumed).
    """
    with open(SCRIPT, encoding="utf-8") as fh:
        src = fh.read()
    for seam in ("NFM_MONITOR_LOG", "NFM_MONITOR_STATE", "NFM_MONITOR_PROBE", "NFM_MONITOR_NOTIFY"):
        if seam + ":-" not in src:
            pytest.fail(f"script lost its {seam} env seam — refusing to run against production")
    for seam in ("NFM_SCAN_BASE", "NFM_SCAN_DEADLINE", "NFM_SCAN_REF_POOL"):
        if f'os.environ.get("{seam}"' not in src:
            pytest.fail(f"script lost its {seam} env seam — refusing to run against production")


def _ref(i: int) -> str:
    return f"/_next/static/chunks/r{i}-a1b2c3.js"


def _page_body(n_refs: int) -> str:
    return (
        "<html><head>"
        + "".join(f'<script src="{_ref(i)}"></script>' for i in range(n_refs))
        + "</head><body>ok</body></html>"
    )


class _MockEdge(BaseHTTPRequestHandler):
    """Mock CDN edge: configurable per-page latency + cache-control, and
    per-request latency/status for /_next/static HEADs."""

    def log_message(self, *args):  # silence
        pass

    def _page_spec(self, path):
        return self.server.config["pages"].get(path)

    def do_GET(self):
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
        name = self.path.rsplit("/", 1)[-1]
        flaky = cfg.get("ref_flaky", {})
        remaining = flaky.get(name, 0)
        if remaining > 0:
            # NFM-5490: drop the connection WITHOUT a response — the client
            # sees a transport error (RemoteDisconnected), the ep-4 class.
            # Per-name countdown is deterministic under pool concurrency:
            # a ref's attempt-then-retry is sequential within one worker.
            flaky[name] = remaining - 1
            self.close_connection = True
            return
        time.sleep(cfg.get("ref_lat", 0))
        code = cfg.get("ref_status", {}).get(name, 200)
        self.send_response(code)
        self.send_header("Content-Length", "0")
        self.end_headers()


@pytest.fixture()
def mock_site(tmp_path):
    """Yield (base_url, config). Default: fast healthy site."""
    config = {
        "pages": {
            p: {
                "lat": 0.02,
                "cc": "public, s-maxage=300, stale-while-revalidate",
                "body": _page_body(2),
            }
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
    yield f"http://127.0.0.1:{srv.server_address[1]}", config
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
        # (LaunchAgent, direct egress) runs 24
        # NFM-5490: the production sentinel egresses DIRECTLY (no proxy,
        # NFM-5446-era vantage fix) — a sandbox http_proxy would rewrite
        # mock-edge connection drops into 502 HTTPError *verdicts*, hiding
        # the transport-exception path the retry exists for. Bypass it.
        "NO_PROXY": "127.0.0.1,localhost",
        "no_proxy": "127.0.0.1,localhost",
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
            ["bash", SCRIPT, *args],
            env={**os.environ, **env},
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        pytest.fail(
            f"script did not finish in {timeout}s (args={args!r}) — likely hit the "
            f"real network: stdout={exc.stdout!r}"
        )


def log_text(env):
    with open(env["NFM_MONITOR_LOG"], encoding="utf-8") as fh:
        return fh.read()


def state_dict(env):
    out = {}
    try:
        with open(env["NFM_MONITOR_STATE"], encoding="utf-8") as fh:
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
        "lat": 0.02,
        "cc": "public, s-maxage=300, stale-while-revalidate",
        "body": _page_body(n_refs),
    }
    config["ref_lat"] = lat


# ------------------------------------------------------------- --scan-once


# ------------------------------------------------------ seam-guard unit tests


def _write_fake_script(tmp_path, body):
    p = tmp_path / "fake-monitor.sh"
    p.write_text(body, encoding="utf-8")
    return str(p)


def test_seam_guard_rejects_comment_only_mentions(tmp_path, monkeypatch):
    """A script that only name-drops the seams in a comment must be refused.

    Guards the guard: the check must match the READ expressions
    (``${SEAM:-…}`` / ``os.environ.get("SEAM"``), not bare name presence —
    otherwise a refactor that drops the actual reads keeps passing and tests
    silently fall through to production state (gate finding: source-grep
    too weak).
    """
    monkeypatch.setattr(
        this_module,
        "SCRIPT",
        _write_fake_script(
            tmp_path,
            (
                "#!/usr/bin/env bash\n"
                "# mentions NFM_SCAN_BASE NFM_SCAN_DEADLINE NFM_MONITOR_LOG\n"
                "# NFM_MONITOR_STATE NFM_MONITOR_PROBE NFM_MONITOR_NOTIFY\n"
                "# NFM_SCAN_REF_POOL in a comment only\n"
                "echo prod path\n"
            ),
        ),
    )
    with pytest.raises(pytest.fail.Exception, match="env seam"):
        this_module._require_seams()


def test_seam_guard_accepts_real_reads(tmp_path, monkeypatch):
    monkeypatch.setattr(
        this_module,
        "SCRIPT",
        _write_fake_script(
            tmp_path,
            (
                '#!/usr/bin/env bash\nLOG="${NFM_MONITOR_LOG:-x}"\n'
                'STATE="${NFM_MONITOR_STATE:-x}"\nPROBE="${NFM_MONITOR_PROBE:-x}"\n'
                'NOTIFY="${NFM_MONITOR_NOTIFY:-x}"\n'
                "python3 - <<EOF\nimport os\n"
                'os.environ.get("NFM_SCAN_BASE")\nos.environ.get("NFM_SCAN_DEADLINE")\n'
                'os.environ.get("NFM_SCAN_REF_POOL")\nEOF\n'
            ),
        ),
    )
    this_module._require_seams()  # must not raise


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
    # NFM-5490: deadline must stay just above REF_TIMEOUT (15) so the first
    # ref wave runs and later waves trip the budget mid-scan — at 12 the
    # entry reservation gate would skip everything before any HEAD.
    env["NFM_SCAN_DEADLINE"] = "17"
    r = run_script(env, "--scan-once")
    assert r.returncode == 3, r.stdout
    assert "scan-degraded" in r.stdout


def test_scan_once_ref_cap_overflow_is_degraded(env, mock_site):
    """Refs dropped by the per-page REF_CAP truncation are unverified.

    A page carrying more distinct chunk refs than the per-page cap must not
    read green with only the first N verified (gate review: silent partial
    coverage); the dropped tail counts as refs-truncated => rc 3.
    REF_CAP is 45: overflow needs >45 distinct refs on one page.
    NFM-5454: truncation is its OWN counter and degrades at ANY count —
    the cap-outgrown (runbook §7) signal must stay fail-closed even though
    a single ERRORED ref no longer degrades.
    """
    _base, config = mock_site
    config["pages"]["/"]["body"] = _page_body(50)
    r = run_script(env, "--scan-once")
    assert r.returncode == 3, r.stdout
    assert "scan-degraded" in r.stdout
    assert "refs-truncated=5" in r.stdout


def test_scan_once_single_truncated_ref_still_degrades(env, mock_site):
    """A 46-ref page truncates exactly ONE ref — and still must not read
    green (NFM-5454): truncation fail-closed at count=1 is what keeps the
    cap-outgrown drift detectable once single-ref ERRORS are below
    threshold.
    """
    _base, config = mock_site
    config["pages"]["/"]["body"] = _page_body(46)
    r = run_script(env, "--scan-once")
    assert r.returncode == 3, r.stdout
    assert "refs-truncated=1" in r.stdout


def test_scan_once_single_ref_flake_below_threshold_is_green_with_note(env, mock_site):
    """NFM-5454: ONE errored ref (5xx) is diurnal flake, not degradation.

    Live data 2026-10-11: all 9 noise-grade degraded scans had
    refs-unverified=1 and the 17:48Z sentinel-blind CRITICAL fired on
    three =1 probes (~2 pages/day). A lone error now reads rc 0 with an
    explicit note so heartbeat log-reads keep drift visibility.
    """
    _base, config = mock_site
    # r2 exists ONLY on "/" (default pages carry r0/r1) => exactly 1 skip
    config["pages"]["/"]["body"] = _page_body(3)
    config["ref_status"] = {"r2-a1b2c3.js": 500}
    r = run_script(env, "--scan-once")
    assert r.returncode == 0, r.stdout
    assert "ok (" in r.stdout
    assert "below NFM-5454 degrade threshold 2" in r.stdout


def test_scan_once_two_ref_flakes_still_degrade(env, mock_site):
    """NFM-5454 threshold is 2, not 'any': >=2 errored refs still degrade
    immediately (multi-ref failure = real edge trouble, e.g. the =9 and
    =12 bursts observed 2026-10-11).
    """
    _base, config = mock_site
    # r2/r3 exist ONLY on "/" (default pages carry r0/r1) => exactly 2 skips
    config["pages"]["/"]["body"] = _page_body(4)
    config["ref_status"] = {"r2-a1b2c3.js": 500, "r3-a1b2c3.js": 500}
    r = run_script(env, "--scan-once")
    assert r.returncode == 3, r.stdout
    assert "refs-unverified=2" in r.stdout


def test_scan_once_transient_transport_error_retries_green(env, mock_site):
    """NFM-5490: a dropped-connection HEAD is retried once, not counted
    unverified on the first transport failure.

    Live ep-4 evidence (2026-10-12T10:47:40Z): mid-blindness the sentinel
    sat at refs-unverified=38 while patient-client HEADs from the same
    vantage returned 40/40 HTTP 200 — the refs were verifiable and the
    8 s no-retry urllib client simply gave up. Every ref dropping its
    first attempt and succeeding on the retry must read GREEN.
    """
    _base, config = mock_site
    # per-name countdown: the first r0 request and first r1 request drop
    # (2 transport failures across the scan); retries then find 200s
    config["ref_flaky"] = {"r0-a1b2c3.js": 1, "r1-a1b2c3.js": 1}
    r = run_script(env, "--scan-once")
    assert r.returncode == 0, r.stdout
    assert "ok (" in r.stdout
    assert "refs-unverified" not in r.stdout


def test_scan_once_persistent_transport_error_still_degrades(env, mock_site):
    """NFM-5490 retry is bounded to ONE re-attempt: refs that keep failing
    at transport level stay unverified — fail-closed preserved. The retry
    hardens verification against transient SSL EOF/reset; it must never
    manufacture a false green out of a genuinely unreachable edge.
    """
    _base, config = mock_site
    config["pages"]["/"]["body"] = _page_body(4)
    # r0/r1 exist on all 5 pages, r2/r3 only on "/" => 2*5 + 2 = 12 skips
    config["ref_flaky"] = {f"r{i}-a1b2c3.js": 99 for i in range(4)}
    r = run_script(env, "--scan-once")
    assert r.returncode == 3, r.stdout
    assert "scan-degraded" in r.stdout
    assert "refs-unverified=12" in r.stdout


def test_scan_once_materials_sized_page_verifies_green(env, mock_site):
    """A 40-ref page (live /materials, 2026-10-11) verifies fully under
    REF_CAP=45 and reads green.

    Live-verified regression: at REF_CAP=30 this exact page shape dropped 10
    refs on EVERY scan, so the skip streak could never reset and the
    sentinel-blind CRITICAL latched permanently (deterministic overflow, not
    an edge-latency window — the edge was calm when measured). The cap must
    sit above the real per-page ref count or the sentinel pages forever.
    """
    _base, config = mock_site
    config["pages"]["/"]["body"] = _page_body(40)
    r = run_script(env, "--scan-once")
    assert r.returncode == 0, r.stdout
    assert "ok (" in r.stdout
    assert "refs-unverified" not in r.stdout


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


def test_scan_once_alarm_backstop_preserves_ttl_findings(env, mock_site):
    """SIGALRM itself (not the budget path) must be fail-closed AND keep
    findings already captured. One slow page stalls the page-stage wait past
    the alarm; the four fast pages' 1y-pins are already in `stale` (captured
    from headers before the body read). The handler's output is identified by
    "deadline-exceeded", which the budget path never prints."""
    _base, config = mock_site
    for p in ["/datasets", "/browse", "/potentials", "/materials"]:
        config["pages"][p]["cc"] = "public, s-maxage=31536000"
    config["pages"]["/"]["lat"] = 3.0  # > DEADLINE: alarm fires mid-page-stage
    env["NFM_SCAN_DEADLINE"] = "2"
    r = run_script(env, "--scan-once")
    assert r.returncode == 3, r.stdout
    assert "deadline-exceeded" in r.stdout  # the SIGALRM backstop ran
    assert "P1 stale 1y-pin" in r.stdout  # captured findings survived
    assert "s-maxage=31536000" in r.stdout


# ------------------------------------------------- real-probe integration


def test_integration_green_run(env, mock_site):
    r = run_script(env)
    assert r.returncode == 0, r.stdout
    assert "OK — all checks passed" in log_text(env)
    st = state_dict(env)
    assert st["level"] == "OK"
    assert st["scan_skip_streak"] == "0"


def test_integration_single_ref_flake_logs_note_not_warn(env, mock_site):
    """NFM-5454 drift-watch lane: one flaked ref on an otherwise green run
    logs a NOTE line (heartbeat reads it) — no WARN, no streak movement.
    """
    _base, config = mock_site
    # r2 exists ONLY on "/" (default pages carry r0/r1) => exactly 1 skip
    config["pages"]["/"]["body"] = _page_body(3)
    config["ref_status"] = {"r2-a1b2c3.js": 500}
    r = run_script(env)
    assert r.returncode == 0, r.stdout
    lt = log_text(env)
    assert "NOTE — chunk-scan ok with 1 ref unverified" in lt
    assert "chunk-scan DEGRADED" not in lt
    st = state_dict(env)
    assert st["scan_skip_streak"] == "0"
    assert st["level"] == "OK"


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
    config["pages"]["/"] = {"lat": 0.02, "cc": "public, s-maxage=300", "body": _page_body(2)}
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
