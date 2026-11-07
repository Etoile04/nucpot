"""Tests for scripts/nfm-site-monitor.sh — NFM-5892 streak>=2 episode booking.

Defect (e9, 2026-11-07 03:20Z -> 03:33Z, parent NFM-5890 / ticket NFM-5892):
the first REAL episode to enter full-edge via lane-by-lane escalation
(streak-1 PARTIAL_DEGRADED-shaped 4/6 -> streak-2 full-edge 5/6) paged via
the streak>=2 lane with NO s1 call — no t2-ledger append, no episode count,
no rate-limit marker, no open window. The NFM-5664 ruling 35aa85eb §4
machinery was evaluated only at streak==1, so every rule keyed on episode
counts / delta accounting was blind to the episode.

Fix under test: on the streak>=2 CRITICAL paths (public-edge +
PARTIAL_DEGRADED + their ongoing-reminder variants), when the s1 conjuncts
match AND no window is open, s1_book_streak2 books the episode AT the
CRITICAL tick and the t2-ledger count + §4 ref + rate-limit marker ride the
CRITICAL line. The page stays on the streak>=2 lane (no double-page); the
stamped last_page suppresses the streak-1 lane's own page if the same
episode re-enters at streak 1 within the cooldown. TRANSIT_BROWNOUT ticks
never book. The streak-1 path is byte-for-byte unchanged (covered by
test_nfm_site_monitor_streak1_page.py — the full suite stays green).

Fixture-replay harness reuses the NFM-5497/NFM-5667 shape: the sentinel
runs for real against env seams, with NFM_MONITOR_PROBE swapped for a
canned health_check.py-format report. The fixtures replay e9's exact two
tick shapes (03:20:09Z PD-shaped 4/6 with 2 P0, 03:26:02Z full-edge 5/6
with 2 P0 — hard codes, both hostnames).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from test_nfm_site_monitor import log_text, mock_site, run_script, state_dict

SCRIPT = Path(__file__).resolve().parent.parent / "nfm-site-monitor.sh"

# TARGETS order (drift-guard: health_check.py::TARGETS must keep these names).
CACHED_LANES = ("Frontend Homepage", "Frontend Browse Page")
TUNNEL_LANE = "Backend API Health"
OTHER_LANES = ("Backend API Potentials", "Backend API Reference Values")
VERIFY_LANE = "AutoVC API Health"
ALL_NAMES = (*CACHED_LANES, TUNNEL_LANE, *OTHER_LANES, VERIFY_LANE, "Supabase Health")

BROWNOUT_LAT = 1.5  # same staging constant as test_nfm_site_monitor_refs

_counter = iter(range(10000))


@pytest.fixture(autouse=True)
def _isolate_s1_state(env):
    """NFM-5888 discipline: s1-matching tests MUST set NFM_MONITOR_S1_STATE.

    The base env fixture predates the S1 seam; without this, a booking would
    append to the PRODUCTION ~/.nfm-site-monitor.s1.state — a false
    T2-ledger episode and a live rate-limit write (the exact NFM-5876
    incident class the three-lane file's isolation comment documents).
    """
    env["NFM_MONITOR_S1_STATE"] = str(Path(env["NFM_MONITOR_LOG"]).parent / "monitor.s1.state")


@pytest.fixture()
def env(tmp_path, mock_site):
    """base seams + the s1-lane state seam + a recording notify lane +
    origin-green (the PD conjunct needs origin-direct answering 200)."""
    base, _config = mock_site
    probe = tmp_path / "probe_ok.py"
    probe.write_text('print("3 check(s) passed")\n', encoding="utf-8")
    notify = tmp_path / "notify.sh"
    notify.write_text(
        "#!/bin/bash\necho \"$*\" >> " + str(tmp_path / "notify.log") + "\n",
        encoding="utf-8",
    )
    notify.chmod(0o755)
    return {
        "NFM_SCAN_BASE": base,
        "NFM_SCAN_DEADLINE": "240",
        "NFM_SCAN_REF_POOL": "4",
        "NO_PROXY": "127.0.0.1,localhost",
        "no_proxy": "127.0.0.1,localhost",
        "NFM_MONITOR_LOG": str(tmp_path / "monitor.log"),
        "NFM_MONITOR_STATE": str(tmp_path / "monitor.state"),
        "NFM_MONITOR_S1_STATE": str(tmp_path / "monitor.s1.state"),
        "NFM_MONITOR_PROBE": str(probe),
        "NFM_MONITOR_NOTIFY": str(notify),
        "NFM_MONITOR_REF_STATE": str(tmp_path / "monitor.ref.state"),
        "NFM_REF_CF_URL": base + "/ref-cf",
        "NFM_REF_NONCF_URL": base + "/ref-ncf",
        "NFM_REF_DOM_URL": base + "/ref-dom",
        "NFM_REF_TIMEOUT": "5",
        "NFM_REF_BASELINE_MIN": "3",
        "NFM_ORIGIN_BASE": base,
    }


# ------------------------------------------------------------------ helpers


def _require_booking_seam() -> None:
    """Fail fast if the NFM-5892 booking seam is gone (base guard pattern)."""
    src = SCRIPT.read_text(encoding="utf-8")
    for marker in ("s1_book_streak2", "s2_book_ev", "NFM_MONITOR_S1_STATE" + ":-"):
        if marker not in src:
            pytest.fail(f"script lost its NFM-5892 seam ({marker!r}) — refusing to run")


def _report(failed_rows):
    """health_check.py stdout shape WITH the full table (✅ + ❌ rows) —
    the lane parser reads passing rows too."""
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


def _pd_shape_4of6():
    """e9 03:20:09Z — PARTIAL_DEGRADED-shaped streak-1 tick: tunnel + verify
    (the 2 P0) + both other API lanes dark, both cached HTML lanes green,
    origin green. 4/6 edge checks failed, 2 P0 => s1_match=0 (4 < conjunct
    5): correctly NO booking at streak 1 (pre-fix behavior preserved)."""
    return [
        (TUNNEL_LANE, "P0", "234ms", "HTTP 530: "),
        (VERIFY_LANE, "P0", "402ms", "HTTP 530: "),
        (OTHER_LANES[0], "P1", "230ms", "HTTP 530: "),
        (OTHER_LANES[1], "P1", "228ms", "HTTP 530: "),
    ]


def _fulledge_5of6():
    """e9 03:26:02Z — full-edge streak-2 tick: tunnel (P0) + verify (P0) +
    both cached HTML lanes + one API lane dark, hard codes, one API lane
    green. 5/6 edge checks failed, 2 P0, both hostnames, hard-dominant =>
    s1_match=1, verdict FULL_EDGE_OUTAGE (tunnel + cached both fail)."""
    return [
        (CACHED_LANES[0], "P1", "120ms", "HTTP 502: Bad Gateway"),
        (CACHED_LANES[1], "P1", "118ms", "HTTP 502: Bad Gateway"),
        (TUNNEL_LANE, "P0", "234ms", "HTTP 530: "),
        (OTHER_LANES[0], "P1", "125ms", "HTTP 502: Bad Gateway"),
        (VERIFY_LANE, "P0", "402ms", "HTTP 530: "),
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


def ref_state_dict(env):
    out = {}
    try:
        with open(env["NFM_MONITOR_REF_STATE"], encoding="utf-8") as fh:
            for line in fh:
                if "=" in line:
                    k, v = line.rstrip("\n").split("=", 1)
                    out[k] = v
    except FileNotFoundError:
        pass
    return out


def notify_lines(env):
    try:
        with open(os.path.dirname(env["NFM_MONITOR_LOG"]) + "/notify.log", encoding="utf-8") as fh:
            return [ln for ln in fh.read().splitlines() if ln.strip()]
    except FileNotFoundError:
        return []


def crit_lines(env):
    return [ln for ln in log_text(env).splitlines() if "CRITICAL — public-edge P0 failure confirmed" in ln]


def t2_evidence_lines(env):
    """CRITICAL-line booking evidence (suffix carries both markers); the
    streak-1 NOTE line says 'rate limit 1800s' and never matches."""
    return [ln for ln in log_text(env).splitlines() if "t2-ledger" in ln and "rate-limit 1/" in ln]


def _seed_state(env, **overrides):
    """Rewrite the monitor state file in place (keys preserved unless
    overridden) — used to reach the reminder cadence without 12 real ticks."""
    current = {}
    path = Path(env["NFM_MONITOR_STATE"])
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                current[k] = v
    except FileNotFoundError:
        pass
    current.update(overrides)
    path.write_text(
        "".join(f"{k}={v}\n" for k, v in current.items()),
        encoding="utf-8",
    )


# ------------------------------------------------------------ seam guard


def test_seam_guard_requires_booking_seam(env):
    """The booking lane must keep its seams or tests could hit prod state."""
    _require_booking_seam()


# ------------------------------------------- AC1/AC2/AC3: the e9 regression


def test_streak1_pd_shape_then_streak2_fulledge_books_exactly_one_episode(env):
    """AC4 core: streak-1 PD-shaped (4/6, 2 P0) -> streak-2 full-edge (5/6,
    2 P0) books EXACTLY ONE episode — one ledger append, one window open,
    one page on the streak>=2 lane, evidence suffix on the CRITICAL line."""
    tmp = os.path.dirname(env["NFM_MONITOR_LOG"])
    # tick 1 — streak 1, PD-shaped: s1_match=0, no booking, PD WARN path
    env["NFM_MONITOR_PROBE"] = _probe(tmp, _report(_pd_shape_4of6()))
    r1 = run_script(env)
    assert r1.returncode == 1, (r1.returncode, log_text(env))
    lt1 = log_text(env)
    assert "WARN — PARTIAL_DEGRADED" in lt1
    assert "CRITICAL" not in lt1
    assert t2_evidence_lines(env) == []          # no booking evidence anywhere
    s1 = s1_state_dict(env)
    assert s1.get("open", "0") == "0"
    assert s1.get("ledger", "") == ""            # streak-1 PD tick books nothing
    assert s1.get("last_page", "0") == "0"
    assert notify_lines(env) == []               # streak-1 PD: WARN only, no page
    # tick 2 — streak 2, full-edge: books at the CRITICAL tick
    env["NFM_MONITOR_PROBE"] = _probe(tmp, _report(_fulledge_5of6()))
    r2 = run_script(env)
    assert r2.returncode == 2, (r2.returncode, log_text(env))
    lt = log_text(env)
    crit = crit_lines(env)
    assert len(crit) == 1
    # AC2: t2-ledger count + ruling §4 ref + rate-limit marker on the line
    assert "t2-ledger: episodes=1 rolling-30d (delta +1" in crit[0]
    assert "rate-limit 1/1800s" in crit[0]
    assert "NFM-5664 ruling 35aa85eb §4" in crit[0]
    # AC3: exactly one page on the streak>=2 lane
    assert len(notify_lines(env)) == 1
    assert "P0 public-edge failure" in notify_lines(env)[0]
    # AC1: booking state landed at the CRITICAL tick
    s1 = s1_state_dict(env)
    assert s1["open"] == "1"
    assert s1["last_page"] != "0"
    assert s1["win_first"] != "-"
    assert len(s1["ledger"].split()) == 1
    assert state_dict(env)["level"] == "CRITICAL"
    assert state_dict(env)["streak"] == "2"
    assert state_dict(env)["pending_page"] not in ("-", "")


def test_booking_close_path_completes_the_episode(env):
    """Spec point 2: once open=1, the existing close path books the episode
    close automatically on the next green tick — no close-path change."""
    tmp = os.path.dirname(env["NFM_MONITOR_LOG"])
    env["NFM_MONITOR_PROBE"] = _probe(tmp, _report(_pd_shape_4of6()))
    run_script(env)
    env["NFM_MONITOR_PROBE"] = _probe(tmp, _report(_fulledge_5of6()))
    assert run_script(env).returncode == 2
    env["NFM_MONITOR_PROBE"] = _probe_green(tmp)
    r = run_script(env)
    assert r.returncode == 0, (r.returncode, log_text(env))
    lt = log_text(env)
    assert "RECOVERY — all checks passing again after 2 failing probe(s)" in lt
    assert "RECOVERY — streak-1 full-edge episode closed: window" in lt
    s1 = s1_state_dict(env)
    assert s1["open"] == "0"
    assert s1["close_epoch"] != "0"
    assert len(s1["ledger"].split()) == 1        # close never re-appends


# ------------------------------------------- fix spec point 3: idempotence


def test_repeated_failing_ticks_never_reappend_while_window_open(env):
    """Fix spec 3: subsequent streak>=N ticks while the window is open must
    NOT re-append the ledger (window-open gate) — exactly one t2-ledger
    evidence line per episode, last_page stamped only once."""
    tmp = os.path.dirname(env["NFM_MONITOR_LOG"])
    env["NFM_MONITOR_PROBE"] = _probe(tmp, _report(_pd_shape_4of6()))
    run_script(env)                              # streak 1 PD — no booking
    env["NFM_MONITOR_PROBE"] = _probe(tmp, _report(_fulledge_5of6()))
    assert run_script(env).returncode == 2       # streak 2 — books
    s1_after_booking = s1_state_dict(env)
    env["NFM_MONITOR_PROBE"] = _probe(tmp, _report(_fulledge_5of6()))
    r3 = run_script(env)                         # streak 3 — must not re-book
    assert r3.returncode == 2, (r3.returncode, log_text(env))
    s1 = s1_state_dict(env)
    assert s1["ledger"] == s1_after_booking["ledger"]
    assert s1["last_page"] == s1_after_booking["last_page"]
    assert s1["open"] == "1"
    assert s1["win_first"] == s1_after_booking["win_first"]
    assert len(t2_evidence_lines(env)) == 1      # one evidence line per episode


def test_ongoing_reminder_variant_books_once_then_stays_quiet(env):
    """AC1/AC2 (reminder variant): with the reminder cadence reached, the
    ongoing-reminder CRITICAL line carries the evidence suffix at the
    booking tick; a later reminder tick (window open) stays suffix-free and
    never re-appends."""
    tmp = os.path.dirname(env["NFM_MONITOR_LOG"])
    # seed the monitor state near the reminder cadence (REMINDER_RUNS=12)
    _seed_state(env, streak="0", since_remind="11")
    env["NFM_MONITOR_PROBE"] = _probe(tmp, _report(_pd_shape_4of6()))
    assert run_script(env).returncode == 1       # streak 1 PD WARN (since_remind -> 12)
    env["NFM_MONITOR_PROBE"] = _probe(tmp, _report(_fulledge_5of6()))
    r2 = run_script(env)                         # streak 2: since_remind=13 >= 12 -> reminder
    assert r2.returncode == 2, (r2.returncode, log_text(env))
    reminder = [ln for ln in log_text(env).splitlines() if "CRITICAL (ongoing reminder" in ln]
    assert len(reminder) == 1
    assert "t2-ledger: episodes=1 rolling-30d (delta +1" in reminder[0]
    assert "rate-limit 1/1800s" in reminder[0]
    assert len(s1_state_dict(env)["ledger"].split()) == 1
    # re-seed the cadence (the reminder branch reset since_remind to 0) and
    # take one more failing tick: reminder fires again, booking does not
    _seed_state(env, since_remind="11")
    env["NFM_MONITOR_PROBE"] = _probe(tmp, _report(_fulledge_5of6()))
    assert run_script(env).returncode == 2
    reminders = [ln for ln in log_text(env).splitlines() if "CRITICAL (ongoing reminder" in ln]
    assert len(reminders) == 2
    assert "t2-ledger" not in reminders[1]
    assert len(s1_state_dict(env)["ledger"].split()) == 1
    assert len(t2_evidence_lines(env)) == 1


# ------------------------------------------- AC3: no double-page per episode


def test_streak1_reentry_within_cooldown_never_double_pages(env):
    """AC3 mechanism: after a streak>=2 booking + green close, the same
    episode re-entering at streak 1 within the 1/1800s cooldown is
    suppressed by the stamped last_page — NOTE, no page, no ledger append
    (<10 min green gap = same episode)."""
    tmp = os.path.dirname(env["NFM_MONITOR_LOG"])
    env["NFM_MONITOR_PROBE"] = _probe(tmp, _report(_pd_shape_4of6()))
    run_script(env)
    env["NFM_MONITOR_PROBE"] = _probe(tmp, _report(_fulledge_5of6()))
    assert run_script(env).returncode == 2       # booking + page
    env["NFM_MONITOR_PROBE"] = _probe_green(tmp)
    assert run_script(env).returncode == 0       # close
    assert len(notify_lines(env)) == 1
    env["NFM_MONITOR_PROBE"] = _probe(tmp, _report(_fulledge_5of6()))
    r = run_script(env)                          # streak 1 fulledge re-entry
    assert r.returncode == 1, (r.returncode, log_text(env))
    lt = log_text(env)
    assert "streak-1 full-edge match NOT paged (rate limit" in lt
    assert len(notify_lines(env)) == 1           # still exactly one page
    s1 = s1_state_dict(env)
    assert len(s1["ledger"].split()) == 1        # same episode: no double count
    assert s1["open"] == "1"                     # window resumed for the episode


# ------------------------------------------- AC5: non-matching lanes unchanged


def test_pd_escalation_books_nothing(env):
    """PD escalation to streak 2 (2/6 edge — PD page rule owns it): no
    booking, no suffix, exactly one page from the PD lane, s1 untouched."""
    tmp = os.path.dirname(env["NFM_MONITOR_LOG"])
    env["NFM_MONITOR_PROBE"] = _probe(tmp, _report(_pd_shape_4of6()[:2]))
    assert run_script(env).returncode == 1
    r2 = run_script(env)
    assert r2.returncode == 2, (r2.returncode, log_text(env))
    lt = log_text(env)
    assert "CRITICAL — PARTIAL_DEGRADED page (NFM-5876 rule" in lt
    assert "t2-ledger" not in lt
    assert len(notify_lines(env)) == 1
    assert s1_state_dict(env) == {}              # no s1 state write at all


def test_fulledge_below_conjuncts_books_nothing(env):
    """FULL_EDGE_OUTAGE escalation whose shape is short of the s1 conjuncts
    (4/6 edge, 1 P0): the existing lane pages byte-identically (no suffix)
    and the s1 state is never touched."""
    tmp = os.path.dirname(env["NFM_MONITOR_LOG"])
    rows = [(n, s, ms, d) for (n, s, ms, d) in _fulledge_5of6() if n != VERIFY_LANE]
    env["NFM_MONITOR_PROBE"] = _probe(tmp, _report(rows))
    assert run_script(env).returncode == 1       # streak 1 generic WARN
    r2 = run_script(env)
    assert r2.returncode == 2, (r2.returncode, log_text(env))
    crit = crit_lines(env)
    assert len(crit) == 1
    assert "t2-ledger" not in crit[0]            # no suffix: conjuncts unmatched
    assert len(notify_lines(env)) == 1
    assert s1_state_dict(env) == {}


def test_transit_brownout_tick_never_books(env, mock_site):
    """TRANSIT_BROWNOUT suppression exits BEFORE the booking call: a
    transit-side tick at streak>=2 books nothing — ledger unchanged, no
    evidence suffix, no page — even though the failure shape matches the s1
    conjuncts. The booking baseline (a real booking on the same shape with
    a green classification) is established first, inside this test."""
    _base, config = mock_site
    tmp = os.path.dirname(env["NFM_MONITOR_LOG"])
    # episode entry via the >=2 lane: PD-shaped streak 1, fulledge streak 2
    env["NFM_MONITOR_PROBE"] = _probe(tmp, _report(_pd_shape_4of6()))
    run_script(env)
    env["NFM_MONITOR_PROBE"] = _probe(tmp, _report(_fulledge_5of6()))
    assert run_script(env).returncode == 2       # books (classification warmup/fail-open)
    assert len(s1_state_dict(env)["ledger"].split()) == 1
    # close, then prime the reference baselines: the battery accrues on
    # every completed tick (failing ones included), so two extra greens put
    # every lane past BASELINE_MIN=3 — assert trusted, not an exact count
    env["NFM_MONITOR_PROBE"] = _probe_green(tmp)
    for _ in range(2):
        assert run_script(env).returncode == 0
    assert int(ref_state_dict(env)["dom_count"]) >= 3
    # stage the brownout: both int'l lanes slow, domestic control pristine
    config["refs"]["/ref-cf"] = {"lat": BROWNOUT_LAT}
    config["refs"]["/ref-ncf"] = {"lat": BROWNOUT_LAT}
    # streak 1 fulledge re-entry: the streak-1 lane is rate-limited by the
    # booking's stamped last_page (NOTE, resume same episode, no page) —
    # pre-existing behavior, and the no-double-page mechanism in action
    env["NFM_MONITOR_PROBE"] = _probe(tmp, _report(_fulledge_5of6()))
    assert run_script(env).returncode == 1
    assert "streak-1 full-edge match NOT paged (rate limit" in log_text(env)
    # streak 2 under an active TRANSIT_BROWNOUT classification: WARN, no page
    env["NFM_MONITOR_PROBE"] = _probe(tmp, _report(_fulledge_5of6()))
    r = run_script(env)
    assert r.returncode == 1, (r.returncode, log_text(env))
    lt = log_text(env)
    assert "WARN — TRANSIT_BROWNOUT" in lt
    s1 = s1_state_dict(env)
    assert len(s1["ledger"].split()) == 1        # brownout never books
    assert len(notify_lines(env)) == 1           # no page from the brownout tick
    assert len(t2_evidence_lines(env)) == 1      # only the original booking
    assert s1["open"] == "1"                     # from the rate-limited resume,
    assert s1["win_first"] != "-"                # not from any brownout booking


# ------------------------------------------------- static consistency


def test_streak1_paging_path_untouched_static():
    """AC5 static guard: the streak-1 call site and its gate are untouched —
    the ONLY new call into the s1 machinery sits inside the streak>=2 lane
    after the brownout exit, gated on the classified conjuncts."""
    src = SCRIPT.read_text(encoding="utf-8")
    assert 'if [[ "$p0" != "0" ]] && (( streak == 1 )); then' in src
    assert 's1_handle_match "$streak" "$first_fail" "$since_remind"' in src
    assert src.count("s1_book_streak2() {") == 1
    # the booking call is gated on s1_match — never bare
    call_site = src.index("      s1_book_streak2")
    assert "s1_match == 1" in src[call_site - 200:call_site]
    # the booking call site sits AFTER the streak-1 gate in the file
    assert src.index('(( streak == 1 ))') < call_site
