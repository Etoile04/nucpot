"""Tests for scripts/nfm-site-monitor.sh — NFM-5966 replica liveness lane.

CPO dispatch (D2-G3, parent NFM-5963): a fourth evidence lane probing the
z203 HA replica (cloudflared-nucpot-replica.service + nfm-origin-forwarder.
service) over tailscale SSH — one read-only battery per tick, with the
binding severity shape:

- replica-dark ALONE (primary green) = WARN lane entry per tick, NOT a page:
  no notify, no CRITICAL, no pending_page latch, no streak>=2 machinery,
  exit code unchanged (0 on an otherwise green tick);
- replica-dark AND primary-dark = the existing primary handling (the lane
  stays evidence-only on degrade ticks — no replica WARN, streak frozen);
- probe-dependency honesty: ssh_fail / unknown / notfound can NEVER classify
  anything (fail-open) — the lane adds no conjunct to the NFM-5876 matrix,
  so it is structurally impossible for it to create or suppress a
  PARTIAL_DEGRADED;
- NFM-5872 tripwire 3 (tunnel-dark + cached-dark => FULL_EDGE_OUTAGE
  CRITICAL, never PD) stays byte-identical with the lane present;
- streak bookkeeping lives ONLY in the replica lane's own state file
  (NFM_MONITOR_REPLICA_STATE); every pre-existing state_write call site
  stays byte-identical (asserted via the main state's key set).

Harness: same fixture-replay shape as the three-lane suite — the sentinel
runs for real against env seams with NFM_REPLICA_SSH swapped for a stub
`ssh` whose stdout is the canned z203 battery output.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import test_nfm_site_monitor as base
from test_nfm_site_monitor import (  # noqa: F401 — fixtures re-exported
    env,
    log_text,
    mock_site,
    reg_log_lines,
    replica_battery,
    run_script,
    state_dict,
    write_replica_stub_ssh,
    write_replica_stub_ssh_fail,
)
from test_nfm_site_monitor_three_lane import (  # noqa: F401 — fixture re-export
    _fulledge_fixture,
    _pd_fixture,
    _probe,
    _report,
    CACHED_LANES,
    TUNNEL_LANE,
)

SCRIPT = Path(base.SCRIPT)


# ------------------------------------------------------------------ helpers


@pytest.fixture(autouse=True)
def _isolate_lane_state(env):
    """Isolate s1 + replica lane state per test (the NFM-5876 incident
    class: a suite without its seam writes the PRODUCTION state file)."""
    parent = Path(env["NFM_MONITOR_LOG"]).parent
    env["NFM_MONITOR_S1_STATE"] = str(parent / "monitor.s1.state")
    env["NFM_MONITOR_REPLICA_STATE"] = str(parent / "monitor.replica.state")


def _require_lane_seams() -> None:
    src = SCRIPT.read_text(encoding="utf-8")
    for marker in (
        "NFM_MONITOR_REPLICA_STATE:-",
        "NFM_REPLICA_SSH:-",
        "NFM_REPLICA_TARGET:-",
        "NFM_REPLICA_TIMEOUT:-",
        "NFM_REPLICA_FWD_URL:-",
        "replica_probes()",
        "REPLICA — $REPLICA_OUT",
        "--replica-probes-once",
    ):
        if marker not in src:
            pytest.fail(f"script lost its NFM-5966 replica-lane marker ({marker!r})")


def _origin_green(env, mock_site):
    env["NFM_ORIGIN_BASE"] = mock_site[0]


def _green_probe(env):
    tmp = Path(env["NFM_MONITOR_LOG"]).parent
    env["NFM_MONITOR_PROBE"] = _probe(tmp, _report([]), rc=0)


def _replica_lines(env):
    return [ln for ln in log_text(env).splitlines() if "REPLICA — replica: " in ln]


def _replica_state(env):
    out = {}
    try:
        with open(env["NFM_MONITOR_REPLICA_STATE"], encoding="utf-8") as fh:
            for line in fh:
                if "=" in line:
                    k, v = line.rstrip("\n").split("=", 1)
                    out[k] = v
    except FileNotFoundError:
        pass
    return out


def _set_battery(env, **kw):
    env["NFM_REPLICA_SSH"] = write_replica_stub_ssh(
        Path(env["NFM_MONITOR_LOG"]).parent, replica_battery(**kw)
    )


# ------------------------------------------------------------------ seam guard


def test_replica_lane_seams_present(env):
    _require_lane_seams()


# ----------------------------------------------------- evidence line + verdict


def test_green_tick_logs_replica_line_ok_newest_byte_stable(env, mock_site):
    """Green tick: REPLICA evidence line after LANES, OK line stays newest
    and byte-stable; verdict ok with fresh registration."""
    _origin_green(env, mock_site)
    _set_battery(env, rep_load="loaded", rep_active="active",
                 fwd_load="loaded", fwd_active="active",
                 log_lines=reg_log_lines(60), fwd_http="200 0.004")
    _green_probe(env)

    r = run_script(env)
    assert r.returncode == 0, (r.returncode, log_text(env))

    lines = log_text(env).strip().splitlines()
    repl = _replica_lines(env)
    assert repl, "REPLICA evidence line missing on a green tick"
    assert "verdict=ok" in repl[-1]
    assert "unit=active" in repl[-1] and "fwd=active" in repl[-1]
    lanes_idx = [i for i, ln in enumerate(lines) if "LANES — " in ln]
    ok_idx = [i for i, ln in enumerate(lines) if ln.endswith("OK — all checks passed")]
    assert lanes_idx[-1] < lines.index(repl[-1]) < ok_idx[-1]
    assert lines[-1] == lines[ok_idx[-1]], "OK line must stay the tick's newest line"
    assert "WARN" not in log_text(env), "no replica WARN on an ok verdict"
    assert _replica_state(env)["dark_streak"] == "0"


def test_units_notfound_is_silent_note_class(env, mock_site):
    """D2-G2 not yet deployed: verdict=notfound, NO WARN (board-known
    in-flight state must not noise the WARN class)."""
    _origin_green(env, mock_site)
    _set_battery(env)  # default: not-found
    _green_probe(env)

    r = run_script(env)
    assert r.returncode == 0
    assert "verdict=notfound" in _replica_lines(env)[-1]
    assert "WARN" not in log_text(env)
    assert _replica_state(env)["dark_streak"] == "0"


def test_ssh_fail_is_unknown_fail_open(env, mock_site):
    """Transport failure: no replica verdict — fail-open, no WARN, no state
    movement beyond the freeze."""
    _origin_green(env, mock_site)
    env["NFM_REPLICA_SSH"] = write_replica_stub_ssh_fail(Path(env["NFM_MONITOR_LOG"]).parent)
    _green_probe(env)

    r = run_script(env)
    assert r.returncode == 0
    assert "verdict=unknown" in _replica_lines(env)[-1]
    assert "ssh_fail" in _replica_lines(env)[-1]
    assert "WARN" not in log_text(env)
    assert _replica_state(env)["dark_streak"] == "0"


def test_perm_denied_registration_is_unknown_fail_open(env, mock_site):
    """Units active but the registration log unreadable: verdict unknown —
    absence of a replica verdict never classifies dark."""
    _origin_green(env, mock_site)
    _set_battery(env, rep_load="loaded", rep_active="active",
                 fwd_load="loaded", fwd_active="active",
                 log_lines=["tail: cannot open '/var/log/cloudflared-nucpot-replica.log': Permission denied"],
                 fwd_http="200 0.004")
    _green_probe(env)

    r = run_script(env)
    assert r.returncode == 0
    assert "verdict=unknown" in _replica_lines(env)[-1]
    assert "perm_denied" in _replica_lines(env)[-1]
    assert "WARN" not in log_text(env)


# ------------------------------------------------------------- WARN lane entry


def test_replica_dark_warns_no_page(env, mock_site):
    """The binding severity shape: replica-dark ALONE (primary green) = ONE
    WARN lane entry per tick, NOT a page — rc 0, no notify, no latch, main
    state untouched."""
    tmp = Path(env["NFM_MONITOR_LOG"]).parent
    _origin_green(env, mock_site)
    _set_battery(env, rep_load="loaded", rep_active="inactive",  # loaded but down
                 fwd_load="loaded", fwd_active="active",
                 log_lines=reg_log_lines(4000), fwd_http="ERR(curl 7)")
    env["NFM_MONITOR_NOTIFY"] = str(tmp / "notify.sh")
    (tmp / "notify.sh").write_text("#!/bin/bash\necho \"$*\" >> " + str(tmp / "notify.log") + "\n")
    (tmp / "notify.sh").chmod(0o755)
    _green_probe(env)

    r = run_script(env)
    assert r.returncode == 0, (r.returncode, log_text(env))  # NOT a page: rc stays 0

    lt = log_text(env)
    warn = [ln for ln in lt.splitlines() if "WARN — REPLICA_DARK" in ln]
    assert len(warn) == 1
    assert "evidence WARN, no page" in warn[0]
    assert "dark streak 1" in warn[0]
    assert not [ln for ln in lt.splitlines() if ln.startswith("[") and "CRITICAL" in ln]
    # newest line is still the byte-stable green OK
    assert lt.strip().splitlines()[-1].endswith("OK — all checks passed")
    # no page machinery touched
    assert not (tmp / "notify.log").exists()
    assert state_dict(env)["pending_page"] == "-"
    assert state_dict(env)["level"] == "OK"
    assert state_dict(env)["streak"] == "0"
    # streak lives ONLY in the replica lane's own state file
    assert _replica_state(env) == {"dark_streak": "1", "first_dark": _replica_state(env)["first_dark"]}


def test_replica_dark_streak_accumulates_then_recovers(env, mock_site):
    """Second consecutive dark tick accumulates the streak; an ok tick logs
    REPLICA-RECOVERY and resets it."""
    _origin_green(env, mock_site)
    _green_probe(env)

    _set_battery(env, rep_load="loaded", rep_active="inactive",
                 fwd_load="loaded", fwd_active="active",
                 log_lines=reg_log_lines(4000))
    assert run_script(env).returncode == 0
    assert run_script(env).returncode == 0

    warn = [ln for ln in log_text(env).splitlines() if "WARN — REPLICA_DARK" in ln]
    assert len(warn) == 2
    assert "dark streak 2" in warn[-1]
    assert _replica_state(env)["dark_streak"] == "2"

    _set_battery(env, rep_load="loaded", rep_active="active",
                 fwd_load="loaded", fwd_active="active",
                 log_lines=reg_log_lines(30))
    r3 = run_script(env)
    assert r3.returncode == 0
    assert "REPLICA-RECOVERY — replica green again after 2 dark tick(s)" in log_text(env)
    assert _replica_state(env)["dark_streak"] == "0"


def test_reg_stale_while_units_active_is_dark(env, mock_site):
    """Both units systemd-active but the connector hasn't registered within
    N=600s: dark (the NFM-5880 rolling-staleness semantics)."""
    _origin_green(env, mock_site)
    _set_battery(env, rep_load="loaded", rep_active="active",
                 fwd_load="loaded", fwd_active="active",
                 log_lines=reg_log_lines(4000))
    _green_probe(env)

    assert run_script(env).returncode == 0
    assert "verdict=dark" in _replica_lines(env)[-1]
    assert "reg=" in _replica_lines(env)[-1]
    assert "WARN — REPLICA_DARK" in log_text(env)


def test_unknown_verdict_freezes_dark_streak(env, mock_site):
    """unknown/notfound freeze the streak: neither count nor reset (dark,
    unknown, dark => streak 2; then ok recovers with 2)."""
    _origin_green(env, mock_site)
    _green_probe(env)

    _set_battery(env, rep_load="loaded", rep_active="inactive",
                 fwd_load="loaded", fwd_active="active",
                 log_lines=reg_log_lines(4000))
    assert run_script(env).returncode == 0

    env["NFM_REPLICA_SSH"] = write_replica_stub_ssh_fail(Path(env["NFM_MONITOR_LOG"]).parent)
    assert run_script(env).returncode == 0
    assert _replica_state(env)["dark_streak"] == "1"  # frozen, not reset

    _set_battery(env, rep_load="loaded", rep_active="inactive",
                 fwd_load="loaded", fwd_active="active",
                 log_lines=reg_log_lines(4000))
    assert run_script(env).returncode == 0
    assert _replica_state(env)["dark_streak"] == "2"


# ------------------------------------------ degrade ticks: evidence-only, AC3


def test_degrade_tick_replica_is_evidence_only(env, mock_site):
    """Primary PD streak-1 + replica dark: the replica lane adds NO WARN on
    a degrade tick (the primary line carries the tick) and leaves the PD
    path byte-identical."""
    tmp = Path(env["NFM_MONITOR_LOG"]).parent
    _origin_green(env, mock_site)
    _set_battery(env, rep_load="loaded", rep_active="inactive",
                 fwd_load="loaded", fwd_active="active",
                 log_lines=reg_log_lines(4000))
    env["NFM_MONITOR_PROBE"] = _probe(tmp, _report(_pd_fixture()))

    r = run_script(env)
    assert r.returncode == 1, (r.returncode, log_text(env))

    lt = log_text(env)
    assert "verdict=dark" in _replica_lines(env)[-1]  # evidence present
    assert not [ln for ln in lt.splitlines() if "WARN — REPLICA_DARK" in ln], (
        "replica lane must stay evidence-only on a primary degrade tick"
    )
    assert "WARN — PARTIAL_DEGRADED" in lt  # primary path unchanged
    assert _replica_state(env) == {} or _replica_state(env).get("dark_streak", "0") == "0", (
        "streak must be frozen, not incremented, on a degrade tick"
    )


def test_full_edge_regression_with_replica_dark_untouched(env, mock_site):
    """NFM-5872 tripwire 3, WITH the replica lane present and dark:
    tunnel-dark + cached-dark still classifies FULL_EDGE_OUTAGE and pages
    via the existing CRITICAL path — the replica lane can neither create
    nor suppress it."""
    tmp = Path(env["NFM_MONITOR_LOG"]).parent
    _origin_green(env, mock_site)
    _set_battery(env, rep_load="loaded", rep_active="inactive",
                 fwd_load="loaded", fwd_active="active",
                 log_lines=reg_log_lines(4000))
    env["NFM_MONITOR_NOTIFY"] = str(tmp / "notify.sh")
    (tmp / "notify.sh").write_text("#!/bin/bash\necho \"$*\" >> " + str(tmp / "notify.log") + "\n")
    (tmp / "notify.sh").chmod(0o755)

    report = _report(_fulledge_fixture())
    env["NFM_MONITOR_PROBE"] = _probe(tmp, report)
    assert run_script(env).returncode == 1  # streak 1
    r2 = run_script(env)
    assert r2.returncode == 2, (r2.returncode, log_text(env))

    lt = log_text(env)
    crit = [ln for ln in lt.splitlines() if "CRITICAL — public-edge P0 failure confirmed" in ln]
    assert crit, "full-edge CRITICAL path must be byte-identical with the lane present"
    assert "verdict=FULL_EDGE_OUTAGE" in lt
    assert not [ln for ln in lt.splitlines() if "WARN — REPLICA_DARK" in ln]
    assert (tmp / "notify.log").exists()  # the page fired exactly as before
    assert state_dict(env)["pending_page"] != "-"


def test_pd_conjunct_untouched_by_replica_state(env, mock_site):
    """Structural fail-open proof: with the replica lane UNKNOWN (worst
    probe-dependency case) the PD matrix behaves exactly as NFM-5876 —
    streak 1 WARN, no page — and the verdict line is unchanged in shape."""
    tmp = Path(env["NFM_MONITOR_LOG"]).parent
    _origin_green(env, mock_site)
    env["NFM_REPLICA_SSH"] = write_replica_stub_ssh_fail(Path(env["NFM_MONITOR_LOG"]).parent)
    env["NFM_MONITOR_PROBE"] = _probe(tmp, _report(_pd_fixture()))

    r = run_script(env)
    assert r.returncode == 1
    lanes = [ln for ln in log_text(env).splitlines() if "LANES — " in ln][-1]
    assert "verdict=PARTIAL_DEGRADED" in lanes
    # the LANES line carries no replica token — the matrix is byte-identical
    assert "replica=" not in lanes


# ------------------------------------------------------------------ diagnostics


def test_replica_probes_once_touches_no_state(env, mock_site):
    """--replica-probes-once prints the token + verdict, writes no log and
    no state (SRE post-deploy verification without a tick)."""
    _set_battery(env, rep_load="loaded", rep_active="active",
                 fwd_load="loaded", fwd_active="active",
                 log_lines=reg_log_lines(30), fwd_http="200 0.004")
    r = run_script(env, "--replica-probes-once")
    assert r.returncode == 0
    assert "verdict=ok" in r.stdout
    assert env["NFM_MONITOR_REPLICA_STATE"] and not Path(env["NFM_MONITOR_REPLICA_STATE"]).exists()
    assert not Path(env["NFM_MONITOR_LOG"]).exists()
