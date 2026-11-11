# scripts/system-watchdog/ — system-domain watchdog source of truth

Carries the system-domain watchdog `com.nfm.watchdogd` (NFM-5079 system-domain
plist precedent), the registration-gap gate (NFM-5880/NFM-5881) and the
replica-gap gate (NFM-5966, D2-G3). Design note for the primary gate:
NFM-5880 comment `410386c2` (AC1) — all thresholds, signal extraction rules and
the failure-mode table live there; this README is the operator surface.

> This directory did not exist in-repo before NFM-5881: the live watchdog script
> previously existed only on the host. As of this branch, **this directory is the
> source of truth**; host paths are deployed copies (byte-parity rule below).

## Files

| File | Host deploy path | Role |
|---|---|---|
| `nfm-system-watchdogd.sh` | `/usr/local/lib/nfm-watchdog/nfm-system-watchdogd.sh` | the 60s root watchdog (Docker liveness, gui-watchdog liveness) + additive section 3 (registration-gap gate) + additive section 4 (replica-gap gate, NFM-5966) |
| `com.nfm.watchdogd.plist` | `/Library/LaunchDaemons/com.nfm.watchdogd.plist` | byte-parity record — **unchanged** by either gate; no plist deploy is needed for this work |
| `nfm-registration-gap-gate.py` | `/usr/local/lib/nfm-watchdog/nfm-registration-gap-gate.py` | dual-signal gate module, PRIMARY connector (stdlib-only, Python 3.9-compatible for `/usr/bin/python3`) |
| `nfm-replica-gap-gate.py` | `/usr/local/lib/nfm-watchdog/nfm-replica-gap-gate.py` | dual-signal gate module, z203 REPLICA connector (NFM-5966; same stdlib-only constraints) |

Runtime state (NOT in-repo):

| Path | Purpose |
|---|---|
| `/usr/local/lib/nfm-watchdog/reg-gap-gate.mode` | primary-gate DRY_RUN/LIVE flag. **Absent or anything ≠ `LIVE` ⇒ DRY_RUN (default).** |
| `/usr/local/lib/nfm-watchdog/replica-gap-gate.mode` | replica-gate DRY_RUN/LIVE flag (NFM-5966). Same rule; ships absent ⇒ DRY_RUN. |
| `/var/run/nfm-watchdogd/reg-gap-gate-state.json` | primary-gate fire-cap/backoff state (survives watchdogd restarts; cleared by reboot — documented residual) |
| `/Users/lwj04/.nfm-replica-gate-state.json` | replica-gate fire-cap/backoff state (user-home: section 4 runs AS THE CONSOLE USER — see below) |
| `/var/log/nfm-watchdogd.log` | one `reg-gap-gate:` + one `rep-gap-gate-replica:` decision line per 60s tick |

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
for f in nfm-system-watchdogd.sh nfm-registration-gap-gate.py nfm-replica-gap-gate.py; do
  git show "$SHA:scripts/system-watchdog/$f" > "/tmp/$f.new"
  sudo mv "/tmp/$f.new" "/usr/local/lib/nfm-watchdog/$f"
done
sudo chmod 755 /usr/local/lib/nfm-watchdog/nfm-system-watchdogd.sh \
               /usr/local/lib/nfm-watchdog/nfm-registration-gap-gate.py \
               /usr/local/lib/nfm-watchdog/nfm-replica-gap-gate.py
# parity proof
git show "$SHA:scripts/system-watchdog/nfm-system-watchdogd.sh" | diff - /usr/local/lib/nfm-watchdog/nfm-system-watchdogd.sh && echo parity-ok
grep -c 'reg-gap-gate' /usr/local/lib/nfm-watchdog/nfm-system-watchdogd.sh   # ≥ 1
grep -c 'rep-gap-gate' /usr/local/lib/nfm-watchdog/nfm-system-watchdogd.sh   # ≥ 1 (NFM-5966 section 4)
tail -3 /var/log/nfm-watchdogd.log | grep 'reg-gap-gate:'                    # ticking
tail -3 /var/log/nfm-watchdogd.log | grep 'rep-gap-gate-replica:'            # replica gate ticking
```

Rollback of a bad apply: `git show <previous-sha>:...` re-deploy the prior files,
or delete section 3/4 — sections 1–2 (Docker, gui-watchdog) are independent.

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

`scripts/tests/test_nfm_replica_gap_gate.py` — the NFM-5966 replica gate:
constant-level drift guard vs the primary gate (inherited config must stay
unchanged), unit-scope assertions (verify + verify-replica names can never
enter the action), the same decision-matrix/caps/backoff/reset coverage over
the SSH-probed Signal 2, ssh_fail/log_missing fail-closed-for-actions,
the SSH-skip budget rule (closed+clean ⇒ no probe; dirty ⇒ probe), and the
bounded-failure contract.

## Replica gate (NFM-5966, D2-G3) — what can fire, and when

`ssh z203 sudo -n systemctl restart cloudflared-nucpot-replica.service` fires
ONLY when BOTH: (1) sentinel full-edge episode window open — the SAME Signal 1
file and freshness rule as the primary gate, AND (2) the replica connector has
not proven registration within 600s, read from the z203 log tail
(`/var/log/cloudflared-nucpot-replica.log`, one SSH round trip, rolling
staleness + tail-span guard identical to the primary gate). Caps/backoff are
the primary gate's, unchanged: ≤3 fires / rolling hour, backoff
30m→60m→120m (4h cap), reset only on a fully clean tick (s1 closed + replica
proven fresh).

Fail-closed for ACTIONS: `ssh_fail` / `log_missing` / `none_short` are NOFIRE —
the gate never restarts blind on absent evidence (replica visibility is the
site-monitor lane's job; this gate only acts on positive staleness evidence
inside a live full-edge window). SSH budget: the probe is skipped entirely on
closed+clean ticks (rep=n/a); a dirty state still probes so the reset conjunct
can observe replica freshness.

Process identity: watchdogd section 4 runs the module AS THE CONSOLE USER via
`launchctl asuser` + `sudo -u` (the section-1 wedge-proven pattern) so the SSH
probe uses the operator's z203 identity — the same key/known_hosts the
site-monitor lane probe uses (one dependency surface). Consequence: the gate's
state file lives in the user home, and its availability is bounded by the same
asuser path section 1 uses (proven through the NFM-5343 gui wedge).

Unit scope: EXACTLY `cloudflared-nucpot-replica.service`, equality-checked at
import; `cloudflared-verify` (Mac) and `cloudflared-verify-replica.service`
(z203 soak variant, NFM-5965 artifact 2) are asserted out of scope.

Enable-time prerequisite (documented for the D2-G4/G5 host work + CR review):
the LIVE action needs a scoped passwordless restart path on z203 for the
operator identity (e.g. a sudoers rule limited to
`systemctl restart cloudflared-nucpot-replica.service`, or an equivalent
root-owned helper). Without it the LIVE action fails honestly
(`reason=fire_failed`, caps/backoff bounding retries) — the drill (NFM-5883
pattern) must prove the action BEFORE the flag flips to LIVE.

Rollback: flip/delete `/usr/local/lib/nfm-watchdog/replica-gap-gate.mode`, or
remove watchdogd section 4 — sections 1–3 are independent and untouched.

## Constraints (binding, parent NFM-5880)

- Host is production: zero network/firewall/NAT change; the only action is a
  launchctl kickstart of an existing system service.
- NO KeepAlive change (window-gated HA lane): the plist is byte-identical to the
  deployed one.
- Verify tunnel out of scope: label asserted absent from every executable path.
