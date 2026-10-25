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

## 7. Sentinel chunk-scan degraded contract (NFM-5446)

The host sentinel's NFM-5418 chunk-ref scan (5 edge-HTML pages + ref HEAD
verification) failed OPEN until 2026-10-11: it ran serially under a 240 s
deadline, and on deadline expiry it recorded `0 0 scan-deadline-exceeded
(fail-open)` as GREEN. During diurnal edge-interference windows
(NFM-5262, 5-10.4 s/request) the serial scan could never fit 240 s, so
BOTH watched lanes — P1 stale 1y-pins and P0 dead chunk refs — went
unverified while the sentinel read green for hours (observed: ~30
consecutive probes / 3 h 46 m with four pages still 1y-pinned).

Post-NFM-5446 contract (`scripts/nfm-site-monitor.sh`, mirrored to
`~/bin/nfm-site-monitor.sh`):

- **Parallel scan**: page GETs and ref HEADs (pool of 24, NFM-5490) run concurrently
  under one wall budget; the 240 s SIGALRM remains only as a backstop for
  pathological hangs.
- **Fail-closed-or-unknown**: a scan that cannot complete reports
  `scan-degraded` with rc 3 (`--scan-once` exit 3) — never green. Coverage
  caveats are explicit (`refs-unverified=N` — including refs beyond the
  per-page cap of 45 distinct refs, `page-fetch-skipped=…`, `all page
  fetches failed`).
- **TTL spot-check lane (always runs)**: each page's s-maxage/age verdict is
  captured from response headers before the body read, so stale-pin P1
  detection survives ref-budget exhaustion — pins surface as WARN even
  during degraded scans.
- **Only 4xx is a definitive dead ref (P0)**; 5xx/network errors count as
  unverified (transient origin trouble is owned by the base probe lane).
- **Skip-rate escalation**: `scan_skip_streak` in the sentinel state counts
  consecutive degraded scans; ≥3 (~15 min at 300 s cadence, matching
  STALE_SENTINEL_SECONDS) escalates to a CRITICAL `sentinel chunk-scan
  blind` page (monitoring-blindness class, latched `pending_page`, acked
  via `--ack-page` like other CRITICALs). A completing scan resets the
  streak and logs a RECOVERY if it ended a blind episode.

SRE readers: a `WARN — chunk-scan DEGRADED` line means the P0 lane was
unverified for that cycle (investigate if it persists); `CRITICAL —
sentinel chunk-scan blind` pages the sentinel itself. The NFM-5418
close-gate keeps requiring direct fresh-connection reads (s-maxage=300 on
all watched pages) — sentinel green is necessary, not sufficient.

**REF_CAP tuning (2026-10-11):** the per-page verification cap is a
green-blocking ceiling — a page carrying more distinct chunk refs than
REF_CAP truncates the tail into `refs-unverified`, so the scan degrades on
EVERY cycle and the skip-streak CRITICAL latches permanently. This is
deterministic (calm edge, all sampled HEADs 200s at 1–2 s), unlike a
latency-window degradation. Observed: live `/materials` grew to 40
distinct refs under the original cap of 30 → 10 refs dropped per scan →
permanent `sentinel chunk-scan blind` from 10:47Z until the cap was raised
to 45. **Operational rule: `refs-unverified=N > 0` persisting across scans
while spot-check HEADs are fast means the site outgrew REF_CAP — raise it
(keep ~10% headroom above the largest page's distinct-ref count) rather
than acking the page.** Raising the cap increases worst-case scan cost
(5 × REF_CAP HEADs through the pool); the 240 s budget still bounds it
and a genuinely slow edge degrades exactly as before.

**Latency hardening (NFM-5490, 2026-10-12):** episodes 1–4 (census: 2/day
in the NFM-5262 latency regime) were adjudicated benign — P0 lane clean
mid-blindness, fail-closed intact — but the ep-4 *live* adjudication added
decisive evidence: patient-client HEADs from the same vantage returned
40/40 HTTP 200 while the sentinel sat at refs-unverified=38. The blindness
was partly self-inflicted client fragility, not edge impossibility. The
HEAD verifier therefore (a) retries TRANSPORT failures once, budget-gated
(SSL UNEXPECTED_EOF / handshake-reset / tail-latency timeout — the ep-4
class), (b) runs REF_TIMEOUT at 15 s to cover the observed 5–10.4 s
diurnal tail (was 8 s — below the tail, so the whole window converted to
unverified), and (c) pools 24 parallel HEADs (was 12) so ~150 scan-wide
refs at 5–10 s each still drain inside the post-page-stage budget. Verdict
semantics are unchanged: a definitive 4xx is still an immediate P0, a 5xx
is still unknown (base-probe lane owns outages), and persistent transport
failure or budget exhaustion still degrades fail-closed — the retry can
only convert a genuinely-recovered transport error into a real 200, never
manufacture a false green. This is deliberately the OPPOSITE direction
from the NFM-5488-rejected skip-rate allowance: verify more, never mask.
The monitor User-Agent stays byte-identical — it selects the edge's
monitor-UA HTML variant, and changing it would reset the pin-aging the
TTL lane watches.

**Page-lane retry (NFM-5572, 2026-10-18):** the 2026-10-17 mode-B census
(7 `page-fetch-skip` cycles in one day, adjudicated in NFM-5571; every
event carried refs-unverified=0, i.e. ~150 ref HEADs verified in-budget in
the same cycles) showed the page GET lane had the exact defect shape
NFM-5490 fixed for ref HEADs: one transport exception on ONE page GET
(diurnal tail timeout / connection reset / TLS-EOF) → `page-fetch-skipped`,
fetches=4/5, that page's chunk refs unverified for one self-healing 5-min
cycle. `fetch_page()` now retries ONCE on transport exception only, gated
against PAGE_STAGE_BUDGET (one more full PAGE_TIMEOUT must fit); a
definitive HTTP verdict is never retried away, and a skip after the retry
still counts as skipped — the retry can only turn a genuinely-dead
transport error into a real 200, never a false green. Worst case +15 s per
page; the 90 s stage budget and 240 s DEADLINE are unchanged. All 7 of the
10-17 skips recovered on the immediately following cycle, so a single
retry collapses most events; post-deploy, page-fetch-skip day-counts
should return to the ≤1/day band (10-14..10-16 base rate).

**Adjudication extraction note (ep-4 playbook addition):** RSC-payload
(`self.__next_f`) chunk refs are JSON-escaped (`…js\"`). A naive
`[^"']+` capture includes the trailing backslash → HEADing `URL\` yields
phantom 404s (ep-4: 34 apparent dead refs → 0 real after
`rstrip('\\')`). The sentinel's own `ref_re` character class cannot
capture a backslash, so shipped extraction is immune; manual/ad-hoc
adjudication regexes must normalize before HEADing.

**Egress independence (ADR-023 / NFM-5542, 2026-10-15):** SUPERSEDED —
the 2026-10-14 site-monitor blind windows earlier attributed to CN-side
ClientHello DPI were LOCAL proxy-vantage defects (the sentinel's python
lanes rode the macOS system proxy, v2cloud `127.0.0.1:7892`; see
`docs/adr/ADR-023-NFM-5540-sentinel-egress-independence.md`). This
supersession is site-monitor-scope only — ADR-013's broker/GitHub DPI
rulings are a different subsystem and remain valid. Post-ADR-023 the
sentinel's python egress is DIRECT as the primary lane everywhere
(no-proxy openers in `health_check.py` and the scan heredoc; wrapper
exports `no_proxy='*'` as defense-in-depth), so a VPN flap can no longer
blind any lane. Every sentinel run logs a `proxy-state` line at startup
and every degraded cycle carries `scutil-proxy: …` adjacent to its
`mode=/fetches=` evidence: **the next flap adjudicates host-egress vs
edge in one log line** — a healthy scutil proxy state on a mode-A blind
cycle points at the direct lane/edge, an unhealthy one at host egress.
The NFM-5525 confirmation ladder stays as the failure-path cross-check
for direct-lane transients.

## 8. Path-independent reference battery (NFM-5708 / NFM-5706)

Motivation: the 2026-10-24 15:46–16:33Z multi-route P1 burst was
indistinguishable from site-side degradation without a MANUAL reference
battery — both sentinel hostnames ride ONE CN-to-CF-AMS transit path, so
multi-hostname ≠ multi-path. Post-NFM-5708 the monitor runs a reference
battery on every real-probe tick and classifies each nucpot DEGRADE tick
against it.

- **Lanes** (3 parallel lightweight GETs riding the existing 300 s tick —
  no new schedule/daemon; worst case +REF_TIMEOUT per tick): int'l CF-edge
  (`www.cloudflare.com`), int'l non-CF (`ghcr.io`), CN-domestic control
  (`mirrors.ustc.edu.cn`; NFM-5712 swap 2026-10-25 — tuna.tsinghua.edu.cn
  proved permanently unreachable from this vantage). Roles are fixed — the
  classifier depends on
  them. Egress is DIRECT per [ADR-023](../adr/ADR-023-NFM-5540-sentinel-egress-independence.md)
  (no-proxy opener primary, wrapper `no_proxy='*'` defense-in-depth), so a
  VPN flap cannot fake a brownout. Redirects are disabled: a 30x/4xx/5xx
  is a definitive transport verdict and counts as a healthy path sample;
  only transport failure reads UNAVAILABLE — a distinct token, so the
  battery never emits `FAIL` and naive `grep FAIL` beat scans (NFM-4951
  class) stay clean while only a reference is unreachable.
- **Baselines** are per-ENDPOINT RELATIVE EMAs in
  `~/.nfm-site-monitor.ref.state` (own file — the main state family is
  untouched). A lane is slow iff the sample ≥ 2.5× that host's OWN EMA
  (`NFM_REF_SLOW_RATIO`), never an absolute latency. Per-host outcome is
  three-valued (NFM-5712): COMPLETED (any received HTTP status — 2xx/3xx/4xx
  all count as a latency sample) / SLOW (completed but over the relative
  threshold) / UNAVAILABLE (connection-level failure: URLError, TLS, no
  response). A failed probe never touches the baseline and reads
  UNAVAILABLE — never slow — so a dead lane can never manufacture
  slow-based classes. Warmup is ATTEMPT-based (≥ `NFM_REF_BASELINE_MIN`=10
  attempts): a permanently unreachable lane cannot hold the battery in
  warmup forever, and during warmup the battery never suppresses a page.
  The state file carries a `<lane>_url` marker per lane: re-pointing a lane
  at a different URL starts a FRESH baseline (stale prior-host entries are
  dropped on the next rewrite), so after an endpoint swap expect a fresh
  warmup (~50 min at 300 s cadence) for the swapped lane, during which the
  domestic leg records only — warmup dominates the batclass, so the battery
  emits no confident verdict until the new EMA forms.
- **Classification** rides every nucpot DEGRADE log line as
  `[NFM-5706 class=<C>; refs: <per-host timings>]`. Full hint enum (CPO
  charter addendum, folding NFM-5707): `TRANSIT_BROWNOUT` / `SITE_SIDE` /
  `CN_DOMESTIC` / `clean`, with `UNCLASSIFIED` as the fail-open floor.
  The internal battery verdict (`batclass=` in `--ref-probes-once`)
  additionally carries `control_unavailable` — the CN-domestic control
  lane failed at the transport level (see below):
  - `TRANSIT_BROWNOUT` — nucpot slow + ALL int'l refs slow + domestic
    pristine on a TRUSTED baseline (an untrusted-but-succeeding dom lane
    reads slow=0 at any latency — its EMA may be seeded mid-degradation —
    so it must not satisfy the conjunct; the verdict falls back to
    mixed ⇒ `UNCLASSIFIED`, paging fail-open) ⇒ shared CN→int'l transit
    trouble, NOT site-side: LOG-ONLY WARN, NON-PAGING, no SRE wake
    (ruling 5206262b(d) + NFM-5705 R5). It
    gates ONLY the streak≥2 base-lane CRITICAL page; the failure streak
    keeps counting, so the existing page fires the moment transit recovers
    or the classification clears — suppression can only DELAY the page,
    never consume it.
  - `SITE_SIDE` — refs pristine while nucpot degraded ⇒ the §1 detection
    contract and watch-line escalation apply unchanged; the class token
    and reference latencies ride the log lines as evidence.
  - `CN_DOMESTIC` — the CN-domestic control (ustc) COMPLETED but slow
    (NFM-5712: a control that failed at the transport level reads
    UNAVAILABLE, never this class) ⇒
    HINT class: int'l comparison is UNRELIABLE in that state (domestic
    trouble usually slows the int'l lanes too), so the tick is not
    force-fit into the two main classes. NO suppression — existing paging
    path runs fail-open exactly like UNCLASSIFIED; the evidence token
    carries `(intl comparison unreliable)`.
  - `clean` — battery verdict when ALL lanes are pristine (observable as
    `batclass=clean` in `--ref-probes-once` output). On a nucpot DEGRADE
    tick a clean battery maps to `SITE_SIDE`. On a GREEN tick a clean
    battery logs nothing (OK lines byte-stable); a CONFIDENT non-clean
    verdict (`intl_brownout` / `cn_domestic`) logs one `NOTE — ref-battery`
    line — recording only, zero page implications. This covers the
    "int'l degraded but nucpot clean" observability row. Likewise, an
    UNAVAILABLE control on an otherwise classifiable tick logs one
    `NOTE — CONTROL_UNAVAILABLE` line (recording only, non-paging): the
    domestic-pristine precondition is unverifiable, so `TRANSIT_BROWNOUT`
    is unemittable that tick and the DEGRADE classification falls to
    `UNCLASSIFIED` fail-open; an UNAVAILABLE int'l lane likewise forces
    mixed ⇒ `UNCLASSIFIED` — suppression needs certainty, so a transport
    failure can never manufacture the ALL-int'l-slow conjunct.
  - `UNCLASSIFIED` — battery cannot testify (warmup / mixed int'l
    evidence / internal error) ⇒ existing paging path runs fail-open.
- **Replay validation (10-24 window):** the classifier is pinned by a
  table-driven replay of the documented 2026-10-24 15:35–16:32Z burst
  (cloudflare 8.25/3.54/3.01 s vs sub-second norm; ghcr.io 2.15 s vs
  0.77 s baseline; tuna 0.19 s pristine; nucpot degraded ⇒ must classify
  `TRANSIT_BROWNOUT`) plus the boundary rows (control completed-but-slow +
  nucpot degraded ⇒ `CN_DOMESTIC`; all clean ⇒ `clean`/`SITE_SIDE`; int'l
  degraded + nucpot clean ⇒ green-tick NOTE). The replay pins the 10-24
  control host (tuna) as historical data; NFM-5712 adds truth-table rows
  for the three-valued control semantics (control FAIL ⇒ never
  `CN_DOMESTIC`, `TRANSIT_BROWNOUT` unemittable, one recording-only
  `CONTROL_UNAVAILABLE` NOTE on green ticks). Run:
  `python3 -m pytest scripts/tests/test_nfm5708_replay_20261024.py -v`.
- **SRE diagnostic:** `--ref-probes-once` runs the battery once, prints
  per-host timings + classification tokens, and updates ONLY the reference
  baselines — never the main state (rc 0 ran / 3 internal error).
- Suppressed (brownout) ticks do not advance the reminder clock, so the
  first post-suppression CRITICAL is labeled correctly.
- Tunables: `NFM_REF_TIMEOUT`, `NFM_REF_SLOW_RATIO`,
  `NFM_REF_BASELINE_MIN`, `NFM_REF_{CF,NONCF,DOM}_URL`,
  `NFM_MONITOR_REF_STATE`. Full threshold derivations and the drift policy
  (ALPHA=0.125 healthy / ALPHA_SLOW=0.03 above-ratio) live in the
  NFM-5708 header block of `scripts/nfm-site-monitor.sh`.
