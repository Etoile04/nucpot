---
name: self-hosted-runner-recovery
description: >-
  Recovery procedure for the self-hosted macOS runner host
  (`wenjiedeMac-Studio`, `[self-hosted, production]`, ARM64). Covers two
  failure modes: (a) `mkdir: /Users/runner: Permission denied` / toolcache
  write failures (filed by NFM-2243), and (b) `ETIMEDOUT github.com:443` /
  `socket hang up` while downloading setup-* tarballs (filed by NFM-2754).
  Both manifest at `actions/setup-python@v7` and any step that downloads
  from `github.com`.
---

# Triage decision

Before going to either symptom section, identify which failure mode is
active — the fix differs sharply:

| Symptom in step log                                  | Go to                                          |
| ---------------------------------------------------- | ---------------------------------------------- |
| `mkdir: /Users/runner: Permission denied`            | "Permission failure mode" below                |
| `socket hang up` / `ETIMEDOUT 20.205.243.166:443`    | "Network failure mode" below                   |
| Both                                                 | Permission first; network re-check after `chown` regains toolcache |

If neither pattern matches, the failure is in the workflow content itself
(not the host) — check the actual job step output, not the action's
boilerplate.

# Self-Hosted Runner Recovery (macOS ARM64)

## Symptom (Permission failure mode)

`actions/setup-python@v7` (and any other GHA tool that writes to the runner
toolcache, e.g. `actions/setup-node`, `actions/setup-go`) fails with:

```
##[error]mkdir: /Users/runner: Permission denied
##[error]The process '/bin/bash' failed with exit code 1
```

The Python tarball downloads successfully — the failure is in the
**post-extract install** into `/Users/runner/hostedtoolcache`. Any workflow
on the affected self-hosted runner that uses a `setup-*` action can hit the
same line; this is **not** specific to `prod-deploy-event-collector.yml`.

This pattern is reproducible: the next scheduled / manual run of any
self-hosted workflow that calls a `setup-*` action will fail with the
identical line until the host is repaired.

## Affected Workflows

Workflows in this repo that `runs-on: [self-hosted, production]` (the
`wenjiedeMac-Studio` ARM64 runner):

- `prod-deploy-event-collector.yml` (C6.1 OKR collector, NFM-2109)
- `production-deployment.yml`
- `staging-deploy.yml`
- `run-migration.yml`
- `collect-prod-deploy-events.yml` (legacy alias)

`site-monitor.yml` and all `Batch N CI` / `ci.yml` workflows run on
`ubuntu-latest` and are **not** affected.

## Diagnosis (Permission failure mode)

SSH into the runner host (`wenjiedeMac-Studio`) as a user with privilege
on `/Users/runner`:

```bash
# 1. Confirm the runner service user is registered.
ls -ld /Users/runner

# Expected on a healthy host:
#   drwxr-xr-x  runner  staff  ...  /Users/runner
# If the directory is missing OR owned by another user (e.g. root, the
# operator's personal account, or has mode 0755 owned by `lwj04`), the
# toolcache install step will fail.

# 2. Identify the running runner service user.
sudo ps -o user= -p $(pgrep -f "runsvc" | head -1)

# 3. Confirm the runner service can write to its home.
sudo -u runner sh -c 'touch /Users/runner/.probe && rm /Users/runner/.probe && echo writable'

# 4. (If the runner is online) cross-check the registered runner name.
gh api repos/Etoile04/nucpot/actions/runners \
  --jq '.runners[] | "\(.name) online=\(.busy==false) status=\(.status)"'
```

The fastest single-command diagnostic is `ls -ld /Users/runner` — the
ownership / perms are the root cause; everything else is downstream
symptoms.

## Root Cause (Permission failure mode)

`actions/setup-python@v7` writes the installed interpreter to
`${RUNNER_TOOL_CACHE:-${HOME}/hostedtoolcache}`. On macOS, the runner
service's `HOME` is typically `/Users/runner` and `RUNNER_TOOL_CACHE` is
left empty, so the action computes `/Users/runner/hostedtoolcache` and
attempts to `mkdir -p` that prefix. If `/Users/runner` itself is missing,
owned by another UID, or unwritable, the mkdir fails before any toolcache
work happens.

Common triggers:

- The host's `/Users/runner` was created when the runner was first
  registered, then later altered (manual `rm -rf`, privilege change during
  macOS upgrade, `dscl` deletion, etc.).
- A macOS update reset the service-account profile (Sonoma → Sequoia has
  been observed to flatten service users under some MDM configs).
- The `runner` service was re-registered under a different macOS user
  without re-creating its home directory.

## Recovery (Permission failure mode)

Pick **one** of the two options below. Option A is the cheaper fix; Option
B is the canonical fix when ownership is fundamentally wrong (e.g., the
user no longer exists).

### Option A — restore ownership in place (faster)

```bash
sudo chown -R runner:staff /Users/runner
sudo chmod 755 /Users/runner
```

Verify:

```bash
ls -ld /Users/runner
# drwxr-xr-x  runner  staff  ...  /Users/runner

sudo -u runner sh -c 'mkdir -p /Users/runner/hostedtoolcache && echo writable'
```

Then re-run any self-hosted workflow (or wait for the next cron tick):

```bash
gh workflow run prod-deploy-event-collector.yml \
  --repo Etoile04/nucpot --ref main
gh run watch --repo Etoile04/nucpot
```

The **first** run will take ~80s longer than usual (Python 3.12 tarball
re-download + extract); subsequent runs hit the warm cache.

### Option B — re-register the runner (canonical)

Use when the `runner` macOS user no longer exists or Option A fails to make
the toolcache writable.

```bash
# On the host, remove the current runner registration.
cd ~/actions-runner   # or wherever the runner is installed
sudo ./svc.sh stop
sudo ./svc.sh uninstall
./config.sh remove --unattended \
  --token "${GHA_RUNNER_REGISTRATION_TOKEN:?set me}"

# Recreate the service user with a fresh home.
sudo dscl . -create /Users/runner
sudo dscl . -create /Users/runner UniqueID 504
sudo dscl . -create /Users/runner PrimaryGroupID 20
sudo dscl . -create /Users/runner UserShell /bin/bash
sudo dscl . -create /Users/runner RealName "GitHub Actions Runner"
sudo mkdir -p /Users/runner
sudo chown -R runner:staff /Users/runner

# Re-register and start the service.
./config.sh --unattended \
  --url https://github.com/Etoile04/nucpot \
  --token "${GHA_RUNNER_REGISTRATION_TOKEN:?set me}" \
  --labels "self-hosted,production,macOS,ARM64"
sudo ./svc.sh install
sudo ./svc.sh start
```

Verify the runner shows `online` and `busy=False` in:

```bash
gh api repos/Etoile04/nucpot/actions/runners \
  --jq '.runners[] | select(.name=="wenjiedeMac-Studio") | "\(.name) status=\(.status) busy=\(.busy)"'
```

## Prevention (Permission failure mode)

Two small follow-ups worth doing once the host is healthy:

1. **Pin setup-python/python-version in workflows.** Floating
   `python-version: "3.12"` re-downloads on each host cold-start. Pinning
   to a patch version (`python-version: "3.12.10"`) lets the runner cache
   the artifact across runs. Optional, not a substitute for a healthy
   host.

2. **Health-probe the host before declaring it ready.** Add a one-line
   GHA step in `production-deployment.yml`:

   ```yaml
   - name: Sanity-check runner home is writable
     run: |
       test -w "$HOME" || {
         echo "::error::runner home $HOME not writable; toolcache setup will fail."
         echo "::error::See docs/skills/self-hosted-runner-recovery.md."
         exit 1
       }
   ```

   This turns the next `/Users/runner` regression into a **before**
   `setup-python` failure with a clear runbook pointer, instead of a
   cryptic `mkdir` failure midway through tarball install.

## Symptom (Network failure mode)

`actions/setup-python@v7` (and any other GHA step that downloads tools or
artifacts from `github.com`) fails with one of these patterns:

```
Download from "https://github.com/actions/python-versions/releases/download/...tar.gz"
socket hang up
socket hang up
##[error]connect ETIMEDOUT 20.205.243.166:443
```

The IP `20.205.243.166` is GitHub's CDN edge. The host's DNS resolves
`github.com` correctly, but the TCP `connect()` to port 443 hangs until
the per-request timeout fires. The Python tarball **never starts
downloading** — the failure is pre-TCP, not during install.

This pattern is reproducible: every scheduled / manual run of any
self-hosted workflow that calls a `setup-*` action will fail with the
identical timeout until the host's outbound HTTPS path is repaired.

Filed by [NFM-2754](https://github.com/Etoile04/nucpot/issues/2754) on
2026-08-09 after 5+ consecutive `Prod Deploy Event Collector` failures
starting at 20:04 UTC.

## Diagnosis (Network failure mode)

SSH into the runner host (`wenjiedeMac-Studio`) as a user with privilege
on the runner service. Run the steps **in order** — the first one that
hangs or errors is the root cause.

```bash
# 1. Confirm the host can reach github.com at all (operator-level).
curl -v --max-time 10 https://github.com
# Expect: HTTP 200 or 301. A hang means the host has lost outbound
# HTTPS to GitHub; everything else is downstream.

# 2. Confirm DNS resolves.
dig +short github.com
# Expect: one or more A records. If empty, DNS is broken — check
# /etc/resolv.conf and any system-wide VPN client.

# 3. Confirm the runner service user has the same network access.
sudo -u runner curl -v --max-time 10 https://github.com
# If the operator-level curl works but the runner curl fails, the
# runner service is sandboxed (Little Snitch / LuLu / macOS network
# filter / per-process `ALLOW/DENY` rule).

# 4. Check for proxy / VPN / firewall override.
scutil --proxy
# Look for HTTPProxy, HTTPSProxy, SOCKSProxy, ProxyAutoConfigURL.
# If those are set unexpectedly, the runner inherits them via
# environment. A captive-portal PAC file can also silently break
# outbound HTTPS.

# 5. Check the default route and link state.
netstat -rn | head -10
ifconfig en0   # or whichever interface is active
# Look for an IP, a default route, and `status: active`. If the
# interface is `status: inactive`, the link is down.

# 6. (If the runner is online) check the GHA service logs.
sudo log show --predicate 'process == "Runner.Listener"' --last 1h
```

The fastest single-command diagnostic is
`curl -v --max-time 10 https://github.com` from the host. If that hangs,
the host has outbound HTTPS trouble; everything else is downstream.

## Root Cause (Network failure mode)

The macOS runner host is in a degraded network state. Common triggers:

- The Mac Studio went to sleep and the wifi/ethernet link dropped.
- A VPN tunnel went down (split-tunnel dropping 20.205.243.166 from
  scope).
- A host firewall (Little Snitch, LuLu, pf rule) was toggled to deny
  the runner process.
- macOS changed the default network interface (ethernet unplugged,
  wifi joined a captive portal, USB-tethering toggled).
- The GHA runner service restarted into a sandboxed state.
- A DNS-level block (corporate VPN, `dnsmasq` override) resolving
  `github.com` to a blackhole IP.

## Recovery (Network failure mode)

Pick **one** of the options below. Option A is the cheapest first
attempt; Option B handles a stuck runner service; Option C is for
host-firewall blocks.

### Option A — wake + re-link the network (fastest)

```bash
# Wake the display (the Mac Studio may have slept).
caffeinate -u -t 5

# Bounce the active interface (replace en0 with whatever `ifconfig`
# shows as `status: active`).
sudo ifconfig en0 down
sudo ifconfig en0 up

# Or, if on wifi, rejoin from the CLI or GUI.
sudo networksetup -setairportnetwork en0 "<your-ssid>" "<your-password>"

# Verify outbound HTTPS.
curl -v --max-time 10 https://github.com
```

Then re-run any self-hosted workflow:

```bash
gh workflow run prod-deploy-event-collector.yml \
  --repo Etoile04/nucpot --ref main
gh run watch --repo Etoile04/nucpot
```

### Option B — restart the runner service

If the network link is back but the runner process is still stuck:

```bash
cd ~/actions-runner   # or wherever the runner is installed
sudo ./svc.sh restart
```

Verify the runner is `online` and `busy=False`:

```bash
gh api repos/Etoile04/nucpot/actions/runners \
  --jq '.runners[] | select(.name=="wenjiedeMac-Studio") | "\(.name) status=\(.status) busy=\(.busy)"'
```

### Option C — release a host-firewall block

If `curl` from the host works but `sudo -u runner curl` fails, the
runner service is being blocked by a host firewall:

1. Open Little Snitch / LuLu and look for `Runner.Listener` or
   `Runner.Worker` rules that deny outbound 443.
2. Temporarily disable the rule and re-run the workflow.
3. If a custom pf rule is blocking, examine `/etc/pf.conf` and any
   anchor files (typically `/etc/pf.anchors/*`).

## Prevention (Network failure mode)

1. **Health-probe GitHub reachability early.** Add a one-line GHA step
   at the top of every self-hosted workflow:

   ```yaml
   - name: Sanity-check GitHub reachability
     run: |
       curl -fsS --max-time 10 https://github.com >/dev/null || {
         echo "::error::runner cannot reach github.com; check host network."
         echo "::error::See docs/skills/self-hosted-runner-recovery.md (network failure mode)."
         exit 1
       }
   ```

   This turns the next network outage into a **before** `setup-python`
   failure with a clear runbook pointer, instead of a cryptic timeout
   midway through the tarball download.

2. **Keep the host awake.** The Mac Studio self-hosted runner is the
   prod-stack host; sleep is not acceptable. In System Settings →
   Energy:

   - Disable "Put hard disks to sleep when possible".
   - Enable "Wake for network access".
   - Disable "Enable Power Nap" (optional — Power Nap can re-wake on
     timers unrelated to GHA, masking sleep as the failure mode).

3. **Watch the GHA runner service.** `sudo ./svc.sh status` from the
   runner install directory should be a routine check after any host
   reboot or macOS update. A stale `Runner.Listener` log entry is the
   first sign the runner is wedged even before network fails.

4. **Pin setup-python/python-version.** Floating
   `python-version: "3.12"` re-downloads on each host cold-start.
   Pinning to a patch version (`python-version: "3.12.10"`) lets the
   runner cache the artifact across runs. Optional, not a substitute
   for a healthy host.

## Related

- [NFM-2243](/NFM/issues/NFM-2243) — initial failure report (2026-07-30
  collector cron failure, permission failure mode).
- [NFM-2754](/NFM/issues/NFM-2754) — recurrence on a different axis
  (2026-08-09, network failure mode on the same host).
- `docs/architecture/ADR-KR3-prod-emission.md` — C6.1 OKR collector
  architecture and recovery semantics for partial sync-state.
- `docs/skills/post-merge-ci-recovery.md` — wider post-merge verification
  checklist; this runbook is a focused addendum when the failure mode is
  the runner host rather than the repo.
