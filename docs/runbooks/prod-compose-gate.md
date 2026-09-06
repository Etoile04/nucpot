# Runbook: Prod compose gate (NFM-4270 / ADR-013 G2)

Status: **applied on the production host** — 2026-09-05 UTC, Release
Engineer under NFM-4295 from reviewed kit `17edebfbe1` (origin/main tip;
G2 core from PR #1145 squash-merged as `6e222e2040` + py3.9 gate fixes
#1158/#1159/#1161/#1164/#1166/#1167 + nfm4333 #1174). THE WALL, ro/full
gate proxies, six NOPASSWD sudoers entries under `/usr/local/lib/nfm-g2/`,
LaunchDaemons, and the canonical `/usr/local/var/nfm-g2/` state dir are
live. On-host AC-G2 probe 29/30 PASS (the single FAIL is the probe's
own assumption that a desktop user can reach `nfm-full`, which is the
wall working as designed — full socket is `root:prod-deploy 660`,
`prod-deploy` membership is `nfmdeploy` only). AC-G4 drift alarm: live
manifest `c96b8c4d1b`, drift check `in sync`, NFM-4317 (manifest_missing)
resolved by SRE. Evidence on NFM-4273 + NFM-4295. Keep this line current
on any re-application (e.g. after Docker Desktop upgrades).

Context: on 2026-09-04 (NFM-4264) a desktop-agent session ran host-side
`docker compose --env-file docker/.env.prod up -d --build api web` against
prod with zero audit trail. ADR-013 puts a wall at the host layer: prod
mutations are only possible from the dedicated deploy identity, which is
only reachable through root-owned, sudoers-enumerated entry scripts.

## What changes on the host

| Thing | Before | After |
| --- | --- | --- |
| `~/.docker/run/docker.sock` | 755, owner = you | group `prod-deploy`, mode `060` — **you cannot connect; root and `nfmdeploy` can** |
| your `docker` CLI | hits the raw socket | context `nfm-ro` → read-only gate (reads + non-prod stacks still work) |
| prod mutations | any shell | only via the `run-*.sh` entries below |
| evidence | none | `/var/log/nfm-g2/gate-ro.log` JSONL (identity + ts + verb + target) |

A further sudoers-granted entry, `run-record-manifest.sh` (NFM-4273), is
deploy-invoked rather than operator-facing: `deploy_prod.sh` re-records the
G4a deploy manifest through it, and `run-recovery.sh rollback` re-records
directly as `nfmdeploy`. See
[prod-deploy.md §7](./prod-deploy.md) for the manifest contract.

What still works unchanged through `nfm-ro`: `docker ps/inspect/logs/stats`,
`docker compose config`, all staging/autovc/e2e/supabase stack operations,
and image builds/pulls (candidate builds in CI). What now gets a 403:
anything touching `nucpot-prod-*` containers/networks/volumes/images
(delete/re-tag/push/export of prod images, incl. by image id), `docker exec`
into prod, daemon-wide prunes (incl. `docker image prune` — the dangling-
image cleanup cron must move to a sanctioned entry or the retention script),
and container escape hatches (privileged, docker.sock mounts, forbidden-path
binds — `/Users` `/private` `/etc` `/var` `/usr` `/tmp` `/opt` `/Volumes` —
host networking, host device requests).

## Install (Release Engineer, once + after Docker Desktop upgrades)

From a nucpot checkout on the production host:

```bash
sudo bash scripts/host-prod-gate/host_setup.sh
```

Idempotent; every step is guarded. It creates group `prod-deploy` + user
`nfmdeploy` (no login password; reachable only via `sudo -u`), installs the
gate to `/usr/local/lib/nfm-g2/` (root-owned), the sudoers fragment to
`/etc/sudoers.d/nfm-prod-deploy` (visudo-validated), three LaunchDaemons,
locks the raw socket, and points your docker context at the ro gate. It ends
by running the verification probe — exit 0 or it fails loudly.

## Verify (AC probe)

```bash
bash scripts/host-prod-gate/probe_g2.sh     # as the desktop user, NOT root
```

Covers each AC: prod denies (rm/stop/restart/exec/network/volume/prune),
raw-socket direct connect refused, reads frictionless, compose render,
entries root-owned, `sudo -n -l` shows only nfm-g2 NOPASSWD grants, audit
records attributable. CI additionally pins the classifier matrix and proxy
behavior in `scripts/tests/test_nfm_docker_gate_*`.

## Sanctioned paths (the only ways to mutate prod)

| Operation | Command |
| --- | --- |
| Deploy (CI does this) | repo owner: `cd ~/Projects/nucpot && git fetch origin && git reset --hard <sha>`, then `DEPLOY_SHA=<sha> PROXY_PORT=<port> sudo -n -u nfmdeploy /usr/local/lib/nfm-g2/run-deploy.sh` |
| Restart a sick service | `sudo -n -u nfmdeploy /usr/local/lib/nfm-g2/run-recovery.sh restart api\|web\|worker\|lightrag\|db` |
| Rollback (NFM-2148 SHA-tagged) | `sudo -n -u nfmdeploy /usr/local/lib/nfm-g2/run-recovery.sh rollback --tag <last-good-sha>` |
| Standalone SQL (run-migration.yml) | `sudo -n -u nfmdeploy /usr/local/lib/nfm-g2/run-sql.sh <repo-relative.sql>` (or `-` for stdin) |
| Celery inspect | `sudo -n -u nfmdeploy /usr/local/lib/nfm-g2/run-worker-inspect.sh` |

All entries validate arguments before touching docker and log identity to
`gate-full.log`. Anything else: **file a Paperclip issue first** — do not
add ad-hoc entries without review (the sudoers list IS the sanction).

Note: `%admin` users can still `sudo -i` to root and reach the raw socket —
the wall defends the desktop-user/agent context (and everything is audited
when routed through the entries), not a deliberate admin escalation; that
class is G1/G3's job (harness gate).

## Everyday operator notes

* **"permission denied while trying to connect to the Docker daemon socket"**
  from an old shell → your context/DOCKER_HOST predates setup; `docker
  context use nfm-ro`, or open a new shell.
* Docker Desktop may flip your current context back to `desktop-linux` on
  restart; the watchdog (`com.nfm.g2.socket-watchdog`) re-points it at
  `nfm-ro` within ~5 s and logs a `drift` record.
* The Docker Desktop **GUI** talks to `docker.raw.sock`, which is NOT locked;
  if its engine indicator ever misbehaves, the daemon itself is fine —
  check `docker ps`.
* After a Docker Desktop **upgrade** that moves the engine socket, re-run
  `host_setup.sh` (it rewrites `/usr/local/lib/nfm-g2/upstream.conf`).
* Read the audit trail: `tail -f /var/log/nfm-g2/gate-ro.log` (denies) and
  `gate-full.log` (sanctioned mutations — every deploy/recovery lands here).

## Uninstall (rollback the wall)

```bash
sudo launchctl bootout system/com.nfm.g2.socket-watchdog
sudo launchctl bootout system/com.nfm.g2.docker-ro
sudo launchctl bootout system/com.nfm.g2.docker-full
sudo rm /Library/LaunchDaemons/com.nfm.g2.*.plist /etc/sudoers.d/nfm-prod-deploy
sudo rm -rf /usr/local/lib/nfm-g2
sudo chmod 755 "$HOME/.docker/run/docker.sock" && sudo chgrp staff "$HOME/.docker/run/docker.sock"
docker context use desktop-linux
```

(Optionally also remove the `nfmdeploy` user / `prod-deploy` group via
`dscl . -delete`.) Prod mutations become possible from any shell again —
treat uninstall as a deploy-affecting change and announce it.

## Relationship to sibling work

* NFM-4267 (G1+G3) gates the *harness* path; this gate assumes that path can
  be bypassed and still holds.
* NFM-4265 (env/tag semantics) is untouched: `deploy_prod.sh` keeps its
  PROD_IMAGE_TAG contract; only its git-sync block gained a verify-only
  branch under `NFM_G2_DEPLOY_IDENTITY=1`.
