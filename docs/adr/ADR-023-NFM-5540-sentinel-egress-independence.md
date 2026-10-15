# ADR-023 — Sentinel egress independence: direct-as-primary for every probe lane (NFM-5540)

| Field | Value |
| --- | --- |
| **Status** | Accepted (2026-10-15; implemented by [NFM-5542](/NFM/issues/NFM-5542), D5 deploy owned by the SRE sibling) |
| **Date** | 2026-10-15 |
| **Author** | CTO (architecture ruling on [NFM-5540](/NFM/issues/NFM-5540); routed via parent [NFM-5541](/NFM/issues/NFM-5541)) |
| **Scope** | On-host sentinel python egress lanes (`scripts/health_check.py`, `scripts/nfm-site-monitor.sh` embedded scan heredoc) |
| **Upholds** | [ADR-018 — De-proxy CI egress](./ADR-018-NFM-4762-deproxy-ci-egress.md) — the same no-user-proxy principle, extended from CI to the monitoring host |
| **See also** | [NFM-5226](/NFM/issues/NFM-5226) (sentinel contract), [NFM-5525](/NFM/issues/NFM-5525) (confirmation ladder), [NFM-5497](/NFM/issues/NFM-5497) (blind-streak classifier), [NFM-5539](/NFM/issues/NFM-5539) (ep#6 retro; with [ADR-013](./ADR-013-NFM-4266-prod-mutation-guardrails.md) for the DPI scope boundary), retro `3a39f8d9` |

---

## 1. Context

### 1.1 Failure pattern (3 false-CRITICAL episodes in ~20 h; site never down)

| Date (UTC) | Lane | What happened |
| --- | --- | --- |
| 2026-10-14 ×2 | Named checks (`health_check.py`) | 24 consecutive P1 WARN beats, SSL `UNEXPECTED_EOF` through the proxy vantage while curl-direct and external vantage read green (→ NFM-5525 ladder) |
| 2026-10-15 02:12–02:34Z | Chunk scan (`nfm-site-monitor.sh` heredoc) | `fetches=0/5`, mode=A ×3 → false CRITICAL page (`origin=ok`) — the lane NFM-5525 did not cover |

Ground truth (CTO-verified): the sentinel had **three python egress lanes with
inconsistent proxy posture** on a host whose macOS system proxy (v2cloud
`127.0.0.1:7892`, via scutil) applies to bare `urlopen()` through
`getproxies()`:

1. **Named-check lane** — `check_url` primary: bare `urlopen`, rode the system
   proxy. Failure-path confirmation ladder existed (NFM-5279/NFM-5525) but the
   primary still rode the VPN.
2. **Chunk-scan lane** — heredoc `fetch_page` + ref HEADs: `urllib.request`,
   rode the system proxy with **no bypass and no ladder**. This is the lane
   that fired the 10-15 false CRITICAL.
3. **Webhook lane** — `send_alert`: bare `urlopen`; unused on the host today
   (no `ALERT_WEBHOOK`) but the same defect latent.

### 1.2 Why the prior design was insufficient

Timeline proof: the NFM-5525 ladder merged 2026-10-14T16:51Z (`fb098c9e0`)
and was live on main — yet the chunk-scan lane (uncovered) still went blind
that night. Failure-path confirmation cannot fix a lane it does not cover, and
even for covered lanes a proxied primary still measures the user's VPN, not
the site (inverse blindness: a real outage during a VPN flap stays
unclassifiable).

**Records correction (supersession):** the 10-14 site-monitor blind windows
previously attributed to CN-side ClientHello DPI are **superseded** — they
were local proxy-vantage defects. This supersension is site-monitor-scope
only; [ADR-013](./ADR-013-NFM-4266-prod-mutation-guardrails.md)'s
broker/GitHub DPI rulings concern a different subsystem and remain valid.

## 2. Decision

**Principle: the sentinel's egress must never transit user-controlled or
user-session-dependent paths. Direct is the PRIMARY lane everywhere; proxy
paths cease to exist for probes.**

### D1 — ADOPT: no-proxy openers as the primary mechanism (code)

(a) `check_url`: the primary fetch goes through `NO_PROXY_OPENER`
(`build_opener(ProxyHandler({}))`). The NFM-5279/NFM-5525 confirmation ladder
is retained **unchanged** as the failure-path cross-check (covers
python-lane transients like the 10-14T09:34Z `gaierror`).
(b) Chunk-scan heredoc: page GETs **and** ref HEADs through one shared
no-proxy opener (the NFM-5490 bounded pool is untouched).
(c) Webhook sender likewise.

### D2 — ADOPT: `no_proxy="*"` / `NO_PROXY="*"` exports (defense-in-depth)

`nfm-site-monitor.sh` exports both for its python invocations, so any future
bare `urlopen` cannot silently re-couple through the env-var proxy class.
Scoped note (verified at implementation): on macOS the env-var pin
neutralizes env-var proxies only — the scutil system-proxy path is why D1 is
the primary mechanism and D2 is belt-and-braces. curl honors `no_proxy` too;
origin checks were green in all episodes (already effectively direct), so
their semantics are unchanged.

### D3 — ADOPT: proxy-state observability

Every degraded cycle logs a one-line `/usr/sbin/scutil --proxy` summary
(absolute path — GUI-launched agents have a sparse PATH, NFM-4579) adjacent to
the `mode=/fetches=` evidence, plus one proxy-state line at sentinel startup.
The next flap must be adjudicable host-egress vs edge in **one log line**.

### D4 — ADOPT: records correction

Supersede the 10-14 CN-side-ClientHello-DPI attribution for the site-monitor
windows in code comments and sentinel docs (those windows were local proxy
defects). ADR-013's broker/GitHub DPI rulings stay untouched — different
subsystem, still valid.

### D5 — ADOPT: deploy posture (SRE-owned)

Merge to main → redeploy `~/bin/nfm-site-monitor.sh` with a `.pre-nfm5540`
backup (NFM-5454/5497 flow). Pin the probe to a dedicated worktree detached
at `origin/main` (precedent: `nucpot-cadence-detector` / NFM-5350 — the
primary checkout floats on feature branches, a silent-stale-code risk for the
probe).

### Rejected alternatives

- **Env-only `no_proxy` pin** — invisible to tests, lost on new invocation
  paths, and (macOS) does not bypass the scutil system-proxy lookup that
  actually bit us. D2 exists only as defense-in-depth behind D1.
- **curl subprocesses for the page lane** — loses the NFM-5490 24-ref-HEAD
  parallel pool; curl stays as the NFM-5525 confirmation lane only.

### Explicitly out of scope / unchanged

- NFM-5497 mode-A/B classifier semantics — blindness is still blindness; with
  a direct primary, mode A + the D3 scutil line adjudicates host-egress vs
  edge in one line.
- The user socat relay on `*:3000` (documented in the retro; separate concern).
- GHA runners (no system proxy — the change is a no-op there).

## 3. Consequences

- A user VPN flap can no longer produce false CRITICALs/pages from any
  sentinel python lane, and can no longer mask a real outage as
  unclassifiable — the probe measures the site, not the user's tunnel.
- The NFM-5525 ladder's role narrows from "vantage corrector" to
  "direct-lane transient cross-check"; its annotations and fail-closed
  semantics are unchanged, and the NFM-5496 classifier feed keeps seeing
  every confirmed flap.
- Every sentinel log now carries host proxy state at startup and on every
  degraded cycle — future adjudication is single-line.
- Cost: none measurable (a `ProxyHandler({})` opener skips the scutil lookup;
  direct egress is the same path curl used when the proxied lane failed).
- Verification gates (implementation AC, NFM-5542): proxy-independence tests
  pin AC1 (named-check primary, clean pass under poisoned proxies), AC2
  (heredoc page GETs + ref HEADs via the opener, green under dead env proxies
  with `no_proxy` absent), AC3 (wrapper exports + webhook opener), AC4
  (scutil one-liners), AC5 (supersession comments, ADR-013 untouched), AC6
  (this document). AC7 (D5 deploy + first post-deploy cycle evidence) belongs
  to the SRE sibling.

## 4. Implementation

Delegated to Lead Engineer ([NFM-5542](/NFM/issues/NFM-5542), CI/Infra-style
review chain) as a child of [NFM-5541](/NFM/issues/NFM-5541); D5 deploy to the
SRE lane. This ADR is the contract; the child issues carry scope, file paths,
and acceptance criteria.
