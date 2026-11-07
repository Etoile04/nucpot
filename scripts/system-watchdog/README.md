# scripts/system-watchdog/ — system-domain watchdog source of truth

Carries the system-domain watchdog `com.nfm.watchdogd` (NFM-5079 system-domain
plist precedent) and the registration-gap gate (NFM-5880/NFM-5881). Design note:
NFM-5880 comment `410386c2` (AC1) — all thresholds, signal extraction rules and
the failure-mode table live there; this README is the operator surface.

> This directory did not exist in-repo before NFM-5881: the live watchdog script
> previously existed only on the host. As of this branch, **this directory is the
> source of truth**; host paths are deployed copies (byte-parity rule below).

## Files

| File | Host deploy path | Role |
|---|---|---|
| `nfm-system-watchdogd.sh` | `/usr/local/lib/nfm-watchdog/nfm-system-watchdogd.sh` | the 60s root watchdog (Docker liveness, gui-watchdog liveness) + additive section 3 (registration-gap gate) |
| `com.nfm.watchdogd.plist` | `/Library/LaunchDaemons/com.nfm.watchdogd.plist` | byte-parity record — **unchanged** by the gate; no plist deploy is needed for this work |
| `nfm-registration-gap-gate.py` | `/usr/local/lib/nfm-watchdog/nfm-registration-gap-gate.py` | dual-signal gate module (stdlib-only, Python 3.9-compatible for `/usr/bin/python3`) |

Runtime state (NOT in-repo):

| Path | Purpose |
|---|---|
| `/usr/local/lib/nfm-watchdog/reg-gap-gate.mode` | DRY_RUN/LIVE flag. **Absent or anything ≠ `LIVE` ⇒ DRY_RUN (default).** |
| `/var/run/nfm-watchdogd/reg-gap-gate-state.json` | fire-cap/backoff state (survives watchdogd restarts; cleared by reboot — documented residual) |
| `/var/log/nfm-watchdogd.log` | one `reg-gap-gate:` decision line per 60s tick |

## The gate (what can fire, and when)

`launchctl kickstart -k system/com.cloudflare.cloudflared-nucpot` fires ONLY when
BOTH: (1) sentinel full-edge episode window open (`open=1` in
`/Users/lwj04/.nfm-site-monitor.s1.state`, mtime ≤ 900s), AND (2) no
`Registered tunnel connection` line in `/Library/Logs/cloudflared-nucpot.log`
within the last 600s (rolling staleness; tail-span guard covers rotation).
Caps: ≤3 fires / rolling hour, then backoff 30m→60m→120m (4h cap). Reset only on
a fully clean tick. The verify tunnel (`com.cloudflare.cloudflared-verify`) is
never touched.

## DRY_RUN / LIVE flip (rollback = flip only)

```bash
# state (soak): file absent, or
printf 'DRY_RUN\n' | sudo tee /usr/local/lib/nfm-watchdog/reg-gap-gate.mode

# enable (only after ≥24h zero-false-fire DRY_RUN soak, parent AC3):
printf 'LIVE\n' | sudo tee /usr/local/lib/nfm-watchdog/reg-gap-gate.mode
sudo chown root:wheel /usr/local/lib/nfm-watchdog/reg-gap-gate.mode
sudo chmod 644 /usr/local/lib/nfm-watchdog/reg-gap-gate.mode
```

Flip takes effect on the next 60s tick. **No plist unload, no watchdogd restart,
no tunnel restart.**

## Byte-parity host-apply (NFM-5666 pattern)

The watchdogd fires every 60s — an in-place `cp` can race a fire (mid-copy
syntax error). Install via temp + `mv`, from the merged commit, then verify
parity (repo sha == deployed sha == marker grep):

```bash
SHA=<merged-commit-sha>
for f in nfm-system-watchdogd.sh nfm-registration-gap-gate.py; do
  git show "$SHA:scripts/system-watchdog/$f" > "/tmp/$f.new"
  sudo mv "/tmp/$f.new" "/usr/local/lib/nfm-watchdog/$f"
done
sudo chmod 755 /usr/local/lib/nfm-watchdog/nfm-system-watchdogd.sh \
               /usr/local/lib/nfm-watchdog/nfm-registration-gap-gate.py
# parity proof
git show "$SHA:scripts/system-watchdog/nfm-system-watchdogd.sh" | diff - /usr/local/lib/nfm-watchdog/nfm-system-watchdogd.sh && echo parity-ok
grep -c 'reg-gap-gate' /usr/local/lib/nfm-watchdog/nfm-system-watchdogd.sh   # ≥ 1
tail -3 /var/log/nfm-watchdogd.log | grep 'reg-gap-gate:'                    # ticking
```

Rollback of a bad apply: `git show <previous-sha>:...` re-deploy the prior files,
or delete section 3 — sections 1–2 (Docker, gui-watchdog) are independent.

## Soak evidence (parent AC3)

```bash
grep 'reg-gap-gate:' /var/log/nfm-watchdogd.log | tail -n 100
grep 'reg-gap-gate:' /var/log/nfm-watchdogd.log | grep -c 'decision=FIRE'
```

One line per tick, 60s cadence; DRY_RUN FIRE lines are decisions that did NOT
execute — the soak counts them against zero-false-fire.

## Tests

`scripts/tests/test_nfm_registration_gap_gate.py` — decision matrix (both-signals
fire / either-clean no-op), cap/backoff transitions, reset semantics, s1
missing/empty/corrupt/stale, log missing / no-registration span guard, partial
trailing line, DRY_RUN executor-never-called, verify-label negative assertion,
state persistence round-trip.

## Constraints (binding, parent NFM-5880)

- Host is production: zero network/firewall/NAT change; the only action is a
  launchctl kickstart of an existing system service.
- NO KeepAlive change (window-gated HA lane): the plist is byte-identical to the
  deployed one.
- Verify tunnel out of scope: label asserted absent from every executable path.
