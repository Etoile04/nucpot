# SRE activation runbook — ADR-024 runner-egress terminating-proxy lane

**Status: STAGED / ARMED — this runbook activates nothing by itself.**
**Owner: SRE Monitor** (activation owner post-build, per NFM-5583 W5/AC7).
**Scope: wenjiedeMac-Studio GHA runner `production` ONLY.** ADR-018 direct-egress
remains doctrine for every other lane; nothing in this runbook applies elsewhere.

Reading this document is arming. Activation happens only when a §1 trigger fires,
executed by SRE under the pre-authorization below — no CTO round-trip required.

- Ruling source of record: [CTO-RULING] comment `5ebae38d` on NFM-5582
  (option (b) staged exception lane; triggers, report-back, metric).
- Post-activation verdict interpretation: CTO addendum `5f1b8a2d` on NFM-5582
  (2026-10-18 05:28Z), codified in `docs/adr/ADR-024-NFM-5582-runner-egress-terminating-proxy.md` §5.
- Doctrine: `docs/adr/ADR-024-NFM-5582-runner-egress-terminating-proxy.md`
  (merged to main in PR #1491).
- Background incident (episode ledger): NFM-5581 — .NET broker long-poll reset
  by ClientHello-sensitive on-path DPI; 9 episodes / 38 days, windows
  17 min → 87 min → ~10 h.

## 0. Artifact index (all paths real, verified at authoring 2026-10-19)

| What | Where |
| --- | --- |
| Proxy source + plist + CA mint tooling (repo) | `tools/runner-egress-proxy/` (`egress_proxy.py`, `com.nfm.runner-egress-proxy.plist`, `mint_ca.sh`, `nc_der.py`, `README.md`) |
| Proxy tests (repo) | `tools/runner-egress-proxy/test_egress_proxy.py`, `test_mint_ca.py` |
| ADR / doctrine (repo) | `docs/adr/ADR-024-NFM-5582-runner-egress-terminating-proxy.md` |
| Proxy daemon (host) | launchd label `com.nfm.runner-egress-proxy`, RunAtLoad + KeepAlive, binds `127.0.0.1:8443` only |
| Proxy unit file (host) | `~/Library/LaunchAgents/com.nfm.runner-egress-proxy.plist` |
| Host cert dir (host, secrets live ONLY here) | `~/nfm-egress-proxy/` — `ca.cert.pem`, static leaves for `broker.actions.githubusercontent.com`, `github.com`, `pipelinesghubeus23.actions.githubusercontent.com`, `vstoken.actions.githubusercontent.com`. `ca.key`, `ca.srl`, and probe keys were DESTROYED after the W3 negative tests (G2) — nothing can mint new leaves. |
| Proxy log (host) | `~/Library/Logs/nfm-egress-proxy.log` |
| Runner service (host) | launchd label `actions.runner.Etoile04-nucpot.wenjiedeMac-Studio`, home `/Users/lwj04/actions-runner-production` |
| Runner unit file (host) | `~/Library/LaunchAgents/actions.runner.Etoile04-nucpot.wenjiedeMac-Studio.plist` — currently pinned DIRECT: `http_proxy`/`HTTP_PROXY`/`https_proxy`/`HTTPS_PROXY` = `""`, `no_proxy`/`NO_PROXY` = `"*"` |
| Runner service logs (host) | `~/Library/Logs/actions.runner.Etoile04-nucpot.wenjiedeMac-Studio/stdout.log`, `stderr.log` |
| Runner diag / episode fingerprint (host) | `~/actions-runner-production/_diag/Runner_*.log` — Serilog `ERR  BrokerServer` (note the double space) on long-poll resets |
| W4 verification record | NFM-5587 (fail-closed proofs, clean-window validation, BOTH-direction kill-switch verification, controlled cutover, return to DIRECT) |
| Report-back + verdict thread | NFM-5582 |

Trust state at authoring (W3 close, NFM-5586 done 2026-10-19): login keychain
holds EXACTLY ONE nfm CA entry under the `ssl` policy — sha256 fingerprint
`08:D4:72:15:63:43:49:56:5E:9D:5C:FF:DF:65:47:14:59:A0:7C:8C:D0:D0:1A:BA:19:29:80:1E:D6:FC:05:D4`
== the staged `~/nfm-egress-proxy/ca.cert.pem` (minted 2026-10-18 07:28Z,
validity ≤30 d); admin domain (`dump-trust-settings -d`) is empty.

## 1. Pre-authorized activation triggers

From the ruling `5ebae38d` / ADR-024 §4 — **any ONE** of these authorizes
immediate SRE activation with **no further CTO round-trip**:

1. **ep#10 with duration ≥ 2 h**, OR
2. **lane fully dark — zero dispatch ≥ 2 h**, OR
3. **ep#9 relapse after the lever is armed.**

**Triggers stay armed even if ep#9 self-lifts.** Waning is not lifted, and
lifted is not resolved (the NFM-4565 lesson: the 09-10 self-lift cancelled the
September response and 8 more episodes followed over 38 days). Do not stand
down the triggers on an apparent recovery.

Detection sources:

- **Episode (durations, resets):** `~/actions-runner-production/_diag/Runner_*.log`
  — count/start-timestamp the `ERR  BrokerServer` long-poll resets; episode
  ledger and per-episode durations are tracked on NFM-5581.
- **Lane darkness (zero dispatch):** no new job sessions in the runner diag
  logs and no offers/jobs on the lane for ≥ 2 h (GHA run history for
  Etoile04/nucpot filtered to this runner).
- **Relapse:** a fresh reset burst matching the ep#9 fingerprint after the
  lever was armed (armed = W3 staged + this runbook merged).

## 2. Pre-activation checks (verify the lane is healthy BEFORE flipping)

Run all of these; any failure ⇒ do NOT activate, comment the failure on
NFM-5582 and fall back to the retry-loop posture (option (a) accept-and-monitor).

```sh
# 2.1 daemon alive, loopback-only, KeepAlive
launchctl print gui/$(id -u)/com.nfm.runner-egress-proxy | grep -E "state|KeepAlive"
/usr/sbin/lsof -nP -iTCP:8443 -sTCP:LISTEN        # MUST show 127.0.0.1:8443 only
# (lsof is not on PATH in some shells — use the full path)

# 2.2 403-gate live on BOTH axes — probe canon:
# client leg is PLAINTEXT HTTP CONNECT on 8443 (TLS lives on the upstream leg).
# NEVER add --noproxy '*' — it silently disables the proxy and proves nothing.
curl -sS -o /dev/null -w '%{http_code}\n' -x http://127.0.0.1:8443 \
  http://broker.actions.githubusercontent.com/            # non-CONNECT => expect 403
curl -sS -o /dev/null -w '%{http_code}\n' -x http://127.0.0.1:8443 \
  https://example.com/                                    # out-of-allowlist CONNECT => expect 403/deny
# (TLS-first probes against 8443 log "malformed request line" — expected, not a fault.)

# 2.3 trust state — FINGERPRINT-MATCH, never presence-match
# (a stale-generation CA once sat in the keychain; grep for the CA name FALSE-PASSES.
#  Hash the cert DER on BOTH sides — shasum of the .pem FILE bytes is NOT the cert hash.)
security dump-trust-settings                               # exactly ONE nfm entry, ssl policy
security dump-trust-settings -d                            # admin domain: empty
openssl x509 -in ~/nfm-egress-proxy/ca.cert.pem -outform DER | shasum -a 256
security find-certificate -a -c "NFM runner egress proxy CA (staged lane)" -Z \
  | awk '/^SHA-256 hash:/ {print tolower($3)}'             # one hash per enrolled gen — EVERY line
# must EQUAL the file hash above. Do NOT pipe the concatenated -p PEM through a single
# `openssl x509` — it parses only the FIRST cert and a stale gen would false-pass.
# Authoring-time expected (file hash and every enrolled gen): 08d47215634349565e9d5cffdf65471459a07c8cd0d01aba1929801ed6fc05d4
# If a re-mint ever happens (see 2.4), recompute from the file and update §0.

# 2.4 CA validity (minted 2026-10-18, ≤30 d — an expired CA fails activation silently)
openssl x509 -enddate -noout -in ~/nfm-egress-proxy/ca.cert.pem
# If expired or near-expiry at trigger time: STOP. Re-mint via
# tools/runner-egress-proxy/mint_ca.sh (see its README §Staging sequence —
# mint, negative tests, destroy ca.key, enroll, verify, THEN proceed). This
# requires the full W3 staging sequence and a fresh §0 fingerprint update.

# 2.5 runner still on DIRECT (sanity — nothing pre-activated it)
plutil -p ~/Library/LaunchAgents/actions.runner.Etoile04-nucpot.wenjiedeMac-Studio.plist \
  | grep -A8 EnvironmentVariables                # expect the DIRECT pinning: empty proxies + no_proxy="*"
```

## 3. Activation steps (the cutover)

The runner takes proxy config **from service env vars only** (runner 2.337.x:
no TLS-bypass var, `.proxy` file inert). Activation = swap the runner service's
DIRECT pinning for the loopback proxy, exactly as verified in W4 (NFM-5587).

**Arm the kill-switch FIRST** (§6): take a timestamped backup of the current
runner plist before touching it — that backup IS the single-step revert.

```sh
# 3.1 arm the kill-switch (backup = revert artifact)
cp ~/Library/LaunchAgents/actions.runner.Etoile04-nucpot.wenjiedeMac-Studio.plist \
   ~/Library/LaunchAgents/actions.runner.Etoile04-nucpot.wenjiedeMac-Studio.plist.bak-direct.$(date -u +%Y%m%dT%H%M%SZ)

# 3.2 cutover — apply the W4-VERIFIED proxy pinning to the runner service
#     (mechanism: replace http_proxy/https_proxy="" + no_proxy="*" with the
#      loopback proxy per NFM-5587's verified transcript; where this text and
#      the NFM-5587 evidence comment differ, the verified transcript WINS)
#   EnvironmentVariables:
#     https_proxy / HTTPS_PROXY = http://127.0.0.1:8443
#     http_proxy  / HTTP_PROXY  = http://127.0.0.1:8443
#     no_proxy    / NO_PROXY    = 127.0.0.1,localhost
launchctl bootout gui/$(id -u) actions.runner.Etoile04-nucpot.wenjiedeMac-Studio
launchctl bootstrap gui/$(id -u) \
   ~/Library/LaunchAgents/actions.runner.Etoile04-nucpot.wenjiedeMac-Studio.plist

# 3.3 verify the runner now traverses the proxy
tail -f ~/Library/Logs/nfm-egress-proxy.log        # CONNECTs to broker/github dests, no denies
launchctl print gui/$(id -u)/actions.runner.Etoile04-nucpot.wenjiedeMac-Studio | grep -E "state|pid"

# 3.4 confirm dispatch: a job session appears in the runner diag log and completes
tail -f ~/actions-runner-production/_diag/Runner_*.log
```

The proxy daemon itself stays exactly as staged — no daemon-side change is part
of activation. Do NOT widen the allowlist (`egress_proxy.py`
`ALLOWED_DNS_SUFFIXES`: `github.com`, `actions.githubusercontent.com` only) —
out-of-class traffic MUST keep 403ing (G4).

## 4. Report-back obligation (≤ 1 h)

Within **1 hour** of activation, comment on **NFM-5582** (the ruling thread):

- which trigger fired (1/2/3 from §1) and the evidence that fired it,
- activation timestamp (UTC) and who executed,
- §2 check results (daemon / 403-gate / fingerprint-match / CA validity),
- cutover transcript summary (plist backup path from §3.1 included),
- initial proxied-lane state (first dispatch + long-poll behavior).

## 5. Post-activation DPI-proof metric and verdict classification

The proof is **post-activation, during an ACTIVE episode** (direct .NET
ClientHello being reset). A clean-window test cannot prove this — it is the
validation metric (ruling `5ebae38d`; ADR-024 §4–§5).

**Metric:** during an active episode, the proxied lane must sustain
**long-poll + dispatch ≥ 30 min** (broker long-poll sessions on
`broker.actions.githubusercontent.com` surviving and jobs dispatching through
the lane, observed in `~/Library/Logs/nfm-egress-proxy.log` +
`~/actions-runner-production/_diag/Runner_*.log`).

Classify the two mechanisms SEPARATELY (CTO addendum `5f1b8a2d`):

| Observation | Class | Meaning |
| --- | --- | --- |
| (i) Long-poll survival with small payloads | **the metric that matters** | Broker-lane DPI neutralized |
| (ii) Bulk-transfer stalls (e.g. `github.com` 200 + ~493 KB then curl-28) | second mechanism (connection-age / sustained-transfer) | **broker lane may tolerate** — record, do not fail the verdict on it |

**Verdicts:**

- **SUCCESS** — (i) holds ≥ 30 min during the episode.
- **PARTIAL SUCCESS** — proxy fixes handshakes but long-poll still dies
  mid-connection ⇒ **escalate back to the CTO** on NFM-5582 for the contingency
  ladder (egress-relocated runner review).
- **INEFFECTIVE** — the proxy's own re-originated ClientHello is also reset
  (DPI fingerprint widened) ⇒ same escalation path ((c) re-register is
  revisitable only per the ruling: (b) ineffective AND (d) stalled).

**NEVER record a silent (b)-failed verdict.** Every non-SUCCESS outcome goes
back to the CTO on NFM-5582 with the evidence; the lane stays proxied or
reverts per §6 depending on runner behavior, but the verdict is always spoken.

## 6. Kill-switch (single-step revert to DIRECT)

W4 (NFM-5587) verified this in BOTH directions (direct → proxied → direct).
Stated in `tools/runner-egress-proxy/README.md` §Kill-switch. Single step:

```sh
# restore the DIRECT pinning from the §3.1 backup and cycle the service
cp ~/Library/LaunchAgents/actions.runner.Etoile04-nucpot.wenjiedeMac-Studio.plist.bak-direct.<TS> \
   ~/Library/LaunchAgents/actions.runner.Etoile04-nucpot.wenjiedeMac-Studio.plist
launchctl bootout gui/$(id -u) actions.runner.Etoile04-nucpot.wenjiedeMac-Studio
launchctl bootstrap gui/$(id -u) \
   ~/Library/LaunchAgents/actions.runner.Etoile04-nucpot.wenjiedeMac-Studio.plist
# verify: proxy log goes quiet for runner traffic; runner diag shows direct sessions
```

Use it on: any runaway behavior after cutover, suspected scope creep, verdict
INEFFECTIVE, or sunset. Full lane stand-down (rare — only on sunset/revert
decision, not for episode management) additionally stops the daemon and removes
trust:

```sh
launchctl bootout gui/$(id -u)/com.nfm.runner-egress-proxy
security remove-trusted-cert ~/nfm-egress-proxy/ca.cert.pem   # de-escalation is NOT GUI-gated on this host
```

## 7. Sunset

- **Review ≤ 30 d after activation** — put the review on NFM-5582 the day you
  activate (trigger + 30 d).
- **Revert to direct egress** on upstream/ISP resolution (option (d) track,
  NFM-5581) or fingerprint retirement.
- Revert = §6 (service-level first; full stand-down if the lane is retired).
- After any CA re-mint (§2.4) or trust change, update §0 of this runbook via
  the standard chain (LE → CR → RE).

## 8. Proven no-ops stay closed (ADR-024 §7)

Listener restarts, the `useV2Flow` flip, and plist/proxy-env changes **as
mitigations for the DIRECT lane** are proven no-ops. Do not re-try them; the
proxy lane is the only lever.

---

*Maintained under NFM-5583 (AC7), authored via NFM-5588. Episode ledger:
NFM-5581. Ruling thread: NFM-5582. W4 verification: NFM-5587.*
