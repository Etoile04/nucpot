"""Tests for scripts/nfm-site-monitor.sh — NFM-5875 edge cache-state dimension.

Parent NFM-5870 (ADR-026 AC5), NATIVE-YES branch per the NFM-5873 gate
verdict: the response rule dce2cc9a (ruleset 32f1d9a4) is live with
CF-internal max-age=1800 + SWR + stale-if-error=604800 (all
cloudflare_only) on the 5 treated HTML routes, and the client-visible
cache-control byte contract is unchanged (s-maxage=1800,
stale-while-revalidate=31534200 — NFM-5863/NFM-5873 proof (a)). The Worker
sibling closed superseded, so the dimension consumes CF-NATIVE evidence:
cf-cache-status vocabulary + age + the cache-control bytes.

What is pinned here:
  - Every real-probe tick (green + degrade alike) carries a CACHE-STATE
    line between LANES and REPLICA; the OK/WARN/CRITICAL line stays the
    tick's newest line (liveness contract) and stays byte-stable on green.
  - Per-route freshness classification from CF-native headers (fresh /
    stale / dynamic / error) + tick context counts (shield / gap / drift)
    + a one-word batclass.
  - New verdict ORIGIN_OUTAGE_STALE_SERVED — origin + edge-tunnel dark
    while the cached edge PROVABLY serves treated routes (shield >= 1).
    Previously MIXED. Fail-open: without shield evidence the tick stays
    MIXED. Page discipline mirrors NFM-5876 PD (NFM-5666 discipline):
    WARN streak 1, page streak 2.
  - No regression: FULL_EDGE_OUTAGE stays on the existing path byte-for-
    byte (cached dark => the OOS branch cannot fire); the generic streak-1
    WARN text is unchanged for every non-PD/non-OOS verdict.

The cache-state probe gets its OWN fixture edge (full header control:
status / cf-cache-status / age / cache-control per route) on the
NFM_CACHE_STATE_BASE seam — independent from the shared mock_site, which
does not emit cf-cache-status/age.
"""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

import test_nfm_site_monitor as base  # noqa: F401 — registers shared fixtures
from test_nfm_site_monitor import (  # noqa: F401 — env re-exported as pytest fixture
    env,
    log_text,
    mock_site,
    run_script,
    state_dict,
)

SCRIPT = Path(__file__).resolve().parent.parent / "nfm-site-monitor.sh"

CACHED_LANES = ("Frontend Homepage", "Frontend Browse Page")
TUNNEL_LANE = "Backend API Health"
OTHER_LANES = ("Backend API Potentials", "Backend API Reference Values")
VERIFY_LANE = "AutoVC API Health"
ALL_NAMES = (*CACHED_LANES, TUNNEL_LANE, *OTHER_LANES, VERIFY_LANE, "Supabase Health")

ROUTES = ("/", "/datasets", "/browse", "/potentials", "/materials")
CONTRACT_CC = "s-maxage=1800, stale-while-revalidate=31534200"

_counter = iter(range(10000))


# ------------------------------------------------------------------ fixtures


class _CacheEdge(BaseHTTPRequestHandler):
    """Per-route fixture edge: (status, ccs, age, cache-control) spec map."""

    def log_message(self, *args):  # silence
        pass

    def do_GET(self):
        spec = self.server.specs.get(self.path)
        if spec is None:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        status, ccs, age, cc = spec
        self.send_response(status)
        if ccs:
            self.send_header("CF-Cache-Status", ccs)
        if age is not None:
            self.send_header("Age", str(age))
        self.send_header("Cache-Control", cc)
        self.send_header("Content-Length", "5")
        self.end_headers()
        self.wfile.write(b"probe")


@pytest.fixture()
def cache_edge():
    """Yield (base_url, specs-dict). Tests mutate specs between runs."""
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _CacheEdge)
    srv.specs = {}
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", srv.specs
    srv.shutdown()
    srv.server_close()


def _all_fresh_contract():
    return {r: (200, "HIT", 100 + i, CONTRACT_CC) for i, r in enumerate(ROUTES)}


def _all_stale_contract(age=5400):
    return {r: (200, "STALE", age, CONTRACT_CC) for r in ROUTES}


def _all_erroring():
    return {
        r: (502, None, None, "private, max-age=0, no-store, no-cache") for r in ROUTES
    }


@pytest.fixture(autouse=True)
def _isolate_s1_state(env):
    """Isolate the s1 episode/ledger state per test (production-file guard;
    same hard-won reason as test_nfm_site_monitor_three_lane)."""
    env["NFM_MONITOR_S1_STATE"] = str(Path(env["NFM_MONITOR_LOG"]).parent / "monitor.s1.state")


def _require_cache_seams() -> None:
    src = SCRIPT.read_text(encoding="utf-8")
    for marker in (
        "cache_state_probes",
        "cache_state_classify",
        "CACHE-STATE — ",
        "ORIGIN_OUTAGE_STALE_SERVED",
        "CS_SHIELD",
        "NFM_CACHE_STATE_BASE",
    ):
        if marker not in src:
            pytest.fail(f"script lost its NFM-5875 seam ({marker!r}) — refusing to run")


def _probe(tmp_path, report, rc=1):
    p = Path(tmp_path) / f"probe_{next(_counter)}.py"
    p.write_text(
        "import sys\nsys.stdout.write(%r)\nsys.exit(%d)\n" % (report, rc),
        encoding="utf-8",
    )
    return str(p)


def _report(failed_rows):
    failed = {name: (sev, ms, detail) for name, sev, ms, detail in failed_rows}
    p0 = sum(1 for r in failed_rows if r[1] == "P0")
    lines = [
        "Starting NucPot health checks...",
        f"Targets: {len(ALL_NAMES)}",
        "",
        "## NucPot Health Check Report",
        "",
        f"**{len(failed_rows)}/{len(ALL_NAMES)} checks FAILED** ❌",
        "",
        "| Target | Status | Response | Details |",
        "|--------|--------|----------|---------|",
    ]
    for name in ALL_NAMES:
        if name in failed:
            sev, ms, detail = failed[name]
            lines.append(f"| ❌ {name} | {sev} | {ms} | {detail} |")
        else:
            sev = "P2" if name == "Supabase Health" else ("P0" if name in (TUNNEL_LANE, VERIFY_LANE) else "P1")
            lines.append(f"| ✅ {name} | {sev} | 12ms | OK |")
    lines += ["", f"⚠️  {len(failed_rows)} check(s) failed ({p0} P0 critical)"]
    return "\n".join(lines)


def _cache_lines(env):
    return [ln for ln in log_text(env).splitlines() if "CACHE-STATE — " in ln]


def _lanes_lines(env):
    return [ln for ln in log_text(env).splitlines() if "LANES — " in ln]


def _origin_green(env, mock_site):
    env["NFM_ORIGIN_BASE"] = mock_site[0]


def _origin_dark(env):
    env["NFM_ORIGIN_BASE"] = "http://127.0.0.1:9"  # nothing listens on :9


def _oos_rows():
    """OOS probe shape: origin DARK (seam) + tunnel fail + cached green."""
    return [
        (TUNNEL_LANE, "P0", "234ms", "HTTP 502: Bad Gateway"),
        (OTHER_LANES[0], "P1", "230ms", "HTTP 502: Bad Gateway"),
    ]


def _fulledge_rows():
    return [
        (CACHED_LANES[0], "P1", "120ms", "HTTP 502: Bad Gateway"),
        (CACHED_LANES[1], "P1", "118ms", "HTTP 502: Bad Gateway"),
        (TUNNEL_LANE, "P0", "234ms", "HTTP 502: Bad Gateway"),
        (OTHER_LANES[0], "P1", "125ms", "HTTP 502: Bad Gateway"),
    ]


# ------------------------------------------------------------------- AC5


def test_require_cache_seams_present(env):
    _require_cache_seams()


def test_green_tick_carries_cache_state_line_fresh(env, mock_site, cache_edge):
    """AC5: a green tick carries a CACHE-STATE line with per-route tokens,
    batclass=fresh; the OK line stays the newest and byte-stable; the
    CACHE-STATE line sits between LANES and REPLICA."""
    base_url, specs = cache_edge
    specs.update(_all_fresh_contract())
    _origin_green(env, mock_site)
    env["NFM_CACHE_STATE_BASE"] = base_url
    env["NFM_MONITOR_PROBE"] = _probe(Path(env["NFM_MONITOR_LOG"]).parent, _report([]), rc=0)

    r = run_script(env)
    assert r.returncode == 0, (r.returncode, log_text(env))

    lines = log_text(env).strip().splitlines()
    cs = [ln for ln in lines if "CACHE-STATE — " in ln]
    assert cs, "CACHE-STATE line missing on a green tick"
    assert "batclass=fresh" in cs[-1]
    assert "fresh=5" in cs[-1] and "stale=0" in cs[-1] and "shield=0" in cs[-1] and "gap=0" in cs[-1] and "drift=0" in cs[-1]
    assert "/=fresh(200,ccs=hit,a=" in cs[-1]
    lanes_idx = next(i for i, ln in enumerate(lines) if "LANES — " in ln)
    rep_idx = next(i for i, ln in enumerate(lines) if "REPLICA — " in ln)
    cs_idx = lines.index(cs[-1])
    assert lanes_idx < cs_idx < rep_idx, "CACHE-STATE must sit between LANES and REPLICA"
    ok_idx = [i for i, ln in enumerate(lines) if ln.endswith("OK — all checks passed")]
    assert lines[-1] == lines[ok_idx[-1]], "OK line must stay the tick's newest line"
    assert "CACHE-STATE" not in lines[ok_idx[-1]], "green OK line must stay byte-stable"
    assert "verdict=ALL_OK" in lines[lanes_idx]


def test_stale_while_origin_ok_batclass_stale(env, mock_site, cache_edge):
    """Serve-stale observed while the origin lane is green: per-route stale
    class from cf-cache-status/age, batclass=stale, shield=0."""
    base_url, specs = cache_edge
    specs.update(_all_stale_contract())
    _origin_green(env, mock_site)
    env["NFM_CACHE_STATE_BASE"] = base_url
    env["NFM_MONITOR_PROBE"] = _probe(Path(env["NFM_MONITOR_LOG"]).parent, _report([]), rc=0)

    r = run_script(env)
    assert r.returncode == 0, (r.returncode, log_text(env))

    cs = _cache_lines(env)[-1]
    assert "batclass=stale" in cs
    assert "stale=5" in cs and "shield=0" in cs and "gap=0" in cs
    assert "=stale(200,ccs=stale,a=5400)" in cs


def test_drift_flag_counts_contract_delta(env, mock_site, cache_edge):
    """cache-control bytes off the NFM-5873 contract on 200s => drift
    counted, batclass=drift (revert-trigger detection)."""
    base_url, specs = cache_edge
    specs.update({r: (200, "HIT", 50, "public, max-age=300") for r in ROUTES})
    _origin_green(env, mock_site)
    env["NFM_CACHE_STATE_BASE"] = base_url
    env["NFM_MONITOR_PROBE"] = _probe(Path(env["NFM_MONITOR_LOG"]).parent, _report([]), rc=0)

    r = run_script(env)
    assert r.returncode == 0, (r.returncode, log_text(env))

    cs = _cache_lines(env)[-1]
    assert "drift=5" in cs
    assert "batclass=drift" in cs
    assert cs.count("!") == 5


def test_error_pages_do_not_count_drift(env, mock_site, cache_edge):
    """Error pages legitimately carry no-store error cache-control — they
    must not flag contract drift (else every outage tick false-drifts)."""
    base_url, specs = cache_edge
    specs.update(_all_erroring())
    _origin_green(env, mock_site)
    env["NFM_CACHE_STATE_BASE"] = base_url
    env["NFM_MONITOR_PROBE"] = _probe(Path(env["NFM_MONITOR_LOG"]).parent, _report([]), rc=0)

    r = run_script(env)
    # green health probe + green origin: the tick is OK; the cache-state
    # lane records the edge errors as evidence (edge-degraded), drift-free
    assert r.returncode == 0, (r.returncode, log_text(env))

    cs = _cache_lines(env)[-1]
    assert "drift=0" in cs, "error pages must not flag drift"
    assert "gap=0" in cs and "shield=0" in cs, "origin green => no shield/gap context"
    assert "batclass=edge-degraded" in cs and "err=5" in cs


# --------------------------------------------- ORIGIN_OUTAGE_STALE_SERVED


def test_oos_verdict_requires_shield(env, mock_site, cache_edge):
    """Origin + tunnel dark + cached green + PROVEN shield => the new
    verdict; immediate WARN at streak 1 naming the shield counts."""
    base_url, specs = cache_edge
    specs.update(_all_stale_contract())
    _origin_dark(env)
    env["NFM_CACHE_STATE_BASE"] = base_url
    tmp = Path(env["NFM_MONITOR_LOG"]).parent
    env["NFM_MONITOR_PROBE"] = _probe(tmp, _report(_oos_rows()))

    r = run_script(env)
    assert r.returncode == 1, (r.returncode, log_text(env))

    lt = log_text(env)
    assert "verdict=ORIGIN_OUTAGE_STALE_SERVED" in _lanes_lines(env)[-1]
    assert "WARN — ORIGIN_OUTAGE_STALE_SERVED (origin + edge-tunnel dark, users shielded by cached edge HTML; cache-state shield=5 stale=5 fresh=0): streak 1/2" in lt
    cs = _cache_lines(env)[-1]
    assert "shield=5" in cs and "gap=0" in cs and "batclass=shielded" in cs
    assert state_dict(env)["pending_page"] == "-", "no page latch at streak 1"
    # mutual exclusion with the streak-1 full-edge rule (cached green caps
    # edge failures at 4/6 < conjunct 5; here 2/6)
    assert "streak-1 full-edge" not in lt


def test_oos_without_shield_evidence_stays_mixed(env, mock_site, cache_edge):
    """Fail-open: probe dark (no shield evidence) => the tick stays MIXED
    and rides the GENERIC streak-1 WARN — byte-identical to pre-5875."""
    _origin_dark(env)
    env["NFM_CACHE_STATE_BASE"] = "http://127.0.0.1:9"  # nothing listens
    tmp = Path(env["NFM_MONITOR_LOG"]).parent
    env["NFM_MONITOR_PROBE"] = _probe(tmp, _report(_oos_rows()))

    r = run_script(env)
    assert r.returncode == 1, (r.returncode, log_text(env))

    lt = log_text(env)
    assert "verdict=MIXED" in _lanes_lines(env)[-1]
    assert "ORIGIN_OUTAGE_STALE_SERVED" not in lt
    assert "WARN — failure streak 1/2 (2 check(s) failed, 1 P0)" in lt
    cs = _cache_lines(env)[-1]
    assert "gap=5" in cs and "shield=0" in cs and "batclass=gap" in cs


def test_oos_streak2_pages_with_latch(env, mock_site, cache_edge):
    """Page at streak 2 — CRITICAL naming the NFM-5875 rule, cache-state
    counts in the line, pending_page latched, level CRITICAL."""
    base_url, specs = cache_edge
    specs.update(_all_stale_contract())
    _origin_dark(env)
    env["NFM_CACHE_STATE_BASE"] = base_url
    tmp = Path(env["NFM_MONITOR_LOG"]).parent
    env["NFM_MONITOR_PROBE"] = _probe(tmp, _report(_oos_rows()))

    assert run_script(env).returncode == 1  # streak 1: WARN
    r2 = run_script(env)
    assert r2.returncode == 2, (r2.returncode, log_text(env))

    lt = log_text(env)
    assert "CRITICAL — ORIGIN_OUTAGE_STALE_SERVED page (NFM-5875 rule: WARN streak 1, page streak 2; NFM-5666 discipline)" in lt
    assert "cache-state shield=5 gap=0 stale=5" in lt
    assert "users shielded, content frozen" in lt
    assert state_dict(env)["pending_page"] != "-"
    assert state_dict(env)["level"] == "CRITICAL"
    assert state_dict(env)["streak"] == "2"
    assert "CRITICAL — public-edge P0 failure confirmed" not in lt, (
        "the generic full-edge page text must NOT fire for an OOS verdict"
    )


# ------------------------------------------------------------- no regression


def test_full_edge_outage_unchanged_when_cached_dark(env, mock_site, cache_edge):
    """cached=fail keeps FULL_EDGE_OUTAGE (the OOS branch cannot fire — the
    e7/e8-shape outage classifies exactly as before); the CACHE-STATE line
    carries the gap evidence without touching the verdict."""
    base_url, specs = cache_edge
    specs.update(_all_erroring())
    _origin_dark(env)
    env["NFM_CACHE_STATE_BASE"] = base_url
    tmp = Path(env["NFM_MONITOR_LOG"]).parent
    env["NFM_MONITOR_PROBE"] = _probe(tmp, _report(_fulledge_rows()))

    r = run_script(env)
    assert r.returncode in (1, 2), (r.returncode, log_text(env))

    lt = log_text(env)
    assert "verdict=FULL_EDGE_OUTAGE" in _lanes_lines(env)[-1]
    assert "ORIGIN_OUTAGE_STALE_SERVED" not in lt
    cs = _cache_lines(env)[-1]
    assert "gap=5" in cs and "shield=0" in cs and "batclass=gap" in cs
    # the live 2026-11-16 episode shape, now visible as a dimension: edge
    # erroring (502) on treated routes while the origin is dark
    assert "=err(502,ccs=none,a=0)" in cs


def test_partial_degraded_mutually_exclusive_with_oos(env, mock_site, cache_edge):
    """PD (origin ok + tunnel dark + cached ok) keeps its verdict and its
    own WARN text even when the cache-state probe serves fresh HTML."""
    base_url, specs = cache_edge
    specs.update(_all_fresh_contract())
    _origin_green(env, mock_site)
    env["NFM_CACHE_STATE_BASE"] = base_url
    tmp = Path(env["NFM_MONITOR_LOG"]).parent
    env["NFM_MONITOR_PROBE"] = _probe(tmp, _report(_oos_rows()))

    r = run_script(env)
    assert r.returncode == 1, (r.returncode, log_text(env))

    lt = log_text(env)
    assert "verdict=PARTIAL_DEGRADED" in _lanes_lines(env)[-1]
    assert "WARN — PARTIAL_DEGRADED (edge-tunnel dark, users shielded by cached edge): streak 1/2" in lt
    assert "ORIGIN_OUTAGE_STALE_SERVED" not in lt
    cs = _cache_lines(env)[-1]
    assert "shield=0" in cs, "origin green => no shield context even when fresh=5"
    assert "fresh=5" in cs
