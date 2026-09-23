#!/bin/bash
# ============================================================================
# NFM-5149 — host-entry re-propagation for /usr/local/lib/nfm-g2.
#
# Repo-side fixes to host-tracked entries (scripts/host-prod-gate/entries/*.sh)
# ship through the deploy path DOCKER-ONLY: the deploy identity (nfmdeploy)
# has no root path (G2 wall, ADR-013) and cannot write the root-owned copies
# under /usr/local/lib/nfm-g2/. Until NFM-5149 nothing in the release cycle
# propagated them — every host-tracked fix lived or died on a manual
# multi-step operator catch-up (NFM-5094, then NFM-5134: the NFM-5122 base-10
# etime fix sat un-applied for 2 consecutive deploy cycles while the watchdog
# kept exec'ing the pre-fix host blob).
#
# Modes:
#   --check     Unprivileged drift probe (agents, CI, canaries): sha256 every
#               entries/*.sh against the installed copy. Greppable output:
#                 ENTRY-IN-SYNC    <name> sha=<sha256>
#                 HOST-ENTRY-DRIFT <name> repo_sha=<sha> host_sha=<sha|missing>
#               Exit 0 in sync; 1 drift; 2 operational (no gate installed /
#               unreadable — never an alarm condition).
#   --apply     ROOT ONLY (operator's interactive sudo — the ONE operator
#               action per release, NFM-5149 AC1). Backs up and installs
#               drifted entries only. Refuses unless every byte it installs
#               is the committed blob of a sha reachable from origin/main
#               (NFM-4297 CR F7 SHA binding):
#                 * repo HEAD must be an ancestor of origin/main, and
#                 * each entry's working-tree sha must equal its HEAD blob
#                   sha (no uncommitted bytes ride the apply).
#               Idempotent: in-sync ⇒ no-op. Audit JSONL at
#               /var/log/nfm-g2/entry-sync.log; backups under
#               /usr/local/lib/nfm-g2/backups/entry-sync/<UTC-ts>/.
#   --selftest  Hermetic: fake repo + fake G2 tree; exercises check / apply /
#               SHA-binding refusals end-to-end. Touches nothing real.
#
# G2 wall preserved: there is deliberately NO sudoers grant for this script.
# --apply requires the operator to type their own sudo password — that IS the
# per-release authorization boundary (NFM-5149 constraint). Agents can only
# ever run --check. No docker bind-mount circumvention, no LaunchDaemon
# auto-pull: a root daemon applying agent-staged bytes would move the
# authorization boundary and is out of scope for v1 (documented in
# docs/runbooks/prod-deploy.md §11 as a future option needing explicit
# sign-off).
#
# No restarts: host consumers of these entries are a fresh bash per fire
# (lightrag watchdog StartInterval 300) — file replacement takes effect on
# the next fire. entry-sync therefore never touches launchctl.
#
# NFM_HESYNC_* env vars are hermetic-test hooks only; sudo env_reset never
# passes them in production (same convention as NFM_G2_* in run-deploy.sh).
# ============================================================================
set -euo pipefail

G2="${NFM_HESYNC_G2:-/usr/local/lib/nfm-g2}"
LOG_FILE="${NFM_HESYNC_LOG:-/var/log/nfm-g2/entry-sync.log}"

log()  { printf '[nfm-g2 entry-sync] %s\n' "$*"; }
die()  { printf '[nfm-g2 entry-sync] FATAL: %s\n' "$*" >&2; exit 1; }

usage() { sed -n '2,45p' "$0" | sed 's/^# \{0,1\}//'; }

SELF_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "${SELF_DIR}/../.." && pwd)"
ENTRIES_DIR="${REPO}/scripts/host-prod-gate/entries"
[ -d "${ENTRIES_DIR}" ] || die "${ENTRIES_DIR} not found — run from a nucpot checkout"

# macOS ships shasum; CI (linux) ships sha256sum. Support both.
sha256_file() {
  if command -v shasum >/dev/null 2>&1; then shasum -a 256 "$1" | awk '{print $1}'
  else sha256sum "$1" | awk '{print $1}'; fi
}
sha256_stdin() {
  if command -v shasum >/dev/null 2>&1; then shasum -a 256 | awk '{print $1}'
  else sha256sum | awk '{print $1}'; fi
}

MODE="${1:-}"
case "${MODE}" in
  --check|--apply|--selftest|--help) ;;
  *) printf 'usage: entry-sync.sh --check|--apply|--selftest\n' >&2; exit 64 ;;
esac
if [ "${MODE}" = "--help" ]; then usage; exit 0; fi

# ============================================================================
# --check — unprivileged drift probe
# ============================================================================
if [ "${MODE}" = "--check" ]; then
  if [ ! -d "${G2}" ]; then
    printf 'ENTRY-SYNC-SKIP host gate dir not present: %s (host_setup.sh not run here)\n' "${G2}"
    exit 2
  fi
  DRIFT_COUNT=0
  for SRC in "${ENTRIES_DIR}"/*.sh; do
    NAME="$(basename "${SRC}")"
    REPO_SHA="$(sha256_file "${SRC}")" || die "cannot hash ${SRC}"
    if [ ! -r "${G2}/${NAME}" ]; then
      printf 'HOST-ENTRY-DRIFT %s repo_sha=%s host_sha=missing\n' "${NAME}" "${REPO_SHA}"
      DRIFT_COUNT=$((DRIFT_COUNT + 1))
    else
      HOST_SHA="$(sha256_file "${G2}/${NAME}")" || die "cannot hash ${G2}/${NAME}"
      if [ "${REPO_SHA}" = "${HOST_SHA}" ]; then
        printf 'ENTRY-IN-SYNC %s sha=%s\n' "${NAME}" "${REPO_SHA}"
      else
        printf 'HOST-ENTRY-DRIFT %s repo_sha=%s host_sha=%s\n' "${NAME}" "${REPO_SHA}" "${HOST_SHA}"
        DRIFT_COUNT=$((DRIFT_COUNT + 1))
      fi
    fi
  done
  if [ "${DRIFT_COUNT}" -eq 0 ]; then
    printf 'ENTRY-SYNC-OK all host entries in sync with repo\n'
    exit 0
  fi
  printf 'ENTRY-SYNC-DRIFT count=%s — propagate with: sudo bash scripts/host-prod-gate/entry-sync.sh --apply\n' "${DRIFT_COUNT}"
  exit 1
fi

# ============================================================================
# --selftest — hermetic end-to-end exercise against a fake repo + fake G2
# ============================================================================
if [ "${MODE}" = "--selftest" ]; then
  T="$(mktemp -d "${TMPDIR:-/tmp}/nfm-hesync-selftest.XXXXXXXX")"
  trap 'rm -rf "${T}"' EXIT
  PASS=0; FAIL=0
  ok()  { PASS=$((PASS + 1)); printf 'SELFTEST-PASS %s\n' "$1"; }
  bad() { FAIL=$((FAIL + 1)); printf 'SELFTEST-FAIL %s\n' "$1"; }

  # Fake repo; the COPY is the system under test (REPO resolves inside $T).
  FR="${T}/repo"; FE="${FR}/scripts/host-prod-gate/entries"
  mkdir -p "${FE}"
  cp "${SELF_DIR}/entry-sync.sh" "${FR}/scripts/host-prod-gate/entry-sync.sh"
  chmod 0755 "${FR}/scripts/host-prod-gate/entry-sync.sh"
  printf '#!/bin/bash\necho alpha-v1\n' > "${FE}/alpha.sh"
  printf '#!/bin/bash\necho beta-v1\n'  > "${FE}/beta.sh"
  GIT_ARGS=(-c user.email=hesync@selftest -c user.name=hesync)
  git -C "${FR}" init -q
  git -C "${FR}" "${GIT_ARGS[@]}" add -A
  git -C "${FR}" "${GIT_ARGS[@]}" commit -q -m "init entries"
  git -C "${FR}" update-ref refs/remotes/origin/main HEAD

  G2F="${T}/g2"; LOGF="${T}/entry-sync.log"
  mkdir -p "${G2F}"
  cp "${FE}/alpha.sh" "${G2F}/alpha.sh"            # in sync
  printf '#!/bin/bash\necho beta-OLD\n' > "${G2F}/beta.sh"   # drifted

  HOOKS=(env "NFM_HESYNC_G2=${G2F}" "NFM_HESYNC_LOG=${LOGF}" NFM_HESYNC_ALLOW_NONROOT=1)
  TOOL="${FR}/scripts/host-prod-gate/entry-sync.sh"

  # T1: --check flags exactly the drifted entry.
  set +e; OUT="$("${HOOKS[@]}" bash "${TOOL}" --check 2>&1)"; RC=$?; set -e
  if [ "${RC}" = 1 ] && printf '%s' "${OUT}" | grep -q 'HOST-ENTRY-DRIFT beta.sh' \
                     && printf '%s' "${OUT}" | grep -q 'ENTRY-IN-SYNC alpha.sh'; then
    ok "T1 check flags drift (rc=1)"
  else bad "T1 check flags drift (rc=${RC}: ${OUT})"; fi

  # T2: --apply installs the drifted entry, backs up, audits.
  set +e; OUT="$("${HOOKS[@]}" bash "${TOOL}" --apply 2>&1)"; RC=$?; set -e
  if [ "${RC}" = 0 ] && [ "$(sha256_file "${FE}/beta.sh")" = "$(sha256_file "${G2F}/beta.sh")" ] \
     && ls "${G2F}"/backups/entry-sync/*/beta.sh >/dev/null 2>&1 \
     && grep -q '"applied":"beta.sh"' "${LOGF}"; then
    ok "T2 apply installs + backs up + audits"
  else bad "T2 apply installs + backs up + audits (rc=${RC}: ${OUT})"; fi

  # T3: idempotent — second --check is green, second --apply is a no-op.
  set +e; OUT="$("${HOOKS[@]}" bash "${TOOL}" --check 2>&1)"; RC=$?; set -e
  if [ "${RC}" = 0 ]; then ok "T3 check green after apply"; else bad "T3 check green after apply (rc=${RC}: ${OUT})"; fi
  set +e; OUT="$("${HOOKS[@]}" bash "${TOOL}" --apply 2>&1)"; RC=$?; set -e
  if [ "${RC}" = 0 ] && printf '%s' "${OUT}" | grep -q 'nothing to do'; then
    ok "T3 apply idempotent no-op"
  else bad "T3 apply idempotent no-op (rc=${RC}: ${OUT})"; fi

  # T4: uncommitted working-tree edits are refused (SHA binding part 2).
  printf '\n# dirty local edit\n' >> "${FE}/alpha.sh"
  set +e; OUT="$("${HOOKS[@]}" bash "${TOOL}" --apply 2>&1)"; RC=$?; set -e
  if [ "${RC}" != 0 ] && printf '%s' "${OUT}" | grep -q 'uncommitted working-tree edits'; then
    ok "T4 dirty worktree refused"
  else bad "T4 dirty worktree refused (rc=${RC}: ${OUT})"; fi
  git -C "${FR}" checkout -- scripts/host-prod-gate/entries/alpha.sh

  # T5: a commit NOT reachable from origin/main is refused; reachable applies.
  printf '#!/bin/bash\necho beta-v2\n' > "${FE}/beta.sh"
  git -C "${FR}" "${GIT_ARGS[@]}" add -A
  git -C "${FR}" "${GIT_ARGS[@]}" commit -q -m "beta v2 (unsanctioned until main)"
  set +e; OUT="$("${HOOKS[@]}" bash "${TOOL}" --apply 2>&1)"; RC=$?; set -e
  if [ "${RC}" != 0 ] && printf '%s' "${OUT}" | grep -q 'not reachable from origin/main'; then
    ok "T5 unsanctioned HEAD refused"
  else bad "T5 unsanctioned HEAD refused (rc=${RC}: ${OUT})"; fi
  git -C "${FR}" update-ref refs/remotes/origin/main HEAD
  set +e; OUT="$("${HOOKS[@]}" bash "${TOOL}" --apply 2>&1)"; RC=$?; set -e
  if [ "${RC}" = 0 ] && [ "$(sha256_file "${FE}/beta.sh")" = "$(sha256_file "${G2F}/beta.sh")" ]; then
    ok "T5 sanctioned HEAD applies"
  else bad "T5 sanctioned HEAD applies (rc=${RC}: ${OUT})"; fi

  printf 'SELFTEST-RESULT pass=%s fail=%s\n' "${PASS}" "${FAIL}"
  [ "${FAIL}" -eq 0 ] || exit 1
  exit 0
fi

# ============================================================================
# --apply — root-only staged apply (the one operator action per release)
# ============================================================================
if [ "$(id -u)" -ne 0 ] && [ "${NFM_HESYNC_ALLOW_NONROOT:-0}" != "1" ]; then
  die "--apply requires root (the operator's per-release authorization, NFM-5149): sudo bash scripts/host-prod-gate/entry-sync.sh --apply"
fi
if [ ! -d "${G2}" ]; then
  die "host gate dir not present: ${G2} — run host_setup.sh first (this host has no G2 install to update)"
fi
# Pin Apple git: Homebrew git's libintl dylib is unreadable outside the
# desktop user (NFM-4357 trap, same pin as deploy_prod.sh).
GIT_BIN="${NFM_HESYNC_GIT_BIN:-/usr/bin/git}"
[ -x "${GIT_BIN}" ] || GIT_BIN="$(command -v git || true)"
[ -n "${GIT_BIN}" ] || die "git not found"

cd "${REPO}"
HEAD_SHA="$("${GIT_BIN}" rev-parse HEAD)" || die "not a git repo: ${REPO}"

# NFM-4297 CR F7 binding, entry-sync form: only committed, origin/main-
# reachable bytes may be installed root-owned. A stale local origin/main ref
# refuses rather than trusts (fetch as the repo owner and retry).
if ! "${GIT_BIN}" merge-base --is-ancestor HEAD origin/main >/dev/null 2>&1; then
  die "HEAD ${HEAD_SHA} is not reachable from origin/main — refusing unsanctioned bytes. Sync the checkout (git fetch origin main && git reset --hard origin/main) and retry."
fi

TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
BACKUP_DIR="${G2}/backups/entry-sync/${TS}"
APPLIED=""
CHECKED=0

for SRC in "${ENTRIES_DIR}"/*.sh; do
  NAME="$(basename "${SRC}")"
  REPO_SHA="$(sha256_file "${SRC}")" || die "cannot hash ${SRC}"
  CHECKED=$((CHECKED + 1))
  if [ -r "${G2}/${NAME}" ]; then
    HOST_SHA="$(sha256_file "${G2}/${NAME}")" || die "cannot hash ${G2}/${NAME}"
    if [ "${REPO_SHA}" = "${HOST_SHA}" ]; then
      continue
    fi
  fi
  # Binding part 2: the working-tree bytes must be the committed HEAD blob —
  # an uncommitted local edit must never ride a root-owned install.
  HEAD_BLOB_SHA="$("${GIT_BIN}" show "HEAD:scripts/host-prod-gate/entries/${NAME}" | sha256_stdin)" \
    || die "cannot read HEAD blob for ${NAME} (untracked entry — commit it first)"
  if [ "${REPO_SHA}" != "${HEAD_BLOB_SHA}" ]; then
    die "${NAME} has uncommitted working-tree edits — commit to main first; only reviewed bytes are applied"
  fi
  mkdir -p "${BACKUP_DIR}"
  if [ -e "${G2}/${NAME}" ]; then
    cp -p "${G2}/${NAME}" "${BACKUP_DIR}/${NAME}"
  fi
  if [ "$(id -u)" -eq 0 ]; then
    install -m 0755 -o root -g wheel "${SRC}" "${G2}/.${NAME}.hesync-new"
  else
    install -m 0755 "${SRC}" "${G2}/.${NAME}.hesync-new"
  fi
  mv -f "${G2}/.${NAME}.hesync-new" "${G2}/${NAME}"
  printf 'ENTRY-APPLIED %s sha=%s (backup: %s)\n' "${NAME}" "${REPO_SHA}" "${BACKUP_DIR}/${NAME}"
  APPLIED="${APPLIED}${APPLIED:+ }${NAME}"
done

# Audit row (JSONL; entry names are [a-z0-9._-]+ so no escaping needed).
ACTOR="entry-sync:$(id -un)"
if [ -n "${SUDO_USER:-}" ]; then ACTOR="entry-sync:${SUDO_USER}"; fi
OUTCOME="in_sync"
if [ -n "${APPLIED}" ]; then OUTCOME="applied"; fi
if printf '{"ts":"%s","actor":"%s","head":"%s","entries_checked":%s,"applied":"%s","outcome":"%s"}\n' \
     "${TS}" "${ACTOR}" "${HEAD_SHA}" "${CHECKED}" "${APPLIED}" "${OUTCOME}" >> "${LOG_FILE}" 2>/dev/null; then
  :
else
  printf '[nfm-g2 entry-sync] WARNING: could not append audit row to %s\n' "${LOG_FILE}" >&2
fi

if [ -z "${APPLIED}" ]; then
  log "all ${CHECKED} host entries already in sync with ${HEAD_SHA:0:12} — nothing to do"
else
  log "applied: ${APPLIED} (from ${HEAD_SHA:0:12}); host consumers pick the new bytes up on their next fire — no restart needed"
fi
exit 0
