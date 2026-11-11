#!/usr/bin/env bash
# install-replica.sh — deploy the NFM HA tunnel replica stack to z203.
#
# NFM-5965 (D2-G2) artifact. Runs ON THE MAC (the executor vantage that holds
# both tunnel plists and SSH access to z203); every z203 mutation goes through
# SSH. Nothing here touches the Mac primary connector, its plist, or its
# generator — Mac-side work lives in macos-set-main-keepalive-true.sh.
#
# Modes:
#   preflight (default)  READ-ONLY. Fails closed on any blocked condition.
#   soak                 Install + enable VERIFY replica + forwarder only
#                        (shadow soak per D2-G4; MAIN unit installed but
#                        left disabled and NOT started).
#   cutover              Window step: stop/disable VERIFY replica, enable +
#                        start MAIN replica. (Forwarder already up from soak.)
#   verify               Post-install health checks only.
#   uninstall            Remove the replica stack from z203. Never touches
#                        the operator's own containers or services.
#
# State-changing modes require --yes (this script never prompts interactively;
# it aborts instead).
#
# Tokens never appear in argv, stdout, or logs: they are extracted from the
# Mac plists with PlistBuddy and piped straight into `sudo tee` over SSH.
#
# Ground truth encoded here (2026-11-11/12, CTO + CPO probes):
#   - z203 = z203@z203-thinkstation-p3-tower, Ubuntu 22.04, systemd 249,
#     tailscale 100.70.30.21, Mac origins at 100.65.21.27.
#   - BLOCKER DISCOVERED: operator containers own z203 :3000
#     (nucpot-prod-linux-web) and :8002 (nucpot-autovc-api-1), and a legacy
#     locally-managed cloudflared.service with dead credentials
#     ("Unauthorized: Tunnel not found") crash-loops on the box. Preflight
#     treats all three as BLOCKED conditions: freeing the ports and disabling
#     the zombie are operator/consent actions, never automated here.

set -euo pipefail

Z203_TARGET="${Z203_TARGET:-z203@z203-thinkstation-p3-tower}"
MAIN_PLIST="/Library/LaunchDaemons/com.cloudflare.cloudflared-nucpot.plist"
VERIFY_PLIST="/Library/LaunchDaemons/com.cloudflare.cloudflared-verify.plist"
CLOUDFLARED_BIN="/usr/local/bin/cloudflared"
CADDY_BIN="/usr/local/bin/caddy"
CADDY_VERSION_DEFAULT="v2.8.4"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

MODE="preflight"
YES=0
STAGED_CLOUDFLARED=""
STAGED_CADDY=""

usage() { sed -n '2,35p' "${BASH_SOURCE[0]}"; exit "${1:-0}"; }

while [ $# -gt 0 ]; do
    case "$1" in
        --mode) MODE="$2"; shift 2 ;;
        --yes) YES=1; shift ;;
        --staged-cloudflared) STAGED_CLOUDFLARED="$2"; shift 2 ;;
        --staged-caddy) STAGED_CADDY="$2"; shift 2 ;;
        -h|--help) usage 0 ;;
        *) echo "unknown arg: $1" >&2; usage 2 ;;
    esac
done

log()  { printf '[install-replica] %s\n' "$*"; }
die()  { printf '[install-replica] BLOCKED: %s\n' "$*" >&2; exit 1; }

require_yes() {
    [ "$YES" -eq 1 ] || die "mode '$MODE' mutates z203; re-run with --yes after reviewing preflight output"
}

# ---------- token extraction (Mac side; never printed) ----------

token_from_plist() {
    # $1 = plist path; prints the token on stdout. Caller MUST consume it
    # without echoing (pipe into ssh/sudo tee).
    python3 - "$1" <<'PYEOF'
import plistlib, sys
with open(sys.argv[1], "rb") as fh:
    args = plistlib.load(fh).get("ProgramArguments", [])
if "--token" not in args:
    sys.exit("no --token element in plist ProgramArguments")
tok = args[args.index("--token") + 1]
if not tok.startswith("eyJ") or len(tok) < 100:
    sys.exit("token element failed sanity check; plist layout may have drifted")
print(tok)
PYEOF
}

push_token_env() {
    # $1 = plist path, $2 = remote env file path
    local token
    token="$(token_from_plist "$1")"
    printf 'TUNNEL_TOKEN=%s\n' "$token" | ssh "${SSH_OPTS[@]}" "$Z203_TARGET" \
        "sudo tee '$2' >/dev/null && sudo chmod 0600 '$2' && sudo chown root:root '$2'"
}

# ---------- remote helpers ----------
# The route to z203 flaps through the local proxy (CN line); every call gets
# a connect timeout, and the connectivity gate retries before declaring the
# host unreachable.

SSH_OPTS=(-o BatchMode=yes -o ConnectTimeout=10)

r() { ssh "${SSH_OPTS[@]}" "$Z203_TARGET" "$@"; }

remote_bash() {
    ssh "${SSH_OPTS[@]}" "$Z203_TARGET" "bash -s" < <(printf '%s\n' "$1")
}

ssh_ready() {
    local _attempt
    for _attempt in 1 2 3; do
        if r true 2>/dev/null; then return 0; fi
        sleep 2
    done
    return 1
}

# ---------- preflight (read-only) ----------

preflight() {
    local failures=0
    log "=== PREFLIGHT (read-only) target=$Z203_TARGET ==="

    log "[1/9] SSH reachable"
    ssh_ready || { die "ssh $Z203_TARGET unreachable after 3 attempts"; }

    log "[2/9] z203 :3000 / :8002 free (fail-closed port gate)"
    local listeners
    listeners="$(remote_bash 'ss -tln | awk "(\$4 ~ /:3000\$/ || \$4 ~ /:8002\$/)"')"
    if [ -n "$listeners" ]; then
        failures=$((failures + 1))
        log "  FAIL - ports occupied (operator containers as of 2026-11-11):"
        printf '%s\n' "$listeners" | sed 's/^/    /'
        log "  -> consent ask: remap nucpot-prod-linux-web (3000) and"
        log "     nucpot-autovc-api-1 (8002) host port mappings, or CTO"
        log "     re-adjudicates the forwarder design (netns isolation)."
    else
        log "  OK - both free"
    fi

    log "[3/9] no legacy cloudflared.service (zombie with dead creds)"
    local zombie
    zombie="$(remote_bash 'systemctl is-active cloudflared.service 2>/dev/null || echo inactive')"
    if [ "$zombie" = "active" ]; then
        failures=$((failures + 1))
        log "  FAIL - legacy cloudflared.service still active (register-failing"
        log "         'Tunnel not found'); operator must: sudo systemctl disable --now cloudflared"
    else
        log "  OK - absent or inactive ($zombie)"
    fi

    log "[4/9] tailscale up on z203"
    if remote_bash 'tailscale ip -4 2>/dev/null | grep -q .'; then
        log "  OK - $(r 'tailscale ip -4 2>/dev/null | head -1')"
    else
        failures=$((failures + 1)); log "  FAIL - tailscale not answering"
    fi

    log "[5/9] Mac origins reachable from z203 over tailscale"
    if remote_bash 'curl -s -m 8 -o /dev/null -w "%{http_code}" http://100.65.21.27:3000/api/v1/health | grep -q "^200$"'; then
        log "  OK - :3000 answers 200"
    else
        failures=$((failures + 1)); log "  FAIL - 100.65.21.27:3000 not 200 from z203"
    fi
    if remote_bash 'curl -s -m 8 -o /dev/null http://100.65.21.27:8002/'; then
        log "  OK - :8002 answers (404-on-probe expected)"
    else
        failures=$((failures + 1)); log "  FAIL - 100.65.21.27:8002 not reachable from z203"
    fi

    log "[6/9] systemd >= 240 (append: log driver + DynamicUser)"
    if remote_bash 'test "$(systemctl --version | head -1 | cut -d" " -f2)" -ge 240'; then
        log "  OK - $(r 'systemctl --version | head -1')"
    else
        failures=$((failures + 1)); log "  FAIL - systemd too old for append:/DynamicUser semantics"
    fi

    log "[7/9] binaries: cloudflared + caddy present, staged, or locally recoverable"
    if remote_bash "test -x $CLOUDFLARED_BIN"; then
        log "  OK - $CLOUDFLARED_BIN present ($(r "$CLOUDFLARED_BIN --version 2>/dev/null | head -1" || echo '?'))"
    elif [ -n "$STAGED_CLOUDFLARED" ] && [ -f "$STAGED_CLOUDFLARED" ]; then
        log "  OK - staged cloudflared provided: $STAGED_CLOUDFLARED"
    elif remote_bash 'test -x /home/z203/bin/cloudflared'; then
        log "  OK - recoverable from z203: /home/z203/bin/cloudflared ($(remote_bash '/home/z203/bin/cloudflared --version 2>/dev/null | head -1' || echo '?'))"
    else
        failures=$((failures + 1)); log "  FAIL - no cloudflared source (binary, staged path, or z203 copy)"
    fi
    if remote_bash "test -x $CADDY_BIN"; then
        log "  OK - $CADDY_BIN present"
    elif [ -n "$STAGED_CADDY" ] && [ -f "$STAGED_CADDY" ]; then
        log "  OK - staged caddy provided: $STAGED_CADDY"
    else
        log "  WARN - no caddy yet; install will download (CN line may need the staged path)"
    fi

    log "[8/9] sudo available non-interactively on z203"
    if remote_bash 'sudo -n true 2>/dev/null'; then
        log "  OK - NOPASSWD sudo"
    else
        failures=$((failures + 1))
        log "  FAIL - sudo needs a password (agent runs are non-interactive)."
        log "  -> consent ask: one operator sudo session for the install"
        log "     commands, or a scoped NOPASSWD grant for the window."
    fi

    log "[9/9] tokens extractable from BOTH Mac plists (not printed)"
    if token_from_plist "$MAIN_PLIST" >/dev/null 2>&1; then
        log "  OK - MAIN plist token readable"
    else
        failures=$((failures + 1)); log "  FAIL - MAIN plist token extraction failed ($MAIN_PLIST)"
    fi
    if token_from_plist "$VERIFY_PLIST" >/dev/null 2>&1; then
        log "  OK - VERIFY plist token readable"
    else
        failures=$((failures + 1)); log "  FAIL - VERIFY plist token extraction failed ($VERIFY_PLIST)"
    fi

    if [ "$failures" -gt 0 ]; then
        die "preflight failed with $failures blocked condition(s) — see lines above; resolve (operator/consent) and re-run. NOTHING was changed."
    fi
    log "=== PREFLIGHT PASS — safe to proceed to a state-changing mode with --yes ==="
}

# ---------- install / soak / cutover ----------

stage_binaries() {
    # F1b (NFM-5965 CR): two INDEPENDENT blocks — a pre-existing cloudflared
    # must not short-circuit caddy staging (early return skipped the caddy
    # checks; forwarder ExecStartPre would then fail on a missing binary).
    if ! remote_bash "test -x $CLOUDFLARED_BIN"; then
        if [ -n "$STAGED_CLOUDFLARED" ]; then
            log "staging cloudflared from $STAGED_CLOUDFLARED"
            scp -o BatchMode=yes -o ConnectTimeout=10 "$STAGED_CLOUDFLARED" "$Z203_TARGET:/tmp/nfm-cloudflared"
            r "sudo install -m 0755 /tmp/nfm-cloudflared $CLOUDFLARED_BIN && rm -f /tmp/nfm-cloudflared"
        elif remote_bash 'test -x /home/z203/bin/cloudflared'; then
            log "staging cloudflared from existing /home/z203/bin/cloudflared"
            r "sudo install -m 0755 /home/z203/bin/cloudflared $CLOUDFLARED_BIN"
        else
            die "no cloudflared source; supply --staged-cloudflared"
        fi
    fi
    if ! remote_bash "test -x $CADDY_BIN"; then
        if [ -n "$STAGED_CADDY" ]; then
            log "staging caddy from $STAGED_CADDY"
            scp -o BatchMode=yes -o ConnectTimeout=10 "$STAGED_CADDY" "$Z203_TARGET:/tmp/nfm-caddy"
            r "sudo install -m 0755 /tmp/nfm-caddy $CADDY_BIN && rm -f /tmp/nfm-caddy"
        else
            # F2: filename derived from CADDY_VERSION_DEFAULT (was hardcoded
            # twice) + sha256-verify the tarball against the release .sha256.
            local cver="${CADDY_VERSION_DEFAULT#v}"
            log "downloading caddy $CADDY_VERSION_DEFAULT on z203 (sha256-verified)"
            r "curl -fsSL -o /tmp/nfm-caddy.tar.gz 'https://github.com/caddyserver/caddy/releases/download/${CADDY_VERSION_DEFAULT}/caddy_${cver}_linux_amd64.tar.gz' \
               && curl -fsSL -o /tmp/nfm-caddy.sha256 'https://github.com/caddyserver/caddy/releases/download/${CADDY_VERSION_DEFAULT}/caddy_${cver}_linux_amd64.tar.gz.sha256' \
               && echo \"\$(cat /tmp/nfm-caddy.sha256)  /tmp/nfm-caddy.tar.gz\" | sha256sum -c - >/dev/null \
               && tar -xzf /tmp/nfm-caddy.tar.gz -C /tmp caddy \
               && sudo install -m 0755 /tmp/caddy $CADDY_BIN \
               && rm -f /tmp/nfm-caddy.tar.gz /tmp/nfm-caddy.sha256 /tmp/caddy" \
            || die "caddy download/verify failed (CN line); supply --staged-caddy with a Mac-side binary"
        fi
    fi
    log "caddy: $(r "$CADDY_BIN version 2>/dev/null | head -1")"
}

copy_unit_files() {
    log "staging unit files + Caddyfile to z203"
    ssh "${SSH_OPTS[@]}" "$Z203_TARGET" "mkdir -p /tmp/nfm-replica-units"
    scp -o BatchMode=yes -o ConnectTimeout=10 "$SCRIPT_DIR"/units/*.service "$SCRIPT_DIR/Caddyfile.nfm-origin-forwarder" \
        "$Z203_TARGET:/tmp/nfm-replica-units/"
    remote_bash '
        set -e
        sudo install -d -m 0755 /etc/nfm/replica /etc/nfm/origin-forwarder /opt/nfm/z203-replica/units
        sudo install -m 0644 /tmp/nfm-replica-units/*.service /opt/nfm/z203-replica/units/
        sudo install -m 0644 /opt/nfm/z203-replica/units/*.service /etc/systemd/system/
        sudo install -m 0644 /tmp/nfm-replica-units/Caddyfile.nfm-origin-forwarder /etc/nfm/origin-forwarder/Caddyfile
        sudo tee /etc/logrotate.d/nfm-replica >/dev/null <<LOGEOF
/var/log/cloudflared-nucpot-replica.log /var/log/cloudflared-verify-replica.log /var/log/nfm-origin-forwarder/access.log {
    weekly
    maxsize 20M
    rotate 3
    copytruncate
    compress
    missingok
    notifempty
}
LOGEOF
        sudo systemctl daemon-reload
        rm -rf /tmp/nfm-replica-units
    '
    log "units installed: cloudflared-nucpot-replica / cloudflared-verify-replica / nfm-origin-forwarder + logrotate"
}

write_env_files() {
    log "writing token env files from Mac plists (0600 root; never echoed)"
    push_token_env "$MAIN_PLIST"   /etc/nfm/replica/nucpot-replica.env
    push_token_env "$VERIFY_PLIST" /etc/nfm/replica/verify-replica.env
}

check_unit_state() {
    # $1 unit, $2 expected is-active result
    local unit="$1" expected="$2" actual
    actual="$(r "systemctl is-active $unit 2>/dev/null || echo inactive")"
    if [ "$actual" = "$expected" ]; then
        log "  OK - $unit is $actual"
    else
        die "$unit is '$actual', expected '$expected'"
    fi
}

post_start_checks() {
    log "connector + forwarder health (on z203)"
    r 'sleep 20'
    local main_log="/var/log/cloudflared-nucpot-replica.log"
    local verify_log="/var/log/cloudflared-verify-replica.log"
    if r "grep -c 'Registered tunnel connection' $main_log 2>/dev/null || true" | grep -qv '^0$'; then
        log "  OK - MAIN replica registered with the edge"
    else
        log "  WARN - no MAIN registration line yet in $main_log"
    fi
    if r "grep -c 'Registered tunnel connection' $verify_log 2>/dev/null || true" | grep -qv '^0$'; then
        log "  OK - VERIFY replica registered with the edge"
    else
        log "  WARN - no VERIFY registration line yet in $verify_log"
    fi
    if r 'curl -s -m 8 -o /dev/null -w "%{http_code}" http://localhost:3000/api/v1/health | grep -q "^200$"'; then
        log "  OK - forwarder localhost:3000 -> Mac origin round-trip 200"
    else
        die "forwarder :3000 round-trip failed after start (non-200 or unreachable)"
    fi
    if r 'curl -s -m 8 -o /dev/null -w "%{http_code}" http://localhost:8002/ | grep -q 404'; then
        log "  OK - forwarder :8002 round-trip answered (404-on-probe expected)"
    else
        die "forwarder :8002 round-trip failed after start"
    fi
}

do_soak() {
    require_yes
    stage_binaries
    copy_unit_files
    write_env_files
    log "SOAK: enabling forwarder + VERIFY replica only (MAIN stays disabled)"
    r 'sudo systemctl enable --now nfm-origin-forwarder.service'
    r 'sudo systemctl enable --now cloudflared-verify-replica.service'
    r 'sudo systemctl disable cloudflared-nucpot-replica.service >/dev/null 2>&1 || true'
    check_unit_state nfm-origin-forwarder active
    check_unit_state cloudflared-verify-replica active
    check_unit_state cloudflared-nucpot-replica inactive
    post_start_checks
    log "=== SOAK MODE DEPLOYED — verify pool now has 2 connectors; MAIN pool untouched ==="
}

do_cutover() {
    require_yes
    stage_binaries
    copy_unit_files
    write_env_files
    log "CUTOVER: switching replica from VERIFY to MAIN tunnel"
    r 'sudo systemctl disable --now cloudflared-verify-replica.service'
    r 'sudo systemctl enable --now cloudflared-nucpot-replica.service'
    check_unit_state cloudflared-verify-replica inactive
    check_unit_state cloudflared-nucpot-replica active
    post_start_checks
    log "=== CUTOVER DEPLOYED — edge pool for 04b1e559 now expects 2 connectors ==="
    log "window executor: confirm 2-connector edge state + origin/cached lanes green (runbook §14)"
}

do_uninstall() {
    require_yes
    log "UNINSTALL: removing the replica stack (operator containers are NEVER touched)"
    r 'sudo systemctl disable --now cloudflared-nucpot-replica.service 2>/dev/null || true'
    r 'sudo systemctl disable --now cloudflared-verify-replica.service 2>/dev/null || true'
    r 'sudo systemctl disable --now nfm-origin-forwarder.service 2>/dev/null || true'
    r 'sudo rm -f /etc/systemd/system/cloudflared-nucpot-replica.service \
                 /etc/systemd/system/cloudflared-verify-replica.service \
                 /etc/systemd/system/nfm-origin-forwarder.service \
                 /etc/logrotate.d/nfm-replica'
    r 'sudo rm -rf /etc/nfm/replica /etc/nfm/origin-forwarder /opt/nfm/z203-replica'
    if [ "$YES" = "1" ] && [ "${REMOVE_BINARIES:-0}" = "1" ]; then
        r "sudo rm -f $CLOUDFLARED_BIN $CADDY_BIN"
    else
        log "binaries left in place (set REMOVE_BINARIES=1 to remove $CLOUDFLARED_BIN $CADDY_BIN)"
    fi
    r 'sudo systemctl daemon-reload'
    log "=== UNINSTALLED — z203 returned to pre-replica state (minus binaries, by default) ==="
}

case "$MODE" in
    preflight) preflight ;;
    soak)      do_soak ;;
    cutover)   do_cutover ;;
    verify)    check_unit_state nfm-origin-forwarder active
               check_unit_state cloudflared-verify-replica active
               post_start_checks ;;
    uninstall) do_uninstall ;;
    *) usage 2 ;;
esac
