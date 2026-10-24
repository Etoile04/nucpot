"""Tests for scripts/nfm-site-monitor.sh — NFM-5708/NFM-5706 reference probes.

Context (NFM-5706, carrier NFM-5708): the 2026-10-24 15:46-16:33Z multi-route
P1 burst was indistinguishable from site-side degradation without a MANUAL
reference battery (NFM-5704/NFM-5705) because both sentinel routes ride ONE
CN-to-CF-AMS transit path — multi-hostname != multi-path. The sentinel now
carries three path-independent reference lanes (int'l CF-edge, int'l non-CF,
CN-domestic control) with per-host RELATIVE baselines, and classifies every
nucpot degrade tick:

- ``TRANSIT_BROWNOUT`` — nucpot slow + ALL int'l refs slow (>= SLOW_RATIO x
  their own baseline) + domestic control pristine => log-only WARN, NON-PAGING
  (no PAGE-UNACK latch, no notify, exit 1) per ruling 5206262b(d)/NFM-5705 R5;
- ``SITE_SIDE`` — nucpot slow + refs pristine => the existing watch-line
  escalation path (streak>=2 CRITICAL page) UNCHANGED, with the class + ref
  latencies riding the log line;
- ``CN_DOMESTIC`` — CPO charter addendum hint class: the CN-domestic control
  itself is degraded => int'l comparison is UNRELIABLE, so the tick is not
  force-fit into the two main classes; NO suppression (fail-open paging
  exactly like UNCLASSIFIED) and the evidence token says so;
- ``clean`` — battery verdict when ALL lanes are pristine (batclass=clean in
  --ref-probes-once output; maps to SITE_SIDE on a nucpot degrade tick; a
  clean battery on a green tick logs nothing, keeping OK lines byte-stable);
- ``UNCLASSIFIED`` — warmup (baseline not yet trusted), mixed evidence,
  or reference-lane internal error => existing path
  (fail-open toward paging — only confident evidence may suppress a page).

Pinned contracts:

- baselines live in the ~/.nfm-site-monitor.* family (seamed via
  NFM_MONITOR_REF_STATE), accrue on green ticks, and are RELATIVE (a lane at
  60x its own ~10 ms baseline classifies slow; no absolute latency gates);
- failed probes never touch the baseline and read slow (conservative);
- reference lanes are DIRECT-pinned (ADR-023): an env http(s)_proxy pointing
  at a dead port cannot influence them;
- green ticks log NO classification token (existing OK lines byte-stable);
- the streak-1 full-edge rule (NFM-5667) and the scan-blind lanes
  (NFM-5446/5497) are untouched — brownout suppression gates ONLY the
  streak>=2 base-lane CRITICAL branch.
"""

from __future__ import annotations

from test_nfm_site_monitor import (
    env,  # noqa: F401  (pytest fixture, re-exported)
    log_text,
    mock_site,  # noqa: F401  (pytest fixture, re-exported)
    run_script,
    state_dict,
)

BROWNOUT_LAT = 1.5  # ~5x the mock lanes' ~300 ms all-in baseline — unambiguous slow


# ------------------------------------------------------------------ helpers


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


def _prime(env, n=3):
    """Green ticks to warm the reference baselines past BASELINE_MIN."""
    for _ in range(n):
        r = run_script(env)
        assert r.returncode == 0, r.stdout


def _fail_probe(env, p0=2):
    """Swap in a failing base probe: p0 rows are latency-class on the main
    hostname (no streak-1 full-edge conjuncts can match — that rule stays
    outside these tests by construction)."""
    rows = [
        "| ❌ Frontend Homepage | P0 | GET / | Timeout after 10s |",
        "| ❌ Backend API Health | P0 | GET /api/health | Timeout after 10s |",
    ]
    p = env["NFM_MONITOR_PROBE"] + ".fail"
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(f"print('{p0} check(s) failed ({p0} P0 critical)')\n")
        for row in rows[:p0]:
            fh.write(f"print('{row}')\n")
        fh.write("raise SystemExit(1)\n")
    env["NFM_MONITOR_PROBE"] = p


def _brownout_intl(config):
    """Stage a CN->int'l transit brownout: both int'l lanes slow, domestic
    control pristine."""
    config["refs"]["/ref-cf"] = {"lat": BROWNOUT_LAT}
    config["refs"]["/ref-ncf"] = {"lat": BROWNOUT_LAT}


# ------------------------------------------------------- --ref-probes-once


def test_ref_probes_once_prints_tokens_and_warmup(env, mock_site):
    r = run_script(env, "--ref-probes-once")
    assert r.returncode == 0, r.stdout
    assert "intl_slow=0/2 dom_slow=0 warmup=1" in r.stdout  # cold baselines
    assert "slow=0" in r.stdout


# ------------------------------------------------------------ C-AC1 baselines


def test_ref_state_initializes_and_accrues_on_green_ticks(env, mock_site):
    """Baselines live in the state-file family and accrue per host; green
    tick log lines stay byte-stable (no classification token)."""
    for _ in range(3):
        assert run_script(env).returncode == 0
    st = ref_state_dict(env)
    for key in ("cf", "ncf", "dom"):
        assert st[f"{key}_count"] == "3"
        assert float(st[f"{key}_ema_ms"]) > 0
    lt = log_text(env)
    assert "OK — all checks passed" in lt
    assert "class=" not in lt


# ------------------------------------------- C-AC4 direct-pinned (ADR-023)


def test_ref_lane_immune_to_proxy_env(env, mock_site):
    """ADR-023: hostile env proxies must not influence the reference lanes —
    the known confounder is the user VPN (127.0.0.1:7892 hard-failing TLS,
    NFM-5705). Explicit ProxyHandler({}) opener + no_proxy='*' export."""
    for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
        env[k] = "http://127.0.0.1:1"  # nothing listens there
    _prime(env, 2)
    r = run_script(env, "--ref-probes-once")
    assert r.returncode == 0, r.stdout
    assert "FAIL" not in r.stdout
    assert ref_state_dict(env)["cf_count"] == "3"  # probes really ran


# ---------------------------------------------------- C-AC3 classification


def test_transit_brownout_suppresses_page(env, mock_site):
    """THE ruling behavior: nucpot P0-degraded at streak>=2 while ALL int'l
    refs are slow and the domestic control is pristine => WARN, exit 1, no
    CRITICAL, no PAGE-UNACK latch, no page (5206262b(d)/NFM-5705 R5)."""
    _base, config = mock_site
    _prime(env, 3)
    assert ref_state_dict(env)["dom_count"] == "3"
    _brownout_intl(config)
    _fail_probe(env, p0=2)
    r1 = run_script(env)  # streak 1: WARN either way, class must ride the line
    assert r1.returncode == 1, r1.stdout
    assert "class=TRANSIT_BROWNOUT" in log_text(env)
    r2 = run_script(env)  # streak 2: would CRITICAL-page pre-NFM-5708
    assert r2.returncode == 1, r2.stdout
    lt = log_text(env)
    warn = [ln for ln in lt.splitlines() if "TRANSIT_BROWNOUT" in ln][-1]
    assert "5206262b" in warn
    assert "intl_slow=2/2" in warn
    # no CRITICAL log LINE ever fired (the word itself appears inside the
    # streak-1 WARN prose "confirming before CRITICAL" — match line class)
    assert not [ln for ln in lt.splitlines() if "site-monitor: CRITICAL" in ln]
    st = state_dict(env)
    assert st["level"] == "WARN"
    assert st["streak"] == "2"  # nucpot failure streak keeps counting
    assert st["pending_page"] in ("-", "")  # no latch — nothing to reconcile


def test_transit_brownout_p1_only_warn_carries_class(env, mock_site):
    """P1-only degrade ticks (the actual 10-24 burst shape) carry the class
    + reference latencies on their WARN line."""
    _base, config = mock_site
    _prime(env, 3)
    _brownout_intl(config)
    p = env["NFM_MONITOR_PROBE"] + ".p1"
    with open(p, "w", encoding="utf-8") as fh:
        fh.write("print('1 check(s) failed (0 P0 critical)')\n")
        fh.write(
            "print('| ❌ Frontend Browse Page | P1 | GET /browse | exceeds latency limit |')\n"
        )
        fh.write("raise SystemExit(1)\n")
    env["NFM_MONITOR_PROBE"] = p
    r = run_script(env)
    assert r.returncode == 1, r.stdout
    warn = [ln for ln in log_text(env).splitlines() if "P1-only failures" in ln][-1]
    assert "class=TRANSIT_BROWNOUT" in warn
    assert "refs:" in warn


def test_site_side_pages_at_streak2_with_token(env, mock_site):
    """Refs pristine + nucpot P0-degraded => SITE_SIDE token rides the WARN
    and the streak-2 CRITICAL page fires exactly as before (existing
    watch-line escalation, 5206262b as amended)."""
    _prime(env, 3)
    _fail_probe(env, p0=2)
    r1 = run_script(env)
    assert r1.returncode == 1, r1.stdout
    assert "class=SITE_SIDE" in log_text(env)
    r2 = run_script(env)
    assert r2.returncode == 2, r2.stdout
    crit = [
        ln
        for ln in log_text(env).splitlines()
        if "CRITICAL — public-edge P0 failure confirmed" in ln
    ][-1]
    assert "class=SITE_SIDE" in crit
    assert "refs:" in crit
    assert state_dict(env)["pending_page"] not in ("-", "")  # latched as before


def test_warmup_degrade_is_unclassified_and_pages(env, mock_site):
    """Fail-open during warmup: with cold baselines the int'l lanes cannot
    testify, classification reads UNCLASSIFIED, and the existing streak>=2
    page fires — suppression needs a TRUSTED baseline."""
    _base, config = mock_site
    _brownout_intl(config)  # staged BEFORE any baseline exists
    _fail_probe(env, p0=2)
    r1 = run_script(env)
    assert r1.returncode == 1, r1.stdout
    assert "class=UNCLASSIFIED" in log_text(env)
    r2 = run_script(env)
    assert r2.returncode == 2, r2.stdout
    assert "CRITICAL — public-edge P0 failure confirmed" in log_text(env)


def test_cn_domestic_class_pages_fail_open(env, mock_site):
    """CPO charter addendum: domestic control degraded (host-egress trouble
    or CN-domestic path degraded — here WITH intl also slow, the ambiguous
    combo) => HINT class CN_DOMESTIC, never force-fit into
    TRANSIT_BROWNOUT/SITE_SIDE — int'l comparison is unreliable in that
    state. NO suppression: existing paging path runs fail-open at streak 2."""
    _base, config = mock_site
    _prime(env, 3)
    _brownout_intl(config)
    config["refs"]["/ref-dom"] = {"lat": BROWNOUT_LAT}
    _fail_probe(env, p0=2)
    r1 = run_script(env)
    assert r1.returncode == 1, r1.stdout
    warn = [ln for ln in log_text(env).splitlines() if "class=CN_DOMESTIC" in ln][-1]
    assert "intl comparison unreliable" in warn
    assert "dom_slow=1" in warn
    r2 = run_script(env)
    assert r2.returncode == 2, r2.stdout
    crit = [
        ln
        for ln in log_text(env).splitlines()
        if "CRITICAL — public-edge P0 failure confirmed" in ln
    ][-1]
    assert "class=CN_DOMESTIC" in crit
    assert "intl comparison unreliable" in crit


def test_ref_lane_internal_error_fail_closed(env, mock_site):
    """Reference-lane internal failure (unwritable state) must NEVER
    suppress a page: classification falls to UNCLASSIFIED, existing lane
    pages at streak 2."""
    _prime(env, 3)
    blk = env["NFM_MONITOR_STATE"] + ".blk"
    with open(blk, "w", encoding="utf-8") as fh:
        fh.write("not a directory\n")
    env["NFM_MONITOR_REF_STATE"] = blk + "/ref.state"
    _fail_probe(env, p0=2)
    run_script(env)
    r2 = run_script(env)
    assert r2.returncode == 2, r2.stdout
    assert "CRITICAL — public-edge P0 failure confirmed" in log_text(env)


def test_failed_intl_probe_reads_slow_not_pristine(env, mock_site):
    """A hard-failing int'l lane (drop) counts as SLOW, never pristine —
    with both int'l lanes down + domestic pristine the tick classifies
    TRANSIT_BROWNOUT only after baselines are trusted (fail reads slow)."""
    _base, config = mock_site
    _prime(env, 3)
    config["refs"]["/ref-cf"] = {"drop": 99}
    config["refs"]["/ref-ncf"] = {"drop": 99}
    _fail_probe(env, p0=2)
    r = run_script(env)
    assert r.returncode == 1, r.stdout
    warn = [ln for ln in log_text(env).splitlines() if "class=" in ln][-1]
    assert "class=TRANSIT_BROWNOUT" in warn
    assert "FAIL" in warn


def test_partial_intl_slow_stays_unclassified(env, mock_site):
    """Only ONE int'l lane slow is NOT transit-brownout evidence (needs
    ALL int'l refs slow — mis-suppression is the dangerous direction)."""
    _base, config = mock_site
    _prime(env, 3)
    config["refs"]["/ref-cf"] = {"lat": BROWNOUT_LAT}
    _fail_probe(env, p0=2)
    run_script(env)
    r2 = run_script(env)
    assert r2.returncode == 2, r2.stdout
    crit = [
        ln
        for ln in log_text(env).splitlines()
        if "CRITICAL — public-edge P0 failure confirmed" in ln
    ][-1]
    assert "class=UNCLASSIFIED" in crit
    assert "intl_slow=1/2" in crit


def test_redirecting_reference_counts_as_healthy_sample(env, mock_site):
    """Live defect (2026-10-25): ghcr.io answers / with a 301 while its
    redirect target stalls past any timeout — following redirects made the
    lane read FAIL on a HEALTHY host. Redirects are disabled and any
    definitive HTTP verdict (30x/4xx/5xx) is a healthy path sample."""
    _base, config = mock_site
    config["refs"]["/ref-ncf"] = {"code": 301}
    for _ in range(3):
        r = run_script(env, "--ref-probes-once")
        assert r.returncode == 0, r.stdout
    st = ref_state_dict(env)
    assert st["ncf_count"] == "3"  # 301s accrued as samples, not FAILs
    assert "FAIL" not in st["ncf_last"]


def test_dead_intl_lane_exits_warmup_and_brownout_stays_reachable(env, mock_site):
    """Review defect: a lane that NEVER succeeds (ghcr egress blocked from a
    CN vantage) held the WHOLE battery in warmup forever — warmup must be
    attempt-based. After BASELINE_MIN probes the battery testifies even with
    a dead lane: it reads slow (never pristine), and cf-slow + dead-ncf +
    pristine domestic still classifies TRANSIT_BROWNOUT instead of the
    classifier being permanently inert."""
    _base, config = mock_site
    config["refs"]["/ref-ncf"] = {"drop": 99}
    for _ in range(3):
        assert run_script(env, "--ref-probes-once").returncode == 0
    r = run_script(env, "--ref-probes-once")
    assert r.returncode == 0, r.stdout
    assert "warmup=0" in r.stdout  # attempts, not successes, end warmup
    config["refs"]["/ref-cf"] = {"lat": BROWNOUT_LAT}
    _fail_probe(env, p0=2)
    assert run_script(env).returncode == 1  # streak 1: WARN, class rides line
    r2 = run_script(env)  # streak 2: dead-ncf reads slow => all intl slow
    assert r2.returncode == 1, r2.stdout  # suppressed, NOT a page
    warn = [ln for ln in log_text(env).splitlines() if "class=" in ln][-1]
    assert "class=TRANSIT_BROWNOUT" in warn
    assert not [ln for ln in log_text(env).splitlines() if "site-monitor: CRITICAL" in ln]


def test_untrusted_domestic_never_satisfies_pristine(env, mock_site):
    """Review defect: the dom-pristine conjunct of TRANSIT_BROWNOUT was
    satisfiable WITHOUT trusted evidence — an untrusted-but-succeeding dom
    lane reads slow=0 at any latency (its EMA seeded mid-degradation). tuna
    fails its first BASELINE_MIN attempts (battery exits warmup
    attempt-based, dom cnt=0), then recovers; with both int'l lanes slow the
    tick must be UNCLASSIFIED (mixed) and page fail-open at streak 2 —
    exactly the dom-degraded+intl-slow state the CPO charter maps to
    CN_DOMESTIC-adjacent fail-open, never suppression."""
    _base, config = mock_site
    config["refs"]["/ref-dom"] = {"drop": 99}  # tuna unreachable from t0
    for _ in range(3):
        assert run_script(env).returncode == 0  # green; dom att accrues, cnt=0
    st = ref_state_dict(env)
    assert st["dom_att"] == "3" and st["dom_count"] == "0"  # untrusted, no baseline
    config["refs"]["/ref-dom"] = {"lat": 0.2}  # recovers — but baseline still cold
    _brownout_intl(config)
    _fail_probe(env, p0=2)
    r1 = run_script(env)  # streak 1: WARN, class must NOT be TRANSIT_BROWNOUT
    assert r1.returncode == 1, r1.stdout
    assert "class=UNCLASSIFIED" in log_text(env)
    r2 = run_script(env)  # streak 2: pages fail-open — no suppression
    assert r2.returncode == 2, r2.stdout
    assert "CRITICAL — public-edge P0 failure confirmed" in log_text(env)
    assert not [ln for ln in log_text(env).splitlines() if "class=TRANSIT_BROWNOUT" in ln]


def test_suppressed_ticks_do_not_advance_reminder_clock(env, mock_site):
    """Review defect: brownout-suppressed ticks are log-only and page
    nobody, so they must not accrue since_remind — otherwise the FIRST
    CRITICAL page after suppression clears logs itself as 'ongoing
    reminder' (the calibration-evidence line the CTO review reads)."""
    _base, config = mock_site
    _prime(env, 3)
    _brownout_intl(config)
    _fail_probe(env, p0=2)
    assert run_script(env).returncode == 1  # streak 1 WARN
    st = state_dict(env)
    st["since_remind"] = "10"  # one suppressed tick from the reminder edge
    with open(env["NFM_MONITOR_STATE"], "w", encoding="utf-8") as fh:
        for k, v in st.items():
            fh.write(f"{k}={v}\n")
    assert run_script(env).returncode == 1  # streak 2: suppressed WARN
    config["refs"]["/ref-cf"] = {"lat": 0.2}  # brownout clears mid-incident
    config["refs"]["/ref-ncf"] = {"lat": 0.2}
    r = run_script(env)  # streak 3: first CRITICAL page of the incident
    assert r.returncode == 2, r.stdout
    crit = [ln for ln in log_text(env).splitlines() if "site-monitor: CRITICAL" in ln][-1]
    assert "public-edge P0 failure confirmed" in crit
    assert "ongoing reminder" not in crit
