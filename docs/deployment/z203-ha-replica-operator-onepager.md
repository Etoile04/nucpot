# z203 HA tunnel replica — operator one-pager

> D2 charter [NFM-5963](/NFM/issues/NFM-5963) · artifacts NFM-5965 · window
> NFM-5968 (Fri 2026-11-13 19:00–21:00 UTC). One page: what will run on your
> box (z203), what it costs, what it touches, how to remove it, and what we
> need from you.

## What runs on z203 (after install; none of it yet as of 2026-11-12)

| Component | Unit | What it does |
|---|---|---|
| Replica connector | `cloudflared-nucpot-replica.service` | Second cloudflared connector for the MAIN tunnel (`04b1e559`, nucpot.dpdns.org). Same token family as the Mac daemon; Cloudflare treats it as a second connector in the same pool. |
| Soak connector | `cloudflared-verify-replica.service` | Same, for the VERIFY tunnel (`847668eb`, verify.nucpot.dpdns.org) — used only during the pre-window shadow soak. Only ONE of the two connectors is enabled at any time. |
| Origin forwarder | `nfm-origin-forwarder.service` (caddy) | Listens **loopback-only** on `127.0.0.1/::1 :3000` and `:8002` and proxies to your Mac origins (`100.65.21.27`) over Tailscale. Nothing is exposed on the LAN. |
| Log rotation | `/etc/logrotate.d/nfm-replica` | Weekly, 20M cap, copytruncate, 3 rotations, compressed. |

Files: `/etc/systemd/system/*.service` (3 units), `/etc/nfm/replica/*.env`
(tunnel tokens, **0600 root**), `/etc/nfm/origin-forwarder/Caddyfile`,
`/opt/nfm/z203-replica/` (staged copies), binaries at
`/usr/local/bin/{cloudflared,caddy}`. Logs: `/var/log/cloudflared-*-replica.log`,
`/var/log/nfm-origin-forwarder/access.log`.

## Resource footprint

Two cloudflared processes (~30–80 MB RSS each, only one runs at a time) +
caddy (~20–40 MB). CPU ≈ idle; 4 edge connections per active connector.
Disk: rotated logs bounded well under 100 MB. Everything is `Restart=always`
— self-healing, no cron.

## Blast radius (consent scope)

- **Soak (pre-window):** only the verify-hostname share splits across 2
  connectors. The MAIN tunnel and prod traffic are untouched until the
  window.
- **Window (60 min):** prod ingress runs with 2 connectors; a scripted
  10-minute failure drill intentionally darks the Mac primary while the
  replica carries traffic; a 10-minute rollback drill unloads the replica.
  The Mac plist `KeepAlive` flips to `true` (with timestamped backups).
- **Never touched:** your containers (docker compose projects), the edge
  config (no Cloudflare credentials exist agent-side), the Mac origin
  servers, and z203 ports other than the loopback :3000/:8002 pairs — see
  the ask below.

## What we need from you (consent card items)

1. **z203 as the always-on replica host** (decline ⇒ VPS fallback, $3–5/mo,
   within standing pre-auth).
2. **Verify-pool shadow soak** (decline ⇒ stop; alternatives are an operator
   action, not an agent improvisation).
3. **Port freeing + zombie cleanup (the G2-discovered blocker):**
   `nucpot-prod-linux-web` currently binds host `0.0.0.0:3000` and
   `nucpot-autovc-api-1` binds `0.0.0.0:8002` — exactly the addresses the
   replica's edge config dials. Remapping those two host port mappings
   (e.g. `3002:3000`, `8012:8000`) unblocks the forwarder. Separately,
   `cloudflared.service` (a June-era, locally-managed unit) has **dead
   credentials** ("Unauthorized: Tunnel not found") and crash-loops every
   ~32s; its stale config also points `nucpot.dpdns.org → z203:3100` should
   those creds ever revive — `sudo systemctl disable --now cloudflared`
   recommended. If you'd rather not remap, the design needs a CTO
   re-adjudication (network-namespace isolation); nothing installs either
   way until this is resolved — the installer fails closed.
4. **One sudo session at install** (or a scoped NOPASSWD grant for the
   window): the installer is non-interactive and currently z203 sudo asks
   for a password; ~10 `sudo` commands total (unit install, env files,
   daemon-reload, enable/start).

## How to remove everything

```bash
# on the Mac (orchestrates z203 over SSH):
scripts/z203-replica/install-replica.sh --mode uninstall --yes
# binaries optional: REMOVE_BINARIES=1 scripts/z203-replica/install-replica.sh --mode uninstall --yes
```

Stops + disables the three units, deletes unit files, `/etc/nfm/*`,
`/opt/nfm/z203-replica`, and the logrotate entry. Your containers and the
Mac primary are never touched. Mac-side KeepAlive revert (if you want the
pre-D2 plist behavior back):
`scripts/z203-replica/macos-set-main-keepalive-true.sh rollback` (restores
the newest timestamped backups in `~/nfm-backups/plist-history/`).

## Risk this mitigates (why)

14 full-edge episodes/30d, 8/8 in the prefix-blackhole class
(198.41.192/200.x `:7844` blackholed) — single-path, no host-side lever.
The replica gives the edge a second connector on a decorrelated network
vantage (different physical line + ASN), so a Mac-side blackhole no longer
darkens nucpot.dpdns.org. Assessment: [NFM-5871](/NFM/issues/NFM-5871).
