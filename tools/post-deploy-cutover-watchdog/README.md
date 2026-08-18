# post-deploy-cutover-watchdog

Stale-container watchdog for the NFM-3320 post-deploy cutover system.

## Purpose

Companion to `tools/post-deploy-cutover-assert/assert.sh`. While assert.sh runs **at deploy time** and blocks the workflow on failure, this watchdog runs on a **6-hour cron** and alerts via Feishu when the NFM-3320 condition is detected: a deploy reported success but the running containers were never actually recreated.

## How it works

1. Reads the last line of `~/.nfmd/master-deploy-events.jsonl` to get the most recent deploy SHA and timestamp.
2. Resolves the expected Docker image IDs by tagging `nucpot-prod-{api,lightrag,web}:<sha>`.
3. Inspects each running `nucpot-prod-*` container and compares its Image ID against the expected one.
4. Applies a false-positive guard (AC-3.4): only alerts if the container's `Created` timestamp is **before** the deploy timestamp. If the container was created after the deploy, it stays silent.
5. On stale detection, sends a Feishu webhook alert with full details for each stale service.

All signal sources are **read-only** — the watchdog never modifies containers or the deploy events file.

## Exit codes

| Code | Meaning |
|------|----------|
| 0 | All containers match, or no deploy since container creation |
| 80 | Stale container(s) detected and alert sent |
| 2 | Usage error |

## Cron setup

The watchdog runs via `.github/workflows/site-monitor.yml` on the Mac Studio self-hosted runner:

```
17 */6 * * *   # Every 6 hours, offset 17min
```

## Usage

```bash
# Normal run (cron)
./watchdog.sh

# Dry-run: print verdict without sending alert
./watchdog.sh --dry-run

# Custom deploy events file
./watchdog.sh --deploy-jsonl /path/to/events.jsonl
```

## Environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| `ALERT_WEBHOOK` | No | Feishu incoming-webhook URL. If unset, prints alert to stderr and exits 0. |
| `DEPLOY_JSONL` | No | Path to master-deploy-events.jsonl. Defaults to `~/.nfmd/master-deploy-events.jsonl`. |
| `SERVICES` | No | Comma-separated container names. Defaults to the 4 nucpot-prod-* services. |

## Running tests

```bash
pytest tools/post-deploy-cutover-watchdog/test_watchdog.py -v
```

Tests use a fake `docker` shim (no Docker required).
