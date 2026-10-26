"""NFM-5720 — the two post-G3 classifier refinements (CTO [CTO-ARCH-OBS]
b826f9b0 on NFM-5706, 2026-10-25T20:01Z; QA d6ab085c/8ad28898 + CTO
adjudication 00bdefaa on NFM-5705). False-NEGATIVE class only: missed
degradation marking; no false pages; no false TB-while-clean.

OBS-1 — dual absolute+relative slow thresholds. The per-host EMA baselines
chase sustained degradation (cf EMA climbed 1232->1763ms across 10-25), so
14:07:25Z printed ``slow=0, batclass=clean`` with cf at an absolute 2150ms
(~2.8x the ~760ms clean baseline) — SITE_SIDE labels over-trust and
TRANSIT_BROWNOUT under-fires during long brownouts. A lane is now SLOW when
the sample exceeds EITHER the relative arm (EMA x SLOW_RATIO) OR the lane's
absolute floor.

Floors = 2.5x the clean-period EMA baselines on the 10-25 log of record
(within the CTO's "floors = 2-3x those" range; 2.5 mirrors SLOW_RATIO):

    | lane | clean baseline (evidence)              | floor |
    |------|----------------------------------------|-------|
    | cf   | ~760ms  (00:0x ema 750-755; recovered 811-835) | 1900ms |
    | ncf  | ~640ms  (00:0x ema 633-638; recovered 688-762) | 1600ms |
    | dom  | ~230ms  (post-swap ema 215-222; 251-298)      |  575ms |

Reconciliation (CR-relevant): the CTO's prose says "flag slow when sample >
max(EMA x ratio, absolute floor)", but the literal max-form leaves the CTO's
OWN 14:07:25Z vector unflagged at every floor in the stated 2-3x range
(2150 < max(4337, 1520..2280) for all of them) — it implements no fix for
the named defect. Either-arm (equivalently min-of-thresholds) satisfies the
AC3 vector; the floor arm equals the relative arm the EMA would apply had it
never ratcheted, so it only ever LOWERS a threshold — the false-positive
class is untouched (clean lanes sit far under their floors).

OBS-2 — intl-transit precedence over cn_domestic. 19:30:04Z printed
``batclass=cn_domestic`` with ``intl_slow=2/2 AND dom_slow=1`` (cf 2012 /
ghcr 1993 / ustc 656ms, all slow). All-three-slow is transit-class shape per
the charter; the ``intl_slow==2/2`` conjunct now outranks ``dom_slow``, and
cn_domestic means precisely "dom degraded while intl NOT both slow". The
``lane_trusted["dom"]`` fail-open gate survives the reorder.

The log-of-record replay (18 post-NFM-5712 ticks, 2026-10-25T09:04Z ->
2026-10-26T00:56Z, transcribed verbatim from
~/Library/Logs/nfm-site-monitor.log) drives the REAL script per tick: the
historical per-lane EMAs are seeded into the state file, the logged samples
are staged as absolute mock-lane latencies, and ``--ref-probes-once`` runs
the live classifier. AC3 evidence:

- 14:07:25Z now marks slow (cf slow 0->1 via the floor; batclass clean ->
  mixed — the degrade-tick label moves SITE_SIDE -> UNCLASSIFIED, the
  fail-open direction);
- 19:30:04Z reclassifies cn_domestic -> intl_brownout (transit class);
- zero-false-TB invariant: no prior-clean tick flips to intl_brownout, and
  every tick that DOES flip has BOTH intl lanes >= their absolute floors
  (genuine transit degradation, not threshold noise). No prior
  TRANSIT_BROWNOUT line existed in the log to flip (checked: 0 occurrences
  log-wide) — the replay pins the forward invariant instead.

Replay command (cited in the PR description):

    python3 -m pytest scripts/tests/test_nfm5720_classifier_refinements.py -v
"""

from __future__ import annotations

import os
import re

import pytest
from test_nfm_site_monitor import (
    env,  # noqa: F401  (pytest fixture, re-export)
    log_text,
    mock_site,  # noqa: F401  (pytest fixture, re-export)
    run_script,
    state_dict,
)
from test_nfm_site_monitor_refs import _fail_probe, _prime, ref_state_dict

# NFM-5720 shipped defaults (ms) — 2.5x the clean-period baselines above.
FLOOR = {"cf": 1900.0, "ncf": 1600.0, "dom": 575.0}
ALPHA, ALPHA_SLOW, SLOW_RATIO = 0.125, 0.03, 2.5
# Loopback overhead bound for fidelity of staged absolute samples (the
# mock measures sleep + overhead; suite-load worst case ~200ms).
NOISE_MS = 250.0


# ------------------------------------------------------------------ helpers


def _seed_ref_state(env, emas, counts=10):
    """Seed the reference state file with HISTORICAL per-lane EMAs (and
    trusted counts) so the classifier judges a staged sample against the
    ratchet point the log actually carried at that tick."""
    urls = {
        "cf": env["NFM_REF_CF_URL"],
        "ncf": env["NFM_REF_NONCF_URL"],
        "dom": env["NFM_REF_DOM_URL"],
    }
    with open(env["NFM_MONITOR_REF_STATE"], "w", encoding="utf-8") as fh:
        for lane, url in urls.items():
            fh.write(f"{lane}_url={url}\n")
            fh.write(f"{lane}_count={counts}\n")
            fh.write(f"{lane}_att={counts}\n")
            fh.write(f"{lane}_ema_ms={emas[lane]:.1f}\n")
            fh.write(f"{lane}_last=seeded\n")


def _stage(config, cf_ms, ncf_ms, dom_ms):
    config["refs"]["/ref-cf"] = {"lat": cf_ms / 1000.0}
    config["refs"]["/ref-ncf"] = {"lat": ncf_ms / 1000.0}
    config["refs"]["/ref-dom"] = {"lat": dom_ms / 1000.0}


_TOKEN = r"([a-z0-9.\-]+)=([0-9]+)ms\(ema=([0-9.\-]+)ms,slow=([01]),unavail=([01])\)"


def _parse_probe_by_lane(env, out):
    """Lane-keyed parse of --ref-probes-once output. Mock lanes display as
    127.0.0.1:PORT (same host for all three), so rows are keyed by ORDER —
    lanes always print in the fixed cf, ncf, dom order."""
    found = re.findall(_TOKEN, out)
    lanes = {}
    for (host, ms, _ema, slow, unavail), lane in zip(
        found, ("cf", "ncf", "dom"), strict=False
    ):
        lanes[lane] = {"host": host, "ms": int(ms), "slow": int(slow), "unavail": int(unavail)}
    bat = re.search(r"batclass=([a-z_]+)", out)
    intl = re.search(r"intl_slow=(\d+)/2 dom_slow=(\d+)", out)
    return lanes, bat.group(1) if bat else None, intl.groups() if intl else None


def _clear_floor_overrides(env):
    for k in ("NFM_REF_ABS_FLOOR_CF", "NFM_REF_ABS_FLOOR_NONCF", "NFM_REF_ABS_FLOOR_DOM"):
        env.pop(k, None)
        os.environ.pop(k, None)


# ------------------------------------------------- OBS-1 floor-arm unit rows


def test_floor_catches_ratchet_masked_degradation(env, mock_site):
    """THE OBS-1 vector (14:07:25Z): cf 2150ms against a ratcheted EMA of
    1735ms — the relative arm is 4337ms, so the old rule read slow=0. The
    1900ms floor arm must catch it: slow=1 (either-arm)."""
    _base, config = mock_site
    _clear_floor_overrides(env)
    _seed_ref_state(env, {"cf": 1735.0, "ncf": 974.0, "dom": 304.0})
    _stage(config, 2150, 1063, 131)  # the logged 14:07:25Z samples
    r = run_script(env, "--ref-probes-once")
    assert r.returncode == 0, r.stdout
    lanes, bat, intl = _parse_probe_by_lane(env, r.stdout)
    assert lanes["cf"]["slow"] == 1
    assert lanes["ncf"]["slow"] == 0   # 1063 < floor 1600 and < rel arm 2435
    assert lanes["dom"]["slow"] == 0
    assert intl == ("1", "0")
    assert bat == "mixed"  # was clean — the AC3 reclassification


def test_clean_samples_stay_clean_under_floors(env, mock_site):
    """False-positive guard: present-moment clean readings (2026-10-26
    ~09:05Z manual probe: cf 955 / ghcr 658 / ustc 243ms) sit far under
    their floors — all slow=0, batclass stays clean."""
    _base, config = mock_site
    _clear_floor_overrides(env)
    _seed_ref_state(env, {"cf": 760.0, "ncf": 640.0, "dom": 230.0})
    _stage(config, 955, 658, 243)
    r = run_script(env, "--ref-probes-once")
    assert r.returncode == 0, r.stdout
    lanes, bat, intl = _parse_probe_by_lane(env, r.stdout)
    assert all(v["slow"] == 0 for v in lanes.values())
    assert intl == ("0", "0")
    assert bat == "clean"


def test_floor_boundary_is_inclusive(env, mock_site):
    """The floor arm uses >= (matching the relative arm): a sample staged
    exactly at the floor marks slow (loopback overhead only pushes it
    further over); samples well under BOTH arms (350ms+ margins, safe
    against mock overhead noise) stay clean."""
    _base, config = mock_site
    _clear_floor_overrides(env)
    _seed_ref_state(env, {"cf": 2000.0, "ncf": 2000.0, "dom": 2000.0})  # rel arms 5000
    _stage(config, FLOOR["cf"], 1250, 225)  # ncf/dom 350ms under their floors
    r = run_script(env, "--ref-probes-once")
    assert r.returncode == 0, r.stdout
    lanes, bat, _ = _parse_probe_by_lane(env, r.stdout)
    assert lanes["cf"]["slow"] == 1    # at floor -> slow (>=)
    assert lanes["ncf"]["slow"] == 0   # under both arms
    assert lanes["dom"]["slow"] == 0


def test_default_floors_are_the_documented_production_values(env, mock_site):
    """Pins the SHIPPED defaults (no env override): with a ratcheted EMA of
    2000ms (relative arm 5000ms), a 1950ms cf sample marks slow ONLY via the
    default 1900ms floor — proving the production constants are live."""
    _base, config = mock_site
    _clear_floor_overrides(env)
    _seed_ref_state(env, {"cf": 2000.0, "ncf": 2000.0, "dom": 2000.0})
    _stage(config, 1950, 700, 250)
    r = run_script(env, "--ref-probes-once")
    assert r.returncode == 0, r.stdout
    lanes, _, _ = _parse_probe_by_lane(env, r.stdout)
    assert lanes["cf"]["slow"] == 1
    assert lanes["ncf"]["slow"] == 0
    assert lanes["dom"]["slow"] == 0


def test_floor_caught_sample_folds_at_alpha_slow(env, mock_site):
    """NFM-5720 fold consistency: a floor-caught (over_abs, NOT over_rel)
    sample must fold into the EMA at ALPHA_SLOW — full-weight ALPHA folding
    would accelerate the very ratchet this fix bounds. Expected EMA is
    computed from the MEASURED sample (parsed from the same tick)."""
    _base, config = mock_site
    _clear_floor_overrides(env)
    _seed_ref_state(env, {"cf": 1735.0, "ncf": 2000.0, "dom": 2000.0})
    _stage(config, 2150, 700, 250)
    r = run_script(env, "--ref-probes-once")
    assert r.returncode == 0, r.stdout
    lanes, _, _ = _parse_probe_by_lane(env, r.stdout)
    measured = lanes["cf"]["ms"]
    assert measured >= FLOOR["cf"] and measured < 1735.0 * SLOW_RATIO  # the shape under test
    expected = 1735.0 + ALPHA_SLOW * (measured - 1735.0)
    assert abs(float(ref_state_dict(env)["cf_ema_ms"]) - expected) < 0.5


def test_healthy_sample_still_folds_at_alpha(env, mock_site):
    """Control row for the fold test: a sub-threshold sample (under BOTH
    arms) keeps folding at full ALPHA — the drift policy is unchanged."""
    _base, config = mock_site
    _clear_floor_overrides(env)
    _seed_ref_state(env, {"cf": 1735.0, "ncf": 2000.0, "dom": 2000.0})
    _stage(config, 800, 700, 250)
    r = run_script(env, "--ref-probes-once")
    assert r.returncode == 0, r.stdout
    lanes, _, _ = _parse_probe_by_lane(env, r.stdout)
    measured = lanes["cf"]["ms"]
    assert measured < FLOOR["cf"] and measured < 1735.0 * SLOW_RATIO
    expected = 1735.0 + ALPHA * (measured - 1735.0)
    assert abs(float(ref_state_dict(env)["cf_ema_ms"]) - expected) < 0.5


# --------------------------------------------- OBS-2 precedence (token level)


def test_all_three_slow_reads_intl_brownout(env, mock_site):
    """THE OBS-2 vector (19:30:04Z): intl_slow=2/2 AND dom_slow=1 — the
    transit conjunct now outranks dom_slow (was batclass=cn_domestic)."""
    _base, config = mock_site
    _clear_floor_overrides(env)
    _seed_ref_state(env, {"cf": 801.0, "ncf": 691.0, "dom": 236.0})
    _stage(config, 2012, 1993, 656)  # the logged 19:30:04Z samples
    r = run_script(env, "--ref-probes-once")
    assert r.returncode == 0, r.stdout
    lanes, bat, intl = _parse_probe_by_lane(env, r.stdout)
    assert all(v["slow"] == 1 for v in lanes.values())
    assert intl == ("2", "1")
    assert bat == "intl_brownout"


def test_dom_slow_with_intl_clean_stays_cn_domestic(env, mock_site):
    """The charter's cn_domestic, NFM-5720-precise: dom degraded while intl
    NOT both slow (the 09:04:57Z shape) — precedence does not eat the hint
    class."""
    _base, config = mock_site
    _clear_floor_overrides(env)
    _seed_ref_state(env, {"cf": 1232.0, "ncf": 965.0, "dom": 215.0})
    _stage(config, 2074, 814, 620)  # the logged 09:04:57Z samples
    r = run_script(env, "--ref-probes-once")
    assert r.returncode == 0, r.stdout
    lanes, bat, intl = _parse_probe_by_lane(env, r.stdout)
    assert lanes["dom"]["slow"] == 1
    assert intl == ("1", "1")  # cf floor-caught, ncf clean
    assert bat == "cn_domestic"


# ------------------------------------------------ AC3: log-of-record replay
#
# 18 post-NFM-5712 ticks transcribed verbatim from the 10-25/10-26 log of
# record (~/Library/Logs/nfm-site-monitor.log). Per lane: (logged_ms,
# logged_ema, old_slow, new_slow); old batclass is the log's ground truth,
# new batclass is the post-NFM-5720 expectation. Pre-5712 ticks (tuna-era)
# are not replayable: that control host is gone by design (NFM-5712 swap).

REPLAY = [
    # id,                ts,                  level/class, cf, ncf, dom, old_bat, new_bat
    ("r01", "2026-10-25T09:04:57Z", "NOTE green", (2074, 1232, 0, 1), (814, 965, 0, 0), (620, 215, 1, 1), "cn_domestic", "cn_domestic"),
    ("r02", "2026-10-25T09:22:01Z", "WARN SITE_SIDE", (1148, 1276, 0, 0), (790, 902, 0, 0), (151, 222, 0, 0), "clean", "clean"),
    ("r03", "2026-10-25T14:00:37Z", "NOTE green", (3283, 1514, 0, 1), (2946, 913, 1, 1), (2173, 247, 1, 1), "cn_domestic", "intl_brownout"),
    ("r04", "2026-10-25T14:07:25Z", "WARN SITE_SIDE", (2150, 1735, 0, 1), (1063, 974, 0, 0), (131, 304, 0, 0), "clean", "mixed"),
    ("r05", "2026-10-25T14:29:48Z", "NOTE green", (3687, 1670, 0, 1), (3753, 989, 1, 1), (1902, 263, 1, 1), "cn_domestic", "intl_brownout"),
    ("r06", "2026-10-25T14:44:15Z", "WARN CN_DOMESTIC", (2868, 1838, 0, 1), (3747, 1055, 1, 1), (1751, 295, 1, 1), "cn_domestic", "intl_brownout"),
    ("r07", "2026-10-25T15:07:38Z", "NOTE green", (2114, 1763, 0, 1), (1069, 1071, 0, 0), (1180, 358, 1, 1), "cn_domestic", "cn_domestic"),
    ("r08", "2026-10-25T15:14:10Z", "NOTE green", (4678, 1807, 1, 1), (2506, 1071, 0, 1), (2656, 383, 1, 1), "cn_domestic", "intl_brownout"),
    ("r09", "2026-10-25T16:18:45Z", "WARN SITE_SIDE", (1338, 1569, 0, 0), (665, 1028, 0, 0), (187, 282, 0, 0), "clean", "clean"),
    ("r10", "2026-10-25T16:24:22Z", "WARN UNCLASSIFIED", (932, 1540, 0, 0), (4454, 982, 1, 1), (175, 270, 0, 0), "mixed", "mixed"),
    ("r11", "2026-10-25T16:46:55Z", "NOTE green", (924, 1418, 0, 0), (691, 964, 0, 0), (10222, 238, 1, 1), "cn_domestic", "cn_domestic"),
    ("r12", "2026-10-25T18:47:11Z", "WARN SITE_SIDE", (710, 916, 0, 0), (683, 718, 0, 0), (205, 251, 0, 0), "clean", "clean"),
    ("r13", "2026-10-25T18:52:30Z", "NOTE green", (1007, 890, 0, 0), (1004, 714, 0, 0), (672, 246, 1, 1), "cn_domestic", "cn_domestic"),
    ("r14", "2026-10-25T19:30:04Z", "NOTE green", (2012, 801, 1, 1), (1993, 691, 1, 1), (656, 236, 1, 1), "cn_domestic", "intl_brownout"),
    ("r15", "2026-10-25T20:19:50Z", "WARN SITE_SIDE", (1410, 811, 0, 0), (804, 762, 0, 0), (322, 298, 0, 0), "clean", "clean"),
    ("r16", "2026-10-25T21:52:18Z", "WARN SITE_SIDE", (1062, 835, 0, 0), (735, 688, 0, 0), (281, 261, 0, 0), "clean", "clean"),
    ("r17", "2026-10-26T00:28:41Z", "NOTE green", (1110, 820, 0, 0), (1104, 688, 0, 0), (846, 296, 1, 1), "cn_domestic", "cn_domestic"),
    ("r18", "2026-10-26T00:56:33Z", "WARN SITE_SIDE", (966, 966, 0, 0), (699, 837, 0, 0), (259, 432, 0, 0), "clean", "clean"),
]


@pytest.mark.parametrize(
    "rid,ts,shape,cf,ncf,dom,old_bat,new_bat", REPLAY, ids=[r[0] for r in REPLAY]
)
def test_replay_tick(rid, ts, shape, cf, ncf, dom, old_bat, new_bat, env, mock_site):
    """Replay one log-of-record tick: seed the historical EMAs, stage the
    logged samples as absolutes, run the live classifier, assert the
    post-NFM-5720 slow flags + batclass. Fidelity guard: the measured mock
    sample must stay within NOISE_MS of the logged value — the row is only
    valid replay evidence while that holds."""
    _base, config = mock_site
    _clear_floor_overrides(env)
    env["NFM_REF_TIMEOUT"] = "20"  # admit the largest logged sample (10.2s)
    _seed_ref_state(env, {"cf": float(cf[1]), "ncf": float(ncf[1]), "dom": float(dom[1])})
    _stage(config, cf[0], ncf[0], dom[0])
    r = run_script(env, "--ref-probes-once")
    assert r.returncode == 0, r.stdout
    lanes, bat, intl = _parse_probe_by_lane(env, r.stdout)
    for lane, spec in (("cf", cf), ("ncf", ncf), ("dom", dom)):
        logged_ms, _ema, _old, new_slow = spec
        measured = lanes[lane]["ms"]
        assert abs(measured - logged_ms) <= NOISE_MS, (rid, lane, measured, logged_ms)
        assert lanes[lane]["slow"] == new_slow, (rid, lane, lanes[lane], spec)
    assert bat == new_bat, (rid, r.stdout)


def test_replay_ac3_named_vectors():
    """AC3's two named vectors are in the table with the required deltas."""
    rows = {r[1]: r for r in REPLAY}
    r1407 = rows["2026-10-25T14:07:25Z"]
    assert r1407[3][3] == 1 and r1407[7] == "mixed"  # cf slow 0->1; clean -> mixed
    r1930 = rows["2026-10-25T19:30:04Z"]
    assert r1930[7] == "intl_brownout"  # cn_domestic -> transit class


def test_replay_zero_false_tb_while_clean():
    """AC3 zero-false-TB invariant, table-wide: (a) NO prior-clean tick
    reclassifies to intl_brownout (or anything suppress-worthy); (b) every
    tick that DOES reclassify to intl_brownout has BOTH intl lanes at/over
    their absolute floors on the LOGGED samples — genuine transit-shape
    degradation, not threshold noise; (c) no tick keeps intl_slow below 2
    while reading intl_brownout."""
    for rid, ts, _shape, cf, ncf, dom, old_bat, new_bat in REPLAY:
        if old_bat == "clean":
            assert new_bat in ("clean", "mixed"), (rid, old_bat, new_bat)
        if new_bat == "intl_brownout":
            assert cf[0] >= FLOOR["cf"] and ncf[0] >= FLOOR["ncf"], (rid, cf[0], ncf[0])
            assert cf[3] == 1 and ncf[3] == 1 and dom[3] == 1, rid
        if new_bat == "cn_domestic":
            assert dom[3] == 1 and not (cf[3] == 1 and ncf[3] == 1), rid


def test_replay_refs_clean_site_side_subset_stands():
    """AC4 input (reported to NFM-5705): the CTO's refs-clean SITE_SIDE
    subset 09:22/16:18/18:47 (+ the same-shape 20:19/21:52/00:56) stays
    refs-clean post-fix — no sample at/over any lane floor, batclass clean.
    The SITE_SIDE labels stand as site-side degradation evidence."""
    subset = {
        "2026-10-25T09:22:01Z", "2026-10-25T16:18:45Z", "2026-10-25T18:47:11Z",
        "2026-10-25T20:19:50Z", "2026-10-25T21:52:18Z", "2026-10-26T00:56:33Z",
    }
    rows = {r[1]: r for r in REPLAY}
    assert subset <= rows.keys()
    for ts in subset:
        rid, _ts, shape, cf, ncf, dom, old_bat, new_bat = rows[ts]
        assert old_bat == "clean" and new_bat == "clean", (rid, old_bat, new_bat)
        assert max(cf[0] / FLOOR["cf"], ncf[0] / FLOOR["ncf"], dom[0] / FLOOR["dom"]) < 1.0


# --------------------------------------- behavioral rows (full tick, paged())


def test_1407_shape_degrade_tick_reads_unclassified(env, mock_site):
    """End-to-end AC3 row 1: the 14:07:25Z tick was a P1-only WARN labeled
    SITE_SIDE on a clean battery. Post-fix the same shape (cf floor-caught
    at 2150ms vs ratcheted EMA 1735ms) reads intl_slow=1/2 => batclass
    mixed => class UNCLASSIFIED on the WARN line — the fail-open direction
    (P1-only never pages; the label stops over-trusting SITE_SIDE)."""
    _base, config = mock_site
    _clear_floor_overrides(env)
    _seed_ref_state(env, {"cf": 1735.0, "ncf": 974.0, "dom": 304.0})
    _stage(config, 2150, 1063, 131)
    p = env["NFM_MONITOR_PROBE"] + ".p1"
    with open(p, "w", encoding="utf-8") as fh:
        fh.write("print('1 check(s) failed (0 P0 critical)')\n")
        fh.write("print('| ❌ Frontend Homepage | P1 | GET / | 5869ms exceeds limit 5000ms |')\n")
        fh.write("raise SystemExit(1)\n")
    env["NFM_MONITOR_PROBE"] = p
    r = run_script(env)
    assert r.returncode == 1, r.stdout
    warn = [ln for ln in log_text(env).splitlines() if "P1-only failures" in ln][-1]
    assert "class=UNCLASSIFIED" in warn
    assert "intl_slow=1/2" in warn
    assert "batclass=mixed" in warn


def test_1930_shape_suppresses_p0_page_as_transit(env, mock_site):
    """End-to-end AC3 row 2 / OBS-2 paging semantics: the 19:30:04Z shape
    (all three refs slow) on a nucpot P0 degrade at streak 2 now classifies
    TRANSIT_BROWNOUT and suppresses the CRITICAL page — pre-5720 this shape
    read CN_DOMESTIC (fail-open page). The nucpot streak keeps counting, so
    suppression only ever delays the page (5206262b(d) semantics)."""
    _base, config = mock_site
    _clear_floor_overrides(env)
    _seed_ref_state(env, {"cf": 801.0, "ncf": 691.0, "dom": 236.0})
    _stage(config, 2012, 1993, 656)
    _fail_probe(env, p0=2)
    assert run_script(env).returncode == 1  # streak 1 WARN carries the class
    warn = [ln for ln in log_text(env).splitlines() if "class=" in ln][-1]
    assert "class=TRANSIT_BROWNOUT" in warn
    r2 = run_script(env)  # streak 2: suppressed, log-only
    assert r2.returncode == 1, r2.stdout
    lt = log_text(env)
    assert not [ln for ln in lt.splitlines() if "site-monitor: CRITICAL" in ln]
    st = state_dict(env)
    assert st["level"] == "WARN" and st["streak"] == "2"
    assert st["pending_page"] in ("-", "")


def test_pristine_refs_with_floors_never_suppress(env, mock_site):
    """Soak-invariant pin (AC5 class): with the production floors live, a
    PRISTINE battery (all lanes at norm, none near any floor) on a nucpot
    P0 degrade at streak 2 classifies SITE_SIDE and the CRITICAL page fires
    exactly as before — the floors cannot manufacture suppression on clean
    references."""
    _base, config = mock_site
    _clear_floor_overrides(env)
    _prime(env, 3)
    _fail_probe(env, p0=2)
    assert run_script(env).returncode == 1
    assert "class=SITE_SIDE" in log_text(env)
    r2 = run_script(env)
    assert r2.returncode == 2, r2.stdout
    assert "CRITICAL — public-edge P0 failure confirmed" in log_text(env)
