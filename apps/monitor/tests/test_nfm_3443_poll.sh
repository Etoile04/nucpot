#!/usr/bin/env bash
# Test harness for apps/monitor/nfm-3443-poll.sh
# Exercises the script in a sandbox:
#     * overrides STATE_DIR, JWT_FILE, SSH_KEY via env vars
#     * puts a fake `ssh` and `curl` on PATH that record interactions
#     * verifies the comment body matches the NFM-3460 spec exactly
#
# Run:  bash apps/monitor/tests/test_nfm_3443_poll.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
POLL_SH="${SCRIPT_DIR}/../nfm-3443-poll.sh"
[[ -f "${POLL_SH}" ]] || { echo "FAIL: missing ${POLL_SH}" >&2; exit 1; }

TMP="$(mktemp -d)"
trap 'rm -rf "${TMP}"' EXIT

# Sandbox state dir + jwt file
export NFM_STATE_DIR="${TMP}/state"
export NFM_JWT_FILE="${TMP}/jwt"
export NFM_SSH_KEY="${TMP}/ssh_key"  # not used; fake ssh bypasses
mkdir -p "$(dirname "${NFM_JWT_FILE}")"
echo "fake-jwt-for-test" > "${NFM_JWT_FILE}"
chmod 0400 "${NFM_JWT_FILE}"

# Fake bin dir on PATH: ssh + curl interceptors
FAKE_BIN="${TMP}/bin"
mkdir -p "${FAKE_BIN}"

# Fake curl: writes -d payload to ${NFM_CURL_DUMP}, prints 201
cat >"${FAKE_BIN}/curl" <<'EOF'
#!/usr/bin/env bash
dump="${NFM_CURL_DUMP:-/dev/null}"
prev=""
for a in "$@"; do
  if [[ "${prev}" == "-d" ]]; then
    printf '%s\n' "${a}" > "${dump}"
  fi
  prev="${a}"
done
echo "201"
EOF
chmod +x "${FAKE_BIN}/curl"

# Fake ssh: returns canned output per command substring
cat >"${FAKE_BIN}/ssh" <<'EOF'
#!/usr/bin/env bash
log="${NFM_SSH_LOG:-/dev/null}"
printf 'CMD %s\n' "$*" >> "${log}"
cmd="$*"
case "${cmd}" in
  *"squeue"*"--state=RUNNING"*) printf '7000001 03:00:00\n' ; exit 0 ;;
  *"squeue"*)                    printf '7000001 02:00:00\n7000002 02:30:00\n' ; exit 0 ;;
  *"find"*)                      printf '42\n' ; exit 0 ;;
  *"sacct"*)                     printf '3\n' ; exit 0 ;;
  *"wc -c"*)                     printf '6000\n' ; exit 0 ;;
  *"tail"*)                      printf 'INFO starting\nERROR controller out of sync\nINFO done\n' ; exit 0 ;;
  *)                              exit 0 ;;
esac
EOF
chmod +x "${FAKE_BIN}/ssh"

export PATH="${FAKE_BIN}:${PATH}"
export PAPERCLIP_API_URL="http://paperclip.test/api"
export NFM_3443_ID="29e3107d-5283-409c-8e7f-f762a218e2e5"
export NFM_CURL_DUMP="${TMP}/posted.json"
export NFM_SSH_LOG="${TMP}/ssh.log"

pass() { printf '\033[32m✓\033[0m %s\n' "$*"; }
fail() { printf '\033[31m✗\033[0m %s\n' "$*" >&2; exit 1; }

# --- Test 1: bash -n ---
bash -n "${POLL_SH}" && pass "bash -n clean" || fail "bash -n failed"

# --- Test 2: first-run state file content ---
TEST_STATE="${TMP}/state_first_run"
export NFM_STATE_DIR="${TEST_STATE}"
rm -rf "${TEST_STATE}"
"${POLL_SH}" >/dev/null 2>&1 || true
state="${TEST_STATE}/state.json"
[[ -f "${state}" ]] || fail "first-run state file not created"
today="$(date -u +%Y-%m-%d)"
expected="{\"queue_depth\":2,\"converged\":42,\"failed\":3,\"job_start_date\":\"${today}\",\"last_log_pos\":6000}"
actual="$(cat "${state}")"
[[ "${actual}" == "${expected}" ]] && pass "first-run state file content matches" \
  || { printf 'expected: %s\nactual:   %s\n' "${expected}" "${actual}" >&2; fail "first-run state file content mismatch"; }

# --- Test 3: silent exit 0 when no material change ---
cat > "${TEST_STATE}/state.json" <<EOF
{"queue_depth":2,"converged":42,"failed":3,"job_start_date":"${today}","last_log_pos":6000}
EOF
rm -f "${NFM_CURL_DUMP}"
set +e
"${POLL_SH}" >"${TMP}/stdout" 2>"${TMP}/stderr"
code=$?
set -e
[[ -f "${NFM_CURL_DUMP}" ]] && fail "no-change run unexpectedly posted a comment"
[[ "${code}" -eq 0 ]] && pass "no-change exit 0" || fail "no-change exit code ${code}"
grep -qi "no material change" "${TMP}/stderr" && pass "no-change logs to stderr" || fail "no-change log missing"

# --- Test 4: synthetic material change → exact comment body ---
export NFM_FAILURE_THRESHOLD_PCT=0
cat > "${TEST_STATE}/state.json" <<EOF
{"queue_depth":2,"converged":42,"failed":3,"job_start_date":"${today}","last_log_pos":6000}
EOF
rm -f "${NFM_CURL_DUMP}"
"${POLL_SH}" >"${TMP}/stdout4" 2>"${TMP}/stderr4" || true
[[ -f "${NFM_CURL_DUMP}" ]] || fail "synthetic change did not post a comment"

python3 - "${NFM_CURL_DUMP}" <<'PYEOF' >"${TMP}/body.txt"
import json, sys
with open(sys.argv[1]) as f:
    obj = json.load(f)
sys.stdout.write(obj["body"])
PYEOF

body="$(cat "${TMP}/body.txt")"
echo "--- captured body ---"; printf '%s\n' "${body}"; echo "--------------------"

grep -q "^\[external-poll\] material change detected at " <<<"${body}" || fail "body missing '[external-poll]' header"
grep -q "^- queue_depth: 2 -> 2$" <<<"${body}" || fail "queue_depth line wrong"
grep -q "^- converged: 42 -> 42 (delta +0)$" <<<"${body}" || fail "converged line wrong"
grep -q "^- failed: 3 -> 3$" <<<"${body}" || fail "failed line wrong"
grep -q "^- stuck_running: \[" <<<"${body}" || fail "stuck_running line wrong"
grep -q "^- triggered_by: " <<<"${body}" || fail "triggered_by line wrong"
grep -q "^Follow NFM-2783 disposition rules\.$" <<<"${body}" || fail "footer line wrong"
pass "synthetic change → exact spec body"

# --- Test 5: queue_drained detection ---
cat >"${FAKE_BIN}/ssh" <<'EOF'
#!/usr/bin/env bash
log="${NFM_SSH_LOG:-/dev/null}"
printf 'CMD %s\n' "$*" >> "${log}"
cmd="$*"
case "${cmd}" in
  *"squeue"*"--state=RUNNING"*) exit 0 ;;
  *"squeue"*) exit 0 ;;  # empty: queue drained
  *"find"*)  printf '42\n' ; exit 0 ;;
  *"sacct"*) printf '3\n' ; exit 0 ;;
  *"wc -c"*) printf '6000\n' ; exit 0 ;;
  *"tail"*)  printf 'INFO ok\n' ; exit 0 ;;
esac
EOF
chmod +x "${FAKE_BIN}/ssh"
unset NFM_FAILURE_THRESHOLD_PCT
cat > "${TEST_STATE}/state.json" <<EOF
{"queue_depth":5,"converged":42,"failed":3,"job_start_date":"${today}","last_log_pos":6000}
EOF
rm -f "${NFM_CURL_DUMP}"
"${POLL_SH}" >/dev/null 2>&1 || true
[[ -f "${NFM_CURL_DUMP}" ]] || fail "queue_drained did not post comment"
python3 - "${NFM_CURL_DUMP}" <<'PYEOF' >"${TMP}/body5.txt"
import json, sys
with open(sys.argv[1]) as f:
    obj = json.load(f)
sys.stdout.write(obj["body"])
PYEOF
grep -q "^- triggered_by: queue_drained$" "${TMP}/body5.txt" || fail "expected queue_drained trigger"
grep -q "^- queue_depth: 5 -> 0$" "${TMP}/body5.txt" || fail "queue_drained body queue_depth wrong"
pass "queue_drained → triggered_by queue_drained"

# --- Test 6: error_spike detection (logs grew + contain ERROR) ---
# Need queue_depth > 0 so queue_drained doesn't short-circuit.
cat >"${FAKE_BIN}/ssh" <<'EOF'
#!/usr/bin/env bash
log="${NFM_SSH_LOG:-/dev/null}"
printf 'CMD %s\n' "$*" >> "${log}"
cmd="$*"
case "${cmd}" in
  *"squeue"*"--state=RUNNING"*) printf '7000001 02:00:00\n' ; exit 0 ;;
  *"squeue"*)                    printf '7000001 02:00:00\n' ; exit 0 ;;
  *"find"*)                      printf '42\n' ; exit 0 ;;
  *"sacct"*)                     printf '3\n' ; exit 0 ;;
  *"wc -c"*)                     printf '18999\n' ; exit 0 ;;  # grew
  *"tail"*)                      printf 'INFO start\nERROR controller crash\nFATAL lost sync\n' ; exit 0 ;;
esac
EOF
chmod +x "${FAKE_BIN}/ssh"
cat > "${TEST_STATE}/state.json" <<EOF
{"queue_depth":2,"converged":42,"failed":3,"job_start_date":"${today}","last_log_pos":6000}
EOF
rm -f "${NFM_CURL_DUMP}"
"${POLL_SH}" >/dev/null 2>&1 || true
[[ -f "${NFM_CURL_DUMP}" ]] || fail "error_spike did not post comment"
python3 - "${NFM_CURL_DUMP}" <<'PYEOF' >"${TMP}/body6.txt"
import json, sys
with open(sys.argv[1]) as f:
    obj = json.load(f)
sys.stdout.write(obj["body"])
PYEOF
grep -q "^- triggered_by: error_spike$" "${TMP}/body6.txt" || fail "expected error_spike trigger"
pass "error_spike → triggered_by error_spike"

printf "\nAll tests passed.\n"