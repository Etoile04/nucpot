# ADR-018 — De-proxy CI egress: direct egress replaces the 7897 bridge proxy (NFM-4762)

| Field | Value |
| --- | --- |
| **Status** | Accepted |
| **Date** | 2026-09-12 |
| **Author** | Lead Engineer (CEO-DECISION routing) |
| **Scope** | CI egress path on the prod deploy workflow + sanctioned host scripts |
| **Supersedes** | Implicit prior assumption that `127.0.0.1:7897` (Clash Verge bridge) was a usable egress proxy for production deploys |
| **See also** | [CONTEXT.md](../../CONTEXT.md), [ADR-013 — Prod mutation guardrails](./ADR-013-NFM-4266-prod-mutation-guardrails.md), [NFM-4225](/NFM/issues/NFM-4225) (retired), [NFM-4568](/NFM/issues/NFM-4568) (verge-watchdog restored), [NFM-4570](/NFM/issues/NFM-4570) (CEO manual cleanup closeout), [docs/host-hardening-nfm4712.md](../host-hardening-nfm4712.md) (out of scope here — Layer-1/2 mitm CA) |

---

## 1. Context

### 1.1 Decision (CEO, 2026-09-12)

The 7897 bridge proxy (Clash Verge, restored by NFM-4568) is a pure DIRECT
alias with zero capability:

- `rules: []`, `proxies: []`, no profile subscription (Merge.yaml is the empty
  template)
- Measured 2026-09-12: `pypi` via 7897 = **12.0s** (timeout-clipped) vs direct
  **2.2s** (5.5× slowdown)
- All CI endpoints are DIRECT-reachable from this host: `pypi`,
  `files.pythonhosted`, `ghcr.io`, `objects.githubusercontent.com`,
  `github.com`, `api.github.com`
- The last two prod deploys (runs 34664346943, 34637349808) ran green through
  this empty-shell proxy — i.e., direct worked all along, the proxy was
  adding latency without paying for it

The staging side already documents the empty proxy as a no-op
(`staging-deploy.yml` L100-104 comment: "the box's JMS circumvention nodes are
all dead … `HTTP(S)_PROXY` is effectively a no-op").

Forensic closure: the 2026-09-03 23:42 removal of the NFM-4225 infra was
**CEO manual cleanup** (reclaiming 7892 for the user VPN), not a fault — see
NFM-4570 closeout comment.

### 1.2 Why now

The 7897 forwarding chain (Clash Verge → socat forwarder) is a single point of
failure that has bitten production deploys three documented times since
2026-09-06 (NFM-4355 RC12 bridge flap → ECONNREFUSED on artifact upload). The
proxy adds latency, complexity, and a failure mode without providing any
capability that direct egress lacks.

### 1.3 Boundary: 7892 belongs to the user, not NFMD

The 7892 port is the user's v2cloud VPN sentinel domain. NFMD does not own
7892, never assumes it, and never sets `HTTP_PROXY=7892` anywhere in CI. The
mitm CA in the host keychain (NFM-4712 Layer-1/2 inconsistency) is a separate
host-hardening concern that lives outside this ADR — it is governed by the
Layer-1 audit + NFM-4712 follow-ups.

## 2. Decision

### §2.1 CI egress = direct

Production CI egress (`production-deployment.yml`) and staging CI egress
(`staging-deploy.yml`) **set no `HTTP_PROXY` / `HTTPS_PROXY` / `PROXY_PORT`
env**. All jobs reach their endpoints via direct egress. `NO_PROXY` is
retained for explicitness as a no-op without a proxy.

### §2.2 Sanctioned host scripts: PROXY_PORT optional

`scripts/deploy_prod.sh` and `scripts/host-prod-gate/entries/run-deploy.sh`
treat `PROXY_PORT` as **optional**:

- Default: unset → deploy session uses no proxy env → direct egress.
- Backward-compatible: any caller (legacy CI, manual run) that still exports
  `PROXY_PORT` keeps working unchanged; the script honors it and applies
  `HTTP(S)_PROXY` for that session.

This makes the host scripts self-contained: a developer running
`bash scripts/deploy_prod.sh` directly on the production host does not need
to know about the (now-retired) bridge proxy.

### §2.3 Artifact upload bridge-wait step, simplified

The pre-upload "Wait for local proxy bridge" step (NFM-4355 RC12) is gutted:
the proxy forwarder probe is removed (the proxy is gone), and a 30s
deadline-capped DNS probe for `results-receiver.actions.github.com` is kept
as a belt-and-braces regression canary. Warning-only on exhaustion — the
upload step itself owns the strict `if-no-files-found: error` contract.

### §2.4 External smoke tests: no proxy bypass

The `external-smoke-tests` step previously set `HTTP_PROXY: "" HTTPS_PROXY: ""`
to override the deploy-prod job's proxy env. With no global proxy set, the
bypass is removed — public URLs reach the direct route by default.

## 3. Host teardown recipe (gated on PR merge)

The following host-side teardown is **out of scope for this PR** and must run
on the production host **after** the PR merges and a green deploy lands. The
recipe is recorded here so the PR author can hand it to the operator:

1. Unload + remove `com.nucpot.verge-watchdog` (NFM-4568):
   ```sh
   launchctl unload ~/Library/LaunchAgents/com.nucpot.verge-watchdog.plist
   rm ~/Library/LaunchAgents/com.nucpot.verge-watchdog.plist
   ```
2. Unload + remove `com.nfm.7897-fast-poller` (NFM-4583):
   ```sh
   launchctl unload ~/Library/LaunchAgents/com.nfm.7897-fast-poller.plist
   rm ~/Library/LaunchAgents/com.nfm.7897-fast-poller.plist
   ```
3. Quit Clash Verge (`Clash Verge.app`); optionally uninstall.
4. Repo var `PROXY_PORT` → delete (Settings → Secrets and variables →
   Actions → Variables → `PROXY_PORT`). With this PR merged, no workflow
   reads it; keeping the var would just be dead config.
5. **`com.nfm.7892-sentinel` STAYS** — it guards the user's v2cloud VPN
   domain, which is unrelated to CI egress.

## 4. Out of scope

- **7892** (user VPN domain) — untouched; `com.nfm.7892-sentinel` keeps
  running per its own ticket.
- **mitm CA keychain purge (NFM-4712)** — handled there, not here.
- **Docker daemon 3128 entry (NFM-4586)** — AC already met; untouched.
- **`docs/host-hardening-nfm4712.md`** — references 7897 for the Layer-1/2
  mitm CA audit (a different proxy class, on the host keychain); governed by
  NFM-4712 follow-ups.

## 5. Decision matrix

| Question | Decision | Rationale |
| --- | --- | --- |
| Should CI jobs set `HTTP_PROXY` at all? | No | Bridge proxy is an empty DIRECT alias; adds 12s latency, no capability. |
| Should `PROXY_PORT` stay as a CI var? | No | No workflow reads it after this PR. Delete post-merge. |
| Should host scripts (deploy_prod.sh, run-deploy.sh) drop `PROXY_PORT`? | No — make optional | Backward-compat for manual runs / legacy callers; default empty = direct. |
| Should the artifact-upload bridge-wait step be removed entirely? | No — gut it | DNS-only check still has value as a regression canary; the proxy forwarder probe is dead. |
| Should the external-smoke-tests proxy-bypass env stay? | No | Without a global proxy, the bypass is redundant. |
| Boundary: 7892 / mitm CA / Docker 3128 | Untouched | Different problem classes with their own tickets (NFM-4712, NFM-4586, NFM-4570). |

## 6. Reversibility & cost

- **Reversibility**: trivial. Re-introducing a proxy is a 1-PR revert + a
  re-run of the host teardown in reverse. The `PROXY_PORT`-optional host
  scripts mean an interim state where workflows are de-proxied but a manual
  deploy still works through a proxy is fully supported.
- **Migration cost**: low. ~9 env vars + 2 scripts + 1 runbook row + 1 new
  ADR; no schema changes, no migration head needed.
- **Not-reversing cost**: low — proxy stack continues consuming CPU/memory on
  the host with no benefit; bridge flap failures keep redding deploy runs
  (NFM-4355 RC12).

## 7. References

- [NFM-4762](/NFM/issues/NFM-4762) — CEO-DECISION ticket (this work)
- [NFM-4225](/NFM/issues/NFM-4225) — retired 7892→7897 socat shim (CEO
  manual cleanup 2026-09-03 23:42; NFM-4570 closeout)
- [NFM-4568](/NFM/issues/NFM-4568) — verge-watchdog restored
- [NFM-4583](/NFM/issues/NFM-4583) — 7897-fast-poller (30s)
- [NFM-4355](/NFM/issues/NFM-4355) — RC12 artifact-upload ECONNREFUSED
- [NFM-4570](/NFM/issues/NFM-4570) — CEO manual cleanup closeout
- [NFM-4712](/NFM/issues/NFM-4712) — mitm-broker (8899) + Layer-1/2
  inconsistency (cross-ref; not in this PR)
- [NFM-4586](/NFM/issues/NFM-4586) — Docker daemon 3128 entry (already met)
- [ADR-013 — Prod mutation guardrails](./ADR-013-NFM-4266-prod-mutation-guardrails.md) — sudo entry ownership invariants still apply; deploy entry's env validation unchanged
- [CONTEXT.md](../../CONTEXT.md)
