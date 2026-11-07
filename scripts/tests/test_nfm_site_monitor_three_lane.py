"""Tests for scripts/nfm-site-monitor.sh — NFM-5876 three-lane classification.

CPO dispatch, child of NFM-5872 (CTO ruling R4-3 on NFM-5862, 2026-11-07):
the site monitor gains an explicit edge-tunnel probe lane and a per-tick
three-lane classification over origin-direct / edge-tunnel / edge-cached.

Lane inventory (scope guard: verified + extended, NOT duplicated):
  origin-direct — the NFM-5797 discriminator (NFM_ORIGIN_BASE seam in tests;
    contract amended: it now gates the PARTIAL_DEGRADED conjunct — origin
    must be green for PD — but still never feeds streaks/state/paging);
  edge-tunnel  — the EXISTING "Backend API Health" P0 target, exactly the
    CTO-specified URL (uncached API path through the edge hostname);
  edge-cached  — Frontend Homepage + Frontend Browse Page (both must pass)
    plus the chunk-scan edge-html-desync synthesis.

Normative matrix (CTO architecture, "implement as specified"):
  origin ok + tunnel FAIL + cached ok -> PARTIAL_DEGRADED:
    WARN immediately (streak 1); PAGE at streak 2 (NFM-5666 discipline for
    the new class) — CRITICAL line + notify + pending_page latch.
  tunnel FAIL + cached FAIL           -> full-edge outage: existing
    CRITICAL path byte-identical (AC3).
  all other combinations              -> existing semantics (AC3).

Fail-closed direction: PD can only ever DOWNGRADE the label of an
already-failing P0 probe from full-edge-outage to partial-degradation, and
only when the cache shield is PROVEN (both cached lanes green from this
vantage) and the origin is green. Origin-dark, cached-dark, garbled tables
— any of these keeps the existing page path.

Fixture-replay harness reuses the established shape (test_nfm_site_monitor
+ test_nfm_site_monitor_streak1_page): the sentinel runs for real against
env seams, with NFM_MONITOR_PROBE swapped for a canned health_check.py-format
report (this file's _report prints the FULL table — ✅ and ❌ rows — so the
lane parser sees passing lanes too) and NFM_ORIGIN_BASE pointed at the mock
edge (origin green) or a dead port (origin dark).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import test_nfm_site_monitor as base  # noqa: F401 — registers the mock_site fixture
from test_nfm_site_monitor import (  # noqa: F401 — env re-exported as pytest fixture
    env,
    log_text,
    mock_site,
    run_script,
    state_dict,
)

SCRIPT = Path(__file__).resolve().parent.parent / "nfm-site-monitor.sh"

# TARGETS order (drift-guard: health_check.py::TARGETS must keep these names;
# the classifier greps them literally from the probe table).
CACHED_LANES = ("Frontend Homepage", "Frontend Browse Page")
TUNNEL_LANE = "Backend API Health"
OTHER_LANES = ("Backend API Potentials", "Backend API Reference Values")
VERIFY_LANE = "AutoVC API Health"
ALL_NAMES = (*CACHED_LANES, TUNNEL_LANE, *OTHER_LANES, VERIFY_LANE, "Supabase Health")

_counter = iter(range(10000))


# ------------------------------------------------------------------ helpers


@pytest.fixture(autouse=True)
def _isolate_s1_state(env):
    """Isolate the NFM-5667 streak-1 episode/ledger state per test.

    The base env fixture predates this file and does not know the S1 seam;
    without this, an s1-matching fixture (e.g. the AC3 full-edge regression
    below) would page into and append to the PRODUCTION
    ~/.nfm-site-monitor.s1.state — a false T2-ledger episode and a live
    rate-limit write. (Found the hard way during the first test run; the
    production file was repaired and the incident is noted in the NFM-5876
    evidence comment.)
    """
    env["NFM_MONITOR_S1_STATE"] = str(Path(env["NFM_MONITOR_LOG"]).parent / "monitor.s1.state")


def _require_lane_seams() -> None:
    """Fail fast if the NFM-5876 seams vanished (base guard pattern)."""
    src = SCRIPT.read_text(encoding="utf-8")
    for marker in ("three_lane_classify", "PROBE_ROWS=", "PD_PAGE_STREAK="):
        if marker not in src:
            pytest.fail(f"script lost its NFM-5876 seam ({marker!r}) — refusing to run")


def _report(failed_rows):
    """health_check.py stdout shape WITH the full table (✅ + ❌ rows) —
    the real format_results prints every target, and the lane parser reads
    passing rows too."""
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


def _probe(tmp_path, report, rc=1):
    p = Path(tmp_path) / f"probe_{next(_counter)}.py"
    p.write_text(
        "import sys\nsys.stdout.write(%r)\nsys.exit(%d)\n" % (report, rc),
        encoding="utf-8",
    )
    return str(p)


def _lanes_lines(env):
    return [ln for ln in log_text(env).splitlines() if "LANES — " in ln]


def _pd_fixture():
    """The PARTIAL_DEGRADED shape: tunnel lane dark (530, hard), both cached
    lanes green, one more API lane dark, origin+verify+third-party green."""
    return [
        (TUNNEL_LANE, "P0", "234ms", "HTTP 530: "),
        (OTHER_LANES[0], "P1", "230ms", "HTTP 530: "),
    ]


def _fulledge_fixture():
    """Full-edge outage: tunnel + cached BOTH dark, hard codes."""
    return [
        (CACHED_LANES[0], "P1", "120ms", "HTTP 530: "),
        (CACHED_LANES[1], "P1", "118ms", "HTTP 530: "),
        (TUNNEL_LANE, "P0", "234ms", "HTTP 530: "),
        (OTHER_LANES[0], "P1", "125ms", "HTTP 530: "),
    ]


def _origin_green(env, mock_site):
    env["NFM_ORIGIN_BASE"] = mock_site[0]


def _origin_dark(env):
    env["NFM_ORIGIN_BASE"] = "http://127.0.0.1:9"  # nothing listens on :9


# ---------------------------------------------------------------- AC1: lanes


def test_require_lane_seams_present(env):
    _require_lane_seams()


def test_green_tick_logs_lanes_line_with_all_ok(env, mock_site):
    """AC1/AC3: a green tick carries a LANES line with verdict=ALL_OK and all
    three lanes ok; the OK summary line stays the tick's newest line and
    byte-stable (no lanes token inside it); ORIGIN precedes LANES."""
    _origin_green(env, mock_site)
    env["NFM_MONITOR_PROBE"] = _probe(Path(env["NFM_MONITOR_LOG"]).parent, _report([]), rc=0)

    r = run_script(env)
    assert r.returncode == 0, (r.returncode, log_text(env))

    lines = log_text(env).strip().splitlines()
    lanes = [ln for ln in lines if "LANES — " in ln]
    ok_idx = [i for i, ln in enumerate(lines) if ln.endswith("OK — all checks passed")]
    origin_idx = [i for i, ln in enumerate(lines) if "ORIGIN — origin: " in ln]
    assert lanes, "LANES line missing on a green tick"
    assert "verdict=ALL_OK" in lanes[-1]
    assert "origin=ok" in lanes[-1] and "edge-tunnel=ok" in lanes[-1] and "edge-cached=ok" in lanes[-1]
    assert origin_idx and origin_idx[-1] < lines.index(lanes[-1]) < ok_idx[-1]
    assert lines[-1] == lines[ok_idx[-1]], "OK line must stay the tick's newest line"
    assert "LANES" not in lines[ok_idx[-1]], "green OK line must stay byte-stable"


# ------------------------------------------------------- AC2: PD page rule


def test_pd_streak1_warns_immediately_no_page(env, mock_site):
    """AC2 first half: PARTIAL_DEGRADED fires WARN IMMEDIATELY at streak 1 —
    no page, no pending latch, no CRITICAL, no s1 match."""
    tmp = Path(env["NFM_MONITOR_LOG"]).parent
    _origin_green(env, mock_site)
    env["NFM_MONITOR_PROBE"] = _probe(tmp, _report(_pd_fixture()))

    r = run_script(env)
    assert r.returncode == 1, (r.returncode, log_text(env))

    lt = log_text(env)
    assert "WARN — PARTIAL_DEGRADED (edge-tunnel dark, users shielded by cached edge): streak 1/2" in lt
    assert "page at streak 2" in lt
    assert "tunnel lane: HTTP 530:" in lt
    assert not [ln for ln in lt.splitlines() if ln.startswith("[") and "CRITICAL" in ln], "no CRITICAL at streak 1"
    assert "verdict=PARTIAL_DEGRADED" in _lanes_lines(env)[-1]
    assert "edge-tunnel=fail(HTTP 530:)" in _lanes_lines(env)[-1]
    assert state_dict(env)["pending_page"] == "-"
    assert state_dict(env)["level"] == "WARN"
    # mutual exclusion with the streak-1 full-edge rule (PD caps edge
    # failures at 4/6 < conjunct 5; here 2/6)
    assert "streak-1 full-edge" not in lt


def test_pd_streak2_pages_with_latch(env, mock_site):
    """AC2 second half: page at streak 2 — CRITICAL line naming the rule,
    pending_page latched (NFM-5262 reconciliation), level CRITICAL."""
    tmp = Path(env["NFM_MONITOR_LOG"]).parent
    _origin_green(env, mock_site)
    env["NFM_MONITOR_PROBE"] = _probe(tmp, _report(_pd_fixture()))

    assert run_script(env).returncode == 1  # streak 1: WARN
    r2 = run_script(env)
    assert r2.returncode == 2, (r2.returncode, log_text(env))

    lt = log_text(env)
    assert "CRITICAL — PARTIAL_DEGRADED page (NFM-5876 rule: WARN streak 1, page streak 2; NFM-5666 discipline" in lt
    assert "tunnel lane: HTTP 530:" in lt
    assert "users currently shielded by cached edge HTML" in lt
    assert state_dict(env)["pending_page"] != "-"
    assert state_dict(env)["level"] == "CRITICAL"
    assert state_dict(env)["streak"] == "2"
    # the full-edge page text must NOT fire for a PD verdict
    assert "CRITICAL — public-edge P0 failure confirmed" not in lt


def test_pd_recovery_then_ack_contract(env, mock_site):
    """AC3: recovery path unchanged — the green tick after a PD page logs the
    standard RECOVERY + PAGE-UNACK reconciliation lines."""
    tmp = Path(env["NFM_MONITOR_LOG"]).parent
    _origin_green(env, mock_site)
    env["NFM_MONITOR_PROBE"] = _probe(tmp, _report(_pd_fixture()))
    run_script(env)
    run_script(env)
    env["NFM_MONITOR_PROBE"] = _probe(tmp, _report([]), rc=0)

    r = run_script(env)
    assert r.returncode == 0, (r.returncode, log_text(env))
    lt = log_text(env)
    assert "RECOVERY — all checks passing again after 2 failing probe(s)" in lt
    assert "PAGE-UNACK — CRITICAL from" in lt
    # latch survives recovery until the heartbeat acks (existing contract)
    assert state_dict(env)["pending_page"] != "-"


def test_origin_dark_blocks_pd_fail_open(env, mock_site):
    """Fail-closed negative: the PD conjuncts require origin-direct green.
    Origin dark + tunnel dark + cached green -> verdict MIXED -> existing
    page path (full-edge CRITICAL text at streak 2), never PD."""
    tmp = Path(env["NFM_MONITOR_LOG"]).parent
    _origin_dark(env)
    env["NFM_MONITOR_PROBE"] = _probe(tmp, _report(_pd_fixture()))

    r1 = run_script(env)
    assert r1.returncode == 1
    assert "WARN — PARTIAL_DEGRADED" not in log_text(env)
    assert "verdict=MIXED" in _lanes_lines(env)[-1]

    r2 = run_script(env)
    assert r2.returncode == 2
    assert "CRITICAL — public-edge P0 failure confirmed (2 consecutive" in log_text(env)
    assert "PARTIAL_DEGRADED page" not in log_text(env)


def test_cached_dark_with_tunnel_dark_is_full_edge_not_pd(env, mock_site):
    """AC3 matrix: tunnel FAIL + cached FAIL -> FULL_EDGE_OUTAGE -> existing
    CRITICAL path byte-identical; PD text never fires."""
    tmp = Path(env["NFM_MONITOR_LOG"]).parent
    _origin_green(env, mock_site)
    env["NFM_MONITOR_PROBE"] = _probe(tmp, _report(_fulledge_fixture()))

    r1 = run_script(env)
    assert r1.returncode == 1
    assert "WARN — failure streak 1/2" in log_text(env)  # generic text preserved
    assert "verdict=FULL_EDGE_OUTAGE" in _lanes_lines(env)[-1]

    r2 = run_script(env)
    assert r2.returncode == 2
    assert "CRITICAL — public-edge P0 failure confirmed (2 consecutive" in log_text(env)
    assert "PARTIAL_DEGRADED" not in log_text(env)


def test_cached_dark_alone_is_cached_degraded_no_pd(env, mock_site):
    """Matrix: tunnel ok + cached FAIL -> CACHED_DEGRADED evidence verdict;
    the P1-only WARN lane owns it; no PD anywhere."""
    tmp = Path(env["NFM_MONITOR_LOG"]).parent
    _origin_green(env, mock_site)
    rows = [(CACHED_LANES[0], "P1", "6000ms", "Response time 6000ms exceeds limit 5000ms")]
    env["NFM_MONITOR_PROBE"] = _probe(tmp, _report(rows))

    r = run_script(env)
    assert r.returncode == 1
    lt = log_text(env)
    assert "WARN — P1-only failures" in lt
    assert "verdict=CACHED_DEGRADED" in _lanes_lines(env)[-1]
    assert "PARTIAL_DEGRADED" not in lt


def test_garbled_table_stays_unclassified_fail_open(env):
    """Fail-open floor: no parseable rows -> verdict=UNCLASSIFIED; the
    pre-existing p0-unknown WARN text is preserved (never PD)."""
    tmp = Path(env["NFM_MONITOR_LOG"]).parent
    env["NFM_MONITOR_PROBE"] = _probe(tmp, "probe crashed mid-run, partial stdout")
    r = run_script(env)
    assert r.returncode == 1
    lt = log_text(env)
    assert "verdict=UNCLASSIFIED" in _lanes_lines(env)[-1]
    assert "WARN — failure streak 1/2" in lt
    assert "PARTIAL_DEGRADED" not in lt


# --------------------------------------------- AC1: scan synthesis + verdict


def test_scan_desync_synth_counts_as_cached_fail(env, mock_site):
    """On a scan-synthesized tick the classifier reads the RAW probe table
    (PROBE_ROWS) — the tunnel lane was green that tick, the desync counts as
    cached-lane failure -> verdict=CACHED_DEGRADED, P0 dead-ref path
    unchanged."""
    base_url, config = mock_site
    _origin_green(env, mock_site)
    # the base probe is GREEN with a full table (so the tunnel lane parses
    # ok from PROBE_ROWS); the scan then synthesizes the desync failure
    env["NFM_MONITOR_PROBE"] = _probe(Path(env["NFM_MONITOR_LOG"]).parent, _report([]), rc=0)
    # stage a definitive dead chunk ref on `/` (scan rc 1, p0>0 -> synthesis)
    body = config["pages"]["/"]["body"]
    ref_name = re.search(r"/_next/static/[A-Za-z0-9._\-/]+", body).group(0).rsplit("/", 1)[-1]
    config["ref_status"] = {ref_name: 404}

    r = run_script(env)
    assert r.returncode == 1, (r.returncode, log_text(env))
    lt = log_text(env)
    assert "edge-html-desync" in lt
    assert "verdict=CACHED_DEGRADED" in _lanes_lines(env)[-1]
    assert "edge-tunnel=ok" in _lanes_lines(env)[-1]
    # the scan-synthesized P0 keeps the existing page path (streak 1 WARN)
    assert "WARN — failure streak 1/2" in lt


# ------------------------------------------------------------------ AC3: s1


def test_pd_streak1_s1_rule_untouched_mutually_exclusive(env, mock_site):
    """AC3: the NFM-5666/5667 streak-1 full-edge rule still fires unchanged
    on its own signature (all 6 edge checks, hard codes, both hostnames) —
    and PD text never co-fires with it."""
    tmp = Path(env["NFM_MONITOR_LOG"]).parent
    _origin_green(env, mock_site)
    rows = [
        (CACHED_LANES[0], "P1", "120ms", "HTTP 502: Bad Gateway"),
        (CACHED_LANES[1], "P1", "118ms", "HTTP 502: Bad Gateway"),
        (TUNNEL_LANE, "P0", "234ms", "HTTP 530: "),
        (OTHER_LANES[0], "P1", "125ms", "HTTP 530: "),
        (OTHER_LANES[1], "P1", "121ms", "HTTP 530: "),
        (VERIFY_LANE, "P0", "402ms", "HTTP 530: "),
    ]
    env["NFM_MONITOR_PROBE"] = _probe(tmp, _report(rows))

    r = run_script(env)
    assert r.returncode == 2, (r.returncode, log_text(env))
    lt = log_text(env)
    assert "CRITICAL — streak-1 full-edge P0 (NFM-5666 rule, NFM-5664 ruling 35aa85eb §4)" in lt
    assert "verdict=FULL_EDGE_OUTAGE" in _lanes_lines(env)[-1]
    assert "PARTIAL_DEGRADED" not in lt


def test_origin_lane_contract_amendment_documented(env):
    """The NFM-5797 evidence-only contract is amended in-file: the script
    header must state the NFM-5876 amendment (origin gates the PD conjunct)
    so the next reader doesn't trust the stale evidence-ONLY claim."""
    src = SCRIPT.read_text(encoding="utf-8")
    assert "Contract AMENDED by this lane" in src
    assert "PARTIAL_DEGRADED" in src
