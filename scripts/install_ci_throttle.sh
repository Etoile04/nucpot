#!/usr/bin/env bash
# ============================================================================
# NFM-5333 — install the CI download throttle proxy as a LaunchAgent.
#
# Idempotent. Copies scripts/ci_throttle_proxy.py to a stable location
# (~/.nfmd/ci-throttle — NOT a repo checkout, which switches branches),
# renders the launchd plist from the template, and (re)bootstraps the
# agent. No sudo anywhere: the proxy is loopback-only user-space shaping.
#
# Usage:
#   scripts/install_ci_throttle.sh            # install + start + probe
#   scripts/install_ci_throttle.sh --status   # show agent + health state
#
# Retune the cap by editing NFMD_CI_THROTTLE_RATE_MBPS in
# ~/Library/LaunchAgents/io.nfmd.ci-throttle.plist, then:
#   launchctl kickstart -k gui/$(id -u)/io.nfmd.ci-throttle
# ============================================================================
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PROXY_SRC="$REPO_ROOT/scripts/ci_throttle_proxy.py"
PLIST_TEMPLATE="$REPO_ROOT/scripts/host/ci-throttle/io.nfmd.ci-throttle.plist.template"

DEST_DIR="$HOME/.nfmd/ci-throttle"
PLIST_DST="$HOME/Library/LaunchAgents/io.nfmd.ci-throttle.plist"
LABEL="io.nfmd.ci-throttle"
LOG_PATH="$HOME/Library/Logs/nfmd-ci-throttle.log"
UID_N="$(id -u)"

pick_python3() {
    # Prefer the hermetic system interpreter for a launchd agent: a brew
    # path can vanish on upgrade night. Fall back to whatever python3 is
    # on PATH for hosts where CLT python is absent.
    if /usr/bin/python3 -c 'import asyncio' >/dev/null 2>&1; then
        echo /usr/bin/python3
    else
        command -v python3
    fi
}

render_plist() {
    local python3="$1"
    sed -e "s|__HOME__|$HOME|g" -e "s|__PYTHON3__|$python3|g" "$PLIST_TEMPLATE"
}

do_status() {
    echo "== launchctl print gui/$UID_N/$LABEL (condensed)"
    launchctl print "gui/$UID_N/$LABEL" 2>/dev/null \
        | grep -E 'state|pid|last exit|program =' || echo "(not loaded)"
    echo
    echo "== health probe (proxy form)"
    if curl -fsS --max-time 4 -x http://127.0.0.1:7899 \
        http://127.0.0.1:7899/__nfmd_ci_throttle_health; then
        echo
    else
        echo "(health probe failed — see $LOG_PATH)"
        return 1
    fi
    echo "== last log lines"
    tail -n 5 "$LOG_PATH" 2>/dev/null || true
}

do_install() {
    test -f "$PROXY_SRC" || { echo "FATAL: $PROXY_SRC missing" >&2; exit 1; }
    test -f "$PLIST_TEMPLATE" || { echo "FATAL: $PLIST_TEMPLATE missing" >&2; exit 1; }

    mkdir -p "$DEST_DIR" "$(dirname "$PLIST_DST")" "$(dirname "$LOG_PATH")"
    install -m 0755 "$PROXY_SRC" "$DEST_DIR/ci_throttle_proxy.py"

    local python3
    python3="$(pick_python3)"
    echo "Using interpreter: $python3"

    # Re-render every run: rate retunes live in the plist, installs must
    # not clobber a hand-tuned value silently — so only write when the
    # rendered template differs, and say so.
    local rendered="/tmp/io.nfmd.ci-throttle.plist.$$"
    render_plist "$python3" >"$rendered"
    if ! cmp -s "$rendered" "$PLIST_DST" 2>/dev/null; then
        if test -f "$PLIST_DST" \
           && grep -q 'NFMD_CI_THROTTLE_RATE_MBPS' "$PLIST_DST" 2>/dev/null; then
            local old_rate new_rate
            # Extract the bare value between <string>…</string>: a tr
            # delete-set that misses '>' yields ">40>", the rate-substitution
            # sed below then never matches, and the hand-tuned rate would be
            # silently clobbered (shellcheck SC2020 caught the tr form).
            local plist_rate_line
            plist_rate_line="$(sed -n '/NFMD_CI_THROTTLE_RATE_MBPS/{n;p;}' "$PLIST_DST")"
            old_rate="${plist_rate_line#*<string>}"
            old_rate="${old_rate%%</string>*}"
            plist_rate_line="$(sed -n '/NFMD_CI_THROTTLE_RATE_MBPS/{n;p;}' "$rendered")"
            new_rate="${plist_rate_line#*<string>}"
            new_rate="${new_rate%%</string>*}"
            if test "$old_rate" != "$new_rate"; then
                echo "NOTE: preserving hand-tuned rate ${old_rate} (template says ${new_rate})" >&2
                sed "s|<string>${new_rate}</string>|<string>${old_rate}</string>|" \
                    "$rendered" >"${rendered}.tuned"
                mv "${rendered}.tuned" "$rendered"
            fi
        fi
        install -m 0644 "$rendered" "$PLIST_DST"
        echo "Installed $PLIST_DST"
    else
        echo "$PLIST_DST already current"
    fi
    rm -f "$rendered"

    # bootstrap fails with 537ERRINPROGRESS-style noise if the old
    # instance is still registered; bootout first, best-effort.
    launchctl bootout "gui/$UID_N/$PLIST_DST" >/dev/null 2>&1 || true
    launchctl bootstrap "gui/$UID_N" "$PLIST_DST"

    sleep 1
    do_status
}

case "${1:-install}" in
    --status) do_status ;;
    install) do_install ;;
    *)
        echo "usage: $0 [--status]" >&2
        exit 64
        ;;
esac
