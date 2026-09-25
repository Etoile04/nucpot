#!/bin/bash
# ============================================================================
# NFM-4887 — SIGTERM chokepoint for a wedged host ollama MLX runner.
#
# Installed root-owned at /usr/local/lib/nfm-g2/ollama-runner-term.sh by
# scripts/host-prod-gate/host_setup.sh; invoked ONLY by
# start-lightrag-watchdog.sh (which runs as nfmdeploy) through the
# command-enumerated sudoers grant in sudoers.d/nfm-prod-deploy:
#
#   nfmdeploy ALL=(root) NOPASSWD: /usr/local/lib/nfm-g2/ollama-runner-term.sh
#
# Why this exists: the 03:30Z rag_audit_index_coverage reingest burst
# wedges the host MLX runner (qwen3.5:4b-nvfp4; twice observed — NFM-4815
# 2026-09-13, NFM-4886 2026-09-16). `ollama stop` leaves the runner PID
# hung in "Stopping…"; only a SIGTERM recovers it (the server respawns
# the runner on the next request). The runner belongs to the desktop
# user (/Applications/Ollama.app/.../ollama runner --mlx-engine --model
# <m> --port <n>) while the watchdog daemon is nfmdeploy — it cannot
# signal the runner directly, hence this root-owned helper.
#
# The VALIDATION is the security boundary of the sudoers grant — this
# script must never become a general-purpose kill:
#   1. the pid is re-read via ps IMMEDIATELY before the signal (no
#      acting on a stale discovery read),
#   2. the process must be an `ollama runner`,
#   3. its --model must match the requested model (a co-loaded tenant
#      runner — qwen3.8:27b-mlx on this host — is refused),
#   4. exactly one SIGTERM, then a bounded liveness poll. NO SIGKILL
#      escalation: a runner that ignores SIGTERM is an operator record,
#      not something the daemon should force-kill.
#
# NFM-5083 (Option D-2): same chokepoint, additive --max-lifetime <sec>
# mode for preventive recycle of IDLE MLX runners older than the knob
# (initial 1500s = 25 min — may be raised to 90 min once upstream #18505
# lands via NFM-4925). The under-load guard is `ps -o %cpu=` — a runner
# actively decoding pegs CPU and is NEVER recycled, even past the
# lifetime; the mode exists to also recycle a stable, no-longer-needed
# subprocess before it has had time to wedge, so future wedge events
# become bounded single-document failures rather than container-down
# events. The existing wedge-recovery path is untouched: --max-lifetime
# is opt-in (default behavior unchanged).
#
# NFM-5219 (Option E-1): additive --ignore-busy flag for the
# --max-lifetime mode, passed ONLY inside the SRE-tuned burst window
# (03:25-05:00Z) by the lightrag watchdog. The nvfp4 hang PRESENTS as
# eternal busy (prefill stall at processed=total-1, upstream #18505
# still open), so the D-2 under-load guard exempts the wedged runner
# FOREVER — the 2026-09-24T04:00:10Z wedge had ZERO d2= events. With
# the flag the CPU comparison is skipped; the AGE gate and the chokepoint
# validation are unchanged, and the wedge-recovery mode never passes it
# (daytime D-2 behavior stays byte-identical to NFM-5083).
#
# usage: ollama-runner-term.sh <pid> --model <model>
#        ollama-runner-term.sh <pid> --model <model> --max-lifetime <sec> [--ignore-busy]
# Exit codes:
#   0   runner terminated (or already gone — idempotent)
#   1   runner still alive after SIGTERM + grace window
#   64  usage error (bad pid / bad or missing --model / bad --max-lifetime)
#   65  pid is not an `ollama runner` process — REFUSED
#   66  runner --model does not match — REFUSED
#   67  max-lifetime: runner age < knob — SKIP (no kill, too young)
#   68  max-lifetime: runner busy (CPU active) — SKIP — under-load guard
#       (unreachable with --ignore-busy: the CPU comparison is skipped)
# ============================================================================
set -euo pipefail

WAIT_SEC="${NFM_OLLAMA_TERM_WAIT_SEC:-15}"
# External kill, not the shell builtin: deterministic under sudo (the
# default is the absolute real binary) and interceptable by the test
# shims via env — sudo env_reset never passes the override through.
KILL_BIN="${NFM_OLLAMA_TERM_KILL:-/bin/kill}"
# D-2: idle-threshold CPU% (a runner below this is considered idle and
# eligible for preventive recycle). 5.0% leaves ample headroom for any
# real generation burst while ensuring a wedged idle runner is caught.
IDLE_CPU_MAX="${NFM_OLLAMA_TERM_IDLE_CPU_MAX:-5.0}"

usage() {
  echo "usage: ollama-runner-term.sh <pid> --model <model> [--max-lifetime <sec>]" >&2
  exit 64
}

# ---- arg parse --------------------------------------------------------------
# Shape 1: 3 args  → <pid> --model <model>          (wedge-recovery mode)
# Shape 2: 5 args  → <pid> --model <model> --max-lifetime <sec>  (D-2)
# Shape 3: 6 args  → ... --max-lifetime <sec> --ignore-busy      (E-1 window)
ignore_busy=0
case "$#" in
  3) max_lifetime=0 ;;            # 0 = wedge mode (skip the D-2 gate)
  5)
    [ "$4" = "--max-lifetime" ] || usage
    max_lifetime="$5"
    case "${max_lifetime}" in
      ''|*[!0-9]*) usage ;;
    esac
    [ "${max_lifetime}" -gt 0 ] || usage
    ;;
  6)
    [ "$4" = "--max-lifetime" ] || usage
    [ "$6" = "--ignore-busy" ] || usage
    max_lifetime="$5"
    case "${max_lifetime}" in
      ''|*[!0-9]*) usage ;;
    esac
    [ "${max_lifetime}" -gt 0 ] || usage
    ignore_busy=1
    ;;
  *) usage ;;
esac

pid="$1"
[ "$2" = "--model" ] || usage
model="$3"
[ -n "${model}" ] || usage
case "${model}" in
  *'*'*|*'?'*|*'['*|*'\'*|*'"'*) usage ;;
esac
case "${pid}" in
  ''|*[!0-9]*) usage ;;
esac
[ "${pid}" -gt 0 ] || usage

# ---- D-2 helper: parse ps etime -> seconds ---------------------------------
# `ps -o etime=` on macOS returns [[DD-]HH:]MM:SS (blank if process gone).
# All-arithmetic, no external deps; falls back to 0 on any malformed input
# so the policy gate errs on the safe (skip-recycle) side.
etime_to_seconds() {
  local raw="$1" days=0 hours=0 mins=0 secs=0
  # Defensive: strip any space padding a ps variant may emit — `10#`
  # arithmetic below is not space-tolerant, and the split (IFS=:) would
  # otherwise glue a leading space onto the first field.
  raw="${raw// /}"
  # Strip optional DD- prefix.
  case "${raw}" in
    *-*)
      days="${raw%%-*}"
      raw="${raw#*-}"
      case "${days}" in ''|*[!0-9]*) days=0 ;; esac
      ;;
  esac
  # bash 3.2 (host's /bin/bash) has no mapfile/readarray — split via IFS
  # round-trip with positional parameters; HH:MM:SS lands in $1/$2/$3,
  # MM:SS lands in $1/$2. Word-split is intentional (IFS=:).
  local IFS_BAK="${IFS}"
  IFS=:
  # SC2086: word-split is intentional — colon-separated etime parts.
  set -- ${raw}
  IFS="${IFS_BAK}"
  case $# in
    2) mins="$1"; secs="$2" ;;
    3) hours="$1"; mins="$2"; secs="$3" ;;
    *) printf '%s\n' "0"; return ;;
  esac
  # NFM-5122: ps ZERO-PADS every etime field (a 3s-old process prints
  # 00:03) and bash 3.2 `$(( ))` treats leading-zero tokens as OCTAL —
  # 00-07 parse to identical values, but 08/09 throw "value too great
  # for base", which under `set -e` aborts rc=1 BEFORE the busy-guard/
  # SIGTERM (watchdog then misclassifies the probe as d2=error). Force
  # base-10; `${var:-0}` guards empties because `10#` alone rejects an
  # empty expansion (word-split can yield empty fields, e.g. `08::09`).
  # Malformed-input contract (unchanged from the octal era, where a bad
  # token evaluated as an unset name → 0): any non-numeric field falls
  # back to 0 = safe skip rather than aborting — `10#` alone would turn
  # a bad token into a script abort under set -e. Concatenated scan so
  # one case guards all four fields (days is pre-validated above).
  case "${days}${hours}${mins}${secs}" in
    *[!0-9]*) printf '%s\n' "0"; return ;;
  esac
  printf '%s\n' "$(( 10#${days:-0} * 86400 + 10#${hours:-0} * 3600 + 10#${mins:-0} * 60 + 10#${secs:-0} ))"
}

# ---- chokepoint validation (runs FIRST — same as wedge mode) ----------------
# The command read doubles as validation input and liveness probe — the
# same `ps -o command= -p <pid>` shape, re-issued right before the kill.
runner_cmd() { ps -o command= -p "${pid}" 2>/dev/null || true; }

cmd="$(runner_cmd)"
if [ -z "${cmd}" ]; then
  echo "pid=${pid} already-gone"
  exit 0
fi
case "${cmd}" in
  *"ollama runner"*) ;;
  *)
    echo "refuse: pid=${pid} is not an ollama runner process: ${cmd}" >&2
    exit 65
    ;;
esac
case "${cmd}" in
  *"--model ${model} "*|*"--model ${model}") ;;
  *)
    echo "refuse: pid=${pid} model mismatch (wanted --model ${model}): ${cmd}" >&2
    exit 66
    ;;
esac

# ---- D-2 max-lifetime policy check (additive; wedge path skips this) --------
if [ "${max_lifetime}" -gt 0 ]; then
  # `ps -o etime= -p <pid>` on macOS returns [[DD-]HH:]MM:SS (empty if gone).
  # The process table is the same one we just validated — gone-again is the
  # idempotent success case (the race window between read and signal is
  # exactly the case the chokepoint's "always re-read" rule exists for).
  etime_raw="$(ps -o etime= -p "${pid}" 2>/dev/null || true)"
  if [ -z "${etime_raw}" ]; then
    echo "max-lifetime pid=${pid} already-gone (model=${model})"
    exit 0
  fi
  age_secs="$(etime_to_seconds "${etime_raw}")"
  if [ "${age_secs}" -lt "${max_lifetime}" ]; then
    echo "max-lifetime pid=${pid} model=${model} age=${age_secs}s < ${max_lifetime}s — skip"
    exit 67
  fi
  # Under-load guard: a runner mid-decode pegs CPU. The threshold is a
  # floor below the lowest observed real-generation tail (the reingest
  # burst's decode phase samples at ≥500 — well above 5.0%); anything
  # below this is the idle signal we want to recycle on. CPU > threshold
  # → busy, skip. The script reads CPU ONCE immediately before the kill
  # — same freshness guarantee as the chokepoint's re-read of command.
  cpu_raw="$(ps -o %cpu= -p "${pid}" 2>/dev/null || true)"
  # %cpu may be blank, "0.0", " 0.0", or "12.3". Truncate (NOT round)
  # to integer for the threshold comparison — rounding would push 4.9
  # to 5 and falsely flag an idle runner as busy. The decimal-trim is
  # the portable form (no awk dependency, no printf rounding surprises).
  # A missing/blank reading (process vanished between the command check
  # and this read — rare race) defaults to the THRESHOLD value so the
  # runner is treated as busy → skip. The follow-on kill will fail
  # idempotently if the pid is truly gone (handled below).
  cpu_int="${cpu_raw%%.*}"
  cpu_int="${cpu_int// /}"
  cpu_int="${cpu_int:-${idle_int:-5}}"
  idle_int="${IDLE_CPU_MAX%%.*}"
  idle_int="${idle_int// /}"
  idle_int="${idle_int:-5}"
  if [ "${ignore_busy}" -eq 0 ] && [ "${cpu_int}" -ge "${idle_int}" ]; then
    echo "max-lifetime pid=${pid} model=${model} age=${age_secs}s cpu=${cpu_raw} >= ${IDLE_CPU_MAX} — busy, skip"
    exit 68
  fi
  if [ "${ignore_busy}" -eq 1 ]; then
    # NFM-5219 E-1: busy-guard suppressed for the in-window unconditional
    # recycle — the hang's signature IS eternal busy, so this guard would
    # exempt the wedged runner forever. CPU is still read and recorded for
    # the operator; the age gate above has already run.
    echo "max-lifetime pid=${pid} model=${model} age=${age_secs}s cpu=${cpu_raw:-unknown} busy-guard=suppressed — recycling (E-1 window)"
  else
    echo "max-lifetime pid=${pid} model=${model} age=${age_secs}s cpu=${cpu_raw} <= ${IDLE_CPU_MAX} — recycling"
  fi
fi

if ! "${KILL_BIN}" -TERM "${pid}" 2>/dev/null; then
  # Root only fails here if the pid vanished since the read above —
  # which is the idempotent success case.
  echo "pid=${pid} gone-before-signal"
  exit 0
fi

waited=0
while [ "${waited}" -lt "${WAIT_SEC}" ]; do
  if [ -z "$(runner_cmd)" ]; then
    echo "terminated pid=${pid} model=${model}"
    exit 0
  fi
  sleep 1
  waited=$(( waited + 1 ))
done
if [ -z "$(runner_cmd)" ]; then
  echo "terminated pid=${pid} model=${model}"
  exit 0
fi
echo "still-alive pid=${pid} model=${model} after ${WAIT_SEC}s — operator attention" >&2
exit 1