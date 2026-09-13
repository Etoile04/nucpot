# NFM-4712 host hardening — runner plist + drift detector

> **Scope:** Recipe for hardening the macOS GitHub Actions runner LaunchAgent
> plist (`actions.runner.Etoile04-nucpot.wenjiedeMac-Studio.plist`) against
> the NFM-4567 broken-mitm-CA failure mode, plus a drift detector that fails
> closed if the plist regresses.
>
> **Audience:** Release Engineer + any operator provisioning a parallel
> self-hosted runner on the Mac Studio.
>
> **Status:** updated disposition. Layer-1 was shipped 2026-09-11 15:46Z;
> ADR-018 (NFM-4762, 2026-09-12) tore down the 7897 mitm entirely and
> restored the **Layer-2** template on the live plist. Sub-task 2 was
> dispositioned 2026-09-13: Step A voided (no system proxy exists to
> bypass), Step B executed (both stale CA certs purged). See
> **Sub-task 2** below.

## TL;DR

The runner LaunchAgent plist must carry the **Layer-2 (ADR-018)** template:

```xml
<key>HTTPS_PROXY</key>  <string></string>
<key>HTTP_PROXY</key>   <string></string>
<key>http_proxy</key>   <string></string>
<key>https_proxy</key>  <string></string>
<key>NO_PROXY</key>     <string>*</string>
<key>no_proxy</key>     <string>*</string>
```

Empty-string proxies mean no process spawned under this LaunchAgent honours
any HTTP/S proxy — neither the (removed) 7897 mitm nor any future
`scutil --proxy` system proxy. `NO_PROXY='*'` is defense-in-depth for
runtimes that only read the bypass variable.

…and the plist **must not** carry `SSL_CERT_FILE`, `NODE_EXTRA_CA_CERTS`,
`REQUESTS_CA_BUNDLE`, or `CURL_CA_BUNDLE`.

Run `./bin/audit-runner-plist.sh` after every plist edit. It exits non-zero
on drift or on a failed broker-TLS probe. Set `AUDIT_EXPECTED_LAYER=1` to
audit a host that still deliberately runs the historical mitm template.

## Why this state

### The failure mode we're defending against

`NFM-4567` shipped a local MITM CA (`NFM-4567 Local Broker MITM CA`) used
by a mitmproxy at `127.0.0.1:7897` to intercept GitHub-side TLS for
diagnostics. The CA's broker certificate was mis-issued; the runner's
`broker.actions.githubusercontent.com` handshake failed with `ERR_CERT_AUTHORITY_INVALID`
for ~62 hours (NFM-4628 → NFM-4333 G2 freeze).

### Disposition history

- **NFM-4630 Path A (Layer-2, 2026-09-10):** emptied the proxy vars +
  `NO_PROXY='*'`. Worked, but was reverted the same day in favour of
  keeping mitm for non-GitHub traffic.
- **NFM-4712 Layer-1 (2026-09-11):** kept `HTTPS_PROXY=7897` for
  non-GitHub traffic and added an explicit `NO_PROXY` bypass list for
  every GitHub-side host, so broker TLS skipped the mitm CA entirely.
- **ADR-018 / NFM-4762 (2026-09-12):** CEO decision — the bridge proxy
  had no subscription, was a DIRECT alias, and slowed egress. 7897 torn
  down (port closed, watchdog/poller plists removed, `PROXY_PORT` repo
  variable deleted). The live plist was flipped back to the Layer-2
  template (backup `plist.bak-pre-adr018.20260912`). Layer-1 is now a
  **historical** template only, selectable via `AUDIT_EXPECTED_LAYER=1`.

### Why both upper- and lower-case variants

`launchd` and the various language runtimes (.NET `HttpClient`, Python
`urllib`, Go `net/http`, Node `fetch`) do not agree on which case is
authoritative. The runner process is itself a .NET binary; .NET's
`HttpClient` reads `http_proxy` / `https_proxy` (lowercase), and the
subprocesses it spawns (`pip`, `npm`, `docker`, `gh`, `curl`) read
`HTTP_PROXY` / `HTTPS_PROXY`. We set both to be defensive — see the
audit script's proxy/bypass key lists.

## Recipe — apply this to a new runner

1. **Edit the LaunchAgent plist.** Mirror the `EnvironmentVariables` block
   from `~/Library/LaunchAgents/actions.runner.Etoile04-nucpot.wenjiedeMac-Studio.plist`
   on the existing host. Empty-string proxies must be present as empty
   strings — launchd treats absent keys differently from empty strings
   when merging the service-environment dict.
2. **Apply with `launchctl unload && load`** — *not* `kickstart -k`:

   ```sh
   launchctl unload ~/Library/LaunchAgents/<label>.plist
   launchctl load   ~/Library/LaunchAgents/<label>.plist
   ```

   `launchctl kickstart -k` re-runs `runsvc.sh` under the **cached** service
   environment and silently ignores EnvironmentVariables edits. This is
   the failure mode that turned `NFM-4600` into a multi-hour debug. See
   `launchctl-kickstart-does-not-reload-env-vars.md`.
3. **Run the drift detector:**

   ```sh
   cd ~/Projects/nucpot
   ./bin/audit-runner-plist.sh ~/Library/LaunchAgents/<label>.plist
   ```

   Expect: `[audit-runner-plist] OK: <path> matches Layer-2 template`
   and exit code 0.
4. **Verify with a real broker round-trip** (the audit script's probe
   only checks the TLS handshake; this verifies an actual GitHub job
   dispatch):

   ```sh
   curl -sS -o /dev/null -w '%{http_code}\n' \
     --max-time 8 \
     https://broker.actions.githubusercontent.com/_apis/v1/_health
   # expect 404 (the path is not real; TLS handshake success is what matters)
   ```

No `scutil --proxy` ExceptionsList step is needed: the host currently has
**no system proxy configured** (`scutil --proxy` shows all proxies
disabled), and the Layer-2 plist neutralizes any future system proxy for
runner-spawned processes anyway.

## Drift detection

`bin/audit-runner-plist.sh` asserts (Layer-2 default):

| Check | Failure mode caught |
|---|---|
| `HTTPS_PROXY` / `HTTP_PROXY` / `http_proxy` / `https_proxy` = `''` (or absent) | Re-introduction of an upstream proxy — e.g. a re-created mitm at 7897 — that would re-amplify a broken CA |
| `NO_PROXY` / `no_proxy` = `*` (or absent) | Loss of the global bypass — runner traffic becomes proxy-eligible again |
| Forbidden env vars (`SSL_CERT_FILE`, `NODE_EXTRA_CA_CERTS`, `REQUESTS_CA_BUNDLE`, `CURL_CA_BUNDLE`) **absent** | Direct re-amplification of NFM-4567's dead path |
| Live `broker.actions.githubusercontent.com` TLS probe with `NO_PROXY=*` returns a 2xx/3xx/4xx (not 000/7xx) | End-to-end regression — the script can extract a healthy dict but the runner's actual env has drifted |

Exit codes:

| Code | Meaning |
|---|---|
| 0 | OK — plist matches template + broker probe succeeded |
| 1 | Drift detected |
| 2 | Plist file not found |
| 3 | `plutil` cannot extract `EnvironmentVariables` (malformed plist) |
| 4 | `AUDIT_EXPECTED_LAYER` is not 1 or 2 |

Designed to wire into the G2 gate; see `nfm-4712` thread for the
proposal.

## Dead paths — what NOT to do

These were tried during the NFM-4628 → NFM-4630 → NFM-4712 → NFM-4762
chain and closed. Do not relitigate them.

- ❌ **`security add-trusted-cert -d -r trustRoot -k … <ca.crt>` headless.**
  The admin password dialog does not pop under harness/sudo context;
  NFM-4567 closed dead with `rc=124` timeouts.
- ❌ **Re-issuing the mitm CA with `nameConstraints`.** NFM-4592 closed dead
  — `curl` / .NET ignore `nameConstraints` at TLS handshake time even when
  the local CA store accepts the issuance. The validation happens before
  nameConstraints are evaluated.
- ❌ **Recreating a mitm on 7897 and pointing the runner at it.** ADR-018
  (NFM-4762) retired the 7897 domain: the bridge had no subscription, was
  a DIRECT alias, and slowed egress. CI egress is direct by design;
  7892 belongs to the user's VPN and must never be used by NFMD.
- ❌ **`sudo networksetup -setproxybypassdomains` for broker hosts
  (original Sub-task 2 Step A).** Voided 2026-09-13: the host has no
  system proxy (`scutil --proxy` all-disabled), so there is nothing to
  bypass; the command's replace-semantics + fragile ExceptionsList
  capture made it a config-pollution risk with zero benefit. The runner
  plist (Layer-2) already neutralizes system proxies for runner processes.
- ❌ **`launchctl kickstart -k <service>` to apply env edits.** Re-runs
  under cached env; use `unload && load`.

## Sub-task 2 — disposition record (2026-09-13)

Original scope: interactive sudo steps owned by the user. Re-assessed and
executed by the extractor agent under 方案 1 (NFM-4732 thread):

### Step A — `scutil --proxy` ExceptionsList: **VOIDED**

Precondition gone: NFM-4762/ADR-018 removed the mitm; `scutil --proxy`
shows `HTTPEnable: 0 / HTTPSEnable: 0` — no proxy, no ExceptionsList.
The runner plist's Layer-2 env makes runner-spawned processes ignore
system proxies regardless. Executing the original command would have
written a malformed bypass list (leading empty entry) to the wrong
network service (default route is `en1`/Wi-Fi, not `Ethernet`).

### Step B — CA purge: **EXECUTED 2026-09-13**

Two certificates shared the label `NFM-4567 Local Broker MITM CA`
(label-based delete would have missed one). Purged by SHA-1:

```bash
# Backup first (rollback artifact, 2 certs as PEM)
security find-certificate -a -c "NFM-4567" -p ~/Library/Keychains/login.keychain-db \
  > ~/scripts/nfm4567-ca-backup-20260913.pem
security delete-certificate -Z E887E4FCFB1E5C7B4E3D67AC3FFF3B1B14F0DE15 ~/Library/Keychains/login.keychain-db
security delete-certificate -Z 33FF335625079CCC225B3BFF9152F9CF58EACAE9 ~/Library/Keychains/login.keychain-db
security find-certificate -c "NFM-4567"   # → could not be found (both keychains)
```

Neither cert had trust settings (`security dump-trust-settings` empty,
user + admin domain) nor a matching private key (`find-identity -v -p
basic` → 0 identities), so the purge was hygiene, not a functional
change.

### Step C — regression check: **PASSED (rewritten)**

```bash
# Direct broker TLS (the 7897 mitm probe is dead — port closed by ADR-018)
curl -sS -o /dev/null -w '%{http_code}\n' --max-time 8 https://broker.actions.githubusercontent.com/_apis/v1/_health
# → 404, ssl_verify=0

# Direct egress regression
curl -sS -o /dev/null -w '%{http_code}\n' --max-time 8 https://pypi.org/simple/
# → 200

# Audit drift detector (Layer-2 expectation after this PR)
./bin/audit-runner-plist.sh
# → OK: matches Layer-2 template
```

## E2E test plan

| Step | Owner | Pass criterion |
|---|---|---|
| `bin/audit-runner-plist.sh` exits 0 on the live plist | RE | stdout `OK: … matches Layer-2 template`; exit 0 |
| `AUDIT_EXPECTED_LAYER=1 bin/audit-runner-plist.sh` on a Layer-1 plist exits 0 | RE | historical template still selectable |
| `security find-certificate -c "NFM-4567"` returns not-found | user/agent | both login and System keychains |
| broker direct TLS probe returns 2xx-4xx | RE | `curl … https://broker.actions.githubusercontent.com/_apis/v1/_health` |
| NFM-4712 closure | RE + user | PATCH to `done` with Layer-1→ADR-018→purge evidence in thread |

## Related

- NFM-4628 (closed) — acute incident; runner offline 62h
- NFM-4630 (closed) — original Path A (Layer-2) fix
- NFM-4712 (open parent) — hardening umbrella
- NFM-4732 (this issue) — Sub-task 1 PR #1314; Sub-task 2 dispositioned here
- NFM-4762 (closed) — ADR-018 de-proxy decision that superseded Layer-1
- NFM-4567 / NFM-4592 (closed dead) — do-not-retry
- NFM-4600 (closed) — `launchctl kickstart -k` env-cache failure mode
