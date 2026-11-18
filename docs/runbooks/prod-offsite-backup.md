# Runbook — nucpot-prod off-site backups (Mac Studio → z203)

> NFM-6018 · established 2026-11-16 · owner: SRE Monitor lane
>
> Problem this solves: the 4 core nucpot-prod data volumes had **zero off-host
> copy** — the on-host stamp chain (NFM-4749/4750) protects against logical
> mistakes (bad migration, dropped table) but dies with the Mac Studio disk.
> This lane ships every nightly stamp + a Redis snapshot to z203
> (ThinkStation, Tailscale `100.70.30.21`), ~15 min away by wire, and keeps
> them restorable via a scripted drill.

## Topology

```
Mac Studio (wenjiedeMac-Studio)                    z203-thinkstation-p3-tower
  nucpot-prod containers (23-container fleet)        1.8T disk, ~834 Gi free
  └─ 02:00 local  nfm-g2 run-backup.sh ──►  /var/lib/nfmdeploy/nucpot-backups/<stamp>/
                                             (crontab `0 2 * * *`, keep=3, fail-closed manifest)
  └─ 02:30 local  ~/bin/nfm-6018-z203-offsite.sh  ──rsync+ssh over Tailscale──►
                                             ~/backups/nucpot-mac-studio/stamps/<stamp>/
                                             (LaunchAgent com.nfm.6018-z203-offsite, keep=14)
```

## What is backed up, and how consistently

| Artifact | Producer | Consistency mechanism | Size |
|---|---|---|---|
| `db/nfm_db.dump` — **logical Postgres dump (the restore path)** | gated `docker exec nucpot-prod-db pg_dump -Fc` (run-backup.sh, NFM-4749 Plan A) | pg_dump consistent snapshot | ~8.7 MB |
| `volumes/nucpot-prod_prod-db-data.tar.gz` — physical PG data dir | read-only alpine tar via full gate (NFM-4750 Plan B) | crash-consistent-ish; **forensics/structure only, NOT the restore path** | ~40 MB |
| `volumes/nucpot-prod_prod-uploads.tar.gz` | same | file store; tar snapshot | ~80 MB |
| `volumes/nucpot-prod_lightrag-data.tar.gz` | same | near-empty today — the RAG index lives in nfm_db since the PG-KV storage migration; volume coverage is automatic if the index ever moves back | 87 B |
| `volumes/nucpot-prod_migration-audit.tar.gz` | same | append-only audit JSONL | 87 B |
| `redis/dump.rdb` — **Redis snapshot (added by this lane)** | `redis-cli --rdb` fork-based EOF snapshot over host TCP 127.0.0.1:6380 from a disposable `redis:7-alpine` container | fork-consistent RDB; **no exec into prod containers, no prod volume mounts, no G2 gate change, no restarts** | ~0.8 MB |

Every stamp also carries:

- `MANIFEST.sha256` — written by run-backup.sh **only on fail=0** (NFM-4749 AC#3/#4: no false-green manifests, `current` only flips on good stamps)
- `redis/redis-meta.json` — snapshot epoch, sha256, dbsize, key sample (drill + restore verification reference)
- `ship-meta.json` — inner file counts + byte totals of the volume tars (drill reference)

## Cadence + retention

| Layer | Cadence | Retention | Footprint |
|---|---|---|---|
| On-host stamps (Mac Studio) | nightly 02:00 local (existing crontab) | keep **3** | ~361 MB (unchanged by this lane) |
| z203 stamps | shipped nightly 02:30 local | keep **14** daily | ~123 MB/night → **~1.8 GB steady state** (vs 834 Gi free) |
| Restore drill scratch | on demand | reports kept, scratch deleted | ~200 MB transient during drill |

**Disk budget (both ends, stated before first run 2026-11-16):** Mac Studio
source side is ~zero net new (rsync reads the stamp in place; the Redis RDB is
~1 MB transient in /tmp; only the log grows). z203 target side is ~1.8 GB
steady-state inside 834 Gi free — a 10× data-growth scenario still lands under
20 GB. Mac Studio trip-line context (85% / 45 Gi band, NFM-5801/NFM-5171): this
lane does not move either trip-wire.

**Wire reality:** the Tailscale path to z203 measured ~115–125 KB/s (WAN v6
direct). Full stamp ≈ 123 MB ≈ 15–20 min per night. Fine at 02:30; do not
schedule tighter turnarounds against the 02:00 producer without re-measuring.

## Schedules (and how to verify they are armed)

| Job | Schedule | Registered at | Verify by |
|---|---|---|---|
| On-host stamp | `0 2 * * *` crontab (lwj04) | NFM-4749/4750 chain | `tail ~/.nfm-backup.log` — nightly `NFM-G2-BACKUP ... fail=0` |
| Off-site ship | LaunchAgent `com.nfm.6018-z203-offsite`, 02:30 local, RunAtLoad catch-up | `~/Library/LaunchAgents/` | `launchctl list | grep 6018`; `tail ~/Library/Logs/nfm-6018-offsite.log` |

## Monitoring contract (SRE heartbeat ticks)

`~/Library/Logs/nfm-6018-offsite.log` — every run appends one terminal line:

- healthy: `NFM-6018-OFFSITE stamp=<YYYYMMDD-HHMM> fail=0 age=<h>h z203_bytes=<n> redis_bytes=<n> redis_sha=<12> keep=14 verified=yes`
- any failure: `NFM-6018-OFFSITE fail=1 reason=<why> stamp=<stamp or ?>`

Trip-wire: the last line must carry `fail=0` and be **younger than 26 h**.
Stale or `fail=1` → investigate (reason field names the stage: stamp-stale
means the 02:00 producer missed, rsync/ssh reasons mean z203/Tailscale trouble,
redis-* reasons mean the snapshot path broke). `skip=already-running` is a
harmless overlap, not a failure. On-call escalation: page the SRE lane per
AGENTS.md §4 if two consecutive nights fail.

## Restore procedures

### Full Postgres restore (the primary path)

```bash
# into a scratch container (the drill does exactly this — see below)
docker run -d --name restore-pg -e POSTGRES_PASSWORD=x pgvector/pgvector:pg16
docker exec restore-pg psql -U postgres -c "CREATE ROLE nfm LOGIN SUPERUSER" \
                          -c "CREATE DATABASE nfm_db OWNER nfm"
docker cp <stamp>/db/nfm_db.dump restore-pg:/tmp/nfm_db.dump
docker exec restore-pg pg_restore -U nfm -d nfm_db --no-owner --no-privileges /tmp/nfm_db.dump
```

- Image matters: prod DB is `pgvector/pgvector:pg16` — the dump creates
  extensions (incl. `vector`); a plain `postgres:16` fails on them.
  z203 keeps `pgvector/pgvector:pg16` pre-pulled for this reason.

### Redis restore

Copy `<stamp>/redis/dump.rdb` into a fresh redis data dir and start the
container (`redis-server` loads RDB at boot). Verify `DBSIZE` matches
`redis/redis-meta.json` and the sampled keys exist. Caveat: the snapshot is
~30 min **newer** than the PG stamp (taken at ship time); it captures Celery
broker state — a mixed restore may re-deliver in-flight tasks, which is
acceptable for a broker, not for a source of truth.

### Uploads / LightRAG volume restore

`tar xzf <stamp>/volumes/<volume>.tar.gz -C <fresh-volume-mountpoint>/` —
verify with `ship-meta.json` (inner file counts + byte totals).

### z203 as DR site

z203 already runs docker with `pgvector/pgvector:pg16` + `redis:7-alpine`
pre-pulled, and holds the full stamp set under `~/backups/nucpot-mac-studio/`.
A full scratch rebuild of the data layer (PG + Redis + uploads + RAG index) is
possible entirely on z203 without touching the Mac Studio.

## Restore drill (the AC — a backup never restored is not a backup)

Script: `scripts/host/offsite-backup/nfm-6018-restore-drill-z203.sh` (nucpot
repo) — installed on z203 at
`~/backups/nucpot-mac-studio/drill/nfm-6018-restore-drill.sh`.

```bash
ssh z203 'bash ~/backups/nucpot-mac-studio/drill/nfm-6018-restore-drill.sh'   # drills current stamp
ssh z203 'bash ~/backups/nucpot-mac-studio/drill/nfm-6018-restore-drill.sh 20261116-0200'  # explicit stamp
```

What it proves, per run: manifest verifies on z203 → pg_restore completes
rc=0 into a scratch pgvector:pg16 → restored table count equals the dump's
declared TABLE entries → ANALYZE + exact row counts on the 3 largest tables +
spot read → scratch redis loads the RDB with dbsize + sampled-key match →
uploads/LightRAG tars extract with file-count/byte-total match. Scratch
namespace only (`nfm6018-drill-*`); everything torn down after; report in
`drill/reports/`. Terminal line: `NFM-6018-DRILL stamp=<s> result=PASS`.

Cadence: first run 2026-11-16 (evidence on NFM-6018); re-run monthly and after
any schema-changing migration lands on main.

## Manual operations

```bash
# run the ship now (safe any time; idempotent; fails closed on stale stamp)
~/bin/nfm-6018-z203-offsite.sh

# inspect what z203 holds
ssh z203 'ls -1dt ~/backups/nucpot-mac-studio/stamps/*/; readlink ~/backups/nucpot-mac-studio/current'

# re-load the schedule after editing the plist
launchctl unload ~/Library/LaunchAgents/com.nfm.6018-z203-offsite.plist && \
launchctl load   ~/Library/LaunchAgents/com.nfm.6018-z203-offsite.plist
```

Retention edits: `KEEP=14` (z203) in `~/bin/nfm-6018-z203-offsite.sh`;
on-host keep=3 belongs to the NFM-4749 crontab line (`--keep 3`), not this
lane. z203 prune only removes stamps whose `MANIFEST.sha256` is present —
a partial/failed ship is left for forensics and ages out manually.

## Fences this lane operates under

1. **No prod container restarts for backup purposes.** All read paths are
   observational: gated `pg_dump` exec, read-only volume tars, TCP Redis
   snapshot. Nothing here stops, restarts, or redeploys prod.
2. **No G2 gate changes.** The redis-data volume is deliberately NOT added to
   the run-backup.sh allowlist; the TCP `--rdb` path avoids needing it.
   Changing the allowlist stays a root-owned operator-card action.
3. **No trip-line movement.** Mac Studio: no local landing (streamed,
   in-place reads). z203: ~1.8 GB steady state inside 834 Gi free.
4. **Fail-closed everywhere.** A stale or manifest-failing stamp is never
   shipped; z203's `current` only flips after byte-verification; prune only
   touches fully-shipped stamps.

## Known limitations / observations

- The physical PG volume tar is not a guaranteed-consistent restore source
  (live data dir). It exists for corrupt-block forensics and structure
  recovery; restore from `db/nfm_db.dump`.
- Redis is the Celery broker (12 kombu keys at establishment). Low value
  today, cheap to protect, and queue-state loss on restore is tolerable —
  but the ask named Redis explicitly, so it ships.
- **Observation for the security backlog (out of scope here):** prod Redis is
  published on `0.0.0.0:6380` with no password. LAN/tailnet-reachable. The
  backup lane depends on that port; if it gets firewalled or passworded, the
  ship script's redis step fails loudly (fail=1) rather than silently.
- openrsync (macOS default) interoperates with z203's rsync 3.2.7 at
  protocol 29 — verified working; if macOS ever breaks this, switch the ship
  script to `tar | ssh` streaming.
