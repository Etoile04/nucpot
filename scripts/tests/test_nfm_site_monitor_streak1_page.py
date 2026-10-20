"""Tests for scripts/nfm-site-monitor.sh — NFM-5667 streak-1 full-edge page rule.

CTO ruling NFM-5664 comment 35aa85eb §4 (2026-10-20T00:15Z), implemented as
child NFM-5666 / code-half NFM-5667: PAGE at streak 1 IFF ALL FOUR conjuncts
hold on the current failing probe —

1. >=5 of the 6 EDGE checks failed (the two monitored hostnames; the P2
   Supabase third-party check is outside the rule);
2. >=2 P0 checks among them (the two P0 targets are Backend API Health on
   nucpot.dpdns.org and AutoVC API Health on verify.nucpot.dpdns.org);
3. the failure set spans BOTH hostnames (full-edge — partial-hostname
   failures are excluded; NFM-5262 diurnal flaps are exactly this class);
4. the dominant failure class is hard edge status codes (502/530 and other
   52x/53x). Latency-timeout-dominant sets are EXCLUDED and keep the
   existing streak>=2 lane.

Rate limit: max 1 page / 30 min for this class, from a counter independent
of the streak>=2 lane. The existing streak>=2 lane is UNCHANGED (AC4),
including no double-page when both lanes match.

Population basis (ruling): 3/3 prior signature matches were real outages —
Ep#4 2026-10-19T20:03Z sentinel line read "6 checks failed, 2 P0, hard
status codes (502/530)". The fixtures below replay that exact shape
(fx1) and every negative of the truth table (fx2-fx5).

Page payload (ruling amendment 1): window timestamps (first-failure ->
recovery), per-hostname status-class counts, the rolling-30d full-edge
episode count toward ADR-025 D2 trigger T2 (ledger delta, by-episode per
ruling §2: distinct windows separated by >=10 min green), and the
NFM-5664 ruling reference — pages double as T2 counting evidence.

Fixture-replay harness reuses the NFM-5497 shape (test_nfm_site_monitor):
the sentinel runs for real against env seams, with NFM_MONITOR_PROBE
swapped for a canned health_check.py-format report.
"""

from __future__ import annotations

import itertools
import os
import re
import time
from datetime import datetime, timedelta, timezone

import pytest

import test_nfm_site_monitor as base  # noqa: F401 — registers the mock_site fixture
from test_nfm_site_monitor import log_text, mock_site, run_script, state_dict

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "nfm-site-monitor.sh")
HEALTH_CHECK = os.path.join(os.path.dirname(__file__), "..", "health_check.py")

# The 6 EDGE checks (ruling conjunct 1 denominator). Third-party P2 Supabase
# is not an edge check and must not count.
MAIN_P1 = ("Frontend Homepage", "Frontend Browse Page", "Backend API Potentials",
           "Backend API Reference Values")
MAIN_P0 = "Backend API Health"
VERIFY_P0 = "AutoVC API Health"

PAGE_COOLDOWN_S = 1800   # 1 page / 30 min (ruling §4)
EPISODE_GREEN_GAP_S = 600  # ruling §2: >=10 min green separates episodes
LEDGER_WINDOW_DAYS = 30

_counter = itertools.count()


# ------------------------------------------------------------------ helpers


def _require_s1_seam() -> None:
    """Fail fast if the s1-lane state seam is gone (base guard pattern)."""
    with open(SCRIPT, encoding="utf-8") as fh:
        src = fh.read()
    if "NFM_MONITOR_S1_STATE" + ":-" not in src:
        pytest.fail("script lost its NFM_MONITOR_S1_STATE env seam — refusing to run against production")


def _report(failed_rows, total=7):
    """health_check.py stdout shape for a failing probe.

    failed_rows: list of (name, severity, ms, detail) — exactly the ❌ rows.
    """
    p0 = sum(1 for r in failed_rows if r[1] == "P0")
    lines = [
        "Starting NucPot health checks...",
        "Targets: 7",
        "",
        "## NucPot Health Check Report",
        "",
        f"**{len(failed_rows)}/{total} checks FAILED** ❌",
        "",
        "| Target | Status | Response | Details |",
        "|--------|--------|----------|---------|",
    ]
    for name, sev, ms, detail in failed_rows:
        lines.append(f"| ❌ {name} | {sev} | {ms} | {detail} |")
    lines += ["", f"⚠️  {len(failed_rows)} check(s) failed ({p0} P0 critical)"]
    return "\n".join(lines)


def _probe(tmp_path, report, rc=1):
    from pathlib import Path
    p = Path(tmp_path) / f"probe_{next(_counter)}.py"
    p.write_text(
        "import sys\nsys.stdout.write(%r)\nsys.exit(%d)\n" % (report, rc),
        encoding="utf-8",
    )
    return str(p)


def _fulledge_502_530():
    """fx1 — the Ep#4 20:03Z shape: all 6 edge checks dead, hard codes."""
    return [
        (MAIN_P1[0], "P1", "120ms", "HTTP 502: Bad Gateway"),
        (MAIN_P1[1], "P1", "118ms", "HTTP 502: Bad Gateway"),
        (MAIN_P0, "P0", "234ms", "HTTP 530: "),
        (MAIN_P1[2], "P1", "125ms", "HTTP 502: Bad Gateway"),
        (MAIN_P1[3], "P1", "121ms", "HTTP 502: Bad Gateway"),
        (VERIFY_P0, "P0", "402ms", "HTTP 530: "),
    ]


def _probe_green(tmp_path):
    return _probe(tmp_path, "Starting NucPot health checks...\n\n✅ All checks passed", rc=0)


def s1_state_dict(env):
    out = {}
    try:
        with open(env["NFM_MONITOR_S1_STATE"], encoding="utf-8") as fh:
            for line in fh:
                if "=" in line:
                    k, v = line.rstrip("\n").split("=", 1)
                    out[k] = v
    except FileNotFoundError:
        pass
    return out


def s1_write_seed(env, **kv):
    """Seed the s1 state file (tests control the clocks it reads)."""
    defaults = {"last_page": "0", "open": "0", "win_first": "-",
                "close_epoch": "0", "ledger": ""}
    defaults.update(kv)
    with open(env["NFM_MONITOR_S1_STATE"], "w", encoding="utf-8") as fh:
        for k, v in defaults.items():
            fh.write(f"{k}={v}\n")


def _iso(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _recording_notify(tmp_path):
    # notify_page invokes the seam osascript-style: <seam> -e "display
    # notification \"<summary>\" ..." — the summary rides arg 2, so record
    # every arg.
    p = tmp_path / "notify.sh"
    p.write_text(
        "#!/bin/bash\necho \"$*\" >> " + str(tmp_path / "notify.log") + "\n",
        encoding="utf-8",
    )
    p.chmod(0o755)
    return str(p)


@pytest.fixture()
def env(tmp_path, mock_site):
    """base env + the s1-lane state seam + a recording notify lane."""
    _base, _config = mock_site
    # Same seam set as the base harness (mock-site base so green runs can
    # chunk-scan hermetically), plus the s1-lane state seam.
    probe = tmp_path / "probe_ok.py"
    probe.write_text('print("3 check(s) passed")\n', encoding="utf-8")
    e = {
        "NFM_SCAN_BASE": _base,
        "NFM_SCAN_DEADLINE": "240",
        "NFM_SCAN_REF_POOL": "4",
        "NO_PROXY": "127.0.0.1,localhost",
        "no_proxy": "127.0.0.1,localhost",
        "NFM_MONITOR_LOG": str(tmp_path / "monitor.log"),
        "NFM_MONITOR_STATE": str(tmp_path / "monitor.state"),
        "NFM_MONITOR_S1_STATE": str(tmp_path / "monitor.s1.state"),
        "NFM_MONITOR_PROBE": str(probe),
        "NFM_MONITOR_NOTIFY": _recording_notify(tmp_path),
    }
    return e


def s1_page_lines(env):
    return [ln for ln in log_text(env).splitlines() if "CRITICAL — streak-1 full-edge" in ln]


def notify_log(env):
    try:
        with open(os.path.dirname(env["NFM_MONITOR_LOG"]) + "/notify.log", encoding="utf-8") as fh:
            return fh.read()
    except FileNotFoundError:
        return ""


# ------------------------------------------------------------ seam guard


def test_seam_guard_requires_s1_state_seam():
    """The s1 lane must keep its state env seam or tests could hit prod state."""
    _require_s1_seam()


# ------------------------------------------------- AC1 classifier truth table


def test_fx1_fulledge_streak1_pages(env):
    """fx1 (Ep#4 shape): all four conjuncts hold => PAGE at streak 1.

    rc 2, CRITICAL log line, pending_page latched, notify fired, streak
    counter preserved (the streak>=2 lane's own state is not reset).
    """
    tmp = os.path.dirname(env["NFM_MONITOR_LOG"])
    env["NFM_MONITOR_PROBE"] = _probe(tmp, _report(_fulledge_502_530()))
    r = run_script(env)
    assert r.returncode == 2, (r.returncode, r.stdout, log_text(env))
    lt = log_text(env)
    assert "CRITICAL — streak-1 full-edge P0" in lt
    st = state_dict(env)
    assert st["level"] == "CRITICAL"
    assert st["streak"] == "1"          # streak lane NOT reset/altered
    assert st["pending_page"] not in ("-", "")
    assert "streak-1 full-edge" in notify_log(env)
    s1 = s1_state_dict(env)
    assert s1["open"] == "1"
    assert s1["last_page"] != "0"
    assert len(s1["ledger"].split()) == 1


def test_fx1_payload_carries_required_fields(env):
    """AC3: window, per-host class counts, t2 ledger delta, ruling ref."""
    tmp = os.path.dirname(env["NFM_MONITOR_LOG"])
    env["NFM_MONITOR_PROBE"] = _probe(tmp, _report(_fulledge_502_530()))
    run_script(env)
    line = s1_page_lines(env)[0]
    assert "6/6 edge checks failed (2 P0," in line
    assert re.search(r"window \d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z -> recovery pending", line)
    assert "nucpot.dpdns.org={502x4,530x1}" in line
    assert "verify.nucpot.dpdns.org={530x1}" in line
    assert "t2-ledger" in line and "rolling-30d" in line
    assert "episodes=1" in line
    assert "delta +1" in line
    assert "NFM-5664" in line and "35aa85eb" in line


def test_fx2_partial_hostname_no_page(env):
    """fx2 negative (conjunct 3): verify-hostname green => partial-edge, no
    page even with 5 hard failures. The NFM-5262 diurnal-flap exclusion."""
    tmp = os.path.dirname(env["NFM_MONITOR_LOG"])
    rows = [
        (MAIN_P1[0], "P1", "120ms", "HTTP 502: Bad Gateway"),
        (MAIN_P1[1], "P1", "118ms", "HTTP 502: Bad Gateway"),
        (MAIN_P0, "P0", "234ms", "HTTP 502: Bad Gateway"),
        (MAIN_P1[2], "P1", "125ms", "HTTP 502: Bad Gateway"),
        (MAIN_P1[3], "P1", "121ms", "HTTP 502: Bad Gateway"),
    ]
    env["NFM_MONITOR_PROBE"] = _probe(tmp, _report(rows))
    r = run_script(env)
    assert r.returncode == 1, (r.returncode, log_text(env))
    assert s1_page_lines(env) == []
    assert "WARN — failure streak 1/2" in log_text(env)
    assert s1_state_dict(env).get("open", "0") == "0"


def test_fx2b_verify_latency_failure_still_hard_dominant_pages(env):
    """Boundary: 5 hard on main + 1 latency on verify is hard-DOMINANT
    (5>1) with both hostnames and 2 P0 — matches the rule as ruled; the
    exclusion is latency-DOMINANT sets, not any-latency sets."""
    tmp = os.path.dirname(env["NFM_MONITOR_LOG"])
    rows = _fulledge_502_530()
    rows[-1] = (VERIFY_P0, "P0", "—", "Timeout after 10s")
    env["NFM_MONITOR_PROBE"] = _probe(tmp, _report(rows))
    r = run_script(env)
    assert r.returncode == 2, (r.returncode, log_text(env))
    assert s1_page_lines(env)
    line = s1_page_lines(env)[0]
    assert "verify.nucpot.dpdns.org={latencyx1}" in line


def test_fx3_latency_dominant_no_page_keeps_streak2_lane(env):
    """fx3 negative (conjunct 4): latency-timeout-dominant set => NO streak-1
    page; a second failing probe still pages via the EXISTING streak>=2
    lane (unchanged)."""
    tmp = os.path.dirname(env["NFM_MONITOR_LOG"])
    rows = [(n, "P0" if n in (MAIN_P0, VERIFY_P0) else "P1", "—", "Timeout after 10s")
            for n in (*MAIN_P1, MAIN_P0, VERIFY_P0)]
    rows[0] = (MAIN_P1[0], "P1", "—", "Connection error: timed out")
    env["NFM_MONITOR_PROBE"] = _probe(tmp, _report(rows))
    r1 = run_script(env)
    assert r1.returncode == 1, (r1.returncode, log_text(env))
    assert s1_page_lines(env) == []
    r2 = run_script(env)
    assert r2.returncode == 2, (r2.returncode, log_text(env))
    assert "CRITICAL — public-edge P0 failure confirmed (2 consecutive" in log_text(env)
    assert len([ln for ln in log_text(env).splitlines() if "streak-1 full-edge" in ln]) == 0


def test_fx4_lt5_checks_no_page(env):
    """fx4 negative (conjunct 1): 4 of 6 edge checks failed => no page."""
    tmp = os.path.dirname(env["NFM_MONITOR_LOG"])
    rows = [
        (MAIN_P1[0], "P1", "120ms", "HTTP 502: Bad Gateway"),
        (MAIN_P1[1], "P1", "118ms", "HTTP 502: Bad Gateway"),
        (MAIN_P0, "P0", "234ms", "HTTP 530: "),
        (VERIFY_P0, "P0", "402ms", "HTTP 530: "),
    ]
    env["NFM_MONITOR_PROBE"] = _probe(tmp, _report(rows))
    r = run_script(env)
    assert r.returncode == 1, (r.returncode, log_text(env))
    assert s1_page_lines(env) == []


def test_fx5_lt2_p0_no_page(env):
    """fx5 negative (conjunct 2): 5 edge checks failed but only 1 P0."""
    tmp = os.path.dirname(env["NFM_MONITOR_LOG"])
    rows = [
        (MAIN_P1[0], "P1", "120ms", "HTTP 502: Bad Gateway"),
        (MAIN_P1[1], "P1", "118ms", "HTTP 502: Bad Gateway"),
        (MAIN_P1[2], "P1", "125ms", "HTTP 502: Bad Gateway"),
        (MAIN_P1[3], "P1", "121ms", "HTTP 502: Bad Gateway"),
        (VERIFY_P0, "P0", "402ms", "HTTP 530: "),
    ]
    env["NFM_MONITOR_PROBE"] = _probe(tmp, _report(rows))
    r = run_script(env)
    assert r.returncode == 1, (r.returncode, log_text(env))
    assert s1_page_lines(env) == []


def test_fx6_other_status_class_no_page(env):
    """fx6 negative (conjunct 4, non-latency side): a failure set of
    non-edge status codes (404-class "other") is not hard-edge-dominant —
    hard=0 can never exceed, so no page; the histogram labels it other."""
    tmp = os.path.dirname(env["NFM_MONITOR_LOG"])
    rows = [
        (MAIN_P1[0], "P1", "120ms", "Expected status 200, got 404"),
        (MAIN_P1[1], "P1", "118ms", "Expected status 200, got 404"),
        (MAIN_P0, "P0", "234ms", "Expected status 200, got 404"),
        (MAIN_P1[2], "P1", "125ms", "Expected status 200, got 404"),
        (VERIFY_P0, "P0", "402ms", "Expected status 200, got 404"),
    ]
    env["NFM_MONITOR_PROBE"] = _probe(tmp, _report(rows))
    r = run_script(env)
    assert r.returncode == 1, (r.returncode, log_text(env))
    assert s1_page_lines(env) == []


def test_unparseable_probe_output_stays_on_existing_lane(env):
    """Fail-closed: probe output with no counts and no ❌ rows cannot prove
    any conjunct — no streak-1 page; the pre-existing p0-unknown WARN path
    (p0="-1", page-worthy at streak>=2) is what handles it."""
    tmp = os.path.dirname(env["NFM_MONITOR_LOG"])
    env["NFM_MONITOR_PROBE"] = _probe(tmp, "probe crashed mid-run, partial stdout")
    r = run_script(env)
    assert r.returncode == 1, (r.returncode, log_text(env))
    assert s1_page_lines(env) == []
    assert "WARN — failure streak 1/2" in log_text(env)


def test_p1_only_failure_skips_s1_lane(env):
    """p0==0 (P1-only failures) never enters the s1 classifier — the
    pre-existing P1-only WARN lane owns it, page rule irrelevant."""
    tmp = os.path.dirname(env["NFM_MONITOR_LOG"])
    rows = [
        (MAIN_P1[0], "P1", "120ms", "HTTP 502: Bad Gateway"),
        (MAIN_P1[1], "P1", "118ms", "HTTP 502: Bad Gateway"),
        (MAIN_P1[2], "P1", "125ms", "HTTP 502: Bad Gateway"),
        (MAIN_P1[3], "P1", "121ms", "HTTP 502: Bad Gateway"),
        ("Supabase Health", "P2", "9000ms", "Timeout after 10s"),
    ]
    env["NFM_MONITOR_PROBE"] = _probe(tmp, _report(rows))
    r = run_script(env)
    assert r.returncode == 1, (r.returncode, log_text(env))
    assert "WARN — P1-only failures" in log_text(env)
    assert s1_page_lines(env) == []


def test_supabase_failure_does_not_count_as_edge(env):
    """Third-party P2 Supabase failing alongside a 4-edge failure must stay
    below the >=5-of-6-EDGE bar (6 rows failed, only 4 edge rows)."""
    tmp = os.path.dirname(env["NFM_MONITOR_LOG"])
    rows = [
        (MAIN_P1[0], "P1", "120ms", "HTTP 502: Bad Gateway"),
        (MAIN_P1[1], "P1", "118ms", "HTTP 502: Bad Gateway"),
        (MAIN_P0, "P0", "234ms", "HTTP 530: "),
        (VERIFY_P0, "P0", "402ms", "HTTP 530: "),
        ("Supabase Health", "P2", "9000ms", "Timeout after 10s"),
    ]
    env["NFM_MONITOR_PROBE"] = _probe(tmp, _report(rows))
    r = run_script(env)
    assert r.returncode == 1, (r.returncode, log_text(env))
    assert s1_page_lines(env) == []


# ------------------------------------------------- AC4 streak>=2 overlap/regression


def test_streak2_overlap_no_double_page(env):
    """AC4: when both lanes match, the streak-1 lane fires its one
    rate-limited page; the streak>=2 CRITICAL on the next probe is the
    EXISTING lane's own page — the s1 lane does not fire again and does
    not alter streak>=2 behavior/state."""
    tmp = os.path.dirname(env["NFM_MONITOR_LOG"])
    env["NFM_MONITOR_PROBE"] = _probe(tmp, _report(_fulledge_502_530()))
    r1 = run_script(env)
    assert r1.returncode == 2
    s1_after_first = s1_state_dict(env)
    r2 = run_script(env)
    assert r2.returncode == 2, (r2.returncode, log_text(env))
    lt = log_text(env)
    assert len(s1_page_lines(env)) == 1, "streak-1 lane must not page twice"
    assert "CRITICAL — public-edge P0 failure confirmed (2 consecutive" in lt
    st = state_dict(env)
    assert st["streak"] == "2"
    assert st["level"] == "CRITICAL"
    assert s1_state_dict(env)["last_page"] == s1_after_first["last_page"]


def test_streak2_lane_never_consumes_s1_counter(env):
    """AC2: counter independence — a pure streak>=2 escalation (no streak-1
    match ever) leaves the s1 rate-limit counter untouched."""
    tmp = os.path.dirname(env["NFM_MONITOR_LOG"])
    rows = [
        (MAIN_P1[0], "P1", "120ms", "HTTP 502: Bad Gateway"),
        (MAIN_P0, "P0", "234ms", "HTTP 530: "),
        (VERIFY_P0, "P0", "402ms", "HTTP 530: "),
    ]
    env["NFM_MONITOR_PROBE"] = _probe(tmp, _report(rows))
    run_script(env)  # streak 1, 3 edge failed < 5 — no s1 match
    run_script(env)  # streak 2 — existing lane CRITICAL
    assert "CRITICAL — public-edge P0 failure confirmed (2 consecutive" in log_text(env)
    assert s1_page_lines(env) == []
    s1 = s1_state_dict(env)
    assert s1.get("last_page", "0") == "0"


def test_s1_page_preserves_streak_lane_reconciliation(env):
    """AC4: after a streak-1 page, the existing recovery/PAGE-UNACK
    reconciliation is unchanged (pending latch survives to a green run)."""
    tmp = os.path.dirname(env["NFM_MONITOR_LOG"])
    env["NFM_MONITOR_PROBE"] = _probe(tmp, _report(_fulledge_502_530()))
    run_script(env)
    env["NFM_MONITOR_PROBE"] = _probe_green(tmp)
    r = run_script(env)
    assert r.returncode == 0, (r.returncode, log_text(env))
    lt = log_text(env)
    assert "RECOVERY — all checks passing again" in lt
    assert "PAGE-UNACK" in lt


# ------------------------------------------------- AC2 rate limiter


def test_rate_limit_suppresses_second_page_within_30min(env):
    """AC2: 1 page / 30 min — an immediate second match (new streak-1 after
    a <10 min green gap) is suppressed with a NOTE, counter unchanged, and
    the ledger does not double-count the same episode."""
    tmp = os.path.dirname(env["NFM_MONITOR_LOG"])
    env["NFM_MONITOR_PROBE"] = _probe(tmp, _report(_fulledge_502_530()))
    assert run_script(env).returncode == 2
    last_page_first = s1_state_dict(env)["last_page"]
    env["NFM_MONITOR_PROBE"] = _probe_green(tmp)
    run_script(env)  # closes the episode (green gap so far < 10 min)
    env["NFM_MONITOR_PROBE"] = _probe(tmp, _report(_fulledge_502_530()))
    r = run_script(env)
    assert r.returncode == 1, (r.returncode, log_text(env))  # suppressed => WARN path
    lt = log_text(env)
    assert len(s1_page_lines(env)) == 1
    assert "streak-1 full-edge match NOT paged (rate limit" in lt
    s1 = s1_state_dict(env)
    assert s1["last_page"] == last_page_first
    assert len(s1["ledger"].split()) == 1  # same episode: no double count


def test_rate_limit_expires_after_cooldown(env):
    """AC2: after the 30-min cooldown a fresh match pages again."""
    tmp = os.path.dirname(env["NFM_MONITOR_LOG"])
    now = time.time()
    s1_write_seed(env, last_page=str(int(now - PAGE_COOLDOWN_S - 1)),
                  close_epoch=str(int(now - EPISODE_GREEN_GAP_S - 1)))
    env["NFM_MONITOR_PROBE"] = _probe(tmp, _report(_fulledge_502_530()))
    r = run_script(env)
    assert r.returncode == 2, (r.returncode, log_text(env))
    assert len(s1_page_lines(env)) == 1
    assert int(s1_state_dict(env)["last_page"]) >= int(now)


# ------------------------------------------------- T2 ledger (ruling §2 / amendment 1)


def test_ledger_counts_distinct_episode_after_10min_green(env):
    """Ruling §2: windows separated by >=10 min green count once each."""
    tmp = os.path.dirname(env["NFM_MONITOR_LOG"])
    now = time.time()
    old = _iso(now - 3600)
    s1_write_seed(env, ledger=old, close_epoch=str(int(now - EPISODE_GREEN_GAP_S - 1)))
    env["NFM_MONITOR_PROBE"] = _probe(tmp, _report(_fulledge_502_530()))
    run_script(env)
    line = s1_page_lines(env)[0]
    assert "episodes=2" in line and "delta +1" in line
    assert len(s1_state_dict(env)["ledger"].split()) == 2


def test_ledger_no_double_count_under_10min_green_gap(env):
    """Ruling §2: a resumed window after <10 min green is the SAME episode
    — no second ledger entry."""
    tmp = os.path.dirname(env["NFM_MONITOR_LOG"])
    now = time.time()
    old = _iso(now - 3600)
    s1_write_seed(env, ledger=old, close_epoch=str(int(now - 300)))
    env["NFM_MONITOR_PROBE"] = _probe(tmp, _report(_fulledge_502_530()))
    run_script(env)
    line = s1_page_lines(env)[0]
    assert "episodes=1" in line and "delta +0" in line
    assert len(s1_state_dict(env)["ledger"].split()) == 1


def test_ledger_prunes_rolling_30d(env):
    """Ledger entries older than 30d drop out of the count."""
    tmp = os.path.dirname(env["NFM_MONITOR_LOG"])
    now = time.time()
    stale = _iso(now - (LEDGER_WINDOW_DAYS * 86400) - 3600)
    fresh = _iso(now - 5 * 86400)
    s1_write_seed(env, ledger=f"{stale} {fresh}",
                  close_epoch=str(int(now - EPISODE_GREEN_GAP_S - 1)))
    env["NFM_MONITOR_PROBE"] = _probe(tmp, _report(_fulledge_502_530()))
    run_script(env)
    line = s1_page_lines(env)[0]
    assert "episodes=2" in line  # fresh kept + the new one; stale pruned
    ledger = s1_state_dict(env)["ledger"].split()
    assert stale not in ledger and len(ledger) == 2


def test_open_episode_continues_window_no_recount(env):
    """open==1 (episode in progress): the window's first-failure timestamp
    is CONTINUED (not restarted) and the ledger is not appended again —
    even when the rate limiter would allow a fresh page."""
    tmp = os.path.dirname(env["NFM_MONITOR_LOG"])
    now = time.time()
    first = _iso(now - 240)
    s1_write_seed(env, open="1", win_first=first, ledger=first, last_page="0")
    env["NFM_MONITOR_PROBE"] = _probe(tmp, _report(_fulledge_502_530()))
    r = run_script(env)
    assert r.returncode == 2, (r.returncode, log_text(env))
    line = s1_page_lines(env)[0]
    assert f"window {first} -> recovery pending" in line
    assert "episodes=1" in line and "delta +0" in line
    s1 = s1_state_dict(env)
    assert s1["win_first"] == first
    assert len(s1["ledger"].split()) == 1


def test_malformed_ledger_entries_are_kept(env):
    """Fail-closed for evidence: a malformed ledger entry is never dropped
    by pruning (kept verbatim), and counting still proceeds."""
    tmp = os.path.dirname(env["NFM_MONITOR_LOG"])
    now = time.time()
    s1_write_seed(env, ledger="garbage-not-a-date",
                  close_epoch=str(int(now - EPISODE_GREEN_GAP_S - 1)))
    env["NFM_MONITOR_PROBE"] = _probe(tmp, _report(_fulledge_502_530()))
    r = run_script(env)
    assert r.returncode == 2, (r.returncode, log_text(env))
    s1 = s1_state_dict(env)
    assert "garbage-not-a-date" in s1["ledger"].split()
    assert len(s1["ledger"].split()) == 2


def test_recovery_line_completes_window_timestamps(env):
    """AC3: window timestamps first-failure -> recovery appear as a pair
    (page carries first-failure; the green run completes the window)."""
    tmp = os.path.dirname(env["NFM_MONITOR_LOG"])
    env["NFM_MONITOR_PROBE"] = _probe(tmp, _report(_fulledge_502_530()))
    run_script(env)
    env["NFM_MONITOR_PROBE"] = _probe_green(tmp)
    run_script(env)
    m = re.search(r"window (\S+) -> recovery pending", s1_page_lines(env)[0])
    assert m, s1_page_lines(env)
    first = m.group(1)
    lt = log_text(env)
    assert re.search(
        r"RECOVERY — streak-1 full-edge episode closed: window %s -> (\S+)" % re.escape(first), lt
    ), lt
    assert s1_state_dict(env)["open"] == "0"


# ------------------------------------------------- static consistency


def test_edge_name_map_matches_health_check_targets():
    """Drift guard: the sentinel's edge-hostname map must cover exactly the
    P0/P1 targets of health_check.py (a renamed/added target would silently
    break conjuncts 1-3)."""
    import importlib.util
    import sys
    spec = importlib.util.spec_from_file_location("health_check", HEALTH_CHECK)
    hc = importlib.util.module_from_spec(spec)
    sys.modules["health_check"] = hc  # dataclasses requires the module in sys.modules
    spec.loader.exec_module(hc)
    edge_targets = {t.name for t in hc.TARGETS if t.severity in ("P0", "P1")}

    with open(SCRIPT, encoding="utf-8") as fh:
        src = fh.read()
    m = re.search(r"s1_host_of\(\) \{.*?# end s1_host_of", src, re.S)
    assert m, "sentinel lost its s1_host_of edge-name map"
    mapped = set(re.findall(r'"([A-Za-z0-9 ]+)"', m.group(0)))
    assert mapped == edge_targets, (mapped, edge_targets)
