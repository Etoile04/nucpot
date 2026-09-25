#!/bin/bash
# ============================================================================
# NFM-4804 (ADR-013 G2) — LightRAG pipeline-stall watchdog.
#
# Installed root-owned at /usr/local/lib/nfm-g2/start-lightrag-watchdog.sh by
# scripts/host-prod-gate/host_setup.sh; invoked every 5 minutes by
# /Library/LaunchDaemons/com.nfm.g2.lightrag-watchdog.plist as the deploy
# identity (nfmdeploy; docker via the full gate socket).
#
# The freeze (NFM-4505 recurrence, 2026-09-12 15:49Z): an httpx.ReadTimeout
# during chunk entity-extraction logs
#
#     ERROR: Failed to extract document 1/1: data_source:<uuid>
#     INFO: Enqueued document processing pipeline stopped
#
# and the enqueue consumer dies while uvicorn stays up — the HTTP healthcheck
# stays green, `restart: unless-stopped` never fires, and every subsequently
# enqueued document silently never processes until an operator runs
# `run-recovery.sh restart lightrag` by hand (163-doc backlog observed).
#
# Probe (false-positive averse; docker logs persist across restarts, so the
# window is anchored at the CURRENT container boot from docker inspect):
#   WEDGED iff, since the current boot:
#     1. "Enqueued document processing pipeline stopped" appears, AND
#     2. the pipeline marker nearest that stop line (within the 5 lines
#        before it) is "Failed to extract document" — a normal queue
#        drain's nearest marker is "Completed processing file N/M" instead
#        (an extraction failure the pipeline then continued past is the
#        chronic benign pattern and does not wedge the consumer), AND
#     3. (implicit in the boot anchor) the stop belongs to the current
#        boot, so a restart — manual, docker, or a previous watchdog
#        action — already started a fresh boot whose logs do not contain
#        the pre-boot wedge.
# Action: the ONLY sanctioned prod routes — run-recovery.sh restart lightrag,
# then (NFM-4816) run-recovery.sh lightrag-reprocess once the consumer is
# revived, so the docs the dead consumer stranded (FAILED/PENDING — they
# would otherwise wait for the next daily rag_audit_index_coverage, up to
# 24h of retrieval degradation) re-enqueue immediately — with a cooldown
# (default 30 min) so a persistently failing pipeline cannot restart-loop.
# Every decision appends one record to
# /var/log/nfm-g2/lightrag-watchdog.log; restart timestamps live in
# /var/log/nfm-g2/lightrag-watchdog.state for the cooldown check.
#
# NFM-4887 (root cause of the 03:30Z wedges, NFM-4815 + NFM-4886): the
# reingest burst wedges the HOST ollama MLX runner (qwen3.5:4b-nvfp4) —
# a container restart cannot fix it; only a runner SIGTERM after a
# failed `ollama stop` recovers the host. On WEDGE the watchdog first
# runs a host-remediation stage (see section 5) so the re-enqueued docs
# do not hang against a still-wedged runner; every host failure fails
# OPEN (logs and proceeds) — the proven container recovery is never
# blocked by host-probe infrastructure.
#
# NFM-5083 Option D-2: preventive recycle of IDLE MLX runners older
# than MLX_RUNNER_MAX_LIFETIME_S (initial 1500s = 25 min — may be
# raised to 90 min once upstream #18505 lands via NFM-4925). The chokepoint
# helper handles the chokepoint validation + etime/cpu policy check; the
# watchdog only invokes it on every probe (this is the point of D-2:
# recycle before wedge probability grows), regardless of whether a
# wedge was detected. Exit 67 = under threshold, exit 68 = busy (under-
# load guard) — both are no-ops and not worth a per-probe log entry.
#
# NFM-5219 Option E-1: burst-window deterministic lifecycle, 03:25-05:00Z
# daily, window-scoped ONLY. Deployed D-2 was a GLOBAL 1500s knob on a
# 5-min cadence, and the nvfp4 hang PRESENTS as eternal busy — so inside
# the nightly burst window the eval never recycled the burst runner
# (2026-09-24T04:00:10Z wedge: ZERO d2= events bracketing it). In-window
# the watchdog (a) recycles the runner once at window entry (pre-burst,
# unconditional, so the 03:30Z burst starts cold), (b) applies the
# in-window max-lifetime knob with the busy-guard DISABLED, (c) logs a
# d2= decision line for EVERY eval (d2=recycled | d2=skip reason=... |
# d2=error) so the E-gate can distinguish 'policy inactive' from 'policy
# active but insufficient', and (d) re-execs the single-probe body every
# E1 tick (<=60s) for the whole window. Outside the window, every
# behavior — cadence, knob, busy-guard, silent 67/68 — is byte-identical
# to NFM-5083.
#
# Exit codes:
#   0  clean (no wedge, or cooldown suppressed)
#   2  wedge detected AND sanctioned restart issued
#   1  probe failure (docker unreachable, container missing, ...)
# ============================================================================
set -euo pipefail

CONTAINER="${NFM_LIGHTRAG_WATCHDOG_CONTAINER:-nucpot-prod-lightrag}"
STATE="${NFM_LIGHTRAG_WATCHDOG_STATE:-/var/log/nfm-g2/lightrag-watchdog.state}"
LOG="${NFM_LIGHTRAG_WATCHDOG_LOG:-/var/log/nfm-g2/lightrag-watchdog.log}"
COOLDOWN_MIN="${NFM_LIGHTRAG_WATCHDOG_COOLDOWN_MIN:-30}"
RECOVERY="${NFM_LIGHTRAG_WATCHDOG_RECOVERY:-/usr/local/lib/nfm-g2/run-recovery.sh}"
LOOKBACK_LINES=5

# NFM-4887 host-runner remediation knobs (all env-overridable so tests
# stay hermetic — a bare pytest run must never touch the real ollama).
OLLAMA_MODEL="${NFM_LIGHTRAG_WATCHDOG_OLLAMA_MODEL:-qwen3.5:4b-nvfp4}"
OLLAMA_URL="${NFM_LIGHTRAG_WATCHDOG_OLLAMA_URL:-http://127.0.0.1:11434}"
GEN_TIMEOUT_SEC="${NFM_LIGHTRAG_WATCHDOG_GEN_TIMEOUT_SEC:-45}"
VERIFY_TIMEOUT_SEC="${NFM_LIGHTRAG_WATCHDOG_VERIFY_TIMEOUT_SEC:-90}"
STOP_WAIT_SEC="${NFM_LIGHTRAG_WATCHDOG_STOP_WAIT_SEC:-15}"
CURL_BIN="${NFM_LIGHTRAG_WATCHDOG_CURL:-/usr/bin/curl}"
OLLAMA_BIN="${NFM_LIGHTRAG_WATCHDOG_OLLAMA:-ollama}"
# Word-split by design (default carries sudo + path); the runner belongs
# to the desktop user, so the daemon (nfmdeploy) signals it only through
# this root-owned validating chokepoint.
TERM_CMD="${NFM_LIGHTRAG_WATCHDOG_TERM:-sudo -n /usr/local/lib/nfm-g2/ollama-runner-term.sh}"
# NFM-5083 D-2: preventive recycle of MLX runners older than this knob
# (default 1500s = 25 min — raises to 90 min after upstream #18505 lands
# via NFM-4925). Set to 0 to disable D-2 (the wedge-recovery path below
# is untouched regardless).
MLX_RUNNER_MAX_LIFETIME_S="${NFM_LIGHTRAG_WATCHDOG_MLX_MAX_LIFETIME_S:-1500}"

# NFM-5219 Option E-1 knobs (all env-overridable for hermetic tests; the
# launchd environment never sets them, so script defaults ARE the policy).
# Bounds are minutes-of-day UTC and must not cross UTC midnight
# (03:25Z=205, 05:00Z=300). The in-window lifetime knob carries a hard
# SRE floor of 300s. E1_STATE is derived from STATE so hermetic tests
# isolating STATE isolate the preburst marker too.
E1_ENABLED="${NFM_LIGHTRAG_WATCHDOG_E1_ENABLED:-1}"
E1_START_MIN="${NFM_LIGHTRAG_WATCHDOG_E1_START_MIN:-205}"
E1_END_MIN="${NFM_LIGHTRAG_WATCHDOG_E1_END_MIN:-300}"
E1_MAX_LIFETIME_S="${NFM_LIGHTRAG_WATCHDOG_E1_MAX_LIFETIME_S:-600}"
E1_TICK_S="${NFM_LIGHTRAG_WATCHDOG_E1_TICK_S:-60}"
E1_STATE="${NFM_LIGHTRAG_WATCHDOG_E1_STATE:-${STATE}.e1}"
# Hermetic-test hooks only (launchd env never carries them): E1_TICKS_MAX
# caps child re-execs per window — a mis-set cap degrades in-window
# cadence back toward the 5-min launchd fires, never correctness.
E1_TICKS_MAX="${NFM_LIGHTRAG_WATCHDOG_E1_TICKS_MAX:-0}"
# Fail-safe validation: any malformed knob disables the window policy
# entirely (daytime D-2 semantics) rather than aborting the probe.
case "x${E1_START_MIN}x${E1_END_MIN}x${E1_MAX_LIFETIME_S}x${E1_TICK_S}x${E1_TICKS_MAX}x" in
  *xx*|*[!0-9x]*) E1_ENABLED=0 ;;
esac
# SRE-tunable floor (NFM-5219 scope 2): never below 300s in-window.
if [ "${E1_MAX_LIFETIME_S}" -lt 300 ] 2>/dev/null; then
  E1_MAX_LIFETIME_S=300
fi
E1_IN_WINDOW=0
# NFM-5122: `date -u +%H` / `%M` are zero-padded ("09") and bash 3.2 treats
# leading-zero tokens as OCTAL — 08/09 abort with "value too great for
# base" under set -e. Force base-10 on both fields.
_e1_mod=$(( 10#$(date -u +%H) * 60 + 10#$(date -u +%M) ))
if [ "${E1_ENABLED}" = "1" ] \
   && [ "${_e1_mod}" -ge "${E1_START_MIN}" ] 2>/dev/null \
   && [ "${_e1_mod}" -lt "${E1_END_MIN}" ] 2>/dev/null; then
  E1_IN_WINDOW=1
fi

# Under launchd (UserName=nfmdeploy) PATH is the secure path and no docker
# context/DOCKER_HOST is set, so the CLI would target the walled default
# socket and every probe silently degrades to running=absent. Same pinned
# env as the other sanctioned entries (run-recovery/run-cleanup); the
# inherited PATH stays first so hermetic tests can prepend a fake docker.
export PATH="${PATH:+${PATH}:}/usr/local/bin:/usr/local/sbin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export DOCKER_HOST="${NFM_LIGHTRAG_WATCHDOG_DOCKER_HOST:-unix:///var/run/nfm-g2/docker-full.sock}"
export DOCKER_CONFIG="${NFM_LIGHTRAG_WATCHDOG_DOCKER_CONFIG:-/var/lib/nfmdeploy/.docker}"

mkdir -p "$(dirname "${STATE}")" "$(dirname "${LOG}")"

now_epoch() { date +%s; }

log_record() {
  # One line per decision; consumed by operators grepping the G2 log dir.
  printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >>"${LOG}"
}

# ---------------------------------------------------------------------------
# 0. Shared host-runner helpers (used by BOTH the D-2 always-run stage and
#    the wedge-recovery host stage). Hoisted here so the D-2 invocation
#    at section 1b can call find_runner before the wedge-stage definitions
#    at section 5 — bash resolves function names at CALL time (not at the
#    definition time of the enclosing function), so an earlier reference
#    inside max_lifetime_check to a not-yet-defined find_runner would fail
#    "command not found". Defining these here keeps both paths honest.
# ---------------------------------------------------------------------------
runner_cmd_for_pid() { ps -o command= -p "$1" 2>/dev/null || true; }

find_runner() {
  # The runner serving the RAG model: `ollama runner ... --model <m>`.
  # A co-loaded tenant runner (different --model) is invisible here.
  local pid cmd
  for pid in $(pgrep -f 'ollama runner' 2>/dev/null || true); do
    cmd="$(runner_cmd_for_pid "${pid}")"
    case "${cmd}" in
      *"--model ${OLLAMA_MODEL} "*|*"--model ${OLLAMA_MODEL}")
        RUNNER_PID="${pid}"
        return 0
        ;;
    esac
  done
  return 1
}

# ---------------------------------------------------------------------------
# 0b. NFM-5219 E-1 burst-window wrapper. When a fire lands inside the
#     window, HOLD the job: re-exec the single-probe body every E1 tick
#     (<=60s) until window close, then exit 0 — in-window lifecycle eval
#     runs at burst cadence instead of the 5-min daytime cadence. launchd
#     never co-runs the same label, so the skipped interval fires are by
#     design (AC3: daytime cadence untouched). Children carry E1_CHILD=1
#     so they run the body exactly once and never re-enter this wrapper;
#     NFM_LIGHTRAG_WATCHDOG_E1_SELF is a hermetic-test hook (launchd
#     invokes this file directly, so prod always takes the $0 branch).
#     A hand-run of this script inside the window also enters the loop —
#     bounded by the same tick/age gates, so the worst case is a doubled
#     tick rate, never an unbounded action.
# ---------------------------------------------------------------------------
if [ "${NFM_LIGHTRAG_WATCHDOG_E1_CHILD:-0}" != "1" ] && [ "${E1_IN_WINDOW}" = "1" ]; then
  log_record "e1=window-enter bounds=${E1_START_MIN}-${E1_END_MIN} tick=${E1_TICK_S}s lifetime=${E1_MAX_LIFETIME_S}s busy_guard=off"
  _e1_ticks=0
  while :; do
    NFM_LIGHTRAG_WATCHDOG_E1_CHILD=1 bash "${NFM_LIGHTRAG_WATCHDOG_E1_SELF:-$0}" || true
    _e1_ticks=$(( _e1_ticks + 1 ))
    if [ "${E1_TICKS_MAX}" -gt 0 ] 2>/dev/null && [ "${_e1_ticks}" -ge "${E1_TICKS_MAX}" ]; then
      break
    fi
    # NFM-5122: %H/%M are zero-padded — force base-10 (08/09 would abort).
    _e1_mod=$(( 10#$(date -u +%H) * 60 + 10#$(date -u +%M) ))
    [ "${_e1_mod}" -lt "${E1_END_MIN}" ] || break
    sleep "${E1_TICK_S}"
  done
  log_record "e1=window-exit ticks=${_e1_ticks}"
  exit 0
fi

# ---------------------------------------------------------------------------
# 1. Container up? (if not, compose `restart: unless-stopped` owns it)
# ---------------------------------------------------------------------------
running="$(docker inspect -f '{{.State.Running}}' "${CONTAINER}" 2>/dev/null || true)"
if [ "${running}" != "true" ]; then
  log_record "probe=skip container=${CONTAINER} running=${running:-absent} (compose policy owns restarts)"
  exit 0
fi

# ---------------------------------------------------------------------------
# 1a. NFM-5219 E-1 pre-burst recycle: exactly once per UTC day, on the
#     first in-window eval, recycle the RAG runner UNCONDITIONALLY (wedge-
#     mode chokepoint — no age gate, no busy guard) so the 03:30Z burst
#     starts on a cold runner. The date-keyed marker stops the <=60s
#     in-window ticks from repeating it; UTC midnight rolls it over
#     naturally. LightRAG-side recovery of the preempted request is the
#     proven D-1 90s consumer timeout + lightrag-reprocess path.
# ---------------------------------------------------------------------------
e1_preburst() {
  [ "${E1_IN_WINDOW}" = "1" ] || return 0
  local today last rc
  today="$(date -u +%Y-%m-%d)"
  last="$(cat "${E1_STATE}" 2>/dev/null || true)"
  [ "${last}" = "${today}" ] && return 0
  if find_runner; then
    rc=0
    ${TERM_CMD} "${RUNNER_PID}" --model "${OLLAMA_MODEL}" || rc=$?
    case "${rc}" in
      0) log_record "d2=recycled window=1 phase=preburst model=${OLLAMA_MODEL} pid=${RUNNER_PID}" ;;
      *) log_record "d2=error window=1 phase=preburst model=${OLLAMA_MODEL} pid=${RUNNER_PID} rc=${rc} (operator attention)" ;;
    esac
  else
    log_record "d2=skip window=1 phase=preburst reason=no-runner model=${OLLAMA_MODEL}"
  fi
  printf '%s\n' "${today}" >"${E1_STATE}" || true
}
e1_preburst || log_record "d2=infra-error window=1 phase=preburst (failed open; wedge check proceeds)"

# ---------------------------------------------------------------------------
# 1b. NFM-5083 D-2 — preventive max-lifetime recycle (always-run stage).
#     Runs on every probe regardless of wedge status; the point of D-2
#     is to recycle before wedge probability grows. Failure paths fail
#     OPEN (logs and proceeds) so the wedge-recovery path below is
#     never blocked by D-2 infra. Exit 67/68 are no-ops — they signal
#     "not eligible right now" and the next 5-minute probe will retry.
#
#     NFM-5219 E-1: inside the burst window the effective policy is the
#     in-window knob with the busy-guard DISABLED (the nvfp4 hang
#     PRESENTS as eternal busy — a busy-exempt eval never recycles the
#     wedged runner), and EVERY decision leaves a d2= line (recycled /
#     skip+reason / error) so the E-gate can tell 'policy inactive' from
#     'policy active but insufficient'. Outside the window the policy,
#     line formats, and silent 67/68 are byte-identical to NFM-5083.
# ---------------------------------------------------------------------------
max_lifetime_check() {
  local eff_lifetime="" busy_flag=""
  if [ "${E1_IN_WINDOW}" = "1" ]; then
    eff_lifetime="${E1_MAX_LIFETIME_S}"
    busy_flag="--ignore-busy"
  else
    eff_lifetime="${MLX_RUNNER_MAX_LIFETIME_S}"
  fi
  if [ "${eff_lifetime}" -le 0 ] 2>/dev/null; then
    return 0
  fi
  if ! find_runner; then
    if [ "${E1_IN_WINDOW}" = "1" ]; then
      log_record "d2=skip window=1 reason=no-runner model=${OLLAMA_MODEL}"
    fi
    return 0
  fi
  rc=0
  # shellcheck disable=SC2086  # busy_flag word-split is intentional
  ${TERM_CMD} "${RUNNER_PID}" --model "${OLLAMA_MODEL}" \
    --max-lifetime "${eff_lifetime}" ${busy_flag} || rc=$?
  if [ "${E1_IN_WINDOW}" = "1" ]; then
    case "${rc}" in
      0)  log_record "d2=recycled window=1 model=${OLLAMA_MODEL} pid=${RUNNER_PID} max_lifetime_s=${eff_lifetime} busy_guard=off" ;;
      67) log_record "d2=skip window=1 reason=under-max-lifetime age_lt=${eff_lifetime}s model=${OLLAMA_MODEL} pid=${RUNNER_PID}" ;;
      68) log_record "d2=skip window=1 reason=busy model=${OLLAMA_MODEL} pid=${RUNNER_PID} busy_guard=on (unexpected in-window — flag not passed?)" ;;
      *)  log_record "d2=error window=1 model=${OLLAMA_MODEL} pid=${RUNNER_PID} rc=${rc} (operator attention)" ;;
    esac
  else
    case "${rc}" in
      0)  log_record "d2=recycled model=${OLLAMA_MODEL} pid=${RUNNER_PID} max_lifetime_s=${eff_lifetime}" ;;
      67) ;;  # under threshold — common, no log noise
      68) ;;  # busy — legitimate load, no log noise
      *)  log_record "d2=error model=${OLLAMA_MODEL} pid=${RUNNER_PID} rc=${rc} (operator attention)" ;;
    esac
  fi
}
max_lifetime_check || log_record "d2=infra-error (D-2 stage failed open; wedge check proceeds)"

# ---------------------------------------------------------------------------
# 2. Logs since the current boot (docker logs survive restarts; the anchor
#    makes any pre-boot wedge invisible, which is exactly the recovered case)
# ---------------------------------------------------------------------------
boot_at="$(docker inspect -f '{{.State.StartedAt}}' "${CONTAINER}" 2>/dev/null || true)"
if [ -z "${boot_at}" ]; then
  log_record "probe=error container=${CONTAINER} reason=no-StartedAt"
  exit 1
fi

# NOTE: /bin/bash on this host is 3.2 — no mapfile, and no array
# expansion under `set -u` on a possibly-empty array. The log is parsed
# from a plain string via here-string instead.
LOGS="$(docker logs --since "${boot_at}" "${CONTAINER}" 2>&1 || true)"

stop_line=0
line_no=0
while IFS= read -r line; do
  line_no=$(( line_no + 1 ))
  case "${line}" in
    *"Enqueued document processing pipeline stopped"*) stop_line=${line_no} ;;
  esac
done <<<"${LOGS}"

if [ "${stop_line}" -eq 0 ]; then
  log_record "probe=clean container=${CONTAINER} boot=${boot_at} reason=no-pipeline-stop"
  exit 0
fi

# ---------------------------------------------------------------------------
# 3. Error-induced stop? The pipeline marker NEAREST the stop line decides:
#    - "Failed to extract document" (with traceback lines) directly before
#      the stop → the enqueue consumer died mid-document → WEDGE.
#    - "Completed processing file N/M" before the stop → normal queue
#      drain (deploy / cleanup backlog). A "Failed to extract" on an
#      EARLIER document that the pipeline then continued past is the
#      chronic benign pattern (28/24h) and must not restart.
# ---------------------------------------------------------------------------
ctx_from=$(( stop_line - LOOKBACK_LINES )); [ "${ctx_from}" -lt 1 ] && ctx_from=1
marker=""
line_no=0
while IFS= read -r line; do
  line_no=$(( line_no + 1 ))
  if [ "${line_no}" -ge "${ctx_from}" ] && [ "${line_no}" -lt "${stop_line}" ]; then
    case "${line}" in
      *"Failed to extract document"*) marker=failed ;;
      *"Completed processing file"*) marker=completed ;;
    esac
  fi
done <<<"${LOGS}"

if [ "${marker}" != "failed" ]; then
  log_record "probe=clean container=${CONTAINER} boot=${boot_at} reason=normal-drain"
  exit 0
fi

# ---------------------------------------------------------------------------
# 4. Cooldown — never restart-loop a persistently failing pipeline
# ---------------------------------------------------------------------------
now="$(now_epoch)"
last_restart=0
if [ -f "${STATE}" ]; then
  # shellcheck disable=SC1090
  last_restart="$(grep -E '^last_restart=' "${STATE}" | tail -1 | cut -d= -f2 || true)"
  [ -z "${last_restart}" ] && last_restart=0
fi
cooldown_sec=$(( COOLDOWN_MIN * 60 ))
if [ $(( now - last_restart )) -lt "${cooldown_sec}" ]; then
  log_record "probe=suppressed container=${CONTAINER} reason=cooldown age_min=$(( (now - last_restart) / 60 ))"
  exit 0
fi

# ---------------------------------------------------------------------------
# 5. Host runner remediation (NFM-4887) — runs BEFORE the container
#    recovery so the re-enqueued docs do not hang against a still-wedged
#    host runner. Twice-validated manual playbook, automated:
#    tiny generate probe → `ollama stop` → SIGTERM iff the runner PID
#    survives the stop → verify generate. Every failure path fails OPEN:
#    log and continue — the container-level recovery below is proven and
#    must never be blocked by host-probe infrastructure.
#    (runner_cmd_for_pid + find_runner are defined in section 0 — D-2 needs
#    them too, and bash resolves function names at CALL time.)
# ---------------------------------------------------------------------------
gen_probe() {
  # $1 = curl --max-time budget. Success = HTTP 200 + a completed
  # generation ("done":true). A healthy multi-tenant host serves this in
  # ~20-30s (queued behind tenant traffic — NFM-4886 baseline); the
  # wedged runner returns nothing at all.
  local t="$1" body http payload
  body="$(mktemp "${TMPDIR:-/tmp}/nfm-lrw-gen.XXXXXX")" || return 1
  payload="$(printf '{"model":"%s","prompt":"ping","stream":false,"options":{"num_predict":1}}' "${OLLAMA_MODEL}")"
  http="$("${CURL_BIN}" -sS --max-time "${t}" -o "${body}" -w '%{http_code}' \
    "${OLLAMA_URL}/api/generate" -d "${payload}" 2>/dev/null || true)"
  if [ "${http}" = "200" ] && grep -q '"done": *[Tt]rue' "${body}" 2>/dev/null; then
    rm -f "${body}"
    return 0
  fi
  rm -f "${body}"
  return 1
}

stop_runner_graceful() {
  # 0 = runner pid gone after the bounded window; 1 = PID survived
  # (the twice-observed wedge signature: stop hangs in "Stopping…").
  # The CLI child is reaped/killed either way — a wedged server hangs it.
  "${OLLAMA_BIN}" stop "${OLLAMA_MODEL}" >/dev/null 2>&1 &
  local cli_pid=$!
  local waited=0
  while [ "${waited}" -lt "${STOP_WAIT_SEC}" ]; do
    if [ -z "$(runner_cmd_for_pid "${RUNNER_PID}")" ]; then
      break
    fi
    sleep 1
    waited=$(( waited + 1 ))
  done
  kill "${cli_pid}" 2>/dev/null || true
  wait "${cli_pid}" 2>/dev/null || true
  [ -z "$(runner_cmd_for_pid "${RUNNER_PID}")" ]
}

host_remediate() {
  if gen_probe "${GEN_TIMEOUT_SEC}"; then
    log_record "host=healthy model=${OLLAMA_MODEL} probe=generate"
    return 0
  fi
  if ! find_runner; then
    log_record "host=runner-absent model=${OLLAMA_MODEL} (server down or model unloaded; nothing to signal)"
    return 0
  fi
  cpu="$(ps -o %cpu= -p "${RUNNER_PID}" 2>/dev/null || true)"
  log_record "host=wedge-suspect model=${OLLAMA_MODEL} pid=${RUNNER_PID} cpu=${cpu:-unknown}"
  if stop_runner_graceful; then
    log_record "host=stop-recovered model=${OLLAMA_MODEL} pid=${RUNNER_PID}"
  else
    log_record "host=stop-failed model=${OLLAMA_MODEL} pid=${RUNNER_PID} (pid alive after ollama stop — wedge signature)"
    rc=0
    ${TERM_CMD} "${RUNNER_PID}" --model "${OLLAMA_MODEL}" || rc=$?
    log_record "host=term pid=${RUNNER_PID} rc=${rc}"
  fi
  if gen_probe "${VERIFY_TIMEOUT_SEC}"; then
    log_record "host=verified model=${OLLAMA_MODEL} probe=generate"
  else
    log_record "host=verify-failed model=${OLLAMA_MODEL} probe=generate (operator attention)"
  fi
}

# ---------------------------------------------------------------------------
# 6. Sanctioned recovery only
# ---------------------------------------------------------------------------
log_record "probe=WEDGE container=${CONTAINER} boot=${boot_at} action=host-unwedge+restart-lightrag"
host_remediate || log_record "host=probe-error (host stage failed open; container recovery proceeds)"
# The LaunchDaemon already runs as nfmdeploy (see the plist UserName); sudo
# to the same identity is only needed when an operator runs the probe by
# hand from another account. The prefix is env-overridable so tests can
# exercise the recovery route without sudo.
invoke_recovery() {
  if [ "$(id -un)" = "nfmdeploy" ]; then
    bash "${RECOVERY}" "$@"
  else
    ${NFM_LIGHTRAG_WATCHDOG_SUDO-sudo -n -u nfmdeploy} bash "${RECOVERY}" "$@"
  fi
}
rc=0
invoke_recovery restart lightrag || rc=$?
printf 'last_restart=%s\n' "$(now_epoch)" >"${STATE}"
log_record "action=restart-lightrag rc=${rc}"
# NFM-4816: consumer revived → re-enqueue the FAILED/PENDING docs the dead
# consumer stranded, now instead of at the next daily audit. Only after a
# successful restart: reprocessing into a still-dead consumer would strand
# the docs again. A failing reprocess does not change the disposition (the
# wedge was addressed; the daily audit remains the fallback path).
if [ "${rc}" -eq 0 ]; then
  rrc=0
  invoke_recovery lightrag-reprocess || rrc=$?
  log_record "action=lightrag-reprocess rc=${rrc}"
else
  log_record "action=lightrag-reprocess skipped reason=restart-failed rc=${rc}"
fi
exit 2
