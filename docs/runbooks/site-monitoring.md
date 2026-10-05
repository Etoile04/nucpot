# Site Monitoring — Two-Lane Architecture & Cadence Contract

Owners: SRE (detection/paging) · Lead Engineer (workflow & detector code)
References: NFM-18 (original monitor) · NFM-3337 (stale-container watchdog) ·
NFM-5226 (starvation root cause + on-host sentinel) · NFM-5270 (AutoVC target
fix) · **NFM-5309 (this contract + cadence watchdog)**

## 1. The two lanes

| | Primary (latency-critical) | Supplementary (this workflow) |
|---|---|---|
| Component | `~/bin/nfm-site-monitor.sh` launchd agent (NFM-5226) | `.github/workflows/site-monitor.yml` |
| Cadence | 5 min (host cron — not starvable by GitHub) | Hourly nominal `23 * * * *` + `17 */6` watchdog |
| Realistic delivery | 5 min (verified) | **~8 events/day** under chronic starvation |
| Detection contract | ≤10 min worst case (2-probe P0 confirm) | Best-effort; no latency contract |
| Vantage | The Mac Studio host (inside the tunnel) | **GitHub's network — outside the tunnel** |
| Alerting | Sentinel log → SRE heartbeat (2h) | GitHub issue (`issues:write`, P0 label) |
| Covers | All `scripts/health_check.py` targets | Public edge from outside + 6h container sanity (NFM-5269/5270) on the self-hosted runner |

The lanes fail independently: the GitHub lane is the only *external-vantage*
probe, which is why it is kept despite starvation — but nothing latency-
critical may depend on it.

## 2. Scheduled-event starvation (the constraint that shaped this)

GitHub drops the overwhelming majority of this repo's scheduled events
upstream of run creation — chronic, repo-wide, frequency-independent:

| Window | `*/10` monitor nominal 144/d | delivered | `17 */6` watchdog nominal 4/d | delivered |
|---|---|---|---|---|
| W39 census (NFM-5226) | 144/d | ~11/day | 4/d | — |
| W40 census (NFM-5309, 09-28→10-05) | 144/d | **7.9/day (5.5%)**, per-day 7/9/8/7/8/10/8/1 | 4/d | **5 runs in 14d (~9%)** |

All delivered runs succeed — the jobs never fail; the events never arrive.
Consequences:

- Higher-frequency crons buy **nothing** (delivery is ~8/day regardless), so
  NFM-5309 dropped the nominal from `*/10` to hourly. The workflow header now
  documents this; do not "restore" `*/10`.
- The `17 */6` entry's literal is **load-bearing**: the watchdog job's `if:`
  string-matches `github.event.schedule == '17 */6 * * *'`. Change one, break
  the other.
- Demonstrated cost of the blind spot: the 10-01 AutoVC SSL-EOF P0
  (NFM-5277, 18 min) was retro-filed ~2.5 h later instead of live-caught.

## 3. Cadence watchdog (under-firing now pages)

`scripts/site_monitor_cadence_check.py` — runs **outside** GitHub Actions
(a starved scheduler cannot be its own watchdog), installed via
`scripts/cron/site-monitor-cadence.cron` (every 4h at :41).

```
python3 scripts/site_monitor_cadence_check.py --json   # SRE §3 census record
# verdicts: OK · WARN (<4 runs/24h) · CRITICAL (0 runs/24h or newest >18h old)
# exit codes: 0 OK · 1 execution error (never a cadence page) · 2 WARN · 3 CRIT
```

Thresholds are census-derived (baseline ~8/day): WARN at half-baseline,
CRITICAL on silence. Tune via `--warn-below/--crit-below/--max-silence-hours`
only with a fresh `--json` census in hand. Logic is unit-tested in
`scripts/tests/test_site_monitor_cadence_check.py` (including the gh
`--created` exact-day-filter trap the first implementation hit).

## 4. KR-SRE-5 evaluation note (for SRE adjudication)

The re-check criterion "≥50% cadence sustained" was written against the
`*/10` nominal. Against the honest hourly schedule (28/day nominal with the
watchdog entry), current delivery (~8/day) is ~29% of nominal — the fraction
does not magically improve by lowering the denominator, and it should not be
gamed by starving the nominal further. Recommended reading: evaluate KR-SRE-5
Green on (a) the cadence watchdog reading OK continuously, or (b) the next P0
being live-caught by either lane — both already encoded in the NFM-5309
re-check clause. The KR definition is SRE's to adjudicate; this note only
flags the denominator change.

## 5. GitHub Support escalation bundle (Lane 2 — owner action)

Filing a support ticket requires the repo owner's account; Lead Engineering
prepared this bundle for the CTO/owner to file as-is.

- **Repo/workflow:** `Etoile04/nucpot` · `site-monitor.yml` (workflow id
  295249236, state active) · default branch schedules only.
- **Claim:** since at least 2026-09-14, scheduled events are delivered at
  ~5–9% of nominal across ALL scheduled workflows in the repo,
  frequency-independent (~8 events/day aggregate per workflow), while
  non-scheduled Actions run normally.
- **Evidence (repro):**
  ```
  gh run list --workflow site-monitor.yml --created "2026-09-28..2026-10-05" \
    --json createdAt,conclusion   # → 58 runs, all success; 7.9/day vs 144/day
  gh run list --workflow collect-prod-deploy-events.yml ...   # */5 → ~9/35h
  gh run list --workflow update-stale-prs.yml ...             # hourly → 23%
  ```
- **Ruled out (NFM-5226 diagnosis):** runner capacity (0s queue times),
  billing (public repo), repo-wide Actions outage (dispatch/other events
  fine), workflow syntax (delivered runs succeed 100%).
- **Ask:** confirm whether this repo is subject to scheduled-event
  throttling, and what delivery floor GitHub commits to.

## 6. Re-check protocol

Per NFM-5309: 7 days after this lands, SRE re-censuses
(`site_monitor_cadence_check.py --json`). Success = watchdog reads OK for the
window (no WARN/CRIT) AND no monitoring-blindness incident recurred; the
`*/10`-era expectation of 144/day is retired with this contract.
