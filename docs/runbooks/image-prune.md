# Weekly Docker Image Prune Runbook

> **Owning issue:** [NFM-3672](/NFM/issues/NFM-3672)
> **Predecessor:** [NFM-3670](/NFM/issues/NFM-3670) — disk-pressure incident that this cron prevents from recurring.
> **Status:** Implemented and installed on the prod dev host (2026-08-24).

This runbook is the operational source of truth for the weekly
`docker image prune -af` job. The script lives at
[`scripts/cron/nucpot-image-prune.d/nucpot-image-prune`](../../scripts/cron/nucpot-image-prune.d/nucpot-image-prune).
The launchd plist lives at `~/Library/LaunchAgents/com.nucpot.docker-image-prune.plist`.

## Why

NFM-3670 closed when the SRE heartbeat at 2026-08-24T11:28Z detected
production disk at 86% (62 GiB free), up from 81% four hours earlier.
Image accumulation from high deploy velocity (4 deploys/8h) was the
cause — `docker system df` showed 30.53 GB reclaimable across 27
unused images. A one-shot `docker image prune -af` (action #5) reclaimed
29.81 GB (86% → 80%).

At a churn rate of ~3.5 GiB/hr the same pattern would recur in 5–7 days.
The lowest-risk fix is a weekly scheduled prune — Docker reference
counting keeps active images intact; only dangling and un-tagged layers
are reclaimed.

## Schedule

| Field | Value | Rationale |
|-------|-------|-----------|
| Day of week | Sunday | Off-hours — avoids weekday deploy windows (4 deploys/8h). |
| Hour | 03:00 local | Quiet hour; well after any Friday-evening deploys. |
| LaunchAgent label | `com.nucpot.docker-image-prune` | Matches existing `com.nucpot.*` convention. |
| Calendar key | `StartCalendarInterval` | macOS-native periodic scheduling; survives reboots. |

## Files

| Path | Role |
|------|------|
| `scripts/cron/nucpot-image-prune.d/nucpot-image-prune` | **Canonical source-of-truth** (in-repo). Lives on `main`. |
| `/Users/lwj04/.local/bin/nucpot-image-prune` | **Runtime copy** — what the launchd plist actually invokes. Decouples the job from repo worktree churn / branch switches / `git pull`. |
| `~/Library/LaunchAgents/com.nucpot.docker-image-prune.plist` | launchd schedule (per-user). |
| `/Users/lwj04/.local/log/nucpot/image-prune.log` | Log destination (append-only). |

After pulling a new version from `main`, re-sync the runtime copy:

```bash
cp scripts/cron/nucpot-image-prune.d/nucpot-image-prune \
   /Users/lwj04/.local/bin/nucpot-image-prune
chmod +x /Users/lwj04/.local/bin/nucpot-image-prune
```

## Operational guardrails

- **NO `--filter until=...`** — documented as a no-op in this environment
  (Docker re-creates intermediate layers with current timestamps, so the
  time filter matches everything and reclaims nothing). See
  NFM-3670 recovery note + memory `docker-prune-until-filter-is-a-noop`.
- **`--all -f` is safe** — Docker reference counting protects the
  active image set; only dangling + un-tagged layers are reclaimed.
- **Do NOT run during deploy windows** — schedule is fixed to Sunday
  03:00; ad-hoc `launchctl start com.nucpot.docker-image-prune` should
  only be used for verification, never during a deploy.
- **Log rotation** — prune logs older than `NUCPOT_PRUNE_RETENTION` (default 30) days.

## Install procedure (already applied on the prod dev host)

```bash
# 1. Ensure the canonical script is on disk (after merge to main).
#    Skipped on this host — the file already lives in
#    /Users/lwj04/Projects/nucpot/scripts/cron/nucpot-image-prune.d/.

# 2. Place the runtime copy where the plist expects it.
cp /Users/lwj04/Projects/nucpot/scripts/cron/nucpot-image-prune.d/nucpot-image-prune \
   /Users/lwj04/.local/bin/nucpot-image-prune
chmod +x /Users/lwj04/.local/bin/nucpot-image-prune

# 3. Ensure the log directory exists.
mkdir -p /Users/lwj04/.local/log/nucpot

# 4. Install the plist (the plist ProgramArguments references
#    /Users/lwj04/.local/bin/nucpot-image-prune — see Files table).
launchctl unload ~/Library/LaunchAgents/com.nucpot.docker-image-prune.plist 2>/dev/null || true
cp <plist> ~/Library/LaunchAgents/com.nucpot.docker-image-prune.plist
plutil -lint ~/Library/LaunchAgents/com.nucpot.docker-image-prune.plist

# 5. Load the plist (persisted across reboots via -w).
launchctl load -w ~/Library/LaunchAgents/com.nucpot.docker-image-prune.plist
```

## Verify

```bash
# 1. Job is registered with launchd.
launchctl list | grep nucpot.docker-image-prune
#   expected: "0\t-\tcom.nucpot.docker-image-prune"

# 2. Next-run time is correct (should show next Sunday 03:00 local).
launchctl print gui/$UID/com.nucpot.docker-image-prune | grep -A2 "next run"

# 3. Smoke-run the script and confirm the log file is written.
launchctl start com.nucpot.docker-image-prune
sleep 5
tail -30 /Users/lwj04/.local/log/nucpot/image-prune.log
#   expected: pre/post df, pre/post docker system df, "done (rc=0)".

# 4. Confirm services are still healthy (api on :8001, web on :3000).
curl -fsS --max-time 5 http://localhost:3000/api/v1/health || echo "API DOWN"
curl -fsS --max-time 5 -o /dev/null -w "%{http_code}\n" http://localhost:3000/
```

## Rollback / uninstall

```bash
# Stop and remove the schedule.
launchctl unload ~/Library/LaunchAgents/com.nucpot.docker-image-prune.plist
rm ~/Library/LaunchAgents/com.nucpot.docker-image-prune.plist

# The script + log are not auto-removed (they're harmless), but can be
# cleaned up if desired:
#   rm /Users/lwj04/Projects/nucpot/scripts/cron/nucpot-image-prune.d/nucpot-image-prune
#   rm /Users/lwj04/.local/log/nucpot/image-prune.log
```

## Expected outcomes after first scheduled run

- `df -h /System/Volumes/Data` shows reclaimable rows in `docker system df`
  dropping below 1 GB between consecutive prunes (NFM-3670 baseline: 3.4 GB
  reclaimable at issue creation).
- **No service disruption** — api / web / worker / lightrag remain healthy;
  prune only affects un-tagged layers.

## Related

- [NFM-3670](/NFM/issues/NFM-3670) — disk-pressure incident.
- [NFM-3672](/NFM/issues/NFM-3672) — implementing issue (this cron).
- [docs/runbooks/v2-rollout.md](v2-rollout.md) — sibling runbook.
