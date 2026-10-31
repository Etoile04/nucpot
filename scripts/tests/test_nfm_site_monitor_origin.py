"""Tests for scripts/nfm-site-monitor.sh — NFM-5797 local-origin discriminator probes.

Context (NFM-5797; QA request dbda0458 on NFM-5795): NFM-5795 exonerated the
origin for /browse via MANUAL fresh-conn probes (localhost:3000 => 3ms) and QA
adopted the classification default "refs-clean + in-tick API-green = path-side"
— but the discriminator was offered, never built, so every future refs-clean
P1 single needed a round-trip courier to classify (the NFM-5797 Homepage
series, x3, was exactly that courier). The sentinel now probes the ORIGIN
itself — two fresh-connection GETs per tick to ``/`` and ``/browse`` on
``http://localhost:3000`` (the tunnel's originService; ``/`` included per the
NFM-5797 ask, NFM-5795 covered /browse only) — and logs one ``ORIGIN —``
evidence line per tick BEFORE the summary line.

Pinned contracts:

- evidence-ONLY: the lane never feeds alarms, streaks, classification,
  state, or paging; ``--origin-probes-once`` touches no log and no state;
- a failed route is an ERR-tagged EVIDENCE token and rc stays 0 (conn-refused
  / timeout / curl-rc classes) — the origin being down is not a sentinel
  error, the base probe owns availability;
- reading rule: a single-digit-ms origin token under a SITE_SIDE P1 WARN
  classifies path-side IN-TICK (NFM-5795 verdict, self-served); hundreds of
  ms+ or a non-200 / ERR tag is the courier-resume signal QA adopted;
- the OK/WARN/CRITICAL summary line stays the tick's newest line (the
  newest-line liveness read is preserved; the per-tick startup line already
  establishes the multi-line-tick class) and green OK lines stay byte-stable
  (no origin token inside them — NFM-5708 convention);
- DIRECT-pinned (ADR-023 lineage): hostile proxy env must not influence the
  lane (curl --noproxy '*', absolute binary path per NFM-4579).
"""

from __future__ import annotations

import re
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from test_nfm_site_monitor import (
    env,  # noqa: F401  (pytest fixture, re-exported)
    log_text,
    mock_site,  # noqa: F401  (pytest fixture, re-exported)
    run_script,
)

TOKEN_200_RE = re.compile(r"origin: /=200/\d+ms/\d+B /browse=200/\d+ms/\d+B\b")


# ------------------------------------------------------- --origin-probes-once


def test_origin_probes_once_happy_path(env, mock_site):
    """Both routes answer 200 with integer-ms latency and byte counts."""
    base, _config = mock_site
    env["NFM_ORIGIN_BASE"] = base
    r = run_script(env, "--origin-probes-once")
    assert r.returncode == 0, r.stdout
    assert TOKEN_200_RE.search(r.stdout), r.stdout


def test_origin_probes_once_conn_refused_is_evidence(env, mock_site):
    """A dead origin is ERR-tagged evidence, rc stays 0 — availability is the
    base probe's job, not this lane's."""
    base, _config = mock_site
    env["NFM_ORIGIN_BASE"] = base
    # port 9 (discard): nothing may listen on loopback :9 in the sandbox
    env["NFM_ORIGIN_BASE"] = "http://127.0.0.1:9"
    r = run_script(env, "--origin-probes-once")
    assert r.returncode == 0, r.stdout
    assert "origin: /=ERR(conn-refused) /browse=ERR(conn-refused)" in r.stdout


def test_origin_timeout_is_evidence(env, mock_site):
    """A route over the socket budget reads ERR(timeout) while the other
    route still measures — per-route isolation."""
    base, config = mock_site
    config["pages"]["/"] = {**config["pages"]["/"], "lat": 2.0}
    env["NFM_ORIGIN_BASE"] = base
    env["NFM_ORIGIN_TIMEOUT"] = "1"
    r = run_script(env, "--origin-probes-once")
    assert r.returncode == 0, r.stdout
    assert "origin: /=ERR(timeout)" in r.stdout, r.stdout
    assert "/browse=200/" in r.stdout, r.stdout


def test_origin_non_200_prints_status(env):
    """A non-200 origin answer is printed verbatim as the route's code —
    the origin-side signal the courier-resume rule reads."""

    class _H(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(503)
            self.send_header("Content-Length", "7")
            self.end_headers()
            self.wfile.write(b"origin\n")

        def log_message(self, *args):
            pass

    srv = HTTPServer(("127.0.0.1", 0), _H)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        env["NFM_ORIGIN_BASE"] = f"http://127.0.0.1:{srv.server_port}"
        r = run_script(env, "--origin-probes-once")
        assert r.returncode == 0, r.stdout
        assert "origin: /=503/" in r.stdout, r.stdout
        assert "/browse=503/" in r.stdout, r.stdout
    finally:
        srv.shutdown()
        srv.server_close()


def test_origin_lane_immune_to_proxy_env(env, mock_site):
    """ADR-023 lineage: hostile env proxies must not influence the lane —
    curl --noproxy '*' + absolute binary path (NFM-4579)."""
    base, _config = mock_site
    for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
        env[k] = "http://127.0.0.1:1"  # nothing listens there
    env["NFM_ORIGIN_BASE"] = base
    r = run_script(env, "--origin-probes-once")
    assert r.returncode == 0, r.stdout
    assert TOKEN_200_RE.search(r.stdout), r.stdout


# ------------------------------------------------------------- tick emission


def test_green_tick_logs_origin_line_before_ok(env, mock_site):
    """Every real-probe tick logs the ORIGIN line; the OK summary line stays
    the tick's newest line (liveness read) and stays byte-stable (no origin
    token inside it)."""
    base, _config = mock_site
    env["NFM_ORIGIN_BASE"] = base
    assert run_script(env).returncode == 0

    lines = log_text(env).strip().splitlines()
    assert lines, "log must not be empty"
    origin_idx = [i for i, ln in enumerate(lines) if "ORIGIN — origin: " in ln]
    ok_idx = [i for i, ln in enumerate(lines) if "OK — all checks passed" in ln]
    assert origin_idx, "ORIGIN evidence line missing on a green tick"
    assert ok_idx, "OK summary line missing"
    # summary line is the tick's newest line; ORIGIN precedes it
    assert origin_idx[-1] < ok_idx[-1]
    assert "origin:" not in lines[ok_idx[-1]], "OK line must stay byte-stable"
    assert lines[-1] == lines[ok_idx[-1]]


def test_degrade_tick_logs_origin_adjacent_to_warn(env, mock_site):
    """On a P1-only degrade tick the ORIGIN line rides the same tick —
    in-tick classification of refs-clean singles is the point (NFM-5797)."""
    base, _config = mock_site
    env["NFM_ORIGIN_BASE"] = base
    probe = env["NFM_MONITOR_PROBE"] + ".p1fail"
    with open(probe, "w", encoding="utf-8") as fh:
        fh.write("print('| ❌ Frontend Homepage | P1 | 6000ms | over budget |')\n")
        fh.write("print('1 check(s) failed (0 P0 critical)')\n")
        fh.write("raise SystemExit(1)\n")
    env["NFM_MONITOR_PROBE"] = probe

    r = run_script(env)
    assert r.returncode == 1, (r.returncode, r.stdout)

    lines = log_text(env).strip().splitlines()
    origin_idx = [i for i, ln in enumerate(lines) if "ORIGIN — origin: " in ln]
    warn_idx = [i for i, ln in enumerate(lines) if "WARN — P1-only failures" in ln]
    assert origin_idx and warn_idx
    assert origin_idx[-1] < warn_idx[-1]
    assert "origin: /=200/" in lines[origin_idx[-1]]
