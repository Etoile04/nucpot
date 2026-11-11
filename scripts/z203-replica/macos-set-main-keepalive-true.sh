#!/usr/bin/env bash
# macos-set-main-keepalive-true.sh — flip the Mac MAIN tunnel daemon to
# launchd KeepAlive=true (always restart), the only primary-side change of
# the D2 window (NFM-5968 step 1).
#
# NFM-5965 (D2-G2) artifact. RUNS ON THE MAC. Three coordinated edits:
#   1. Backup  : plist + generator copied to timestamped .bak files first.
#   2. Generator: ~/.cloudflared/tunnel-start.sh write_one_plist() gains a
#      keepalive mode parameter — MAIN emits <true/> (always), VERIFY keeps
#      the current oncrash dict (SuccessfulExit=false) so the non-scope
#      verify plist inherits NO behavior change on a future sync-token run.
#      This script NEVER runs sync_plist / pkill — the generator change only
#      takes effect on a deliberate future token-sync by the operator.
#   3. Live plist: /Library/LaunchDaemons/com.cloudflare.cloudflared-nucpot.plist
#      KeepAlive dict {SuccessfulExit: false} -> bool true, then
#      bootout + bootstrap reload, then serving-continuity checks.
#
# Semantics note: KeepAlive={SuccessfulExit: false} restarts the daemon only
# after a NON-zero exit (crash) — a clean exit (0) stays dead. KeepAlive=true
# restarts regardless: strictly more available for an always-on connector.
#
# Modes:
#   apply    Do the flip. Requires --replica-state expected-active|absent so
#            the window executor pins the expected continuity envelope
#            BEFORE anything is touched.
#   verify   Print current KeepAlive state (plist + generator), no changes.
#   rollback Restore newest plist + generator backups, reload, re-verify.

set -euo pipefail

PLIST="/Library/LaunchDaemons/com.cloudflare.cloudflared-nucpot.plist"
GENERATOR="$HOME/.cloudflared/tunnel-start.sh"
BACKUP_DIR="$HOME/nfm-backups/plist-history"
LABEL="com.cloudflare.cloudflared-nucpot"
PUBLIC_URL="https://nucpot.dpdns.org"
LOG_FILE="/Library/Logs/cloudflared-nucpot.log"

MODE="verify"
REPLICA_STATE=""
Z203_TARGET="${Z203_TARGET:-z203@z203-thinkstation-p3-tower}"

usage() { sed -n '2,33p' "${BASH_SOURCE[0]}"; exit "${1:-0}"; }
while [ $# -gt 0 ]; do
    case "$1" in
        apply|verify|rollback) MODE="$1"; shift ;;
        --replica-state) REPLICA_STATE="$2"; shift 2 ;;
        -h|--help) usage 0 ;;
        *) echo "unknown arg: $1" >&2; usage 2 ;;
    esac
done

TS="$(date -u +%Y%m%dT%H%M%SZ)"
PLIST_BAK="$BACKUP_DIR/${LABEL}.plist.bak.${TS}"
GENERATOR_BAK="${GENERATOR}.bak.${TS}"

log() { printf '[keepalive] %s\n' "$*"; }
die() { printf '[keepalive] FAILED: %s\n' "$*" >&2; exit 1; }

plist_keepalive_is_bool_true() {
    /usr/libexec/PlistBuddy -c 'Print :KeepAlive' "$PLIST" 2>/dev/null | grep -qx 'true'
}

# ---------- generator edit (exact-block replace, count==1 asserted) ----------

edit_generator() {
    python3 - "$GENERATOR" <<'PYEOF'
import sys

path = sys.argv[1]
src = open(path).read()

edits = [
    # 1. write_one_plist signature + keepalive_block selection
    (
        'write_one_plist() {\n'
        '    local plist_path="/Library/LaunchDaemons/${1}.plist"\n'
        '    local log_path="$2"\n'
        '    local token="$3"\n'
        '\n',
        'write_one_plist() {\n'
        '    local plist_path="/Library/LaunchDaemons/${1}.plist"\n'
        '    local log_path="$2"\n'
        '    local token="$3"\n'
        '    # keepalive mode: "always" (bool true) for the MAIN tunnel, legacy\n'
        '    # dict (restart-on-crash only) for everything else. D2/NFM-5968.\n'
        '    local keepalive_mode="${4:-oncrash}"\n'
        '    if [ "$keepalive_mode" = "always" ]; then\n'
        "        local keepalive_block=$'    <key>KeepAlive</key>\\n    <true/>'\n"
        '    else\n'
        "        local keepalive_block=$'    <key>KeepAlive</key>\\n"
        "    <dict>\\n"
        "        <key>SuccessfulExit</key>\\n"
        "        <false/>\\n"
        "    </dict>'\n"
        '    fi\n'
        '\n',
    ),
    # 2. heredoc KeepAlive dict block -> interpolated block variable
    (
        '    <key>KeepAlive</key>\n'
        '    <dict>\n'
        '        <key>SuccessfulExit</key>\n'
        '        <false/>\n'
        '    </dict>\n',
        '${keepalive_block}\n',
    ),
    # 3. sync_plist call sites: MAIN always, VERIFY unchanged (explicit)
    (
        '    write_one_plist "com.cloudflare.cloudflared-nucpot" "$NUCPOT_LOG" "$CLOUDFLARE_TUNNEL_TOKEN_NUCPOT"\n',
        '    write_one_plist "com.cloudflare.cloudflared-nucpot" "$NUCPOT_LOG" "$CLOUDFLARE_TUNNEL_TOKEN_NUCPOT" "always"\n',
    ),
    (
        '    write_one_plist "com.cloudflare.cloudflared-verify" "$VERIFY_LOG" "$CLOUDFLARE_TUNNEL_TOKEN_VERIFY"\n',
        '    write_one_plist "com.cloudflare.cloudflared-verify" "$VERIFY_LOG" "$CLOUDFLARE_TUNNEL_TOKEN_VERIFY" "oncrash"\n',
    ),
    # 4. manage_label missing-plist fallback: mode by tunnel name
    (
        '                write_one_plist "$label" "/Library/Logs/cloudflared-${name}.log" "$token"\n',
        '                local ka_mode="oncrash"\n'
        '                [ "$name" = "nucpot" ] && ka_mode="always"\n'
        '                write_one_plist "$label" "/Library/Logs/cloudflared-${name}.log" "$token" "$ka_mode"\n',
    ),
]

out = src
for old, new in edits:
    count = out.count(old)
    if count != 1:
        sys.exit("generator drifted: expected exactly 1 match, found %d for block starting %r"
                 % (count, old[:60]))
    out = out.replace(old, new)

open(path, "w").write(out)
print("generator edit applied: 5 blocks replaced")
PYEOF
    bash -n "$GENERATOR" || die "generator failed bash -n after edit — restore from $GENERATOR_BAK"
}

# ---------- continuity gates ----------

check_replica_envelope() {
    case "$REPLICA_STATE" in
        expected-active)
            ssh -o BatchMode=yes "$Z203_TARGET" 'systemctl is-active cloudflared-nucpot-replica' \
              | grep -qx active || die "--replica-state expected-active but MAIN replica is NOT active on z203"
            log "envelope: replica ACTIVE (covers the reload gap)"
            ;;
        expected-absent)
            ssh -o BatchMode=yes "$Z203_TARGET" 'systemctl is-active cloudflared-nucpot-replica 2>/dev/null || echo inactive' \
              | grep -qx inactive || die "--replica-state expected-absent but MAIN replica IS active on z203"
            log "envelope: replica ABSENT (brief primary restart bump inside the window, per 5968 step 1)"
            ;;
        *) die "apply requires --replica-state expected-active|expected-absent" ;;
    esac
}

verify_serving() {
    sleep 15
    launchctl print "system/$LABEL" 2>/dev/null | grep -q "pid = " || die "daemon not loaded after reload"
    log "daemon loaded: $(launchctl print "system/$LABEL" 2>/dev/null | awk '/^[[:space:]]*pid =/ {print $3}')"
    local code
    code="$(curl -s -o /dev/null -w '%{http_code}' --connect-timeout 10 "$PUBLIC_URL" || echo 000)"
    [ "$code" = "200" ] || die "public URL returned $code after reload"
    log "public: $PUBLIC_URL -> 200"
    sleep 5
    if tail -50 "$LOG_FILE" 2>/dev/null | grep -q "Registered tunnel connection"; then
        log "connector: fresh registration found in $LOG_FILE"
    else
        die "no tunnel registration in $LOG_FILE within the check window — primary is loaded but NOT serving"
    fi
}

case "$MODE" in
    verify)
        log "plist KeepAlive:"; /usr/libexec/PlistBuddy -c 'Print :KeepAlive' "$PLIST" | sed 's/^/  /'
        if grep -q 'keepalive_mode' "$GENERATOR"; then
            log "generator: keepalive-mode parameterization PRESENT"
        else
            log "generator: legacy (no keepalive_mode) — plist bool true can be reverted by the next sync-token run"
        fi
        plist_keepalive_is_bool_true && log "STATE: KeepAlive=true (bool)" || log "STATE: KeepAlive is NOT bool true"
        ;;
    apply)
        [ -f "$PLIST" ] || die "plist not found: $PLIST"
        [ -f "$GENERATOR" ] || die "generator not found: $GENERATOR"
        mkdir -p "$BACKUP_DIR"
        check_replica_envelope
        log "backing up plist -> $PLIST_BAK"
        sudo cp "$PLIST" "$PLIST_BAK" && sudo chown "$(id -u):$(id -g)" "$PLIST_BAK"
        log "backing up generator -> $GENERATOR_BAK"
        cp "$GENERATOR" "$GENERATOR_BAK"
        log "editing generator (parameterized keepalive; MAIN=always, VERIFY=legacy)"
        edit_generator
        log "rewriting live plist: KeepAlive dict -> bool true"
        sudo /usr/libexec/PlistBuddy -c 'Delete :KeepAlive' -c 'Add :KeepAlive bool true' "$PLIST"
        plutil -lint "$PLIST" >/dev/null || die "plist failed plutil -lint after rewrite"
        plist_keepalive_is_bool_true || die "KeepAlive is not bool true after rewrite"
        log "reloading daemon (bootout + bootstrap)"
        sudo launchctl bootout "system/$LABEL" 2>/dev/null || true
        sleep 2
        sudo launchctl bootstrap system "$PLIST"
        verify_serving
        log "=== APPLY COMPLETE — KeepAlive=true live; backups at:"
        log "    $PLIST_BAK"
        log "    $GENERATOR_BAK"
        log "rollback: $0 rollback (restores both + reloads)"
        ;;
    rollback)
        PLIST_BAK="$(ls -1t "$BACKUP_DIR/${LABEL}.plist.bak."* 2>/dev/null | head -1 || true)"
        [ -n "$PLIST_BAK" ] || die "no plist backup found in $BACKUP_DIR"
        GENERATOR_BAK="${GENERATOR}.bak.${PLIST_BAK##*.bak.}"
        log "restoring plist from $PLIST_BAK"
        sudo cp "$PLIST_BAK" "$PLIST" && sudo chown root:wheel "$PLIST" && sudo chmod 0644 "$PLIST"
        if [ -f "$GENERATOR_BAK" ]; then
            log "restoring generator from $GENERATOR_BAK"
            cp "$GENERATOR_BAK" "$GENERATOR"
        else
            log "NOTE - no generator backup at $GENERATOR_BAK; leaving generator as-is"
        fi
        log "reloading daemon"
        sudo launchctl bootout "system/$LABEL" 2>/dev/null || true
        sleep 2
        sudo launchctl bootstrap system "$PLIST"
        verify_serving
        log "=== ROLLBACK COMPLETE — original plist behavior restored ==="
        ;;
    *) usage 2 ;;
esac
