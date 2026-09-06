# Host-side prod compose gate (NFM-4270 / ADR-013 G2)

Denies unsanctioned production docker mutations **at the host layer** — even
when the actor bypasses every harness-level gate and drives the real docker
binary (or a raw socket curl) directly.

Spec: `docs/adr/ADR-013-NFM-4266-prod-mutation-guardrails.md` (G2/G5).
Operator doc: **`docs/runbooks/prod-compose-gate.md`** — start there.

## Shape

```
                     ┌─ /var/run/nfm-g2/docker-ro.sock   (666, everyone)
 docker CLI ────────▶│  ro gate proxy   — reads OK, non-prod mutations OK,
 (desktop users,     │                    prod-scoped mutations 403 + audit
  agents, crons)     └────────────┬────────────────────────────────────────┘
                                   ▼
 sudo (command-enumerated, root-owned entries)      ┌─ /var/run/nfm-g2/docker-full.sock
   /usr/local/lib/nfm-g2/run-{deploy,recovery,...} ▶│  full gate proxy — allow all,
   → runs as nfmdeploy (group prod-deploy)          │  audit every mutation
                                                   └────────────┬─────────────
                     ┌─ raw daemon socket: group prod-deploy, mode 060  ──┘
 THE WALL ──────────▶│  owner (desktop user) gets EACCES on connect; root
                     │  and prod-deploy connect. Watchdog re-asserts perms.
```

* **Wall** — `chgrp prod-deploy; chmod 060` on the real Docker engine socket
  (`~/.docker/run/docker.sock`). Docker Desktop recreates it on restart; the
  root LaunchDaemon watchdog re-locks within 5 s.
* **ro gate** — the desktop user's default docker context (`nfm-ro`): `ps`,
  `inspect`, `logs`, `stats`, `compose config` frictionless; staging/autovc/
  e2e stacks keep mutating freely; anything prod-scoped (names, compose
  project labels, networks, volumes, prod images — delete/re-tag/push/export,
  incl. opaque id refs) is denied 403 with the sanctioned-path message;
  daemon-wide prunes and container escape hatches (privileged, docker.sock
  mounts, forbidden-path binds incl. `/tmp` `/opt` `/Volumes`, host
  networking, host device requests) are denied regardless of scope.
* **full gate** — reachable only by `nfmdeploy` (socket 660 group
  `prod-deploy`) through root-owned sudoers-enumerated entry scripts.
* **Audit** — JSONL at `/var/log/nfm-g2/gate-{ro,full}.log`; every deny
  carries peer identity (uid/pid/user/cmd via LOCAL_PEERCRED/PEERPID),
  timestamp, verb, target (AC-G2.6).

## Files

| Path | Purpose |
| --- | --- |
| `nfm_docker_gate/policy.py` | pure request classifier (unit-tested matrix) |
| `nfm_docker_gate/proxy.py` | one-request-per-connection filtering proxy, streaming-safe |
| `nfm_docker_gate/peercred.py` | AF_UNIX peer identity (macOS LOCAL_PEERCRED/PEERPID, Linux SO_PEERCRED) |
| `nfm_docker_gate/audit.py` | JSONL audit log |
| `nfm_docker_gate/watchdog.py` | socket-perms + docker-context watchdog |
| `nfm_docker_gate_proxy.py` | launchd entry for either proxy mode |
| `entries/run-deploy.sh` | sanctioned deploy (deploy_prod.sh as nfmdeploy) |
| `entries/run-pre-deploy-assert.sh` | sanctioned pre-deploy DB↔code assertion |
| `entries/run-record-manifest.sh` | G4a deploy-manifest record as the deploy identity at the canonical G4 state dir (NFM-4273) |
| `entries/run-recovery.sh` | NFM-1664 recovery: `restart <svc>` / `rollback --tag <sha>` |
| `entries/run-worker-inspect.sh` | post-deploy celery inspect |
| `entries/run-sql.sh` | run-migration.yml standalone SQL |
| `entries/start-proxy.sh`, `entries/start-watchdog.sh` | launchd shims (read `upstream.conf`) |
| `sudoers.d/nfm-prod-deploy` | command-enumerated NOPASSWD grants (AC-G2.4) |
| `launchd/*.plist` | LaunchDaemons (ro, full, watchdog) |
| `host_setup.sh` | idempotent installer — `sudo bash host_setup.sh` |
| `probe_g2.sh` | AC verification probe (run as the desktop user) |
| `config.json` | prod scope prefixes (names, not files) |

## Develop

```bash
uv run --project apps/api pytest scripts/tests/test_nfm_docker_gate_*.py -v
```

The launchd proxies run under the host's `/usr/bin/python3` (3.9), so the
package must stay 3.9-clean — no runtime PEP 604 unions, no `datetime.UTC`,
catch `socket.timeout` (not `TimeoutError`). `test_nfm_docker_gate_py39_compat.py`
pins this via a functional smoke under the real 3.9 interpreter; ruff's
UP017/UP045/UP041 ignores for the package hold the style constraint in lint.

Stdlib + pytest only. Local end-to-end without installing:

```bash
./nfm_docker_gate_proxy.py --mode ro --listen /tmp/g2/ro.sock \
    --upstream "$HOME/.docker/run/docker.sock" --log /tmp/g2/gate.log
docker --host unix:///tmp/g2/ro.sock ps
docker --host unix:///tmp/g2/ro.sock rm -f nucpot-prod-api   # -> 403 nfm-g2
```
