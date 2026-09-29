# post-deploy-cutover-watchdog

Stale-container watchdog for the NFM-3320 post-deploy cutover system, plus
the NFM-5269 container sanity check.

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

## container_sanity_check.sh — NFM-5269 / NFM-5270

Second check in the same 6-hour job, covering the two failure classes the
2026-09-29 AutoVC outage proved invisible:

1. **Restart loops** — any container in `restarting` state, or with
   `RestartCount >= 10` while its last start is younger than 600s (a live
   loop restarts every few seconds). A high-but-stale count on a stable
   container (the restored NFM-5269 containers carry 125) is **INFO-only**
   with a recreate hint — it must not page.
2. **Empty bind sources** — docker auto-creates a deleted bind-mount source
   as an *empty* directory, so the deletion is invisible until the next
   daemon restart breaks the stack. Every bind source of every in-scope
   container is asserted present and non-empty.

Scope defaults to `--filter name=nucpot` (prod + staging + autovc +
supabase_db_nucpot). User-facing detection of an AutoVC outage is the
10-minute URL probe (`scripts/health_check.py` AutoVC target); this check
is host-side defense-in-depth and also sees non-user-visible loops
(e.g. a celery worker crash-looping while the API serves). Raising its
cadence means more self-hosted checkouts on the prod host — deliberately
not done in NFM-5270.

| Variable | Default | Description |
|----------|---------|-------------|
| `CONTAINER_FILTER` | `name=nucpot` | `docker ps` filter defining the container scope. |
| `RESTART_ALERT_THRESHOLD` | `10` | RestartCount at which the churning check engages. |
| `RESTART_LOOP_WINDOW_SECONDS` | `600` | Last-start age under which a high count counts as an active loop. |
| `BIND_EMPTY_ALLOWLIST` | *(empty)* | Comma-separated exact host paths whose emptiness is legitimate. |
| `ALERT_WEBHOOK` | *(empty)* | As above; unset ⇒ alert to stderr, exit 0. |

Exit codes: `0` clean/operational skip (docker unusable is never an alarm),
`81` violation(s) detected and alert sent, `2` usage error.

## Running tests

```bash
pytest tools/post-deploy-cutover-watchdog/test_watchdog.py \
       tools/post-deploy-cutover-watchdog/test_container_sanity_check.py -v
```

Tests use a fake `docker` shim (no Docker required).
