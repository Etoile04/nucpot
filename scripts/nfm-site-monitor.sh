#!/usr/bin/env bash
# nfm-site-monitor.sh — NFM-5226 public-edge site monitor sentinel.
#
# Why: GitHub Actions scheduled-event delivery for this repo is chronically
# starved repo-wide (~10 of 144 expected */10 firings/day; */5 and hourly
# crons equally starved; queue times 0s; public repo; GH status green).
# The site-monitor workflow cannot be made to fire on schedule from inside
# the repo, so the primary detection lane moves on-host. The GitHub cron
# stays as a free best-effort supplement; no repo/workflow change.
#
# Alarm contract (documented on NFM-5226):
#   - Probe cadence: every 300s via ~/Library/LaunchAgents/com.nfm.site-monitor.plist
#   - Detection: P0 failure confirmed after 2 consecutive failing probes
#     -> "[ts] site-monitor: CRITICAL ..." line below (worst case ~10 min)
#   - Escalation: SRE Monitor heartbeat (2h) reads this log; CRITICAL lines
#     page SRE -> [SRE-CRITICAL] Paperclip issue to Lead Engineer.
#     Worst-case alert latency = 10 min detect + 2h pickup ~= 2h10m.
#   - Missed-page reconciliation (NFM-5262, 2026-09-28): a brief P0 that
#     recovers before heartbeat pickup is invisible to a green read, so the
#     sentinel latches pending_page at the CRITICAL transition and logs
#     "PAGE-UNACK" on recovery. It stays latched (and is re-stated by every
#     RECOVERY while latched) until the SRE heartbeat retro-files the
#     [SRE-CRITICAL] issue and runs `--ack-page`. Guarantees issue-filing
#     <= 2h10m even for self-recovered incidents (2026-09-28 07:30Z class).
#   - Sentinel liveness: an OK/WARN/CRITICAL line is written EVERY run; if the
#     newest line is older than 15 min, treat the sentinel itself as dead
#     (monitoring-blindness guard).
#   - P1-only failures: WARN lines, never CRITICAL (matches workflow semantics
#     where issue creation requires p0_failures > 0). The NFM-5418 stale-pin
#     lane is P1: watched-page edge copies still carrying s-maxage>3600 are
#     WARN-only (functional-but-stale HTML) — with the legacy-chunk shim
#     (NFM-5442) serving dead refs, this lane is what makes "purge landed"
#     observable (WARN -> OK) until the edge re-caches at 300s.
#
# NFM-5446 (2026-10-11) — chunk-scan fail-open fix:
#   - Defect: the 5-page chunk-ref scan ran page GETs and ref HEADs fully
#     SERIALLY under a 240s SIGALRM whose expiry handler printed
#     "scan-deadline-exceeded (fail-open)" with rc 0 = GREEN. During diurnal
#     edge-interference windows (NFM-5262, 5-10.4s/request) the serial scan
#     can never fit 240s, so BOTH watched lanes (P1 stale 1y-pins, P0 dead
#     chunk refs) went silently unverified while the sentinel read green
#     (~30 consecutive probes / 3h46m observed live 2026-10-11).
#   - Fix: pages and ref HEADs now run in PARALLEL (bounded pool), and a
#     scan that still cannot complete within its budget reports
#     scan-degraded with rc 3 — never green. Definitive findings gathered
#     before the budget ran out (dead refs, TTL pins) are still reported.
#   - TTL spot-check lane (always runs): each page's s-maxage/age verdict is
#     taken from response HEADERS before the body is read, so stale-pin P1
#     detection survives ref-verification budget exhaustion.
#   - Skip-rate alert: >= SCAN_SKIP_CRITICAL consecutive degraded scans
#     (>=3, ~15 min at 300s cadence — matches STALE_SENTINEL_SECONDS)
#     escalates to a CRITICAL sentinel-blind page (same monitoring-blindness
#     class as the sentinel-broken internal-error lane). A completing scan
#     resets the streak and logs a RECOVERY if it ended a blind episode.
#   - Env seams (test-only; unset in production => identical behavior):
#     NFM_SCAN_BASE, NFM_SCAN_DEADLINE, NFM_SCAN_REF_POOL, NFM_MONITOR_LOG,
#     NFM_MONITOR_STATE, NFM_MONITOR_PROBE, NFM_MONITOR_NOTIFY
#     (scripts/tests/test_nfm_site_monitor.py refuses to run without them).
#   - Gate-review hardening: the ref-cap check iterates `bad` under `lock`
#     (a lock-free sum raced concurrent appends and a RuntimeError in the
#     worker would silently DROP a real P0 finding); final reporting and the
#     SIGALRM backstop read findings through a retrying lock-free snapshot
#     (the handler must never take `lock` — the interrupted main thread may
#     hold it) and the backstop now reports verified P0s (rc 1) instead of
#     hardcoding p0=0; snapshot failure is degraded-unknown, never green.
#
# NFM-5454 (2026-10-11, SRE tuning) — single-ref flake threshold:
#   - Post-#1469 data (2026-10-11): every noise-grade degraded scan had
#     refs-unverified=1 (a single ref HEAD error, NFM-5262 diurnal class;
#     9 occurrences; the 17:48Z sentinel-blind CRITICAL fired on three =1
#     probes — ~2 pages/day at that sensitivity). Real failures the same
#     day were multi-ref (=9, =12) or cap-era (=10).
#   - Change: refs-unverified degrades only at >= REFS_DEGRADE_MIN (2). A
#     lone unverified ref logs a NOTE-tagged ok line instead (drift watch:
#     a PERSISTENT single = cap/edge investigation, runbook §7).
#     Over-budget truncation is counted separately (refs-truncated) and
#     degrades at ANY count — the cap-outgrown signal stays fail-closed.
#     Definitive 4xx dead refs remain immediate P0 (rc 1) — untouched.
#     SCAN_SKIP_CRITICAL stays 3: blindness detection latency unchanged.
#
# NFM-5497 (2026-10-13) — blind-streak edge-flap classifier:
#   - Problem: the NFM-5446 skip-streak lane counted ANY 3 consecutive
#     degraded chunk-scan cycles as sentinel-blind and paged CRITICAL —
#     including benign diurnal edge-flap bursts (NFM-5262) whose cycles
#     merely starve ref HEADs while pages fetch fine (ep-5, 2026-10-13:
#     all-fetches-failed, then refs-unverified=12, then =2 -> CRITICAL
#     page at ~daily cadence; NFM-5493 retro, NFM-5488 triggers 2/3 hit).
#   - Fix: each degraded cycle is classified by fetch coverage, which the
#     scan itself emits as a "fetches=<fetched>/<pages>" token:
#       mode A all-fetches-failed — 0 of 5 page GETs completed (genuine
#         edge-down class): 3 consecutive mode-A cycles -> CRITICAL with
#         semantics byte-identical to the pre-classifier build (threshold
#         SCAN_SKIP_CRITICAL=3, recovery, ack unchanged). A degraded
#         report carrying no fetch evidence also classifies mode A —
#         fail-closed. Launcher failures (no scan report at all) leave
#         the per-mode counters untouched and never page from this lane,
#         exactly as before.
#       mode B refs-unverified-only — >=1 page fetch completed: 3
#         consecutive mode-B cycles -> WARN only (no PAGE-UNACK latch, no
#         page). Escalates to CRITICAL iff a definitive P0 FAIL (dead
#         chunk refs / P0 probe failure — cache-cold-visitor breakage,
#         page-worthy per NFM-5446) lands within/overlapping the streak
#         window (state blind_p0_fail; set by any definitive P0 failure,
#         cleared by a fully green cycle). P0 unknown/skipped never
#         escalates — blind != failed.
#   - Streak counters are per-mode and strictly consecutive (a cycle of
#     one mode increments its counter and resets the other's); any
#     completed scan resets both. A cycle recording a NOTE
#     (refs-unverified < REFS_DEGRADE_MIN, NFM-5454) never enters any
#     streak and needs no mode tag.
#   - Every degraded cycle logs mode=/fetches_failed=/refs_unverified=/
#     p0=/origin= evidence so calibration data accrues for future tuning
#     (NFM-5454 pattern).
#
# NFM-5542 (2026-10-15, ADR-023) — sentinel egress independence:
#   - Ground truth (CTO, NFM-5540 → parent NFM-5541): this sentinel had
#     THREE python egress lanes with inconsistent proxy posture. Bare
#     urllib rides the macOS system proxy (v2cloud 127.0.0.1:7892 via
#     scutil): the 2026-10-15 02:12–02:26Z chunk-scan false CRITICAL
#     (fetches=0/5, mode=A ×3, origin=ok) and both 2026-10-14 blind
#     windows were LOCAL VPN-proxy vantage defects, not site trouble.
#     RECORDS CORRECTION: the 10-14 "CN-side ClientHello DPI" attribution
#     for the site-monitor windows is SUPERSEDED — local monitoring defect
#     (ADR-013's broker/GitHub DPI rulings are a different subsystem and
#     stay valid).
#   - ADR-023: DIRECT IS PRIMARY for every python lane. (D1) the chunk-scan
#     heredoc below fetches pages and HEADs refs through one shared
#     no-proxy opener (scripts/health_check.py::check_url + send_alert got
#     the same opener); (D2) no_proxy='*' / NO_PROXY='*' are exported below
#     so a future bare urlopen cannot silently re-couple through the
#     ENV-VAR proxy class — note the macOS sysconf proxy is only bypassed
#     by the explicit opener, which is why D1 is primary and D2 is
#     defense-in-depth. curl (the NFM-5525 confirmation lane) honors
#     no_proxy too and was already effectively direct (green in every
#     episode, so origin-check semantics are unchanged).
#   - (D3) observability: a one-line /usr/sbin/scutil --proxy summary is
#     logged at sentinel startup and rides every degraded cycle's
#     mode=/fetches= evidence token, so the next flap adjudicates
#     host-egress vs edge in ONE log line.
#
# NFM-5667 (2026-10-20) — streak-1 full-edge page rule (NFM-5666 code half;
# CTO ruling NFM-5664 comment 35aa85eb §4, 2026-10-20T00:15Z):
#   - Gap: the base-probe lane needs STREAK_THRESHOLD=2 consecutive failing
#     probes before paging, so a full-edge P0 that recovers inside one probe
#     interval goes UNPAGED (Ep#4 20:03Z + Ep#5 20:19Z 10-19: both real
#     upstream-blackhole outages, sentinel line read "6 checks failed, 2 P0,
#     hard status codes (502/530)" at streak 1 -> WARN only).
#   - Rule: PAGE at streak 1 IFF ALL FOUR conjuncts hold on the current
#     failing probe: (1) >=5 of the 6 EDGE checks failed (the two monitored
#     hostnames; the P2 Supabase third-party check is outside the rule);
#     (2) >=2 P0 checks among them; (3) the failure set spans BOTH hostnames
#     (full-edge; NFM-5262 diurnal flaps are partial and latency-class and
#     never match); (4) dominant failure class is hard edge status codes
#     (502/530 and other 52x/53x) — latency-timeout-dominant sets are
#     EXCLUDED and keep the streak>=2 lane. Population basis: 3/3 prior
#     signature matches were real outages.
#   - Rate limit: max 1 page / 30 min for this class, from a counter
#     INDEPENDENT of the streak>=2 lane (S1_PAGE_COOLDOWN; the streak>=2
#     lane's pacing — REMINDER_RUNS — never consumes it and vice versa).
#   - Page payload (ruling amendment 1): window timestamps (first-failure at
#     page time; the green run completes the window on its RECOVERY line),
#     per-hostname status-class counts, the rolling-30d full-edge episode
#     count toward ADR-025 D2 trigger T2 with the ledger delta, and the
#     NFM-5664 ruling reference — pages double as T2 counting evidence.
#   - T2 ledger basis (ruling §2): BY-EPISODE — distinct full-edge P0
#     windows separated by >=10 min of green each count once. Episode state
#     (open/close/ledger) lives in its OWN state file (NFM_MONITOR_S1_STATE)
#     so the streak>=2 lane's state format and every pre-existing
#     state_write call site stay byte-identical (AC4). The ledger may be
#     seeded at cutover by writing ISO timestamps into that file (the SRE
#     sibling owns the deploy/verify half); unpruned history older than 30d
#     drops out of the count on the next append.
#   - The existing streak>=2 lane is UNCHANGED: a streak-1 page latches
#     pending_page (same missed-page reconciliation) but never resets or
#     rewrites the streak counter, and a later streak-2 CRITICAL pages per
#     its own semantics — the streak-1 lane fires at most its one
#     rate-limited page per match.
#   - 14-day precision review clause (ruling amendment 2): any false-
#     positive page reverts the rule to streak>=2 for the affected subclass
#     pending CTO re-ruling — the NOTE-tagged suppression/keep lines below
#     accrue the calibration evidence.
#
# NFM-5708 (2026-10-25, carrier for NFM-5706; CTO ruling NFM-5705 R5 +
# watch-line (d) amendment to ruling 5206262b on NFM-5664) — path-independent
# reference probes:
#   - Gap: 2026-10-24 15:46–16:33Z multi-route P1 burst was indistinguishable
#     from site-side degradation without a MANUAL reference battery
#     (NFM-5704/5705). Both sentinel routes ride ONE CN-to-CF-AMS transit
#     path, so multi-hostname != multi-path.
#   - Lanes (3 lightweight GETs, parallel, riding the EXISTING tick cadence —
#     no new schedule/daemon; worst case +REF_TIMEOUT on a tick):
#       cf  = https://www.cloudflare.com   (int'l CF-edge)
#       ncf = https://ghcr.io              (int'l non-CF)
#       dom = https://tuna.tsinghua.edu.cn (CN-domestic control)
#   - Per-host RELATIVE baselines (EMA of healthy samples) live in
#     ~/.nfm-site-monitor.ref.state — thresholds are sample >= SLOW_RATIO x
#     that host's OWN baseline, never absolute latencies. A lane is slow iff
#     sample >= 2.5x baseline (10-24 battery: ghcr 2.15s vs 0.77s = 2.8x,
#     cloudflare 8.25/3.54/3.01s vs ~0.3s; ratio 2.5 catches both with the
#     fail-open direction — only suppression needs certainty). Failed probes
#     never touch the baseline and read SLOW once the battery is out of
#     warmup (>= BASELINE_MIN=10 probes, ~50 min at 300s cadence; warmup is
#     attempt-based, so a permanently unreachable lane cannot hold the
#     battery in WARMUP forever; during warmup it never suppresses a page).
#   - Classification (emitted on every nucpot DEGRADE tick in the log line as
#     "[NFM-5706 class=<C>; refs: <per-host timings>]" — full hint enum per
#     the CPO charter addendum (NFM-5708, folding NFM-5707):
#     TRANSIT_BROWNOUT / SITE_SIDE / CN_DOMESTIC / clean (+ UNCLASSIFIED as
#     the fail-open floor). How to read it:
#       TRANSIT_BROWNOUT = nucpot slow + ALL int'l refs slow + domestic
#         pristine => LOG-ONLY WARN, NON-PAGING, no SRE wake (5206262b(d),
#         NFM-5705 R5). Gates ONLY the streak>=2 base-lane CRITICAL branch;
#         the nucpot failure streak keeps counting, so the moment transit
#         recovers (or classification clears) the existing page fires.
#       SITE_SIDE = nucpot slow + refs pristine => existing watch-line
#         escalation per 5206262b as amended — page semantics unchanged,
#         class + reference latencies ride the log lines as evidence.
#       CN_DOMESTIC = the CN-domestic control (tuna) itself is degraded =>
#         HINT class only: int'l comparison is UNRELIABLE in that state (a
#         dead/slow domestic lane usually means host-egress or CN-domestic
#         path trouble, which also slows the int'l lanes), so the tick is NOT
#         force-fit into TRANSIT_BROWNOUT/SITE_SIDE. NO suppression — the
#         existing paging path runs fail-open exactly like UNCLASSIFIED; the
#         evidence token carries "(intl comparison unreliable)".
#       clean = battery verdict when ALL lanes are pristine (observable in
#         --ref-probes-once output as batclass=clean). On a nucpot DEGRADE
#         tick a clean battery maps to SITE_SIDE; on a GREEN tick the OK line
#         stays byte-stable (no token). A CONFIDENT non-clean battery verdict
#         on a green tick (intl_brownout / cn_domestic) logs one NOTE line —
#         recording only, zero page implications.
#       UNCLASSIFIED = battery could not testify (warmup / mixed int'l
#         evidence / ref lane internal error) — existing paging path,
#         fail-open.
#   - ADR-023 compliance: reference lanes are pinned DIRECT via the explicit
#     no-proxy opener (immune to BOTH the env-var proxy class and the macOS
#     scutil system proxy) plus the wrapper's no_proxy='*' export. Known
#     confounder (documented per NFM-5705): the user VPN at 127.0.0.1:7892
#     hard-fails TLS to nucpot.dpdns.org (6/6 SSL_ERROR_SYSCALL) — these
#     lanes must never ride it, so a VPN flap cannot fake a brownout.
#   - Baseline drift policy: samples below SLOW_RATIO fold into the EMA at
#     ALPHA=0.125; above-ratio samples fold at ALPHA_SLOW=0.03 only, so a
#     1-2h brownout cannot re-baseline the references mid-incident while a
#     genuine multi-hour drift still converges.
#   - Streak-1 full-edge rule (NFM-5667) and the scan-blind lanes
#     (NFM-5446/NFM-5497) are UNTOUCHED; the reference battery appends
#     evidence to the streak-1 page payload but never gates it.
#
# Probe: python3 scripts/health_check.py from the production host checkout
# (stdlib-only, py3.9-compatible). Run WITHOUT ALERT_WEBHOOK (not present on
# host) so no webhook is attempted. Exit 0 = all pass, 1 = failures
# ("N check(s) failed (M P0 critical)" on stdout).
#
# Modes (house style of nfm-4599-expiry-alert.sh):
#   (no args)        — real probe + state machine
#   --simulate-failure  — exercise WARN->CRITICAL logging through the same
#                         logging path with TEST prefix, no real state change
#   --simulate-recovery — exercise RECOVERY logging, TEST prefix
#   --scan-once      — run the NFM-5418 chunk-ref scan once, print result,
#                      no state change (SRE diagnostic).
#                      rc 0 clean / 1 findings / 3 degraded-unknown (NFM-5446)
#   --ref-probes-once — run the NFM-5706 reference battery once and print the
#                      per-host timings + classification tokens (SRE
#                      diagnostic; updates the reference baselines, never
#                      the main state; rc 0 ran / 3 internal error)
#   --ack-page       — SRE heartbeat clears a latched PAGE-UNACK after it has
#                      retro-filed the [SRE-CRITICAL] issue (logs PAGE-ACK)
#
# Exit codes: 0 OK / 1 WARN (streak 1 or P1-only) / 2 CRITICAL / 3 internal error
#
# Why absolute paths: macOS PATH for GUI-launched agents is sparse (NFM-4579).

set -uo pipefail

LOG="${NFM_MONITOR_LOG:-$HOME/Library/Logs/nfm-site-monitor.log}"
STATE="${NFM_MONITOR_STATE:-$HOME/.nfm-site-monitor.state}"
# NFM-5708: reference-battery baselines — own file in the state family so
# every pre-existing state_write call site stays byte-identical (NFM-5667
# pattern for S1_STATE).
REF_STATE="${NFM_MONITOR_REF_STATE:-$HOME/.nfm-site-monitor.ref.state}"
PROBE="${NFM_MONITOR_PROBE:-/Users/lwj04/Projects/nucpot/scripts/health_check.py}"
if [[ -x /opt/homebrew/bin/python3 ]]; then
  PY=/opt/homebrew/bin/python3
else
  PY=/usr/bin/python3
fi

# NFM-5542 (ADR-023) D2: pin python egress to direct, defense-in-depth.
# no_proxy='*' neutralizes ENV-VAR proxies for urllib (the
# getproxies_environment path) and for curl; the macOS system-configuration
# proxy (scutil) is only bypassed by the explicit no-proxy openers (D1) —
# this export stops a future bare urlopen from silently re-coupling through
# the env-var class. Applies to every python invocation below (base probe +
# chunk-scan heredoc).
export no_proxy="*"
export NO_PROXY="*"

STREAK_THRESHOLD=2      # consecutive failing probes before CRITICAL
REMINDER_RUNS=12        # ~1h reminder cadence while CRITICAL persists
INTERNAL_STREAK_CRITICAL=6  # ~30 min of internal errors -> sentinel-broken page
STALE_SENTINEL_SECONDS=900  # reader-side liveness bound (15 min)
SCAN_PAGES="/ /datasets /browse /potentials /materials" # NFM-5418: edge-HTML pages whose chunk refs get verified; NFM-5438 (2026-10-09): +/potentials (3rd 1y-pinned page w/ dead refs, alias-served via LEGACY_ALIAS_REWRITES) +/materials (1y-pinned latent)
SCAN_SKIP_CRITICAL=3    # NFM-5446: consecutive degraded scans -> sentinel-blind CRITICAL (~15 min at 300s cadence)

NOW="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

mkdir -p "$(dirname "$LOG")"

log() {
  printf '[%s] site-monitor: %s\n' "$NOW" "$*" >> "$LOG"
}

# Local notification lane (NFM-5262): the human works on this host; surface a
# CRITICAL transition immediately instead of waiting for the 2h heartbeat.
# Best-effort only — notification failure must never break monitoring.
notify_page() { # summary
  "${NFM_MONITOR_NOTIFY:-/usr/bin/osascript}" -e "display notification \"$1\" with title \"nucpot site-monitor CRITICAL\"" >/dev/null 2>&1 || true
}

# NFM-5542 (ADR-023) D3: one-line macOS proxy posture summary. Absolute
# paths (/usr/sbin/scutil, /usr/bin/awk) — GUI-launched agents have a sparse
# PATH (NFM-4579). Logged at sentinel startup and adjacent to every degraded
# cycle's mode=/fetches= evidence so the next VPN flap adjudicates
# host-egress vs edge in one line. scutil unavailable (non-macOS hosts)
# reads "unknown" — best-effort observability, never a monitoring dependency.
proxy_state_line() {
  local scutil_bin summary
  scutil_bin="${NFM_MONITOR_SCUTIL:-/usr/sbin/scutil}"
  summary="$("$scutil_bin" --proxy 2>/dev/null | /usr/bin/awk '
    $1 == "HTTPEnable"  && $3 == 1 { eh = 1 }
    $1 == "HTTPProxy"   { eh_h = $3 }  $1 == "HTTPPort"  { eh_p = $3 }
    $1 == "HTTPSEnable" && $3 == 1 { es = 1 }
    $1 == "HTTPSProxy"  { es_h = $3 }  $1 == "HTTPSPort" { es_p = $3 }
    $1 == "SOCKSEnable" && $3 == 1 { ek = 1 }
    $1 == "SOCKSProxy"  { ek_h = $3 }  $1 == "SOCKSPort" { ek_p = $3 }
    END {
      if (NR == 0) exit
      out = ""
      if (eh) out = out " http=" eh_h ":" eh_p
      if (es) out = out " https=" es_h ":" es_p
      if (ek) out = out " socks=" ek_h ":" ek_p
      if (out == "") print "scutil-proxy: none"
      else print "scutil-proxy:" out
    }')"
  printf '%s' "${summary:-scutil-proxy: unknown}"
}

# Log rotation: keep last 2000 lines once the file exceeds 50 MiB
# (same shape as nfm-4599-expiry-alert.sh).
if [[ -f "$LOG" ]]; then
  log_size="$(stat -f%z "$LOG" 2>/dev/null || echo 0)"
  if (( log_size > 52428800 )); then
    tail -n 2000 "$LOG" > "$LOG.tmp"
    mv "$LOG.tmp" "$LOG"
    printf '[%s] site-monitor: truncated %s to last 2000 lines (was %s bytes)\n' \
      "$NOW" "$LOG" "$log_size" >> "$LOG"
  fi
fi

# ---- state helpers --------------------------------------------------------
state_get() { # key default
  if [[ -f "$STATE" ]]; then
    v="$(grep -E "^$1=" "$STATE" | tail -1 | cut -d= -f2-)"
    [[ -n "$v" ]] && echo "$v" || echo "$2"
  else
    echo "$2"
  fi
}
state_write() { # streak first_fail since_remind level internal_streak pending_page scan_skip_streak scan_skip_a scan_skip_b blind_p0_fail
  # NFM-5497 adds the per-mode streaks + the blind-window P0-evidence flag.
  # scan_skip_streak keeps its pre-NFM-5497 meaning (consecutive degraded
  # cycles of ANY mode) for display/reader continuity.
  printf 'streak=%s\nfirst_fail=%s\nsince_remind=%s\nlevel=%s\ninternal_streak=%s\npending_page=%s\nscan_skip_streak=%s\nscan_skip_streak_a=%s\nscan_skip_streak_b=%s\nblind_p0_fail=%s\n' \
    "$1" "$2" "$3" "$4" "$5" "$6" "$7" "$8" "${9}" "${10}" > "$STATE.tmp"
  mv "$STATE.tmp" "$STATE"
}

# ---- NFM-5667 streak-1 full-edge page rule (NFM-5664 ruling 35aa85eb §4) ---
# Episode state lives in its OWN file so every pre-existing state_write call
# site (the streak>=2 lane, AC4) stays byte-identical. Keys:
#   last_page    — epoch of the last streak-1 page (rate-limit counter;
#                  0 = never; INDEPENDENT of the streak>=2 lane)
#   open         — 1 while a full-edge episode window is in progress
#   win_first    — ISO ts of the window's first failure ("-" = none yet)
#   close_epoch  — epoch the last window closed green (episode separation)
#   ledger       — space-separated ISO ts of counted episodes (rolling 30d;
#                  seedable at cutover, pruned on append)
S1_STATE="${NFM_MONITOR_S1_STATE:-$HOME/.nfm-site-monitor.s1.state}"
S1_EDGE_TOTAL=6           # 5 checks on nucpot.dpdns.org + 1 on verify.… (P2 Supabase is outside the rule)
S1_FAILED_MIN=5           # conjunct 1: >=5 of the 6 edge checks failed
S1_P0_MIN=2               # conjunct 2: >=2 P0 checks among them
S1_PAGE_COOLDOWN=1800     # rate limit: max 1 page / 30 min for this class
S1_EPISODE_GREEN_GAP=600  # ruling §2: >=10 min green separates episodes

s1_get() { # key default
  if [[ -f "$S1_STATE" ]]; then
    v="$(grep -E "^$1=" "$S1_STATE" | tail -1 | cut -d= -f2-)"
    [[ -n "$v" ]] && echo "$v" || echo "$2"
  else
    echo "$2"
  fi
}
s1_write() { # last_page open win_first close_epoch ledger
  mkdir -p "$(dirname "$S1_STATE")" 2>/dev/null || true
  printf 'last_page=%s\nopen=%s\nwin_first=%s\nclose_epoch=%s\nledger=%s\n' \
    "$1" "$2" "$3" "$4" "$5" > "$S1_STATE.tmp"
  mv "$S1_STATE.tmp" "$S1_STATE"
}

s1_host_of() { # check-name -> main|verify|other (edge-name map; drift-guarded by tests)
  case "$1" in
    "Frontend Homepage"|"Frontend Browse Page"|"Backend API Health"|"Backend API Potentials"|"Backend API Reference Values")
      echo main ;;
    "AutoVC API Health")
      echo verify ;;
    *)
      echo other ;;
  esac
} # end s1_host_of

# Classifier: parse the probe's ❌ rows and set the conjunct tallies.
# Sets: s1_edge_failed s1_p0_failed s1_main_failed s1_verify_failed
#       s1_hard s1_latency s1_main_codes s1_verify_codes s1_match
s1_classify() {
  s1_edge_failed=0; s1_p0_failed=0; s1_main_failed=0; s1_verify_failed=0
  s1_hard=0; s1_latency=0
  s1_main_codes=""; s1_verify_codes=""
  while IFS= read -r row; do
    name="$(printf '%s' "$row" | awk -F'|' '{print $2}' | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' -e 's/^❌[[:space:]]*//')"
    sev="$(printf '%s' "$row" | awk -F'|' '{print $3}' | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
    detail="$(printf '%s' "$row" | awk -F'|' '{print $5}' | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
    host="$(s1_host_of "$name")"
    [[ "$host" == "other" ]] && continue   # third-party (P2 Supabase): outside the rule
    s1_edge_failed=$((s1_edge_failed + 1))
    [[ "$sev" == "P0" ]] && s1_p0_failed=$((s1_p0_failed + 1))
    if [[ "$host" == "main" ]]; then s1_main_failed=$((s1_main_failed + 1)); else s1_verify_failed=$((s1_verify_failed + 1)); fi
    # Failure class: latency-timeout (timeout / latency-over-budget /
    # connection-class — the NFM-5262 diurnal shape) vs hard edge status
    # (502/530 and other 52x/53x — CF error pages RETURNED). Anything else
    # (wrong status/body) is "other" and never counts toward hard.
    code="$(printf '%s' "$detail" | grep -oE '(HTTP|got) [0-9]{3}' | grep -oE '[0-9]{3}' | head -1)"
    if [[ "$detail" == *"Timeout after"* || "$detail" == *"exceeds limit"* || "$detail" == *"Connection error"* ]]; then
      cls="latency"; s1_latency=$((s1_latency + 1))
    elif [[ -n "$code" ]] && { (( code == 502 )) || (( code >= 520 && code <= 539 )); }; then
      cls="$code"; s1_hard=$((s1_hard + 1))
    else
      cls="other"
    fi
    if [[ "$host" == "main" ]]; then
      s1_main_codes="${s1_main_codes}${cls} "
    else
      s1_verify_codes="${s1_verify_codes}${cls} "
    fi
  done < <(printf '%s' "$OUT" | grep -E '^\| *❌')
  s1_match=0
  if (( s1_edge_failed >= S1_FAILED_MIN )) \
     && (( s1_p0_failed >= S1_P0_MIN )) \
     && (( s1_main_failed >= 1 && s1_verify_failed >= 1 )) \
     && (( s1_hard > s1_latency )); then
    s1_match=1
  fi
}

s1_class_hist() { # "502 502 530 " -> "502x2,530x1"
  printf '%s' "$1" | tr ' ' '\n' | sed '/^$/d' | sort | uniq -c | awk '{printf "%sx%s,", $2, $1}' | sed 's/,$//'
}

# Rolling-30d ledger maintenance (calendar math in python — portable, and
# $PY is already a hard dependency of the probe lane). Modes:
#   append <ledger> <now_iso>  -> count, delta, new-ledger (episode counted)
#   count  <ledger> <now_iso>  -> count, +0,   pruned-ledger (no append)
# Malformed entries are KEPT (fail-closed for evidence), never dropped.
s1_ledger() { # mode ledger now_iso
  "$PY" - "$1" "$2" "$3" <<'S1PYEOF'
import sys
from datetime import datetime, timedelta
mode, ledger, now = sys.argv[1], sys.argv[2], sys.argv[3]
def parse(t):
    return datetime.strptime(t, "%Y-%m-%dT%H:%M:%SZ")
entries = [e for e in ledger.split() if e]
try:
    cutoff = parse(now) - timedelta(days=30)
    kept = [e for e in entries if parse(e) > cutoff]
except ValueError:
    kept = entries
if mode == "append":
    kept.append(now)
    print(len(kept), "+1", " ".join(kept))
else:
    print(len(kept), "+0", " ".join(kept))
S1PYEOF
}

# Handle a classifier match at streak 1: episode accounting (ruling §2),
# rate limit (1/30 min), page payload (ruling amendment 1). Exits 2 on a
# real page; returns (after a NOTE line + s1 state update) when suppressed.
s1_handle_match() { # streak first_fail since_remind
  local now_epoch lp open win_first close_ep ledger rate_ok count delta
  now_epoch="$(date -u +%s)"
  lp="$(s1_get last_page 0)"
  open="$(s1_get open 0)"
  win_first="$(s1_get win_first -)"
  close_ep="$(s1_get close_epoch 0)"
  ledger="$(s1_get ledger '')"
  rate_ok=1
  (( lp == 0 || now_epoch - lp >= S1_PAGE_COOLDOWN )) || rate_ok=0
  # Episode accounting (by-episode, >=10 min green separation):
  #  - open==1                 -> window continues: no new ledger entry
  #  - open==0, gap >= 10 min  -> DISTINCT episode: ledger append (+prune)
  #  - open==0, gap <  10 min  -> same episode resumes: no new entry
  delta="+0"
  if (( open == 0 )); then
    if (( close_ep == 0 || now_epoch - close_ep >= S1_EPISODE_GREEN_GAP )); then
      read -r count delta ledger < <(s1_ledger append "$ledger" "$NOW")
      win_first="$NOW"
    else
      read -r count delta ledger < <(s1_ledger count "$ledger" "$NOW")
    fi
  else
    read -r count delta ledger < <(s1_ledger count "$ledger" "$NOW")
  fi
  if (( rate_ok == 1 )); then
    # Page (ruling amendment 1 payload: window, per-host class counts, t2
    # ledger delta + rolling-30d count, ruling reference). Latches the SAME
    # pending_page reconciliation the streak>=2 lane uses; the green run
    # completes the window on its RECOVERY line.
    pending="$(state_get pending_page -)"
    if [[ "$pending" == "-" || -z "$pending" ]]; then pending="$NOW"; fi
    log "CRITICAL — streak-1 full-edge P0 (NFM-5666 rule, NFM-5664 ruling 35aa85eb §4): ${s1_edge_failed}/${S1_EDGE_TOTAL} edge checks failed (${s1_p0_failed} P0, hard=${s1_hard} latency=${s1_latency}) — window ${win_first} -> recovery pending — classes: nucpot.dpdns.org={$(s1_class_hist "$s1_main_codes")} verify.nucpot.dpdns.org={$(s1_class_hist "$s1_verify_codes")} — t2-ledger: episodes=${count} rolling-30d (delta ${delta}; standing was 5/6 at ruling) — rate-limit 1/${S1_PAGE_COOLDOWN}s [$(proxy_state_line)] ${REF_EV}"
    notify_page "streak-1 full-edge P0: ${s1_edge_failed}/${S1_EDGE_TOTAL} edge checks failed (${s1_p0_failed} P0)"
    s1_write "$now_epoch" 1 "$win_first" "$close_ep" "$ledger"
    state_write "$1" "$2" "$3" CRITICAL 0 "$pending" "$scan_skip_next" \
      "$skip_a_next" "$skip_b_next" "$blind_p0_next"
    exit 2
  fi
  # Rate-limited: no page, but the match/episode evidence still accrues
  # (calibration for the 14-day precision review, ruling amendment 2).
  log "NOTE — streak-1 full-edge match NOT paged (rate limit ${S1_PAGE_COOLDOWN}s, last page epoch ${lp}): ${s1_edge_failed}/${S1_EDGE_TOTAL} edge checks failed (${s1_p0_failed} P0, hard=${s1_hard} latency=${s1_latency}), t2-ledger episodes=${count} [NFM-5667] ${REF_EV}"
  s1_write "$lp" 1 "$win_first" "$close_ep" "$ledger"
}


# ---- NFM-5418 chunk-ref scan ----------------------------------------------
# Edge-cached HTML outlives the origin build that produced its content-hashed
# /_next/static refs when pages carry a long s-maxage; after a deploy changes
# chunk hashes, cache-cold visitors get HTML whose refs 404 and React never
# boots (NFM-5418; same class as NFM-5335). HTML-200 health probes are blind
# to this — the HTML serves 200 from the edge while only the refs die.
# Fetch edge HTML for SCAN_PAGES and verify its chunk refs resolve (capped at
# REF_CAP distinct refs per page; truncated refs count as unverified — a
# capped scan degrades, never reads green). A definitive
# 4xx on a ref is P0-class (dead page for cache-cold visitors). Runs only
# when the base probe is green; the base lane owns outage detection.
#
# NFM-5446: pages and ref HEADs run in PARALLEL under one wall budget; a
# budget-exhausted scan is DEGRADED (rc 3, "unknown"), never green. Each
# page's TTL verdict (s-maxage/age) is captured from response headers before
# the body read, so stale-pin P1s survive ref-verification budget exhaustion.
# All exits go through print+flush+os._exit so straggling worker threads can
# never delay or swallow the report. The SIGALRM stays as an absolute
# backstop for pathological hangs (slow-drip bodies, resolver stalls).
chunk_scan() { # prints "<total> <p0> <detail>"; rc 0 clean / 1 findings / 3 degraded-unknown
  "$PY" - $SCAN_PAGES <<'PYEOF'
import os, re, signal, sys, threading, time, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor, wait

base = os.environ.get("NFM_SCAN_BASE", "https://nucpot.dpdns.org")
DEADLINE = float(os.environ.get("NFM_SCAN_DEADLINE", "240"))
t0 = time.monotonic()
def left():
    return DEADLINE - (time.monotonic() - t0)

def page_stage_left():
    # NFM-5572: wall remaining in the page stage — the fetch_page retry
    # gate. Page futures are submitted within milliseconds of t0 (module
    # exec -> pool submit), so this measures PAGE_STAGE_BUDGET from the
    # effective stage start, mirroring left() for the ref lane.
    return PAGE_STAGE_BUDGET - (time.monotonic() - t0)

ua = "nfm-site-monitor/1.3 (+NFM-5418 chunk scan, NFM-5446 fail-closed, NFM-5454 flake threshold)"
ref_re = re.compile(r'/_next/static/[A-Za-z0-9._\-/]+')
smaxage_re = re.compile(r's-maxage=(\d+)')

PAGE_TIMEOUT = 15.0       # per page GET socket timeout
PAGE_STAGE_BUDGET = 90.0  # wall budget for the (parallel) page GETs
REF_TIMEOUT = 15.0        # per ref HEAD socket timeout. NFM-5490: 8→15 — the
                          # NFM-5262 diurnal edge tail runs 5-10.4 s/request
                          # (+SSL handshake), so an 8 s timeout converted the
                          # whole latency window into refs-unverified (ep-4:
                          # 38 unverified while patient-client HEADs from the
                          # same vantage returned 40/40 HTTP 200). Matches
                          # PAGE_TIMEOUT; the DEADLINE still bounds the scan.
REF_POOL = int(os.environ.get("NFM_SCAN_REF_POOL", "24"))  # parallel ref HEADs (serial pre-NFM-5446; 12 pre-NFM-5490).
                          # 24: with ~150 refs scan-wide at 5-10 s each during
                          # a diurnal window, a 12-pool cannot drain the waves
                          # inside the post-page-stage budget even when every
                          # HEAD succeeds; 24 halves the wave count. Worst
                          # case (all HEADs burn the full timeout, retried)
                          # still fail-closes via the budget gate below.
REF_CAP = 45              # max distinct refs verified per page. 2026-10-11: 30→45 —
                          # live /materials carries 40 distinct chunk refs, so cap-30
                          # truncated 10 refs on EVERY scan; the skip streak could then
                          # never reset and the sentinel-blind CRITICAL latched
                          # permanently. Keep above the real per-page ref count; a
                          # persistent refs-unverified>0 across calm-edge scans means
                          # the site outgrew the cap (raise it, see runbook §7).
REFS_DEGRADE_MIN = 2      # NFM-5454: refs-unverified below this is a NOTE, not a
                          # degrade reason — a single errored ref is diurnal
                          # edge-latency noise (2026-10-11: all 9 noise-grade
                          # degrades were =1). Multi-ref failures (=9, =12 same
                          # day) and truncation still degrade immediately.
REPORT_MARGIN = 5.0       # reserve for printing/teardown before SIGALRM

# NFM-5542 (ADR-023) D1: the scan egresses DIRECTLY — page GETs and ref
# HEADs share ONE no-proxy opener. Bare urllib.request.urlopen rides the
# macOS system proxy (getproxies -> scutil): that is what fired the
# 2026-10-15 02:12–02:26Z false CRITICAL (fetches=0/5, mode=A ×3,
# origin=ok while the site was up). The 10-14 "CN-side ClientHello DPI"
# attribution for the site-monitor windows is superseded (NFM-5540): local
# VPN-proxy defects — ADR-013's broker/GitHub DPI rulings are a different
# subsystem and stay valid. ProxyHandler({}) pins this lane direct; the
# wrapper's no_proxy='*' export (D2) additionally covers the env-var proxy
# class. Verdict semantics unchanged: HTTPError handling, timeouts, and
# budgets are identical to the urlopen build.
NO_PROXY_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

lock = threading.Lock()
stale = {}   # page -> s-maxage value (TTL lane; from response headers)
ages = {}    # page -> age header at read time
bad = {}     # page -> ["ref->code", ...]
fetched = 0
refs_seen = 0
skipped_refs = 0    # errored/unknown: HEAD exception, 5xx, budget-skipped, or
                    # in-flight past the stage budget (degrades at >= REFS_DEGRADE_MIN)
truncated_refs = 0  # over budget (distinct > REF_CAP) — degrades at ANY count (NFM-5454)

def _fmt_findings():
    parts = []
    if bad:
        parts.append("P0 dead refs — " + "; ".join(
            "%s: %s" % (p, ",".join(v)) for p, v in bad.items()))
    if stale:
        parts.append("P1 stale 1y-pin — " + "; ".join(
            "%s (s-maxage=%s, age=%ss)" % (p, v, ages.get(p, "?")) for p, v in stale.items()))
    return " | ".join(parts)[:350]

def _snapshot():
    """Consistent (p0, n_stale, findings) read of the worker-mutated dicts.

    Lock-free with retry: the SIGALRM handler must NOT take `lock` (the
    interrupted main thread may hold it -> deadlock), and by emit time
    straggler pool workers can still be appending (shutdown(wait=False)).
    A RuntimeError (dict changed size during iteration) retries the whole
    snapshot; persistent failure returns (-1, -1, "") which callers treat
    as degraded-unknown — never green (fail-closed).
    """
    for _ in range(4):
        try:
            return (sum(len(v) for v in bad.values()), len(stale),
                    _fmt_findings())
        except RuntimeError:
            continue
    return (-1, -1, "")

def _emit(line, rc):
    print(line, flush=True)
    os._exit(rc)

def _deadline(signum, frame):
    # Absolute backstop: budget accounting above should degrade first; this
    # only fires on hangs the socket timeouts cannot bound. NEVER green.
    # Verified findings gathered before the alarm still page: a confirmed
    # dead ref is definitive regardless of coverage (same contract as the
    # main-path p0 branch — rc 1, not 3).
    p0, n_stale, findings = _snapshot()
    # NFM-5497: fetch coverage rides the report so the blind-streak lane
    # can classify the cycle (0 fetched => mode A, >=1 => mode B).
    tag = "fetches=%d/%d" % (fetched, len(sys.argv[1:]) or 1)
    if p0 > 0:
        _emit("%d %d scan-degraded: deadline-exceeded | partial-findings: %s | %s"
              % (p0 + max(n_stale, 0), p0, findings or "count-unavailable", tag), 1)
    msg = "scan-degraded: deadline-exceeded"
    if findings:
        msg += " | partial-findings: " + findings
    msg += " | " + tag
    if n_stale > 0:
        _emit("%d 0 %s" % (n_stale, msg), 3)
    _emit("0 0 %s" % msg, 3)

signal.signal(signal.SIGALRM, _deadline)
signal.alarm(max(1, int(DEADLINE)))

def fetch_page(page):
    global fetched, refs_seen, skipped_refs, truncated_refs
    req = urllib.request.Request(base + page, headers={"User-Agent": ua})
    # NFM-5572 (NFM-5571 adjudication): ONE budget-gated retry on TRANSPORT
    # failures only — the NFM-5490 ref-HEAD semantics ported to the page
    # lane. 2026-10-17 live evidence: 7 mode-B cycles where a single page
    # GET died at transport level (diurnal tail timeout / reset / TLS-EOF)
    # while ~150 ref HEADs verified in-budget in the same cycles — one tail
    # outlier cost one blind 5-min cycle, every skip self-healing on the
    # next cycle. A definitive HTTP verdict is NEVER retried away
    # (HTTPError propagates immediately: the server answered, that skip is
    # real); the retry can only turn a genuinely-dead transport error into
    # a real 200, never a false green, and a failed retry still raises so
    # the page counts skipped (fail-closed). Worst case +PAGE_TIMEOUT (15s)
    # per page; PAGE_STAGE_BUDGET (90s) and the DEADLINE alarm are unchanged
    # and still bound the stage.
    try:
        # ADR-023 (NFM-5542): DIRECT primary — a VPN proxy flap must never
        # read as "all page fetches failed" (the 10-15 02:12Z shape).
        resp = NO_PROXY_OPENER.open(req, timeout=PAGE_TIMEOUT)
    except urllib.error.HTTPError:
        raise  # definitive verdict — never retried away
    except Exception:
        # one more full-timeout attempt cannot fit the page-stage budget:
        # fail-closed NOW — the stage wait would discard the late result
        # anyway (same gate shape as head_ref's left() <= REF_TIMEOUT)
        if page_stage_left() <= PAGE_TIMEOUT:
            raise
        resp = NO_PROXY_OPENER.open(req, timeout=PAGE_TIMEOUT)
    try:
        cc = resp.headers.get("cache-control") or ""
        # TTL lane (NFM-5446): pin verdict from HEADERS, captured before the
        # body read so it survives body-read / downstream budget failures.
        m = smaxage_re.search(cc)
        if m and int(m.group(1)) > 3600:
            with lock:
                stale[page] = m.group(1)
                ages[page] = resp.headers.get("age") or "?"
        body = resp.read(4000000).decode("utf-8", "replace")
    finally:
        resp.close()
    distinct = list(dict.fromkeys(ref_re.findall(body)))
    refs = distinct[:REF_CAP]
    with lock:
        fetched += 1
        refs_seen += len(refs)
        truncated_refs += len(distinct) - len(refs)
    return page, refs

def head_ref(page, ref):
    global skipped_refs
    req = urllib.request.Request(base + ref, method="HEAD", headers={"User-Agent": ua})
    # NFM-5490: one budget-gated retry on TRANSPORT failures only (SSL
    # UNEXPECTED_EOF, handshake/reset, tail-latency timeout) — the ep-4
    # live adjudication proved refs stay verifiable mid-blindness with a
    # patient client (40/40 HTTP 200 while this loop sat at unverified=38).
    # A definitive verdict is never retried away: 4xx stays immediate P0
    # (HTTPError breaks out below) and 5xx stays unknown — the retry can
    # only turn a genuinely-dead transport error into a real 200, never a
    # false green; persistent failure still counts unverified (fail-closed).
    code = None
    for _attempt in (1, 2):
        if left() <= REF_TIMEOUT:  # one more HEAD cannot fit the budget
            with lock:
                skipped_refs += 1
            return
        try:
            code = NO_PROXY_OPENER.open(req, timeout=REF_TIMEOUT).status
            break
        except urllib.error.HTTPError as e:
            code = e.code
            break
        except Exception:
            continue  # retry once while budget remains
    if code is None:
        # both attempts failed at transport level — unverified, fail-closed
        with lock:
            skipped_refs += 1
        return
    if 400 <= code < 500:
        # Only a definitive 4xx means the object is GONE (P0). A 5xx is
        # transient/unknown (origin trouble — the base probe lane owns
        # outages) and must never produce a false P0 page.
        # Cap check under `lock`: iterating `bad` lock-free races concurrent
        # appends from other pool workers; the resulting RuntimeError in this
        # worker would be swallowed by the never-checked future and DROP a
        # real P0 finding (gate review: lock-free dict iteration race).
        with lock:
            if sum(len(v) for v in bad.values()) < 8:
                bad.setdefault(page, []).append("%s->%d" % (ref.rsplit("/", 1)[-1], code))
    elif code >= 500:
        with lock:
            skipped_refs += 1

try:
    pages = sys.argv[1:]
    page_refs, skipped_pages = {}, []
    pool = ThreadPoolExecutor(max_workers=max(1, len(pages)))
    try:
        futs = {pool.submit(fetch_page, p): p for p in pages}
        done, not_done = wait(futs, timeout=PAGE_STAGE_BUDGET)
        for f in done:
            try:
                p, refs = f.result()
                page_refs[p] = refs
            except Exception:
                skipped_pages.append(futs[f])
        skipped_pages.extend(futs[f] for f in not_done)
    finally:
        pool.shutdown(wait=False)

    ref_tasks = [(p, r) for p, refs in page_refs.items() for r in refs]
    if ref_tasks:
        rpool = ThreadPoolExecutor(max_workers=REF_POOL)
        try:
            rfuts = [rpool.submit(head_ref, p, r) for p, r in ref_tasks]
            _d, nd = wait(rfuts, timeout=max(0.0, left() - REPORT_MARGIN))
            # in-flight past the stage budget count as UNVERIFIED (a late
            # result may still land; over-counting stays conservative), and
            # so does any ref whose worker ended exceptional — the ref was
            # never verified, whatever the crash was (gate-review class:
            # unchecked futures must not silently read as coverage).
            with lock:
                skipped_refs += len(nd)
                skipped_refs += sum(1 for f in _d if f.exception() is not None)
        finally:
            rpool.shutdown(wait=False)

    # Snapshot under retry: straggler ref workers may still be appending
    # (rpool.shutdown(wait=False) + unchecked futures), so lock-free
    # iteration here has the same race the cap check had.
    p0, n_stale, findings = _snapshot()
    if p0 < 0 or n_stale < 0:
        _emit("0 0 scan-degraded: internal-error: findings snapshot raced "
              "4 consecutive retries (fail-closed, never green) | "
              "fetches=%d/%d" % (fetched, len(pages)), 3)
    reasons = []
    if fetched == 0:
        reasons.append("all page fetches failed")
    elif refs_seen == 0:
        reasons.append("no chunk refs found in fetched pages")
    if skipped_pages:
        reasons.append("page-fetch-skipped=" + ",".join(sorted(set(skipped_pages))))
    if truncated_refs:
        # cap outgrown (runbook §7 signal) — fail-closed at ANY count
        reasons.append("refs-truncated=%d" % truncated_refs)
    if skipped_refs >= REFS_DEGRADE_MIN:
        reasons.append("refs-unverified=%d" % skipped_refs)
    # NFM-5497: fetch coverage rides every DEGRADED report (only — a clean
    # scan must not grow reasons) — the blind-streak lane classifies mode
    # A/B from this token, never prose.
    if reasons:
        reasons.append("fetches=%d/%d" % (fetched, len(pages)))
    note = ""
    if skipped_refs and skipped_refs < REFS_DEGRADE_MIN:
        # NFM-5454: single-ref flake — visible for drift watch, no degrade
        note = ("; refs-unverified=%d (below NFM-5454 degrade threshold %d "
                "— diurnal single-ref flake, no streak impact)"
                % (skipped_refs, REFS_DEGRADE_MIN))

    if p0 > 0:
        # Definitive dead refs page regardless of coverage caveats.
        d = findings + note + ((" (scan-degraded: " + "; ".join(reasons) + ")") if reasons else "")
        _emit("%d %d %s" % (p0 + n_stale, p0, d), 1)
    if reasons:
        d = "scan-degraded: " + "; ".join(reasons)
        if n_stale > 0:
            d += " | " + findings  # TTL lane carried pins out anyway
        d += note
        _emit("%d 0 %s" % (n_stale, d), 3)
    if n_stale > 0:
        _emit("%d 0 %s" % (n_stale, findings + note), 1)
    _emit("0 0 ok (%d refs verified across %d page(s)%s)" % (refs_seen - skipped_refs, fetched, note), 0)
except Exception as e:
    # Internal errors are degraded-unknown too (was fail-open pre-NFM-5446).
    _emit("0 0 scan-degraded: internal-error: %s | fetches=%d/%d"
          % (e, fetched, len(sys.argv[1:]) or 1), 3)
PYEOF
}

# ---- NFM-5708/NFM-5706 path-independent reference probes -------------------
# Three lightweight GETs (int'l CF-edge / int'l non-CF / CN-domestic control)
# run in PARALLEL on every real-probe tick: green ticks maintain the per-host
# RELATIVE baselines, degrade ticks feed the classification. Direct-pinned
# (ADR-023) — see the NFM-5708 header block for thresholds and semantics.
ref_probes() { # prints one line: per-host timings + slow/warmup tokens; rc 0 ran / 3 internal
  "$PY" - <<'REFPYEOF'
import os, sys, threading, time, urllib.error, urllib.request

STATE = os.environ.get("NFM_MONITOR_REF_STATE",
                       os.path.expanduser("~/.nfm-site-monitor.ref.state"))
TIMEOUT = float(os.environ.get("NFM_REF_TIMEOUT", "10"))
SLOW_RATIO = float(os.environ.get("NFM_REF_SLOW_RATIO", "2.5"))
BASELINE_MIN = int(os.environ.get("NFM_REF_BASELINE_MIN", "10"))
ALPHA = 0.125       # EMA weight per healthy sample (≈8-tick convergence)
ALPHA_SLOW = 0.03   # folded weight for above-ratio samples: a 1-2h brownout
                    # cannot re-baseline the reference mid-incident, but a
                    # genuine multi-hour drift still converges

# Roles FIXED (classifier depends on them): int'l CF / int'l non-CF / domestic.
LANES = [
    ("cf",  os.environ.get("NFM_REF_CF_URL", "https://www.cloudflare.com")),
    ("ncf", os.environ.get("NFM_REF_NONCF_URL", "https://ghcr.io")),
    ("dom", os.environ.get("NFM_REF_DOM_URL", "https://tuna.tsinghua.edu.cn")),
]

# ADR-023 (NFM-5542 lineage): reference lanes egress DIRECT. The explicit
# empty ProxyHandler is PRIMARY — immune to both the env-var proxy class and
# the macOS scutil system proxy; the wrapper's no_proxy='*' export is
# defense-in-depth. Confounder (NFM-5705): the user VPN at 127.0.0.1:7892
# hard-fails TLS (6/6 SSL_ERROR_SYSCALL to nucpot.dpdns.org) — these lanes
# must never ride it, or a VPN flap would fake a transit brownout.
#
# Redirects are DISABLED: the lane measures the REFERENCE HOST, not its
# redirect chain (live 2026-10-25: ghcr.io answers / with a 301 in ~2.6 s
# while the redirect target stalls past any sane timeout). A 30x — like a
# 4xx/5xx — is still a definitive TLS+HTTP verdict from the host: the path
# round-tripped, so the sample is healthy evidence at the measured latency
# (the reference is not the monitored object).
class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # never follow — surfacing 30x as HTTPError to probe()

OPENER = urllib.request.build_opener(
    urllib.request.ProxyHandler({}), _NoRedirect)
UA = "nfm-site-monitor/1.4 (+NFM-5706 path-independent reference probes)"

def load_state():
    st = {}
    try:
        with open(STATE, encoding="utf-8") as fh:
            for line in fh:
                if "=" in line:
                    k, v = line.rstrip("\n").split("=", 1)
                    st[k] = v
    except (FileNotFoundError, NotADirectoryError):
        pass
    return st

def probe(url):
    t0 = time.monotonic()
    try:
        req = urllib.request.Request(url, method="GET",
                                     headers={"User-Agent": UA})
        with OPENER.open(req, timeout=TIMEOUT) as resp:
            resp.read(1024)  # first-KB latency: headers + initial body window
        return int((time.monotonic() - t0) * 1000), None
    except urllib.error.HTTPError:
        # Definitive verdict with redirects disabled (30x) or an error status
        # (4xx/5xx): the TLS+HTTP round trip COMPLETED — healthy path sample
        # at the measured latency. The reference's uptime is not what this
        # lane measures; only transport failure reads FAIL.
        return int((time.monotonic() - t0) * 1000), None
    except Exception as e:  # timeout / TLS / reset — transport "fail"
        return None, type(e).__name__

results = {}
def run_lane(key, url):
    results[key] = probe(url)
ths = [threading.Thread(target=run_lane, args=(k, u)) for k, u in LANES]
for t in ths:
    t.start()
for t in ths:
    t.join()

try:
    st = load_state()
    new_state, disp_parts, slow, lane_trusted = {}, [], {}, {}
    warmup = 0
    for key, url in LANES:
        ms, err = results[key]
        try:
            cnt = int(st.get(key + "_count", "0"))
        except ValueError:
            cnt = 0
        try:
            att = int(st.get(key + "_att", "0"))
        except ValueError:
            att = 0
        try:
            ema = float(st.get(key + "_ema_ms", "nan"))
        except ValueError:
            ema = float("nan")
        trusted = cnt >= BASELINE_MIN
        lane_trusted[key] = trusted
        # Warmup is ATTEMPT-based: a lane whose host is unreachable from this
        # vantage (e.g. ghcr egress blocked) would otherwise hold the whole
        # battery in warmup forever and silence the classifier permanently.
        out_of_warmup = att >= BASELINE_MIN
        if ms is None:
            # A failed probe never touches the baseline (a VPN/host-egress
            # outage must not poison the reference) but READS slow once the
            # battery is out of warmup — fail-closed on both roles: a dead
            # lane is never "pristine" evidence.
            slow[key] = 1 if (trusted or out_of_warmup) else 0
            new_state[key + "_count"] = str(cnt)
            new_state[key + "_att"] = str(att + 1)
            new_state[key + "_ema_ms"] = ("%.1f" % ema) if ema == ema else "-"
            disp = "FAIL(%s)" % err
        else:
            slow[key] = 1 if (trusted and ema == ema
                              and ms >= ema * SLOW_RATIO) else 0
            if ema != ema:            # no baseline yet — seed it
                ema_new = float(ms)
            elif ms >= ema * SLOW_RATIO:
                ema_new = ema + ALPHA_SLOW * (ms - ema)
            else:
                ema_new = ema + ALPHA * (ms - ema)
            new_state[key + "_count"] = str(cnt + 1)
            new_state[key + "_att"] = str(att + 1)
            new_state[key + "_ema_ms"] = "%.1f" % ema_new
            disp = "%dms" % ms
        new_state[key + "_last"] = disp
        if att < BASELINE_MIN:
            warmup = 1
        host = url.split("//", 1)[-1].split("/", 1)[0]
        ema_disp = ("%.0fms" % ema) if ema == ema else "-"
        disp_parts.append("%s=%s(ema=%s,slow=%d)" % (host, disp, ema_disp, slow[key]))
    intl_slow = slow["cf"] + slow["ncf"]
    # Battery-only verdict (CPO charter addendum enum: clean / intl_brownout
    # / cn_domestic, warmup+mixed as non-confident floors). Warmup dominates:
    # an untrusted battery never issues a confident hint. cn_domestic beats
    # intl_brownout — with the domestic control itself degraded, the int'l
    # comparison is unreliable and must not be force-fit into either main
    # class. Roles are fixed (cf+ncf int'l, dom domestic), so 2 = int'l N.
    if warmup:
        batclass = "warmup"
    elif slow["dom"]:
        batclass = "cn_domestic"
    elif intl_slow == 2:
        batclass = "intl_brownout" if lane_trusted["dom"] else "mixed"
    elif intl_slow == 0:
        batclass = "clean"
    else:
        batclass = "mixed"
    tmp = STATE + ".tmp"
    parent = os.path.dirname(STATE)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as fh:
        for k in sorted(new_state):
            fh.write("%s=%s\n" % (k, new_state[k]))
    os.replace(tmp, STATE)
    print("%s | intl_slow=%d/%d dom_slow=%d warmup=%d batclass=%s"
          % (" ".join(disp_parts), intl_slow, 2, slow["dom"], warmup, batclass))
except Exception as e:
    # Internal error: the battery cannot testify — tokens carry warmup=1 and
    # batclass=warmup so the bash classifier fails UNCLASSIFIED (never
    # suppresses a page).
    print("refs-internal-error: %s | intl_slow=-/2 dom_slow=- warmup=1 batclass=warmup" % e)
    sys.exit(3)
REFPYEOF
}

# Parse the battery's machine tokens once per tick. Defaults are the
# fail-closed vocabulary: any missing/garbled token leaves REF_WARMUP=1 and
# sentinel values that can never satisfy the brownout conjuncts.
ref_parse() { # sets REF_INTL_SLOW REF_INTL_TOTAL REF_DOM_SLOW REF_WARMUP REF_BATCLASS from REF_OUT/REF_RC
  REF_INTL_SLOW=-1; REF_INTL_TOTAL=2; REF_DOM_SLOW=-1; REF_WARMUP=1; REF_BATCLASS="warmup"
  (( REF_RC == 0 )) || return 0
  tok="$(printf '%s' "$REF_OUT" | grep -oE 'intl_slow=[0-9]+/[0-9]+' | head -1)"
  if [[ "$tok" == *"="* && "$tok" == */* ]]; then
    REF_INTL_SLOW="${tok#*=}"; REF_INTL_SLOW="${REF_INTL_SLOW%%/*}"
    REF_INTL_TOTAL="${tok##*/}"
  fi
  tok="$(printf '%s' "$REF_OUT" | grep -oE 'dom_slow=[0-9]+' | head -1)"
  [[ -n "$tok" ]] && REF_DOM_SLOW="${tok#*=}"
  tok="$(printf '%s' "$REF_OUT" | grep -oE 'warmup=[0-9]+' | head -1)"
  [[ -n "$tok" ]] && REF_WARMUP="${tok#*=}"
  tok="$(printf '%s' "$REF_OUT" | grep -oE 'batclass=[a-z_]+' | head -1)"
  [[ -n "$tok" ]] && REF_BATCLASS="${tok#*=}"
}

# Classify a nucpot DEGRADE tick against the reference battery (NFM-5705 R5,
# enum extended per the CPO charter addendum: TRANSIT_BROWNOUT / SITE_SIDE /
# CN_DOMESTIC / clean, UNCLASSIFIED as the fail-open floor). Only confident
# evidence reclassifies; UNCLASSIFIED and CN_DOMESTIC both run the existing
# paging path fail-open — CN_DOMESTIC is a hint (domestic control degraded,
# int'l comparison unreliable), never a suppression.
ref_classify() { # sets REF_CLASS (requires REF_BATCLASS parsed)
  REF_CLASS="UNCLASSIFIED"
  case "${REF_BATCLASS:-warmup}" in
    intl_brownout) REF_CLASS="TRANSIT_BROWNOUT" ;;
    clean)         REF_CLASS="SITE_SIDE" ;;
    cn_domestic)   REF_CLASS="CN_DOMESTIC" ;;
    *)             REF_CLASS="UNCLASSIFIED" ;; # warmup / mixed / garbled token
  esac
}

# ---- simulate modes (AC-2 test path; no real state mutation) --------------
if [[ "${1:-}" == "--simulate-failure" ]]; then
  log "TEST WARN — simulated P0 failure (streak 1/$STREAK_THRESHOLD, would not page yet)"
  log "TEST CRITICAL — simulated P0 failure confirmed ($STREAK_THRESHOLD consecutive) — pages SRE via heartbeat log-read"
  echo "simulate-failure: TEST WARN + TEST CRITICAL lines written to $LOG"
  exit 0
fi
if [[ "${1:-}" == "--simulate-recovery" ]]; then
  log "TEST RECOVERY — simulated recovery from failure — SRE closes the alert (recovery detection)"
  echo "simulate-recovery: TEST RECOVERY line written to $LOG"
  exit 0
fi

# ---- scan-once mode (SRE diagnostic; no state mutation, no alarm) ---------
if [[ "${1:-}" == "--scan-once" ]]; then
  scan_out="$(chunk_scan)"; scan_rc=$?
  echo "chunk-scan rc=$scan_rc: $scan_out"
  exit "$scan_rc"
fi

# ---- ref-probes-once mode (NFM-5706 SRE diagnostic; updates ONLY the
# reference baselines — never the main state; prints tokens, no alarm) -------
if [[ "${1:-}" == "--ref-probes-once" ]]; then
  ref_out="$(ref_probes)"; ref_rc=$?
  echo "ref-probes rc=$ref_rc: $ref_out"
  exit "$ref_rc"
fi

# ---- ack mode (SRE heartbeat, after retro-filing the [SRE-CRITICAL]) -------
if [[ "${1:-}" == "--ack-page" ]]; then
  pending="$(state_get pending_page -)"
  if [[ "$pending" != "-" && -n "$pending" ]]; then
    state_write 0 - 0 OK 0 - 0 0 0 -  # NFM-5497: full reset incl. blind-window flag
    log "PAGE-ACK — SRE acked the CRITICAL from $pending (retro [SRE-CRITICAL] filed) — reconciliation closed"
    echo "ack-page: PAGE-ACK written, pending_page cleared"
  else
    log "PAGE-ACK — SRE ack ran with no pending page (nothing to reconcile)"
    echo "ack-page: no pending page"
  fi
  exit 0
fi

# ---- real probe ------------------------------------------------------------
# NFM-5497 classifier state, read once per run: per-mode blind-streak
# counters + the blind-window P0-evidence flag (set by any definitive P0
# failure — base probe or scan dead-refs —, cleared by a fully green
# cycle). The _next variants track this run's updates and are what every
# state_write below persists; they default to "preserved".
skip_a="$(state_get scan_skip_streak_a 0)"
skip_b="$(state_get scan_skip_streak_b 0)"
blind_p0="$(state_get blind_p0_fail -)"
skip_a_next="$skip_a"
skip_b_next="$skip_b"
blind_p0_next="$blind_p0"
N_SCAN_PAGES="$(wc -w <<<"$SCAN_PAGES" | tr -d ' ')"

# NFM-5542 (ADR-023) D3: proxy posture at sentinel startup, every run —
# with the D1 direct primaries a residual flap is attributable to host
# egress (not the edge) from this one line alone.
log "startup proxy-state — $(proxy_state_line) — python lanes pinned direct (ADR-023/NFM-5542)"

if [[ ! -f "$PROBE" ]]; then
  internal_streak="$(( $(state_get internal_streak 0) + 1 ))"
  if (( internal_streak >= INTERNAL_STREAK_CRITICAL )); then
    log "CRITICAL — internal error streak $internal_streak: probe script missing at $PROBE — sentinel cannot monitor (sentinel-broken)"
    pending="$(state_get pending_page -)"
    if [[ "$pending" == "-" || -z "$pending" ]]; then pending="$NOW"; fi
    notify_page "sentinel-broken: probe script missing (internal streak $internal_streak)"
    state_write "$(state_get streak 0)" "$(state_get first_fail -)" "$(state_get since_remind 0)" \
      "$(state_get level OK)" "$internal_streak" "$pending" "$(state_get scan_skip_streak 0)" \
      "$skip_a_next" "$skip_b_next" "$blind_p0_next"
    exit 2
  fi
  log "ERROR internal (streak $internal_streak/$INTERNAL_STREAK_CRITICAL) — probe script missing at $PROBE"
  state_write "$(state_get streak 0)" "$(state_get first_fail -)" "$(state_get since_remind 0)" \
    "$(state_get level OK)" "$internal_streak" "$(state_get pending_page -)" "$(state_get scan_skip_streak 0)" \
    "$skip_a_next" "$skip_b_next" "$blind_p0_next"
  exit 3
fi

OUT="$("$PY" "$PROBE" 2>&1)"
RC=$?

# NFM-5418: with the base probe green, verify edge HTML chunk refs resolve.
# A failure here is synthesized into the base probe's failure format
# ("N check(s) failed (M P0 critical)" + a ❌ detail row) so the existing
# streak/WARN->CRITICAL state machine treats dead refs as P0.
#
# NFM-5446: scan_skip_next is the scan-skip streak to persist this run:
#  - scan completed (rc 0/1)          -> 0 (reset; RECOVERY logged if it
#                                        ended a >=SCAN_SKIP_CRITICAL blind
#                                        episode)
#  - scan degraded (rc 3 / launcher)  -> previous+1; >=SCAN_SKIP_CRITICAL
#                                        consecutive => sentinel-blind CRITICAL
#  - scan not attempted (probe red)   -> preserved
scan_skip_next="$(state_get scan_skip_streak 0)"
scan_degraded_note=""
if (( RC == 0 )); then
  SCAN_OUT="$(chunk_scan 2>/dev/null)"; SCAN_RC=$?
  read -r scan_total scan_p0 scan_detail <<<"$SCAN_OUT"
  if (( SCAN_RC == 1 )); then
    scan_skip_next=0
    skip_a_next=0; skip_b_next=0   # NFM-5497: a completed scan ends any blind episode
    if (( scan_p0 > 0 )); then
      blind_p0_next="$NOW"         # definitive dead refs inside the window
    else
      blind_p0_next="-"            # findings-only (P1 pins): episode closed clean
    fi
    OUT="${scan_total} check(s) failed (${scan_p0} P0 critical)
| ❌ edge-html-desync | dead chunk refs (breaks cache-cold visitors) and/or stale 1y-pinned edge copies on watched pages | ${scan_detail}"
    RC=1
  elif (( SCAN_RC == 3 )); then
    scan_skip_next="$(( $(state_get scan_skip_streak 0) + 1 ))"
    # NFM-5497 mode classification: mode A = 0 page fetches completed
    # (all-fetches-failed, genuine edge-down; a degraded report carrying
    # no fetch evidence also classifies mode A — fail-closed); mode B =
    # >=1 fetch completed (refs-unverified-only, incl. mixed cycles like
    # ep-5 skip 2/3). The scan emits "fetches=<fetched>/<pages>" on every
    # degraded report.
    scan_mode="A"; fetches_failed="?"
    if [[ "$scan_detail" == *"fetches="* ]]; then
      ftok="$(printf '%s' "$scan_detail" | grep -oE 'fetches=[0-9]+/[0-9]+' | head -1)"
      if [[ -n "$ftok" ]]; then
        fetched_n="${ftok#fetches=}"
        ftot="${fetched_n#*/}"
        fetched_n="${fetched_n%%/*}"
        fetches_failed="$(( ftot - fetched_n ))"
        if (( fetched_n > 0 )); then scan_mode="B"; fi
      fi
    elif [[ "$scan_detail" == *"all page fetches failed"* ]]; then
      fetches_failed="$N_SCAN_PAGES"  # pre-token prose fallback, defensive
    fi
    ru_tok="$(printf '%s' "$scan_detail" | grep -oE 'refs-unverified=[0-9]+' | head -1)"
    refs_unv="${ru_tok#refs-unverified=}"
    [[ -n "$refs_unv" ]] || refs_unv="0"
    # Per-mode strictly-consecutive streaks: a cycle of one mode
    # increments its counter and resets the other's.
    if [[ "$scan_mode" == "A" ]]; then
      skip_a_next="$(( skip_a + 1 ))"; skip_b_next=0
    else
      skip_b_next="$(( skip_b + 1 ))"; skip_a_next=0
    fi
    p0_ev="unknown"; [[ -n "${blind_p0_next/-}" ]] && p0_ev="fail"
    # NFM-5542 (ADR-023) D3: the scutil proxy summary rides the SAME token
    # as the mode=/fetches= evidence — one log line adjudicates host-egress
    # vs edge on the next flap (mode A + healthy proxy = host egress).
    mode_ev="[NFM-5497 mode=$scan_mode fetches_failed=$fetches_failed/$N_SCAN_PAGES refs_unverified=$refs_unv p0=$p0_ev origin=ok; streak A=$skip_a_next B=$skip_b_next; $(proxy_state_line)]"
    # TTL-lane findings during a degraded scan still surface as P1 WARN —
    # this is exactly the masking window NFM-5446 closes.
    if [[ "$scan_detail" == *"P1 stale"* ]]; then
      OUT="${scan_total} check(s) failed (0 P0 critical)
| ❌ edge-html-desync | stale 1y-pinned edge copies on watched pages (TTL lane during degraded scan) | ${scan_detail}"
      RC=1
    fi
    if (( skip_a_next >= SCAN_SKIP_CRITICAL )); then
      # NFM-5497 mode A — semantics byte-identical to the pre-classifier
      # build: threshold, latch, notify, recovery, ack all unchanged.
      pending="$(state_get pending_page -)"
      if [[ "$pending" == "-" || -z "$pending" ]]; then pending="$NOW"; fi
      log "CRITICAL — sentinel chunk-scan blind (NFM-5446): scan degraded ${scan_skip_next} consecutive probe(s) — ${scan_detail} — P0 lane UNVERIFIED (monitoring blindness) ${mode_ev}"
      notify_page "sentinel chunk-scan blind (${scan_skip_next} consecutive degraded scans) — P0 lane unverified"
      state_write "$(state_get streak 0)" "$(state_get first_fail -)" "$(state_get since_remind 0)" \
        CRITICAL 0 "$pending" "$scan_skip_next" "$skip_a_next" "$skip_b_next" "$blind_p0_next"
      exit 2
    fi
    if (( skip_b_next >= SCAN_SKIP_CRITICAL )); then
      if [[ "$blind_p0_next" != "-" && -n "$blind_p0_next" ]]; then
        # NFM-5497 fail-closed half: blind + CONFIRMED P0 breakage
        # (dead chunk refs = cache-cold-visitor breakage, NFM-5446
        # page class) still pages, exactly as the pre-classifier build
        # paged every blind streak.
        pending="$(state_get pending_page -)"
        if [[ "$pending" == "-" || -z "$pending" ]]; then pending="$NOW"; fi
        log "CRITICAL — sentinel chunk-scan blind with P0 FAIL evidence (NFM-5497): mode-B streak ${skip_b_next} consecutive degraded + definitive P0 failure at ${blind_p0_next} — ${scan_detail} — dead refs / P0 breakage inside the blind window (page-worthy per NFM-5446) ${mode_ev}"
        notify_page "sentinel chunk-scan blind + P0 FAIL evidence (mode-B streak ${skip_b_next})"
        state_write "$(state_get streak 0)" "$(state_get first_fail -)" "$(state_get since_remind 0)" \
          CRITICAL 0 "$pending" "$scan_skip_next" "$skip_a_next" "$skip_b_next" "$blind_p0_next"
        exit 2
      fi
      # Benign diurnal edge-flap class (NFM-5262): WARN only — no
      # PAGE-UNACK latch, no page, no exit 2.
      log "WARN — chunk-scan blind streak mode=B refs-unverified-only (NFM-5497): degraded ${scan_skip_next} consecutive probe(s), mode-B streak ${skip_b_next} — ${scan_detail} — P0 lane unverified this cycle; no page (diurnal edge-flap class, NFM-5262; escalates only on P0 FAIL evidence) ${mode_ev}"
      scan_degraded_note=1
    else
      log "WARN — chunk-scan DEGRADED, skip streak ${scan_skip_next}/${SCAN_SKIP_CRITICAL} (NFM-5446): ${scan_detail} — P0 lane unverified this cycle ${mode_ev}"
      scan_degraded_note=1
    fi
  elif (( SCAN_RC == 0 )); then
    if [[ "$scan_detail" == *"below NFM-5454 degrade threshold"* ]]; then
      log "NOTE — chunk-scan ok with 1 ref unverified (below NFM-5454 degrade threshold 2; diurnal flake, NFM-5262 class) — no streak impact; persistent single-ref = investigate cap/edge"
    fi
    if (( $(state_get scan_skip_streak 0) >= SCAN_SKIP_CRITICAL )); then
      log "RECOVERY — chunk-scan completed again after blind streak $(state_get scan_skip_streak 0) (NFM-5446) — SRE closes the sentinel-blind alert [NFM-5497 episode split: mode-A $(state_get scan_skip_streak_a 0) / mode-B $(state_get scan_skip_streak_b 0)]"
    fi
    scan_skip_next=0
    skip_a_next=0; skip_b_next=0; blind_p0_next="-"  # episode closed clean
  else
    # scan launcher itself failed (python missing, etc.) — degraded, not green.
    # NFM-5497: no scan report => no mode, no per-mode counter movement
    # (preserved); this lane escalates exactly as before (WARN + overall
    # streak only).
    scan_skip_next="$(( $(state_get scan_skip_streak 0) + 1 ))"
    log "WARN — chunk-scan DEGRADED, skip streak ${scan_skip_next}/${SCAN_SKIP_CRITICAL}: scan launcher rc=$SCAN_RC — P0/pin lanes unverified this cycle [NFM-5497 mode=unknown — no scan report; counters preserved; $(proxy_state_line)]"
    scan_degraded_note=1
  fi
fi

# NFM-5708 (NFM-5706): reference battery — EVERY real-probe tick (green ticks
# maintain baselines; degrade ticks classify). Runs AFTER the base probe/scan
# so it never delays them; worst case +REF_TIMEOUT on the tick (300s cadence).
# REF_CLASS/REF_EV stay unset on green ticks — classification is degrade-only.
REF_OUT="$(ref_probes 2>/dev/null)"; REF_RC=$?
ref_parse
if (( REF_RC != 0 )); then
  REF_OUT="refs-internal-error (rc=$REF_RC) — battery cannot testify"
fi

# CPO charter addendum: a CONFIDENT non-clean battery verdict on a GREEN tick
# (intl_brownout / cn_domestic) logs one NOTE line — recording only, zero page
# implications; a clean battery stays silent so green OK lines are
# byte-stable. This is the "intl degraded but nucpot clean" observability row.
if (( RC == 0 && REF_RC == 0 )) && [[ "$REF_BATCLASS" == "intl_brownout" || "$REF_BATCLASS" == "cn_domestic" ]]; then
  log "NOTE — ref-battery (NFM-5706): $REF_BATCLASS observed while nucpot is GREEN — recording only, no page implications — [refs: $REF_OUT]"
fi

if (( RC == 0 )); then
  internal_streak=0
  prev_streak="$(state_get streak 0)"
  first_fail="$(state_get first_fail -)"
  pending="$(state_get pending_page -)"
  if (( prev_streak > 0 )); then
    log "RECOVERY — all checks passing again after $prev_streak failing probe(s) (first failure $first_fail) — SRE closes the alert"
    # Missed-page reconciliation (NFM-5262): if a CRITICAL was latched and no
    # heartbeat has acked it, keep saying so on every recovery until acked.
    if [[ "$pending" != "-" && -n "$pending" ]]; then
      log "PAGE-UNACK — CRITICAL from $pending recovered without SRE pickup — SRE heartbeat MUST retro-file [SRE-CRITICAL] then run --ack-page"
    fi
  else
    if [[ -n "$scan_degraded_note" ]]; then
      log "OK — all checks passed (chunk-scan degraded: see WARN above — P0 lane unverified)"
    else
      log "OK — all checks passed"
    fi
    if [[ "$pending" != "-" && -n "$pending" ]]; then
      log "PAGE-UNACK (still latched) — CRITICAL from $pending never acked by SRE — retro-file [SRE-CRITICAL] then run --ack-page"
    fi
  fi
  # NFM-5667: a green probe closes any open streak-1 full-edge episode
  # window — this RECOVERY line completes the page's window timestamps
  # (first-failure -> recovery) and records the close epoch for the
  # >=10-min episode-separation rule (ruling §2).
  if [[ "$(s1_get open 0)" == "1" ]]; then
    s1_wf="$(s1_get win_first -)"
    s1_cnt="$(s1_ledger count "$(s1_get ledger '')" "$NOW" | awk '{print $1}')"
    log "RECOVERY — streak-1 full-edge episode closed: window ${s1_wf} -> ${NOW} — t2-ledger episodes=${s1_cnt} rolling-30d [NFM-5667; NFM-5664 ruling 35aa85eb §2]"
    s1_write "$(s1_get last_page 0)" 0 "$s1_wf" "$(date -u +%s)" "$(s1_get ledger '')"
  fi
  state_write 0 - 0 OK 0 "$pending" "$scan_skip_next" "$skip_a_next" "$skip_b_next" "$blind_p0_next"
  exit 0
fi

if (( RC == 1 )); then
  internal_streak=0
  # Parse "N check(s) failed (M P0 critical)". Unparseable output is treated
  # as P0-unknown -> page-worthy (never miss a real P0).
  p0="$(printf '%s' "$OUT" | grep -oE '[0-9]+ P0 critical' | grep -oE '^[0-9]+' | tail -1)"
  [[ -n "$p0" ]] || p0="-1"
  failed="$(printf '%s' "$OUT" | grep -oE '[0-9]+ check\(s\) failed' | grep -oE '^[0-9]+' | tail -1)"
  [[ -n "$failed" ]] || failed="?"
  # NFM-5497: latch P0 evidence for any blind-streak window that is (or
  # becomes) active — p0 != 0 includes "-1" (unparseable = P0-unknown,
  # page-worthy, never miss a real P0). Cleared only by a fully green
  # cycle in the scan lane above.
  if [[ "$p0" != "0" ]]; then
    blind_p0_next="$NOW"
  fi

  streak="$(( $(state_get streak 0) + 1 ))"
  since_remind="$(( $(state_get since_remind 0) + 1 ))"
  if (( streak == 1 )); then
    first_fail="$NOW"
  else
    first_fail="$(state_get first_fail "$NOW")"
  fi

  # Failure detail (NFM-5262): extract the actual failing table rows (❌ …)
  # instead of tail -3, which reliably captured only the passing rows and hid
  # the failure mode (timeout vs latency-budget vs non-200). Fallback to the
  # old tail if no row markers are present.
  faildetail="$(printf '%s' "$OUT" | grep -E '^\| *❌' | sed 's/^| *//; s/ *| */ /g' | tr '\n' '|' | cut -c1-400)"
  [[ -n "$faildetail" ]] || faildetail="$(printf '%s' "$OUT" | grep -viE '^starting|^targets|^$' | tail -3 | tr '\n' '|' | cut -c1-300)"

  # NFM-5708 (NFM-5706): THIS tick is a nucpot degrade — classify it against
  # the reference battery (TRANSIT_BROWNOUT / SITE_SIDE / UNCLASSIFIED) and
  # build the evidence token that rides every degrade log line below. Only
  # TRANSIT_BROWNOUT changes behavior (gates the streak>=2 CRITICAL page);
  # SITE_SIDE/UNCLASSIFIED run the existing watch-line path fail-open.
  ref_classify
  REF_EV="[NFM-5706 class=$REF_CLASS; refs: $REF_OUT]"
  # CN_DOMESTIC hint (CPO charter addendum): domestic control degraded =>
  # int'l comparison unreliable — say so IN the token; behavior stays
  # fail-open (existing paging path), never suppression.
  if [[ "$REF_CLASS" == "CN_DOMESTIC" ]]; then
    REF_EV="$REF_EV (intl comparison unreliable — CN-domestic control degraded)"
  fi

  # NFM-5667: streak-1 full-edge page rule — evaluated ONLY at streak 1
  # (streak>=2 keeps the existing lane; when both lanes match the streak-1
  # lane fires at most its one rate-limited page and never alters
  # streak>=2 behavior — AC4). Unparseable probe output (p0="-1") cannot
  # satisfy the conjuncts and stays on the existing lane — fail-closed.
  if [[ "$p0" != "0" ]] && (( streak == 1 )); then
    s1_classify
    if (( s1_match == 1 )); then
      s1_handle_match "$streak" "$first_fail" "$since_remind"
    fi
  fi

  if [[ "$p0" != "0" ]] && (( streak >= STREAK_THRESHOLD )); then
    if [[ "$REF_CLASS" == "TRANSIT_BROWNOUT" ]]; then
      # NFM-5708 (NFM-5706; ruling 5206262b(d) + NFM-5705 R5): nucpot
      # degraded while ALL international references are slow AND the
      # CN-domestic control is pristine => shared CN->int'l transit
      # brownout, NOT site-side. LOG-ONLY, NON-PAGING, no SRE wake: no
      # CRITICAL line, no notify, no PAGE-UNACK latch. The nucpot failure
      # streak keeps counting, so the existing page fires the moment
      # transit recovers or the classification clears (SITE_SIDE /
      # UNCLASSIFIED) — suppression can only ever DELAY the existing page,
      # never silently consume it.
      log "WARN — TRANSIT_BROWNOUT (NFM-5706/NFM-5708): nucpot degraded (streak $streak, first failure $first_fail) + ALL int'l references slow + domestic control pristine — shared CN-to-int'l transit brownout, log-only / non-paging / no SRE wake per 5206262b(d) + NFM-5705 R5 — $failed check(s) failed ($p0 P0) — $faildetail $REF_EV"
      # Suppressed ticks page nobody, so they must not advance the reminder
      # clock (since_remind) — otherwise the first CRITICAL after
      # suppression clears would mislabel itself "ongoing reminder".
      state_write "$streak" "$first_fail" "$(state_get since_remind 0)" WARN 0 "$(state_get pending_page -)" "$scan_skip_next" \
        "$skip_a_next" "$skip_b_next" "$blind_p0_next"
      exit 1
    fi
    pending="$(state_get pending_page -)"
    if [[ "$pending" == "-" || -z "$pending" ]]; then
      pending="$NOW"   # latch: survives recovery until the heartbeat acks
    fi
    if (( since_remind >= REMINDER_RUNS )); then
      since_remind=0
      log "CRITICAL (ongoing reminder, first failure $first_fail) — $failed check(s) failed ($p0 P0) — $faildetail $REF_EV [$(proxy_state_line)]"
    else
      log "CRITICAL — public-edge P0 failure confirmed ($STREAK_THRESHOLD consecutive probes, first failure $first_fail) — $failed check(s) failed ($p0 P0) — $faildetail $REF_EV [$(proxy_state_line)]"
    fi
    notify_page "P0 public-edge failure: $failed check(s) down ($p0 P0)"
    state_write "$streak" "$first_fail" "$since_remind" CRITICAL 0 "$pending" "$scan_skip_next" \
      "$skip_a_next" "$skip_b_next" "$blind_p0_next"
    exit 2
  fi

  if [[ "$p0" == "0" ]]; then
    log "WARN — P1-only failures ($failed check(s), 0 P0), streak $streak — not page-worthy; reminder cadence applies — $faildetail $REF_EV"
    state_write "$streak" "$first_fail" "$since_remind" WARN 0 "$(state_get pending_page -)" "$scan_skip_next" \
      "$skip_a_next" "$skip_b_next" "$blind_p0_next"
  else
    log "WARN — failure streak $streak/$STREAK_THRESHOLD ($failed check(s) failed, $p0 P0) — confirming before CRITICAL — $faildetail $REF_EV"
    state_write "$streak" "$first_fail" "$since_remind" WARN 0 "$(state_get pending_page -)" "$scan_skip_next" \
      "$skip_a_next" "$skip_b_next" "$blind_p0_next"
  fi
  exit 1
fi

# probe crashed / python error
internal_streak="$(( $(state_get internal_streak 0) + 1 ))"
if (( internal_streak >= INTERNAL_STREAK_CRITICAL )); then
  log "CRITICAL — internal error streak $internal_streak: probe exited rc=$RC — sentinel cannot monitor (sentinel-broken)"
  pending="$(state_get pending_page -)"
  if [[ "$pending" == "-" || -z "$pending" ]]; then pending="$NOW"; fi
  notify_page "sentinel-broken: probe rc=$RC (internal streak $internal_streak)"
  state_write "$(state_get streak 0)" "$(state_get first_fail -)" "$(state_get since_remind 0)" \
    "$(state_get level OK)" "$internal_streak" "$pending" "$(state_get scan_skip_streak 0)" \
    "$skip_a_next" "$skip_b_next" "$blind_p0_next"
  exit 2
fi
log "ERROR internal (streak $internal_streak/$INTERNAL_STREAK_CRITICAL) — probe exited rc=$RC: $(printf '%s' "$OUT" | tail -1 | cut -c1-200)"
state_write "$(state_get streak 0)" "$(state_get first_fail -)" "$(state_get since_remind 0)" \
  "$(state_get level OK)" "$internal_streak" "$(state_get scan_skip_streak 0)" \
  "$skip_a_next" "$skip_b_next" "$blind_p0_next"
exit 3
