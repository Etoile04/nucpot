---
name: self-hosted-runner-recovery
description: >-
  Recovery procedure for self-hosted macOS runner host permission failures
  affecting `actions/setup-python@v7`. Use when a GH Actions run fails with
  `mkdir: /Users/runner: Permission denied` (or any
  `toolcache`/`RUNNER_TOOL_CACHE` write failure) on the `wenjiedeMac-Studio`
  runner. Filed by NFM-2243.
---

# Self-Hosted Runner Recovery (macOS ARM64)

## Symptom

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

## Diagnosis

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

## Root Cause

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

## Recovery

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

## Failure Mode 2 — Network timeout (NFM-2754)

`actions/setup-python@v7` downloads the Python tarball from
`github.com` (IP `20.205.243.166`). If the runner host cannot reach
GitHub, the download hangs and eventually fails:

```
Download from "https://github.com/actions/python-versions/releases/download/3.12.10-.../python-3.12.10-darwin-arm64.tar.gz"
socket hang up
socket hang up
##[error]connect ETIMEDOUT 20.205.243.166:443
```

### Diagnosis

```bash
# 1. Confirm the runner can reach github.com.
curl -sS --connect-timeout 10 https://github.com > /dev/null && echo reachable || echo unreachable

# 2. Check DNS resolution.
nslookup github.com

# 3. Check for proxy / VPN interference.
echo $https_proxy $HTTPS_PROXY $ALL_PROXY
scutil --proxy
```

### Root causes

- Transient ISP / DNS outage on the Mac Studio's network.
- VPN or proxy drop that the runner depends on for egress.
- macOS firewall rule blocking outbound HTTPS.
- GFW / regional connectivity restriction (this runner is in China).

### Recovery

Network timeouts are **transient by nature** — no host-level fix required.
Wait for connectivity to restore, then re-trigger the workflow:

```bash
gh workflow run prod-deploy-event-collector.yml \
  --repo Etoile04/nucpot --ref main
```

GitHub Actions cron does **not** backfill missed triggers — every skipped
15-minute window is lost. The `workflow_run` trigger resumes
automatically once the runner is back online and a `Production Deployment`
completes. The primary 5-min bash collector (`Collect Production Deploy
Events`) is unaffected because it does not download from GitHub.

## Failure Mode 3 — Missing /Users/runner (NFM-2754)

On this host the runner service runs as `lwj04` (not a dedicated
`runner` macOS user). The directory `/Users/runner` does not exist and
the `runner` user is not registered in DirectoryService. However,
`actions/setup-python@v7` hardcodes its default `RUNNER_TOOL_CACHE` to
`/Users/runner/hostedtoolcache`, causing:

```
##[error]mkdir: /Users/runner: Permission denied
```

### Workaround (applied in NFM-2754)

Set `RUNNER_TOOL_CACHE` in the workflow env to a `lwj04`-writable path:

```yaml
env:
  RUNNER_TOOL_CACHE: /Users/lwj04/actions-runner-production/toolcache
```

This is a workflow-level workaround, not a host-level fix. It
eliminates the need for a `/Users/runner` directory or `runner` user.

### Canonical fix (optional)

If you prefer the standard runner layout:

1. Create the `runner` macOS user and `/Users/runner` home (Option B
   above).
2. Re-register the runner service under that user.

This is more work and not required — the `RUNNER_TOOL_CACHE` env-var
override is sufficient and avoids sudo.

## Prevention

Two small follow-ups worth doing once the host is healthy:

1. **Pin setup-python/python-version in workflows.** Floating
   `python-version: "3.12"` re-downloads on each host cold-start. Pinning
   to a patch version (`python-version: "3.12.10"`) lets the runner cache
   the artifact across runs. Optional, not a substitute for a healthy
   host.

2. **Health-probe the host before declaring it ready.** Add a one-line
   GHA step in `production-deployment.yml`:

   ```yaml
   - name: Sanity-check runner toolcache is writable
     run: |
       mkdir -p "${RUNNER_TOOL_CACHE:-$HOME/hostedtoolcache}" 2>/dev/null \
       || echo "::warning::RUNNER_TOOL_CACHE not writable; setup-* actions may fail."
   ```

   This turns the next toolcache regression into an early, descriptive
   warning instead of a cryptic failure midway through setup.

## Related

- NFM-2243 — initial failure report (2026-07-30 collector cron failure,
  `mkdir` permission denied on `/Users/runner`).
- NFM-2754 — recurrence (2026-08-09): network ETIMEDOUT followed by
  same `/Users/runner` permission failure. Fixed with
  `RUNNER_TOOL_CACHE` env-var override.
- `docs/architecture/ADR-KR3-prod-emission.md` — C6.1 OKR collector
  architecture and recovery semantics for partial sync-state.
- `docs/skills/post-merge-ci-recovery.md` — wider post-merge verification
  checklist; this runbook is a focused addendum when the failure mode is
  the runner host rather than the repo.
