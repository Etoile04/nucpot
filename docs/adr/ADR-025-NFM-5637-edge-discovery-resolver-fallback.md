# ADR-025 — Edge-discovery resolver fallback off MagicDNS (NFM-5637)

| Field | Value |
| --- | --- |
| **Status** | Accepted — D1 implemented and live-drilled (2026-10-19); D2 staged with pre-armed reopen triggers; D3 partial risk acceptance |
| **Date** | 2026-10-19 |
| **Author** | CTO ([CTO-DISPOSITION] comment `83ee1b47-0dcf-4e2a-bc35-755b8a5cbb05` on [NFM-5637](/NFM/issues/NFM-5637), 2026-10-19T06:05Z). Authored and merged via [NFM-5640](/NFM/issues/NFM-5640) under parent [NFM-5638](/NFM/issues/NFM-5638). |
| **Scope** | DNS resolution upstream for Tailscale MagicDNS on wenjiedeMac-Studio — i.e. how cloudflared's edge discovery (`region*.v2.argotunnel.com`) gets resolved — ONLY. No egress, route, proxy, daemon, or runtime change. |
| **Doctrine** | [ADR-024 — Runner-egress terminating-proxy exception lane](./ADR-024-NFM-5582-runner-egress-terminating-proxy.md) runner-egress DIRECT invariant untouched: this ADR diversifies resolver upstreams only; the runner lane stays DIRECT (zero proxy sites, PROXY_PORT untouched). |
| **Untouched** | [ADR-023 — Sentinel egress independence](./ADR-023-NFM-5540-sentinel-egress-independence.md) — its ruling stands unchanged. |
| **See also** | [NFM-5637](/NFM/issues/NFM-5637) (ruling thread), [NFM-5638](/NFM/issues/NFM-5638) (parent W1), [NFM-5639](/NFM/issues/NFM-5639) (W1a host implementation + drill evidence), [NFM-5593](/NFM/issues/NFM-5593) (18:01Z incident, closure superseded here), [NFM-5581](/NFM/issues/NFM-5581) (upstream-escalation corpus, blackhole evidence), [NFM-5108](/NFM/issues/NFM-5108) (durable no-restart gate), [NFM-5636](/NFM/issues/NFM-5636) (evidence table the ruling was read from) |

---

## 1. Context

### 1.1 Incident class

The public edge (`nucpot.dpdns.org` + `verify.nucpot.dpdns.org`, both Cloudflare
tunnels) went through **3 P0 events in ~12 h (2026-10-18/19), two distinct
mechanisms, one shared denominator**: the host's single path to the Cloudflare
tunnel edge.

- **NFM-5593, 18:01:09Z — ~2 m 14 s full public-edge dark.** Host DNS: Tailscale
  MagicDNS SERVFAIL on argotunnel region discovery
  (`lookup region1.v2.argotunnel.com on [fd7a:115c:a1e0::53]:53: server
  misbehaving` — Go's pure resolver reading resolv.conf); all 4+4 connections
  lost 18:02:51Z, re-registered 18:03:00–04Z.
- **NFM-5595, 23:35:49Z → 00:21:26Z — 40–46 min.** Upstream prefix-blackhole of
  CF tunnel-edge ranges 198.41.192/200.x (both :7844 and :443, both tunnels)
  while `cloudflare.com` anycast answered 200 from the same host.

Either mechanism alone takes the entire public edge dark, and neither had a
host-side remediation — both could only be waited out. Tunnel restarts, CF-side,
and origin/containers were ruled out first-hand (both daemons PID 1517/1518 up
since Oct 11, zero restarts).

### 1.2 Single-resolver ground truth (CTO probes, 2026-10-19, read-only)

- `/etc/resolv.conf` nameservers = `100.100.100.100` +
  `fd7a:115c:a1e0::53` ONLY (MagicDNS; auto-generated). `scutil --dns`
  resolver #1 = MagicDNS (order 100000); **no fallback nameserver anywhere in
  the global chain**.
- Both cloudflared daemons run token-based
  `tunnel --no-autoupdate --protocol http2 run` with no `--edge` override →
  edge discovery is fully DNS-dependent. When MagicDNS SERVFAILs, Go's pure
  resolver has no second server to try. Single-homing confirmed at config
  level, not just incident level.
- The tailnet ran MagicDNS with **no control-plane resolvers** ("system default
  will be used"), so MagicDNS forwarded every non-tailnet query to a single
  DHCP consumer router (`192.168.3.1` + link-local v6) — one flaky box under
  every lookup on this host.
- `region1.v2.argotunnel.com` resolves **identically** (same 10 × 198.41.192.x)
  via MagicDNS, AliDNS `223.5.5.5`, and DNSpod `119.29.29.29` — public CN
  resolvers fully serve argotunnel discovery today. The fallback was buildable
  with zero CF-side dependency.

## 2. Decision

Ruling on SRE's three asks ([CTO-DISPOSITION] on NFM-5637, 2026-10-19T06:05Z):

| Ask | Disposition |
| --- | --- |
| **D1 — Decouple tunnel-edge discovery from MagicDNS** | **APPROVED, implemented now.** Would have prevented the 18:01Z class outright; config-only cost; viability validated by CTO probes (§1.2). Architectural requirement: *cloudflared's resolution of `region*.v2.argotunnel.com` must survive a MagicDNS SERVFAIL.* Mechanism is SRE's empirical choice; acceptance bar is a live drill against cloudflared itself — **a `dig` is not evidence** (§4). Implemented as W1a on [NFM-5639](/NFM/issues/NFM-5639) (§3). |
| **D2 — Second egress path to edge :7844** | **NOT NOW** — outside NFM-1664's five actions. Recorded as documented risk acceptance with **pre-armed reopen triggers** (§6). |
| **D3 — Accept the risk** | **PARTIAL.** Accepted for mechanism (b) only, under D2's triggers, with detection as the compensating control. NOT accepted for mechanism (a) (§7). |

## 3. D1 implementation — chosen mechanism and rationale (W1a, NFM-5639)

**Mechanism (live since 2026-10-19T06:25Z).** Manual DNS on the primary
physical network service (Wi-Fi = en1) of wenjiedeMac-Studio:

```
networksetup -setdnsservers Wi-Fi 223.5.5.5 119.29.29.29
```

This replaces MagicDNS's single DHCP-router upstream with two independent
public CN resolvers (AliDNS + DNSpod), both verified resolving
`region1.v2.argotunnel.com` to the same 198.41.192.x pool. `/etc/resolv.conf`
itself is untouched (still MagicDNS-first); what changes is where MagicDNS
forwards non-tailnet queries.

**Rationale.** Diversifying the system-DNS set is the one host-side lever that is:

- **durable** (configd-managed; survives reboot and DHCP-renew),
- **rootless** (no password-gated sudo),
- **instantly reversible** (one command, §5),
- **restart-free** (launchd untouched; NFM-5108 no-restart gate held, §4),
- **egress-neutral** (runner stays DIRECT; ADR-024 invariant intact), and
- **host-wide** (every MagicDNS consumer benefits, not just cloudflared).

**Alternatives considered (W1a candidate evaluation):**

| Candidate | Verdict |
| --- | --- |
| Tailscale admin-console global nameservers | Right tailnet-scope complement, **not executable from this host** (controlplane.tailscale.com, no API credential). Recommended follow-up, non-blocking (§8). |
| Scoped `/etc/resolver/argotunnel.com` | Root-gated AND ineffective for cloudflared: Go's pure resolver ignores scoped resolvers (consistent with the NFM-5593 log format; scoped resolver files are cgo-path only). |
| `--edge` static addresses per daemon | **Break-glass only** (§5): CF edge IPs rotate; hours-scale emergency measure. |

## 4. Drill evidence — live MagicDNS-failure drill (2026-10-19, PASSED)

Acceptance bar set by the ruling: **a live drill against cloudflared itself —
a `dig` is not evidence**; dig ≠ cloudflared's resolver code path.

**Design.** MagicDNS failure was simulated by **removing** MagicDNS from the
host resolver chain (`tailscale set --accept-dns=false`) for 115 s — a strictly
stronger consumer-side effect than a SERVFAIL (the MagicDNS path is *gone*, not
just erroring) and the exact end-state consumers hit during NFM-5593-class
events. The surviving path was the fallback set provisioned by §3.

**Timeline (2026-10-19Z):**

- 06:28:16 MagicDNS disabled → by first sample (≤13 s) resolv.conf =
  `nameserver 223.5.5.5` + `nameserver 119.29.29.29` — the OS failed over to
  exactly the mechanism-provisioned fallbacks.
- 8 samples t+13 s … t+115 s: **nucpot 200 8/8** (1.1–3.5 s), **verify 200
  8/8** (1.4–2.6 s) → **total public-edge dark time = 0 s** (budget ≤60 s).
- **cloudflared log deltas across the entire window: empty for both tunnels** —
  zero terminations, zero lookup failures, zero reconnect errors: both tunnels
  maintained registration undisturbed while MagicDNS was gone.
- 06:30:11 MagicDNS restored (`--accept-dns=true`): resolv.conf back to
  MagicDNS within 8 s; ts.net peer resolution restored.

**Durable gate (NFM-5108).** Both daemons
(`com.cloudflare.cloudflared-nucpot` PID 1517, `-verify` PID 1518) `state =
running`, PIDs unchanged since Oct 11 — **no daemon restart occurred** for the
mechanism change or the drill. Both public hostnames 200 at probe time.

**No-regression sweep.** Paperclip API, host ollama, nucpot API (loopback)
200; `gh api` authenticated OK; ts.net peer name resolves; runner lane DIRECT
and untouched.

**Known residual (documented, root-gated).** The drilled variant is "MagicDNS
removed" (resolv.conf rewrites to fallbacks). The micro-variant "MagicDNS
present but :53-blocked" (resolv.conf still lists only MagicDNS) was NOT
drilled — in it, Go's pure resolver has no second nameserver to fall to; only
the §5 break-glass `--edge` pin covers it. The realistic NFM-5593 mode
(MagicDNS SERVFAIL via dead single upstream) is directly fixed by §3.

## 5. Rollback and break-glass

**Rollback (effect immediate, no restarts, ≤1 min):**

```
networksetup -setdnsservers Wi-Fi "Empty"      # back to DHCP DNS
networksetup -getdnsservers Wi-Fi               # expect: There aren't any DNS Servers set.
tailscale dns status | sed -n '/System DNS/,$p' # expect system DNS = DHCP servers again
```

Drill-state rollback (already exercised 06:30:11Z):
`tailscale set --accept-dns=true` restores MagicDNS to resolv.conf (verified
within 8 s).

**Break-glass (NOT deployed).** If edge discovery must survive even
"MagicDNS present-but-blocked", pin edges per daemon: resolve current IPs
(`dig +short region1.v2.argotunnel.com @223.5.5.5`), add
`--edge region1.v2.argotunnel.com:7844,region2.v2.argotunnel.com:7844` to
ProgramArguments in both
`/Library/LaunchDaemons/com.cloudflare.cloudflared-{nucpot,verify}.plist`,
then `sudo launchctl kickstart -k system/com.cloudflare.cloudflared-<name>` and
re-verify the NFM-5108 gate. Requires root + controlled restart; CF edge IPs
rotate — hours-scale emergency measure only.

## 6. D2 — staged second path, pre-armed reopen triggers (verbatim from the ruling)

Second egress path to edge :7844: **NOT NOW** — outside NFM-1664's five
actions. When chartered, the recommended shape is a **second cloudflared
connector replica on an out-of-region VPS running the same tunnel tokens** (CF
load-balances across connectors; the multi-connector shape is already proven
harmless on this host — NFM-5108). One replica covers BOTH mechanisms — (a)
its own resolver, (b) its own WAN — plus the host-outage class. Lower
complexity than multi-WAN. **WARP-on-host rejected as primary**: WARP egress
(162.159.x) rides the same upstream that selectively blackholed
198.41.192/200.x for 40+ min — correlated failure mode, not an independent
path.

**Reopen triggers (pre-armed; any ONE fires → same-heartbeat CTO escalation to
operator/CEO with this ADR attached):**

1. ≥1 further upstream-blackhole-class P0 (198.41.192/200.x unreachable while cloudflare.com anycast fine from same host) within 30 days of this ruling;
2. public-edge P0 count reaches 6 in a rolling 30-day window;
3. any single blackhole event >60 min (current worst: 46 min).

## 7. D3 — partial risk acceptance

**Accepted for mechanism (b) only**, under D2's triggers, with detection as
the compensating control (proven: both events detected, ≤10 min to CRITICAL,
PAGE-UNACK reconciliation closed both). **NOT accepted for mechanism (a)**:
D1 is cheap, in-scope, and the ruling-day probes prove it buildable — "wait it
out" is not an acceptable posture for a preventable failure class.

## 8. Post-landing watch, follow-up, supersession

- **Watch line:** any DNS-class public-edge P0 after W1 lands -> reopen
  [NFM-5637](/NFM/issues/NFM-5637) immediately (mechanism failed, not luck).
- **Follow-up (non-blocking, tailnet scope):** set global nameservers
  (e.g. `223.5.5.5` + `119.29.29.29`) in the Tailscale admin console; composes
  with this host change (console resolvers take precedence as MagicDNS
  upstream when set) and extends the protection to every node on the tailnet.
- **Supersession:** [NFM-5593](/NFM/issues/NFM-5593)'s one-off closure is
  superseded by this ruling (no reopen needed — the pattern is now governed
  here). The NFM-5595 blackhole evidence belongs in the (d) upstream-escalation
  corpus on [NFM-5581](/NFM/issues/NFM-5581) (ADR-024).

---

*Ruling source of record: [CTO-DISPOSITION] comment
`83ee1b47-0dcf-4e2a-bc35-755b8a5cbb05` on NFM-5637 (2026-10-19T06:05Z).
Mechanism, drill evidence, and rollback lifted from the `[W1a-SUMMARY]`,
AC2/AC3, and AC4/AC5 comments on [NFM-5639](/NFM/issues/NFM-5639)
(2026-10-19T06:32Z). This document satisfies AC6 of parent
[NFM-5638](/NFM/issues/NFM-5638).*
