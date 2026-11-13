#!/usr/bin/env bash
# rollback-replica.sh — return to primary-only serving state.
#
# NFM-5965 (D2-G2) artifact. The charter guarantee: the PRIMARY connector is
# untouched at every step, and the final state is primary-only.
#
# Modes:
#   abort (default)          Stop + disable both z203 replica connectors and
#                            the forwarder. Mac primary untouched. This is the
#                            window's "ANY red -> immediate rollback" action.
#   full                     abort + restore the Mac MAIN plist KeepAlive
#                            backup (and the generator backup) taken by
#                            macos-set-main-keepalive-true.sh, reloading the
#                            primary daemon. ORDER MATTERS: the plist is
#                            restored and verified while the replica still
#                            carries traffic, so the primary's reload gap is
#                            covered; only then are the z203 units unloaded.
#
# --keep-forwarder: leave the (harmless, loopback-only) forwarder running to
# speed a re-attempt. Default unloads it for a clean primary-only state.

set -euo pipefail

Z203_TARGET="${Z203_TARGET:-z203@z203-thinkstation-p3-tower}"
BACKUP_DIR="$HOME/nfm-backups/plist-history"
PLIST="/Library/LaunchDaemons/com.cloudflare.cloudflared-nucpot.plist"
GENERATOR="$HOME/.cloudflared/tunnel-start.sh"
PUBLIC_URL="https://nucpot.dpdns.org"

MODE="abort"
KEEP_FORWARDER=0

usage() { sed -n '2,25p' "${BASH_SOURCE[0]}"; exit "${1:-0}"; }
while [ $# -gt 0 ]; do
    case "$1" in
        --mode) MODE="$2"; shift 2 ;;
        --keep-forwarder) KEEP_FORWARDER=1; shift ;;
        -h|--help) usage 0 ;;
        *) echo "unknown arg: $1" >&2; usage 2 ;;
    esac
done

log() { printf '[rollback-replica] %s\n' "$*"; }
die() { printf '[rollback-replica] FAILED: %s\n' "$*" >&2; exit 1; }

r() { ssh -o BatchMode=yes "$Z203_TARGET" "$@"; }

unload_z203() {
    log "unloading z203 replica units"
    r 'sudo systemctl disable --now cloudflared-nucpot-replica.service 2>/dev/null || true'
    r 'sudo systemctl disable --now cloudflared-verify-replica.service 2>/dev/null || true'
    if [ "$KEEP_FORWARDER" = "1" ]; then
        log "forwarder left running (--keep-forwarder)"
    else
        r 'sudo systemctl disable --now nfm-origin-forwarder.service 2>/dev/null || true'
    fi
    r 'systemctl is-active cloudflared-nucpot-replica 2>/dev/null || echo inactive' | grep -q inactive \
        || die "MAIN replica unit still active on z203"
    log "OK - replica units down"
}

latest_backup() {
    ls -1t "$BACKUP_DIR"/com.cloudflare.cloudflared-nucpot.plist.bak.* 2>/dev/null | head -1
}

restore_primary_plist() {
    local bak generator_bak
    bak="$(latest_backup)"
    [ -n "$bak" ] || die "no plist backup found in $BACKUP_DIR — cannot restore KeepAlive"
    generator_bak="${GENERATOR}.bak.$(basename "$bak" | sed 's/.*plist.bak.//')"
    log "restoring plist from $bak (KeepAlive flip undo)"
    sudo cp "$bak" "$PLIST"
    sudo chown root:wheel "$PLIST" 2>/dev/null || true
    sudo chmod 0644 "$PLIST"
    /usr/libexec/PlistBuddy -c 'Print :KeepAlive' "$PLIST" 2>/dev/null | grep -q "SuccessfulExit" \
        || log "NOTE - restored plist KeepAlive shape differs from the original dict form; inspect manually"
    if [ -f "$generator_bak" ]; then
        cp "$generator_bak" "$GENERATOR"
        log "generator restored from $generator_bak"
    else
        log "NOTE - no generator backup at $generator_bak; generator edit (if any) left in place"
    fi
    log "reloading PRIMARY daemon (brief restart; replica is still serving)"
    sudo launchctl bootout system/com.cloudflare.cloudflared-nucpot 2>/dev/null || true
    sleep 2
    sudo launchctl bootstrap system "$PLIST"
    sleep 15
    launchctl print "system/com.cloudflare.cloudflared-nucpot" 2>/dev/null | grep -q "pid = " \
        || die "primary daemon did not come back after reload"
    log "OK - primary reloaded and running"
}

verify_primary_only() {
    sleep 5
    local code
    code="$(curl -s -o /dev/null -w '%{http_code}' --connect-timeout 10 "$PUBLIC_URL" || echo 000)"
    [ "$code" = "200" ] || die "public URL returned $code after rollback — investigate immediately"
    log "OK - $PUBLIC_URL -> 200 (primary-only serving)"
}

case "$MODE" in
    abort)
        unload_z203
        verify_primary_only
        log "=== ROLLBACK COMPLETE (abort) — primary-only state; z203 replica stack disabled ==="
        ;;
    full)
        log "FULL rollback: restore primary FIRST (while replica still covers), then unload z203"
        restore_primary_plist
        verify_primary_only
        unload_z203
        verify_primary_only
        log "=== ROLLBACK COMPLETE (full) — plist+generator restored, z203 stack down ==="
        ;;
    *) usage 2 ;;
esac
