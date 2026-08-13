# Docker Raw Guard

Docker.raw disk usage monitor, alerter, and auto-pruner for macOS Docker Desktop.

**Part of NFM-3019.** Prevents Docker.raw from silently consuming the entire host volume.

## Problem

Docker Desktop on macOS stores all container/image data in a single `Docker.raw` disk image. Without monitoring, it can grow to consume the entire host volume (observed: 92 GB actual / 384 GB apparent on a 460 GB volume) and crash the host.

## How It Works

```
docker-raw-guard.sh
├── Measures Docker.raw size via du
├── Reads host volume capacity via df
├── Computes percentage of volume used
├── < 60%  → INFO log, exit 0
├── ≥ 60%  → WARNING log + macOS notification + webhook, exit 1
└── ≥ 80%  → CRITICAL log + auto-prune + notification, exit 2
```

Auto-prune runs `docker system prune -a -f --volumes` and `docker builder prune -a -f`, then logs space recovered.

## Quick Start

```bash
# 1. Install the script
sudo cp docker-raw-guard.sh /usr/local/bin/
sudo chmod +x /usr/local/bin/docker-raw-guard.sh

# 2. Create log directory (user-writable, no sudo needed)
mkdir -p ~/Library/Logs

# 3. Install the launchd agent (runs every 30 minutes)
cp com.nucpot.docker-raw-guard.plist ~/Library/LaunchAgents/
launchctl load -w ~/Library/LaunchAgents/com.nucpot.docker-raw-guard.plist

# 4. Verify it's loaded
launchctl list | grep docker-raw-guard

# 5. Test manually
/usr/local/bin/docker-raw-guard.sh
echo "Exit code: $?"
```

## Configuration

All thresholds are configurable via environment variables. Set them in the plist's `EnvironmentVariables` dict or export in your shell profile.

| Variable | Default | Description |
|---|---|---|
| `DOCKER_RAW_PATH` | `~/Library/Containers/com.docker.docker/Data/vms/0/data/Docker.raw` | Path to Docker.raw |
| `LOG_FILE` | `~/Library/Logs/docker-raw-guard.log` | Metrics log path (user-writable) |
| `ALERT_THRESHOLD` | `60` | Percentage that triggers warning |
| `PRUNE_THRESHOLD` | `80` | Percentage that triggers auto-prune |
| `ALERT_WEBHOOK_URL` | _(empty)_ | Optional webhook URL for alerts (validated before use) |
| `HOST_VOLUME` | `/` | Host volume to measure against |
| `PRUNE_VOLUMES` | `0` | **Set to `1` to also prune anonymous volumes.** Default `0` (safe) — unattended `--volumes` can destroy live prod DB volumes during deploy windows. |
| `DRY_RUN` | `0` | Set to `1` to log what the script *would* run, without invoking docker. |

### Example: custom thresholds in plist

Add to the `EnvironmentVariables` dict in the plist (use **absolute paths** — launchd does not expand `$HOME` or other shell syntax in plist values):

```xml
<key>ALERT_THRESHOLD</key>
<string>50</string>
<key>PRUNE_THRESHOLD</key>
<string>70</string>
<key>ALERT_WEBHOOK_URL</key>
<string>https://hooks.slack.com/services/XXX/YYY/ZZZ</string>
```

## Log Format

Each run appends a single JSON line to `~/Library/Logs/docker-raw-guard.log`:

```json
{"ts":"2026-08-13T05:30:00Z","level":"INFO","msg":"Docker.raw usage 42.10GB / 460.00GB (9.2%)","usage_gb":42.10,"total_gb":460.00,"pct_used":9.2}
```

Fields: `ts` (ISO 8601 UTC), `level` (INFO/WARNING/CRITICAL/ERROR), `msg`, `usage_gb`, `total_gb`, `pct_used`, `extra` (optional — e.g. `recovered_gb=5.20`).

## Docker Desktop Disk Limit

In addition to the monitoring script, configure Docker Desktop's maximum disk image size via `apply-docker-desktop-limit.sh` (idempotent — safe to re-run). The script writes to:

**File:** `~/.docker/desktop/settings.json`
**Key:** `dataDiskMaxSize`

```json
{
  "dataDiskMaxSize": 64424509440
}
```

Value is in bytes. 60 GB = 60 × 1024³ = 64,424,509,440 bytes.

Recommended limit: **60 GB** — well under a 460 GB volume, leaving room for the OS and other services.

```bash
./apply-docker-desktop-limit.sh                 # 60 GiB default
DISK_LIMIT_GB=80 ./apply-docker-desktop-limit.sh   # 80 GiB override
DRY_RUN=1 ./apply-docker-desktop-limit.sh        # preview without writing
```

> **Important:** After running this, **restart Docker Desktop** for the new limit to take effect. The script never restarts Docker Desktop automatically because doing so would terminate every running container, including the nucpot prod / supabase databases.

## Uninstall

```bash
launchctl unload -w ~/Library/LaunchAgents/com.nucpot.docker-raw-guard.plist
rm ~/Library/LaunchAgents/com.nucpot.docker-raw-guard.plist
sudo rm /usr/local/bin/docker-raw-guard.sh
```

## Safety

- **Volumes protected by default:** Auto-prune NEVER passes `--volumes` unless `PRUNE_VOLUMES=1`. Unattended `--volumes` can destroy live prod database volumes during deploy windows — see [[mac-prod-host-docker-automation-hazards]] in project memory.
- **Daemon check:** Auto-prune only runs if `docker info` succeeds — skipped if Docker is down.
- **DRY_RUN:** `DRY_RUN=1` logs what the script would execute, without invoking docker. Use this to verify config before a real run.
- **Webhook validation:** `ALERT_WEBHOOK_URL` is URL-shape-validated before any `curl` call, so a config typo can't inject shell metacharacters.
- **Idempotent:** Safe to run multiple times; prune commands use `-f` (force, no prompts).
- **No external dependencies:** Uses only Docker CLI and standard macOS tools (`du`, `df`, `bc`, `osascript`, `curl`).
- **User-writable log path:** Default log location is `~/Library/Logs/`, writable without sudo; the script `mkdir -p`s the log dir on startup.
- **launchd `$HOME` caveat:** launchd does not expand `$HOME` in plist `EnvironmentVariables`. Use absolute paths in the plist or rely on the script's bash default.
