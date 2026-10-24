"""Replay validation of the NFM-5708 classifier against the documented
2026-10-24 PM window (CPO charter addendum, folding NFM-5707).

Fixture source (NFM-5704/NFM-5705 manual battery, 15:35-16:32Z):

- cloudflare: 8.25 / 3.54 / 3.01 s samples vs a sub-second (~0.30 s) norm
- ghcr.io:    2.15 s TCP-connect-equivalent sample vs a 0.77 s baseline
- tuna:       0.19 s — pristine CN-domestic control
- nucpot:     degraded (the multi-route P1 burst)

Required verdict for that window: ``TRANSIT_BROWNOUT``.

Every row drives the REAL script end-to-end through the loopback mock-edge
harness (no unit-level shortcut): baselines are primed at the documented
norms, the replay latencies are staged on the mock lanes, and the nucpot
probe is degraded (or not) per row before the tick runs.

Pinned contracts:

- the classifier consumes RATIOS vs each lane's own EMA, so the window is
  replayed as its documented ratios — cloudflare 8.25/3.54/3.01 s are 27.5x /
  11.8x / 10x the 0.30 s norm and ghcr's 2.15 s is 2.79x the 0.77 s norm —
  staged against the MEASURED baseline after priming. All sit above
  SLOW_RATIO=2.5 with no threshold touched, and the staging is immune to
  harness timing noise (a full-suite run inflates mock-lane sleeps enough to
  drop a literal 2.15 s sample below 2.5x its primed EMA — the exact flake
  this fixture exists to avoid);
- the boundary rows pin the CPO addendum enum: tuna degraded + nucpot
  degraded => CN_DOMESTIC (fail-open, pages); all clean => clean battery /
  SITE_SIDE on a degrade tick; int'l degraded + nucpot clean => GREEN tick
  with a recording-only NOTE line and a byte-stable OK line.

Replay command (cited in the PR description):

    python3 -m pytest scripts/tests/test_nfm5708_replay_20261024.py -v
"""

from __future__ import annotations

import pytest
from test_nfm_site_monitor import (
    env,  # noqa: F401  (pytest fixture, re-export)
    log_text,
    mock_site,  # noqa: F401  (pytest fixture, re-export)
    run_script,
    state_dict,
)
from test_nfm_site_monitor_refs import _fail_probe, ref_state_dict

# Documented norms (seconds) — the 10-24 pre-burst baselines.
NORM = {"cf": 0.30, "ncf": 0.77, "dom": 0.19}
# The window's documented sample/NORM ratios (what the classifier consumes).
RATIO = {"cf": 8.25 / 0.30, "cf_354": 3.54 / 0.30, "cf_301": 3.01 / 0.30, "ncf": 2.15 / 0.77}
# Pristine lanes are staged at a pristine absolute.
PRISTINE_CF, PRISTINE_DOM = 0.2, 0.15
# tuna degraded: 1.5 s is ~7.9x its 0.19 s norm — unambiguous, needs no
# ratio staging.
DOM_SLOW = 1.5

# The replay table: every (nucpot, battery) combo the CPO addendum names.
# int'l lanes stage as documented RATIOS vs the measured EMA; see the module
# docstring for why literal seconds are not load-portable.
REPLAY_TABLE = [
    # THE window: 15:35-16:32Z — cloudflare slow, ghcr slow, tuna pristine,
    # nucpot degraded => TRANSIT_BROWNOUT.
    pytest.param(
        "20261024-1525Z-window",
        RATIO["cf"],
        RATIO["ncf"],
        False,
        True,
        "TRANSIT_BROWNOUT",
        id="replay-window",
    ),
    # cloudflare's other two documented samples (3.54 s / 3.01 s) must land
    # in the same class — the verdict may not hinge on WHICH slow sample.
    pytest.param(
        "20261024-cf-354",
        RATIO["cf_354"],
        RATIO["ncf"],
        False,
        True,
        "TRANSIT_BROWNOUT",
        id="replay-cf-3.54s",
    ),
    pytest.param(
        "20261024-cf-301",
        RATIO["cf_301"],
        RATIO["ncf"],
        False,
        True,
        "TRANSIT_BROWNOUT",
        id="replay-cf-3.01s",
    ),
    # Boundary: tuna degraded + nucpot degraded => CN_DOMESTIC (hint, NOT
    # force-fit; int'l comparison unreliable).
    pytest.param(
        "tuna-degraded-nucpot-degraded",
        None,
        None,
        True,
        True,
        "CN_DOMESTIC",
        id="boundary-cn-domestic",
    ),
    # Boundary: all clean + nucpot degraded => clean battery => SITE_SIDE.
    pytest.param(
        "all-clean-nucpot-degraded", None, None, False, True, "SITE_SIDE", id="boundary-site-side"
    ),
]


def _stage(config, cf, ncf, dom):
    config["refs"]["/ref-cf"] = {"lat": cf}
    config["refs"]["/ref-ncf"] = {"lat": ncf}
    config["refs"]["/ref-dom"] = {"lat": dom}


def _prime_at_norms(env, config, n=4):
    """Prime baselines AT the documented norms so replay ratios match the
    fixture numbers (extra tick settles the EMA tightly on the slow-ish
    0.77 s ghcr lane, whose 2.79x replay ratio has the least headroom)."""
    _stage(config, NORM["cf"], NORM["ncf"], NORM["dom"])
    for _ in range(n):
        assert run_script(env).returncode == 0


def _stage_row(env, config, cf_ratio, ncf_ratio, dom_slow):
    """Stage a replay row's window: int'l lanes at their documented RATIO x
    the MEASURED baseline (load-portable), domestic per the row."""
    st = ref_state_dict(env)
    cf = float(st["cf_ema_ms"]) / 1000.0 * cf_ratio if cf_ratio else PRISTINE_CF
    ncf = float(st["ncf_ema_ms"]) / 1000.0 * ncf_ratio if ncf_ratio else NORM["ncf"]
    _stage(config, cf, ncf, DOM_SLOW if dom_slow else PRISTINE_DOM)


@pytest.mark.parametrize("name,cf_ratio,ncf_ratio,dom_slow,nucpot_degraded,expected", REPLAY_TABLE)
def test_replay_row(name, cf_ratio, ncf_ratio, dom_slow, nucpot_degraded, expected, env, mock_site):
    """Each replay row: prime at documented norms, stage the row's lane
    latencies, degrade (or keep green) nucpot, assert the tick's class."""
    _base, config = mock_site
    _prime_at_norms(env, config)
    assert ref_state_dict(env)["dom_count"] == "4"
    _stage_row(env, config, cf_ratio, ncf_ratio, dom_slow)
    if nucpot_degraded:
        _fail_probe(env, p0=2)
        r = run_script(env)
        assert r.returncode == 1, r.stdout  # streak 1 WARN carries the class
        line = [ln for ln in log_text(env).splitlines() if "class=" in ln][-1]
        assert f"class={expected}" in line, line
        if expected == "CN_DOMESTIC":
            assert "intl comparison unreliable" in line
    else:
        r = run_script(env)
        assert r.returncode == 0, r.stdout


def test_replay_window_suppresses_the_streak2_page(env, mock_site):
    """Behavioral close-out of THE window row: at streak 2 (the pre-NFM-5708
    paging edge) the replayed window stays log-only — no CRITICAL, no latch,
    exit 1 — per 5206262b(d)/NFM-5705 R5."""
    _base, config = mock_site
    _prime_at_norms(env, config)
    _stage_row(env, config, RATIO["cf"], RATIO["ncf"], False)
    _fail_probe(env, p0=2)
    assert run_script(env).returncode == 1  # streak 1 WARN
    r2 = run_script(env)  # streak 2: would page pre-NFM-5708
    assert r2.returncode == 1, r2.stdout
    lt = log_text(env)
    warn = [ln for ln in lt.splitlines() if "class=TRANSIT_BROWNOUT" in ln][-1]
    assert "intl_slow=2/2" in warn
    assert "dom_slow=0" in warn
    assert not [ln for ln in lt.splitlines() if "site-monitor: CRITICAL" in ln]
    st = state_dict(env)
    assert st["level"] == "WARN"
    assert st["streak"] == "2"  # keeps counting — suppression only delays
    assert st["pending_page"] in ("-", "")


def test_replay_cn_domestic_row_pages_fail_open(env, mock_site):
    """Behavioral close-out of the CN_DOMESTIC boundary row: the hint class
    never suppresses — the streak-2 CRITICAL page fires exactly as before."""
    _base, config = mock_site
    _prime_at_norms(env, config)
    _stage_row(env, config, None, None, True)
    _fail_probe(env, p0=2)
    assert run_script(env).returncode == 1
    r2 = run_script(env)
    assert r2.returncode == 2, r2.stdout
    crit = [
        ln
        for ln in log_text(env).splitlines()
        if "CRITICAL — public-edge P0 failure confirmed" in ln
    ][-1]
    assert "class=CN_DOMESTIC" in crit
    assert "intl comparison unreliable" in crit


def test_replay_intl_degraded_nucpot_clean_logs_note(env, mock_site):
    """Boundary row: int'l degraded but nucpot CLEAN => GREEN tick, rc 0, OK
    line intact, one recording-only NOTE line, zero page implications (no
    state pollution, no latch)."""
    _base, config = mock_site
    _prime_at_norms(env, config)
    _stage_row(env, config, RATIO["cf"], RATIO["ncf"], False)
    r = run_script(env)  # base probe still green
    assert r.returncode == 0, r.stdout
    lt = log_text(env)
    assert "OK — all checks passed" in lt
    notes = [ln for ln in lt.splitlines() if "NOTE — ref-battery" in ln]
    assert notes, "expected a recording-only ref-battery NOTE line"
    assert "intl_brownout" in notes[-1]
    assert "no page implications" in notes[-1]
    st = state_dict(env)
    assert st["level"] == "OK"
    assert st["pending_page"] in ("-", "")


def test_replay_all_clean_green_tick_is_silent(env, mock_site):
    """Boundary row: all clean + nucpot clean => NO NOTE line, no class token
    anywhere in the log (green OK lines byte-stable), batclass observable
    only via --ref-probes-once."""
    _base, config = mock_site
    _prime_at_norms(env, config)
    r = run_script(env)
    assert r.returncode == 0, r.stdout
    lt = log_text(env)
    assert "NOTE — ref-battery" not in lt
    assert "class=" not in lt
    probe = run_script(env, "--ref-probes-once")
    assert probe.returncode == 0, probe.stdout
    assert "batclass=clean" in probe.stdout
    assert "warmup=0" in probe.stdout
