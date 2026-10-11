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
#   --ack-page       — SRE heartbeat clears a latched PAGE-UNACK after it has
#                      retro-filed the [SRE-CRITICAL] issue (logs PAGE-ACK)
#
# Exit codes: 0 OK / 1 WARN (streak 1 or P1-only) / 2 CRITICAL / 3 internal error
#
# Why absolute paths: macOS PATH for GUI-launched agents is sparse (NFM-4579).

set -uo pipefail

LOG="${NFM_MONITOR_LOG:-$HOME/Library/Logs/nfm-site-monitor.log}"
STATE="${NFM_MONITOR_STATE:-$HOME/.nfm-site-monitor.state}"
PROBE="${NFM_MONITOR_PROBE:-/Users/lwj04/Projects/nucpot/scripts/health_check.py}"
if [[ -x /opt/homebrew/bin/python3 ]]; then
  PY=/opt/homebrew/bin/python3
else
  PY=/usr/bin/python3
fi

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
state_write() { # streak first_fail since_remind level internal_streak pending_page scan_skip_streak
  printf 'streak=%s\nfirst_fail=%s\nsince_remind=%s\nlevel=%s\ninternal_streak=%s\npending_page=%s\nscan_skip_streak=%s\n' \
    "$1" "$2" "$3" "$4" "$5" "$6" "$7" > "$STATE.tmp"
  mv "$STATE.tmp" "$STATE"
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

ua = "nfm-site-monitor/1.2 (+NFM-5418 chunk scan, NFM-5446 fail-closed)"
ref_re = re.compile(r'/_next/static/[A-Za-z0-9._\-/]+')
smaxage_re = re.compile(r's-maxage=(\d+)')

PAGE_TIMEOUT = 15.0       # per page GET socket timeout
PAGE_STAGE_BUDGET = 90.0  # wall budget for the (parallel) page GETs
REF_TIMEOUT = 8.0         # per ref HEAD socket timeout
REF_POOL = int(os.environ.get("NFM_SCAN_REF_POOL", "12"))  # parallel ref HEADs (serial pre-NFM-5446)
REF_CAP = 30              # max distinct refs verified per page
REPORT_MARGIN = 5.0       # reserve for printing/teardown before SIGALRM

lock = threading.Lock()
stale = {}   # page -> s-maxage value (TTL lane; from response headers)
ages = {}    # page -> age header at read time
bad = {}     # page -> ["ref->code", ...]
fetched = 0
refs_seen = 0
skipped_refs = 0  # unverified: over budget or errored (conservative count)

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
    if p0 > 0:
        _emit("%d %d scan-degraded: deadline-exceeded | partial-findings: %s"
              % (p0 + max(n_stale, 0), p0, findings or "count-unavailable"), 1)
    msg = "scan-degraded: deadline-exceeded"
    if findings:
        msg += " | partial-findings: " + findings
    if n_stale > 0:
        _emit("%d 0 %s" % (n_stale, msg), 3)
    _emit("0 0 %s" % msg, 3)

signal.signal(signal.SIGALRM, _deadline)
signal.alarm(max(1, int(DEADLINE)))

def fetch_page(page):
    global fetched, refs_seen, skipped_refs
    req = urllib.request.Request(base + page, headers={"User-Agent": ua})
    resp = urllib.request.urlopen(req, timeout=PAGE_TIMEOUT)
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
        skipped_refs += len(distinct) - len(refs)
    return page, refs

def head_ref(page, ref):
    global skipped_refs
    if left() <= REF_TIMEOUT:  # one more HEAD cannot fit the budget
        with lock:
            skipped_refs += 1
        return
    req = urllib.request.Request(base + ref, method="HEAD", headers={"User-Agent": ua})
    try:
        code = urllib.request.urlopen(req, timeout=REF_TIMEOUT).status
    except urllib.error.HTTPError as e:
        code = e.code
    except Exception:
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
              "4 consecutive retries (fail-closed, never green)", 3)
    reasons = []
    if fetched == 0:
        reasons.append("all page fetches failed")
    elif refs_seen == 0:
        reasons.append("no chunk refs found in fetched pages")
    if skipped_pages:
        reasons.append("page-fetch-skipped=" + ",".join(sorted(set(skipped_pages))))
    if skipped_refs:
        reasons.append("refs-unverified=%d" % skipped_refs)

    if p0 > 0:
        # Definitive dead refs page regardless of coverage caveats.
        d = findings + ((" (scan-degraded: " + "; ".join(reasons) + ")") if reasons else "")
        _emit("%d %d %s" % (p0 + n_stale, p0, d), 1)
    if reasons:
        d = "scan-degraded: " + "; ".join(reasons)
        if n_stale > 0:
            d += " | " + findings  # TTL lane carried pins out anyway
        _emit("%d 0 %s" % (n_stale, d), 3)
    if n_stale > 0:
        _emit("%d 0 %s" % (n_stale, findings), 1)
    _emit("0 0 ok (%d refs verified across %d page(s))" % (refs_seen, fetched), 0)
except Exception as e:
    # Internal errors are degraded-unknown too (was fail-open pre-NFM-5446).
    _emit("0 0 scan-degraded: internal-error: %s" % e, 3)
PYEOF
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

# ---- ack mode (SRE heartbeat, after retro-filing the [SRE-CRITICAL]) -------
if [[ "${1:-}" == "--ack-page" ]]; then
  pending="$(state_get pending_page -)"
  if [[ "$pending" != "-" && -n "$pending" ]]; then
    state_write 0 - 0 OK 0 - 0
    log "PAGE-ACK — SRE acked the CRITICAL from $pending (retro [SRE-CRITICAL] filed) — reconciliation closed"
    echo "ack-page: PAGE-ACK written, pending_page cleared"
  else
    log "PAGE-ACK — SRE ack ran with no pending page (nothing to reconcile)"
    echo "ack-page: no pending page"
  fi
  exit 0
fi

# ---- real probe ------------------------------------------------------------
if [[ ! -f "$PROBE" ]]; then
  internal_streak="$(( $(state_get internal_streak 0) + 1 ))"
  if (( internal_streak >= INTERNAL_STREAK_CRITICAL )); then
    log "CRITICAL — internal error streak $internal_streak: probe script missing at $PROBE — sentinel cannot monitor (sentinel-broken)"
    pending="$(state_get pending_page -)"
    if [[ "$pending" == "-" || -z "$pending" ]]; then pending="$NOW"; fi
    notify_page "sentinel-broken: probe script missing (internal streak $internal_streak)"
    state_write "$(state_get streak 0)" "$(state_get first_fail -)" "$(state_get since_remind 0)" \
      "$(state_get level OK)" "$internal_streak" "$pending" "$(state_get scan_skip_streak 0)"
    exit 2
  fi
  log "ERROR internal (streak $internal_streak/$INTERNAL_STREAK_CRITICAL) — probe script missing at $PROBE"
  state_write "$(state_get streak 0)" "$(state_get first_fail -)" "$(state_get since_remind 0)" \
    "$(state_get level OK)" "$internal_streak" "$(state_get pending_page -)" "$(state_get scan_skip_streak 0)"
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
    OUT="${scan_total} check(s) failed (${scan_p0} P0 critical)
| ❌ edge-html-desync | dead chunk refs (breaks cache-cold visitors) and/or stale 1y-pinned edge copies on watched pages | ${scan_detail}"
    RC=1
  elif (( SCAN_RC == 3 )); then
    scan_skip_next="$(( $(state_get scan_skip_streak 0) + 1 ))"
    # TTL-lane findings during a degraded scan still surface as P1 WARN —
    # this is exactly the masking window NFM-5446 closes.
    if [[ "$scan_detail" == *"P1 stale"* ]]; then
      OUT="${scan_total} check(s) failed (0 P0 critical)
| ❌ edge-html-desync | stale 1y-pinned edge copies on watched pages (TTL lane during degraded scan) | ${scan_detail}"
      RC=1
    fi
    if (( scan_skip_next >= SCAN_SKIP_CRITICAL )); then
      pending="$(state_get pending_page -)"
      if [[ "$pending" == "-" || -z "$pending" ]]; then pending="$NOW"; fi
      log "CRITICAL — sentinel chunk-scan blind (NFM-5446): scan degraded ${scan_skip_next} consecutive probe(s) — ${scan_detail} — P0 lane UNVERIFIED (monitoring blindness)"
      notify_page "sentinel chunk-scan blind (${scan_skip_next} consecutive degraded scans) — P0 lane unverified"
      state_write "$(state_get streak 0)" "$(state_get first_fail -)" "$(state_get since_remind 0)" \
        CRITICAL 0 "$pending" "$scan_skip_next"
      exit 2
    fi
    log "WARN — chunk-scan DEGRADED, skip streak ${scan_skip_next}/${SCAN_SKIP_CRITICAL} (NFM-5446): ${scan_detail} — P0 lane unverified this cycle"
    scan_degraded_note=1
  elif (( SCAN_RC == 0 )); then
    if (( $(state_get scan_skip_streak 0) >= SCAN_SKIP_CRITICAL )); then
      log "RECOVERY — chunk-scan completed again after blind streak $(state_get scan_skip_streak 0) (NFM-5446) — SRE closes the sentinel-blind alert"
    fi
    scan_skip_next=0
  else
    # scan launcher itself failed (python missing, etc.) — degraded, not green
    scan_skip_next="$(( $(state_get scan_skip_streak 0) + 1 ))"
    log "WARN — chunk-scan DEGRADED, skip streak ${scan_skip_next}/${SCAN_SKIP_CRITICAL}: scan launcher rc=$SCAN_RC — P0/pin lanes unverified this cycle"
    scan_degraded_note=1
  fi
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
  state_write 0 - 0 OK 0 "$pending" "$scan_skip_next"
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

  if [[ "$p0" != "0" ]] && (( streak >= STREAK_THRESHOLD )); then
    pending="$(state_get pending_page -)"
    if [[ "$pending" == "-" || -z "$pending" ]]; then
      pending="$NOW"   # latch: survives recovery until the heartbeat acks
    fi
    if (( since_remind >= REMINDER_RUNS )); then
      since_remind=0
      log "CRITICAL (ongoing reminder, first failure $first_fail) — $failed check(s) failed ($p0 P0) — $faildetail"
    else
      log "CRITICAL — public-edge P0 failure confirmed ($STREAK_THRESHOLD consecutive probes, first failure $first_fail) — $failed check(s) failed ($p0 P0) — $faildetail"
    fi
    notify_page "P0 public-edge failure: $failed check(s) down ($p0 P0)"
    state_write "$streak" "$first_fail" "$since_remind" CRITICAL 0 "$pending" "$scan_skip_next"
    exit 2
  fi

  if [[ "$p0" == "0" ]]; then
    log "WARN — P1-only failures ($failed check(s), 0 P0), streak $streak — not page-worthy; reminder cadence applies — $faildetail"
    state_write "$streak" "$first_fail" "$since_remind" WARN 0 "$(state_get pending_page -)" "$scan_skip_next"
  else
    log "WARN — failure streak $streak/$STREAK_THRESHOLD ($failed check(s) failed, $p0 P0) — confirming before CRITICAL — $faildetail"
    state_write "$streak" "$first_fail" "$since_remind" WARN 0 "$(state_get pending_page -)" "$scan_skip_next"
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
    "$(state_get level OK)" "$internal_streak" "$pending" "$(state_get scan_skip_streak 0)"
  exit 2
fi
log "ERROR internal (streak $internal_streak/$INTERNAL_STREAK_CRITICAL) — probe exited rc=$RC: $(printf '%s' "$OUT" | tail -1 | cut -c1-200)"
state_write "$(state_get streak 0)" "$(state_get first_fail -)" "$(state_get since_remind 0)" \
  "$(state_get level OK)" "$internal_streak" "$(state_get scan_skip_streak 0)"
exit 3
