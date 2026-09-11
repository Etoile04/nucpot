# NFM-4712 host hardening — runner plist + drift detector

> **Scope:** Recipe for hardening the macOS GitHub Actions runner LaunchAgent
> plist (`actions.runner.Etoile04-nucpot.wenjiedeMac-Studio.plist`) against
> the NFM-4567 broken-mitm-CA failure mode, plus a drift detector that fails
> closed if the plist regresses.
>
> **Audience:** Release Engineer + any operator provisioning a parallel
> self-hosted runner on the Mac Studio.
>
> **Status:** active disposition (NFM-4712, shipped 2026-09-11 15:46Z).
> Sub-task 2 (interactive CA purge + `scutil --proxy` ExceptionsList update)
> remains user-owned; see **Sub-task 2** below.

## TL;DR

The runner LaunchAgent plist must carry:

```xml
<key>HTTPS_PROXY</key>  <string>http://127.0.0.1:7897</string>
<key>HTTP_PROXY</key>   <string>http://127.0.0.1:7897</string>
<key>http_proxy</key>   <string>http://127.0.0.1:7897</string>
<key>https_proxy</key>  <string>http://127.0.0.1:7897</string>
<key>NO_PROXY</key>
<string>localhost,127.0.0.1,broker.actions.githubusercontent.com,*.actions.githubusercontent.com,*.blob.core.windows.net,*.githubusercontent.com,*.github.com,api.github.com,codeload.github.com,objects.githubusercontent.com,uploads.github.com</string>
<key>no_proxy</key>
<string>localhost,127.0.0.1,broker.actions.githubusercontent.com,*.actions.githubusercontent.com,*.blob.core.windows.net,*.githubusercontent.com,*.github.com,api.github.com,codeload.github.com,objects.githubusercontent.com,uploads.github.com</string>
```

…and **must not** carry `SSL_CERT_FILE`, `NODE_EXTRA_CA_CERTS`,
`REQUESTS_CA_BUNDLE`, or `CURL_CA_BUNDLE`.

Run `./bin/audit-runner-plist.sh` after every plist edit. It exits non-zero
on drift or on a failed broker-TLS probe. Wire it into the G2 gate when
the disposition graduates from per-host to fleet-wide.

## Why this state

### The failure mode we're defending against

`NFM-4567` shipped a local MITM CA (`NFM-4567 Local Broker MITM CA`) used
by a mitmproxy at `127.0.0.1:7897` to intercept GitHub-side TLS for
diagnostics. The CA's broker certificate was mis-issued; the runner's
`broker.actions.githubusercontent.com` handshake failed with `ERR_CERT_AUTHORITY_INVALID`
for ~62 hours (NFM-4628 → NFM-4333 G2 freeze).

`NFM-4630 Path A` initially fixed this by emptying `HTTPS_PROXY` /
`HTTP_PROXY` and setting `NO_PROXY=*` — turning off the mitm for everything
the runner touches. That worked for the broker but **broke every other
non-GitHub mitm path** (PyPI behind the proxy, internal services, etc.).

### The disposition NFM-4712 actually shipped (Layer-1)

Keep `HTTPS_PROXY=http://127.0.0.1:7897` so non-GitHub traffic still
traverses mitm (where it's useful). Add an explicit `NO_PROXY` bypass list
for every GitHub-side host the runner talks to:

- `broker.actions.githubusercontent.com` (job dispatch)
- `*.actions.githubusercontent.com` (artifact upload / download)
- `*.blob.core.windows.net`, `*.githubusercontent.com`, `*.github.com`
- `api.github.com`, `codeload.github.com`,
  `objects.githubusercontent.com`, `uploads.github.com`

…so broker TLS skips the mitm CA entirely and validates against the
system trust store (which has the real CA chain).

### Why both upper- and lower-case variants

`launchd` and the various language runtimes (.NET `HttpClient`, Python
`urllib`, Go `net/http`, Node `fetch`) do not agree on which case is
authoritative. The runner process is itself a .NET binary; .NET's
`HttpClient` reads `http_proxy` / `https_proxy` (lowercase), and the
subprocesses it spawns (`pip`, `npm`, `docker`, `gh`, `curl`) read
`HTTP_PROXY` / `HTTPS_PROXY`. We set both to be defensive — see the
audit script's `EXPECTED_PROXY_KEYS` and `EXPECTED_BYPASS_KEYS` lists.

## Recipe — apply this to a new runner

1. **Edit the LaunchAgent plist.** Mirror the `EnvironmentVariables` block
   from `~/Library/LaunchAgents/actions.runner.Etoile04-nucpot.wenjiedeMac-Studio.plist`
   on the existing host. Keep all four `*_proxy` variants; do **not**
   drop any.
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

   Expect: `[audit-runner-plist] OK: <path> matches NFM-4712 safe template`
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

5. **Append the broker hosts to `scutil --proxy` ExceptionsList.** This
   is the macOS **system** proxy bypass — required for the `.NET`
   `HttpClient` inside the runner process, which respects `scutil` settings
   independently of the LaunchAgent env. See **Sub-task 2** below for the
   exact `networksetup -setproxybypassdomains` incantation. This step
   requires interactive sudo and cannot be done by the harness.

## Drift detection

`bin/audit-runner-plist.sh` asserts three things:

| Check | Failure mode caught |
|---|---|
| `HTTPS_PROXY` / `HTTP_PROXY` / `http_proxy` / `https_proxy` = `http://127.0.0.1:7897` (or absent) | Re-introduction of an upstream proxy that re-amplifies the broken CA |
| `NO_PROXY` / `no_proxy` = broker bypass list (or absent) | Loss of the bypass — broker handshake falls back to mitm |
| Forbidden env vars (`SSL_CERT_FILE`, `NODE_EXTRA_CA_CERTS`, `REQUESTS_CA_BUNDLE`, `CURL_CA_BUNDLE`) **absent** | Direct re-amplification of NFM-4567's dead path |
| Live `broker.actions.githubusercontent.com` TLS probe with `NO_PROXY=*` returns a 2xx/3xx/4xx (not 000/7xx) | End-to-end regression — the script can extract a healthy dict but the runner's actual env has drifted |

Exit codes:

| Code | Meaning |
|---|---|
| 0 | OK — plist matches template + broker probe succeeded |
| 1 | Drift detected **or** broker probe failed |
| 2 | Plist file not found |
| 3 | `plutil` cannot extract `EnvironmentVariables` (malformed plist) |

Designed to wire into the G2 gate; see `nfm-4712` thread for the
proposal.

## Dead paths — what NOT to do

These were tried during the NFM-4628 → NFM-4630 → NFM-4712 incident and
all closed dead. Do not relitigate them.

- ❌ **`security add-trusted-cert -d -r trustRoot -k … <ca.crt>` headless.**
  The admin password dialog does not pop under harness/sudo context;
  NFM-4567 closed dead with `rc=124` timeouts.
- ❌ **Re-issuing the mitm CA with `nameConstraints`.** NFM-4592 closed dead
  — `curl` / .NET ignore `nameConstraints` at TLS handshake time even when
  the local CA store accepts the issuance. The validation happens before
  nameConstraints are evaluated.
- ❌ **Setting `HTTPS_PROXY=http://127.0.0.1:7897` only on the runner
  process and hoping mitm signs the broker cert correctly.** This was the
  pre-NFM-4628 state; it amplified the broken CA. The fix is the bypass
  list, not removing the proxy.
- ❌ **Stricter Layer-2 (`HTTPS_PROXY=''` + `NO_PROXY='*'`).** This is what
  NFM-4630 Path A landed originally and what the home-directory
  `~/Library/LaunchAgents/README_NFM4712.md` still describes. It works
  for the runner but breaks every non-GitHub mitm path. The disposition
  in this document (Layer-1) is the explicit trade-off — see NFM-4712
  thread for the discussion.
- ❌ **`launchctl kickstart -k <service>` to apply env edits.** Re-runs
  under cached env; use `unload && load`.

## Sub-task 2 — interactive user-owned follow-up

The disposition above gets the runner back online (Layer-1 applied
2026-09-11 15:46Z). Two follow-ups remain that require interactive
sudo and cannot be done by the harness — see
`mac-host-root-ops-osascript-pattern.md`.

### Step A — append broker hosts to `scutil --proxy` ExceptionsList

```bash
current=$(/usr/sbin/scutil --proxy | awk -F': ' '/ExceptionsList/{flag=1; next} flag && /0 :/ {gsub(/^[ \t]+|[,"]/,""); print}' | tr '\n' ',' | sed 's/,$//')
sudo networksetup -setproxybypassdomains Ethernet "$current,broker.actions.githubusercontent.com,*.actions.githubusercontent.com"
/usr/sbin/scutil --proxy | grep -A 8 'ExceptionsList'
```

### Step B — interactive CA purge (after Step A)

```bash
security delete-certificate -c "NFM-4567 Local Broker MITM CA" ~/Library/Keychains/login.keychain-db
security find-certificate -c "NFM-4567"   # expect: could not be found
```

### Step C — regression check after purge

```bash
# Direct broker TLS — still works (NO_PROXY bypass unaffected)
curl -sS -o /dev/null -w '%{http_code}\n' --max-time 8 https://broker.actions.githubusercontent.com/_apis/v1/_health

# Mitm-dependent tooling — still works if 7897 is up
curl -x http://127.0.0.1:7897 -sS -o /dev/null -w '%{http_code}\n' --max-time 8 https://pypi.org/simple/

# Audit drift detector
~/scripts/audit-runner-plist.sh
```

## E2E test plan

| Step | Owner | Pass criterion |
|---|---|---|
| `bin/audit-runner-plist.sh` exits 0 on the live plist | RE (this PR) | stdout `OK: … matches NFM-4712 safe template`; exit 0 |
| `compare <merge-base>...<deploy-sha>` shows both new files | RE (this PR) | `bin/audit-runner-plist.sh` and `docs/host-hardening-nfm4712.md` listed as added |
| `git log -1 --format=%s` on deployed branch shows this PR's squash | RE (this PR) | subject includes `NFM-4712` and `#1XXX` (the PR number) |
| `~/scripts/audit-runner-plist.sh` re-run after deploy exits 0 | RE (this PR) | same as step 1 |
| Sub-task 2 (Steps A–C) run interactively by user | user (lwj04) | `scutil --proxy` ExceptionsList contains broker hosts; `security find-certificate -c "NFM-4567"` returns not-found; post-purge regression check passes |
| NFM-4712 closure after Sub-task 2 | RE + user | NFM-4712 PATCH to `done` with both layers' evidence in thread |

## Related

- NFM-4628 (closed) — acute incident; runner offline 62h
- NFM-4630 (closed) — Path A fix; this disposition supersedes with a
  narrower bypass
- NFM-4567 / NFM-4592 (closed dead) — do-not-retry
- NFM-4600 (closed) — `launchctl kickstart -k` env-cache failure mode
- NFM-4712 (open) — parent issue; final closure depends on this PR +
  Sub-task 2
- NFM-4732 (open) — this PR's child issue