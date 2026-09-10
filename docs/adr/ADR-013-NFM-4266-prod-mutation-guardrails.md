# ADR-013 — Prod-mutation guardrails for desktop-agent terminal access (NFM-4266)

| Field | Value |
| --- | --- |
| **Status** | Accepted (CTO decision) |
| **Date** | 2026-09-04 (original); 2026-09-10 amended (see §G6); 2026-09-10 amended again (see §G7, local-MITM CA hardening) |
| **Author** | CTO |
| **Source issue** | [NFM-4266](/NFM/issues/NFM-4266) |
| **Amendment source** | [NFM-4582](/NFM/issues/NFM-4582) (`[CTO-AMEND]`, 2026-09-10; folded in [NFM-4592](/NFM/issues/NFM-4592) local-MITM CA hardening standard per CTO ruling on [NFM-4565](/NFM/issues/NFM-4565) comment `9c082caf`) |
| **Evidence** | SRE attribution comment `c35bc8ce` on [NFM-4264](/NFM/issues/NFM-4264); NFM-4581 evidence comment `93351853-1607-44c9-9fc8-23b51a201385` on [NFM-4579](/NFM/issues/NFM-4579); NFM-4592 acceptance criteria AC1–AC8 |
| **Complements** | [NFM-4265](/NFM/issues/NFM-4265) (LE — stale `PROD_IMAGE_TAG` env-file landmine; orthogonal, both stand) |
| **Related lineage** | [NFM-3320](/NFM/issues/NFM-3320) (deploy cutover asserts), [NFM-2148](/NFM/issues/NFM-2148) (SHA tag pinning), [NFM-1664](/NFM/issues/NFM-1664) (SRE recovery pilot), [NFM-4225](/NFM/issues/NFM-4225) (proxy-resilience shim — **superseded for prod** by [NFM-4567](/NFM/issues/NFM-4567); see §G6), [NFM-4565](/NFM/issues/NFM-4565) / [NFM-4579](/NFM/issues/NFM-4579) / [NFM-4581](/NFM/issues/NFM-4581) (2026-09-10 prod-runner recovery incident), [NFM-4567](/NFM/issues/NFM-4567) (terminating TLS proxy), [NFM-4592](/NFM/issues/NFM-4592) (local-MITM CA hardening standard — accepted AC1/AC2/AC4/AC5/AC7 controls codified in §G7) |

---

## 1. Context

At 2026-09-04 00:05–00:06 CST, a human-driven Hermes desktop agent session (`20260830_202228_9206d8`, live since Aug 30, model glm-5.3-flash, steered by terse `继续` prompts) patched `docker-compose.prod.yml` and ran a host-side `docker compose --env-file docker/.env.prod up -d --build api web` against prod. This bypassed **every** existing prod control — deploy-script cutover assertions ([NFM-3320](/NFM/issues/NFM-3320)), SHA tag pinning ([NFM-2148](/NFM/issues/NFM-2148)), pre/post-deploy asserts, GH Actions audit, and the [NFM-1664](/NFM/issues/NFM-1664) pilot scope — and produced a nominal 79-commit rollback (stale `PROD_IMAGE_TAG` from `docker/.env.prod:9`) with **zero audit trail**, costing ~6h of cross-team attribution work.

Root architectural defect: **all existing prod controls are path-based controls on the sanctioned path. None deny or observe the unsanctioned path.** The actor class "long-lived desktop agent session with host-user terminal access" can reach prod compose state directly, invisibly on success (the Hermes gateway logs command text only for failed commands), and effectively autonomously (terse-prompt continuation over days).

Confirmed control surfaces (recon 2026-09-04, read-only):

- Hermes `~/.hermes/config.yaml` §`command_allowlist` exists and is enforced — the incident session had a *grep* on `docker-compose.prod.yml` hardline-blocked at 23:42:06 while the subsequent *compose mutation* at 00:05 was not blocked. The gate exists; it is mis-scoped.
- Hermes §`approvals` provides confirm-gates (`destructive_slash_confirm: true`) — precedent for interactive confirmation on destructive actions.
- Hermes §`security` ships the **tirith** policy engine (`tirith_enabled: true`, `tirith_timeout: 5`) but with **`tirith_fail_open: true`** — on engine timeout/error, commands proceed. A prod-mutation gate that fails open is a regression by construction (same fail-closed principle as ADR-010's precompletion merge hook flag).
- `terminal.backend: local` — unsandboxed, host-user privileges, as the gateway startup warning already flags.

## 2. Decision

Adopt a **four-layer guardrail set** — deny at the harness, deny at the host, observe commands, observe state — plus a standing routing policy. Guardrails are specified as *properties with acceptance criteria*; mechanism choice within a guardrail is delegated (§6).

### G1 — Deny: Hermes terminal blocklist for prod-compose mutations

The Hermes terminal backend must **refuse** any command whose effect is a prod compose mutation when invoked outside a sanctioned context.

- **Scope (mutation verbs):** `docker compose` (any form: `docker-compose`, `docker compose`) with `up`, `down`, `build`, `rebuild`, `restart`, `stop`, `start`, `rm`, `kill`, `run`, `exec` **when the invocation references** `docker-compose.prod.yml`, `docker/.env.prod`, `--env-file docker/.env.prod`, or a prod project name; plus bare `docker stop|rm|restart|kill` targeting prod containers, plus writes (edit/redirect/tee) to `docker-compose.prod.yml` or `docker/.env.prod`.
- **Explicitly NOT blocked (read-only stays frictionless):** `docker ps`, `docker inspect`, `docker logs`, `docker stats`, `cat`/`grep` of prod files, and `docker compose config` (render-only). SRE canary checks and RE diagnostics must not regress.
- **Sanctioned-context carve-out:** commands originating from `deploy_prod.sh`, the GH self-hosted runner context, or an enumerated [NFM-1664](/NFM/issues/NFM-1664) recovery entry pass. Mechanism (env marker, parent-process check, or allowlist entry) is implementer's choice; it must not be forgeable by a bare terminal command from an agent session (i.e. NOT merely an env var an agent can set in the same command line).
- **Fail-closed:** for prod-mutation rules, tirith (or whichever engine enforces) must fail **closed**. `tirith_fail_open: true` may stand for general policy; prod-mutation rules must not inherit it.
- **Refusal UX:** the block message must name the sanctioned path ("prod mutations route via GH Actions production-deployment.yml or deploy_prod.sh; file a Paperclip issue first if neither fits").
- **AC-G1:** (1) replaying the exact 00:05 command from a desktop session is refused; (2) `docker compose -f docker-compose.prod.yml config` (read-only) still works; (3) `deploy_prod.sh` end-to-end deploy still works; (4) an NFM-1664 pilot recovery action still works; (5) the block is logged with full command text.

### G2 — Deny (belt-and-braces): host-side compose gating

Independent of the harness layer, the prod host must not resolve unsanctioned prod compose mutations. Purpose: an agent that finds a way around the Hermes gate (direct binary path, socket curl) still hits a wall. Candidate mechanisms (implementer picks by cost on this single-host setup): a `docker` CLI wrapper shim that gates mutation verbs on the prod compose/env files by sanctioned-context marker; or docker-socket permission tightening (dedicated deploy identity for the sanctioned paths). Constraint: must not break the GH self-hosted runner, `deploy_prod.sh`, [NFM-1664](/NFM/issues/NFM-1664) recovery, or read-only diagnostics.
- **AC-G2:** invoking the real docker binary directly (bypassing any wrapper) from a non-sanctioned context cannot recreate/stop/rm a prod container.

### G3 — Observe: full command text for prod-touching terminal commands

The Hermes gateway must log **full command text on success** for commands matching the G1 prod-touching patterns (today only errors carry payloads — this is why attribution took ~6h). Scope-limit to prod-touching patterns to bound log growth; retention aligned with existing `agent.log` retention.
- **AC-G3:** after a successful `docker compose ... up -d --build api web`, `~/.hermes/logs/agent.log` (or successor sink) contains the literal command line with a timestamp, greppable without buildx cross-correlation.

### G4 — Observe (backstop): sanctioned-deploy manifest + drift alarm

Assume a future bypass exists. Bound its dwell time.

- The sanctioned deploy path (deploy script and/or GH workflow) records a **deploy manifest** at deploy time: `{deploy SHA, image tags, image digests, service→container mapping, timestamp, actor}` — one JSON artifact on the host, overwritten per deploy.
- A periodic check (existing Hermes cron pattern per [NFM-3195](/NFM/issues/NFM-3195) precedent) diffs live `docker inspect` state (running container image digests per service) against the manifest. Divergence ⇒ auto-file an SRE issue with the diff. This is stronger than the existing stale-image watchdog (age heuristic): it detects *any* unsanctioned mutation, including fresh-build re-tagging like this incident.
- **AC-G4:** manually recreating one prod container out-of-band is detected within one check interval and produces an issue naming the diverged service + expected vs actual digest.

### G5 — Policy: prod-mutation routing (standing rule)

All prod mutations route exclusively through one of: (i) GH Actions `production-deployment.yml`; (ii) `deploy_prod.sh` on-host; (iii) an enumerated [NFM-1664](/NFM/issues/NFM-1664) SRE recovery action. Every other actor class — desktop agent sessions, ad-hoc shells, humans at a terminal — is **read-only on prod**. A desktop session with a prod-touching intent must file a Paperclip issue describing the change and execute it through a sanctioned path; terse-prompt continuation (`继续`) over a long-lived session never authorizes prod mutation. This policy is the norm G1/G2 enforce technically; violations after this ADR are deliberate bypasses, not ambiguity.

### Directional (no work authorized now): workstation/prod-host separation

The structural smell: an interactive, autonomy-leaning desktop harness shares a host and docker socket with prod state. Long-term the right shape is separation (distinct prod host or distinct deploy identity + socket group). Recorded as direction; revisit if G1+G2 prove operationally too costly.

### G6 — System proxy assumptions (amended 2026-09-10, NFM-4582)

Premise correction relative to [NFM-4225](/NFM/issues/NFM-4225) and the original [NFM-4565](/NFM/issues/NFM-4565) ruling (b). The 2026-09-10 prod-runner recovery incident surfaced two facts that were true at the time of the original guidance but cannot be carried forward as assumptions:

- **NFMD must not assume ownership of system-level proxy ports.** The historical 7892 listener — for which [NFM-4225](/NFM/issues/NFM-4225) prescribed a durable socat shim — was at 2026-09-10T09:25Z actually owned by a user-installed application (`咍嗒云 v2cloudCore`, PID 1369, 7d 8h uptime, launchd-managed `application.com.v2cloud.*`, 21 ESTABLISHED client connections from Zotero, Quark Cloud, Chrome, Obsidian, VS Code, Python). The "no owner, NFMD may claim" assumption baked into NFM-4225 is unsafe. Any future 789x / 790x port may similarly be owned by user space at any instant; NFMD components must not bind, assume, or mutate them.
  - Evidence: [NFM-4581](/NFM/issues/NFM-4581) description; NFM-4579 evidence comment `93351853-1607-44c9-9fc8-23b51a201385` (2026-09-10T09:25Z).
  - **Stale-evidence lesson** (same incident): [NFM-4569](/NFM/issues/NFM-4569)'s closure snapshot (~09:12Z, "7892 empty + no owner") was correct at that instant but became stale immediately after — NFM-4567 relaunched the runner at ~09:20Z and the user's 咍嗒云 VPN rebound 7892 within minutes. Snapshots expire on the order of minutes when user space controls the resource. Any governance claim about a system proxy port must be re-verified at the moment of action, not relied on from a prior probe.

- **Runner isolation must be plist-explicit, not absence-based.** The runner plist (`~/Library/LaunchAgents/actions.runner.Etoile04-nucpot.wenjiedeMac-Studio.plist`) must set `HTTPS_PROXY=""` and `HTTP_PROXY=""` literally. The runner must NOT rely on the absence of a system proxy — the moment any user app binds 7892 again, absence-based isolation regresses silently. The plist must also pin `NODE_EXTRA_CA_CERTS` and `SSL_CERT_FILE` to the local MITM CA bundle ([NFM-4567](/NFM/issues/NFM-4567)) so the broker handshake terminates through the runner-side proxy regardless of any system-level TLS interception.
  - **AC-G6.1:** `grep -E '^(HTTPS_PROXY|HTTP_PROXY)=' <plist> | EnvironmentVariables` returns both keys present and empty.
  - **AC-G6.2:** with both keys empty in plist AND `lsof -nP -iTCP:7892 -sTCP:LISTEN` showing a non-NFMD owner, the runner logs `Connected to GitHub` without traversing 7892 (i.e. absence of system proxy is **not** what keeps the runner working).

- **NFM-7892 sentinel cron is the durable monitor, not a one-time cleanup.** The launchd plist at `~/Library/LaunchAgents/com.nfm.7892-sentinel.plist` (StartInterval=300s) reads `scutil --proxy`, compares advertised state to `lsof -nP -iTCP:7892 -sTCP:LISTEN`, and pages SRE if the two diverge. This sentinel is **not** a P0-burndown artifact; it stays in place indefinitely and is not removed when [NFM-4579](/NFM/issues/NFM-4579) closes. Removal requires a successor monitor (e.g. an ADR-013 G4-style drift alarm) explicitly inheriting its scope.
  - Reference: [NFM-4579](/NFM/issues/NFM-4579) Part 2 (sentinel cron) and [NFM-4581](/NFM/issues/NFM-4581) "What's already delivered" — both landed 2026-09-10. Sentinel path: `/Users/lwj04/bin/nfm-7892-sentinel.sh`.

**Supersedence of [NFM-4225](/NFM/issues/NFM-4225) — prod runner scope.** NFM-4225's "durable socat forwarder 7892→7897" remediation is **superseded for the prod runner** (`wenjiedeMac-Studio`). The new architecture is the terminating TLS proxy at `127.0.0.1:8443` deployed under [NFM-4567](/NFM/issues/NFM-4567) — a local-CA MITM whose upstream leg uses OpenSSL (the client stack that demonstrably completes the broker handshake on this host, per [NFM-4569](/NFM/issues/NFM-4569)). The socat shim remains relevant only as a **staging-runner** fallback for the Linux `thinkstation` if and when the same DPI fingerprint recurs there — and only if NFM-4567's design cannot be ported to that host. Until that port-or-fallback decision is made explicitly, do not restore 7892→7897 shims on `wenjiedeMac-Studio`.

### G7 — Local-MITM CA hardening standard (amended 2026-09-10, NFM-4582 + NFM-4592)

If a terminating TLS proxy runs on an NFMD host, the trust it asks the host to extend into the login keychain must be **scoped to exactly the hostnames the proxy actually needs to terminate** and **survivable only for as long as the proxy needs to operate**. An unconstrained root valid for 27 months with a live `ca.key` would let any process holding the leaf mint arbitrary certificates for any name — that is the failure mode the original [NFM-4567](/NFM/issues/NFM-4567) CA exhibits today, and the reason [NFM-4592](/NFM/issues/NFM-4592) was authorized to re-issue it under a tighter standard. The four controls below are the **standing bar** for any future NFMD terminating-proxy CA, not a one-off for this incident.

- **G7.1 — Name-constrained root.** Re-issue the CA with `basicConstraints = critical, CA:TRUE, pathlen:0`, `keyUsage = critical, keyCertSign, cRLSign`, and `nameConstraints = critical` constraining every name type:
  - `permitted;DNS:.actions.githubusercontent.com`
  - `excluded;IP:0.0.0.0/0.0.0.0`, `excluded;IP:::/0` (no IP at all)
  - `excluded;email:.`, `excluded;URI:.`, `excluded;DirName:/` (everything else excluded)
  - Unconstrained types default to unconstrained under RFC 5280; **all five name types** must be listed.
  - **AC-G7.1:** `openssl x509 -in ca.crt -noout -ext nameConstraints,basicConstraints,keyUsage` shows all three extensions present, with `critical` set on each, and `permitted;DNS:.actions.githubusercontent.com` paired with full exclusion of IP/email/URI/DirName.

- **G7.2 — Short validity (30 days, not 825).** `-days 30`. Record `notAfter` in the deploy artifact and file a renewal follow-up (or fold into the [NFM-4574](/NFM/issues/NFM-4574) SRE monitor rule) so expiry pages before it bites — an expired CA silently re-breaks prod deploys the same way a missing listener does.
  - **AC-G7.2:** `openssl x509 -in ca.crt -noout -dates` shows `notAfter` within 30 days of `notBefore`; renewal follow-up issue is filed (or NFM-4574 rule updated) before enrolment.

- **G7.3 — CA private key destroyed after leaf issuance.** The proxy loads a *static* leaf (see `mitm_broker_proxy.py:121` — `ctx.load_cert_chain(certfile=leaf.crt, keyfile=leaf.key)`); `ca.key` has no runtime role after leaf signing. **Destroy order matters:** AC4 (negative-test) needs `ca.key` to mint the throwaway leaf — so destruction is **after** the negative test passes, not before.
  - **AC-G7.3:** after the NFM-4592 AC4 negative test passes, `rm -P` (or equivalent secure delete) on `ca.key` and `ca.srl`; `ls -la ca/` shows absence; proxy is restarted and still serves the broker handshake (proves the key was genuinely unnecessary).

- **G7.4 — Login-keychain / `-p ssl` scope only, never System keychain.** The CA is enrolled with `security add-trusted-cert -r trustRoot -p ssl -k ~/Library/Keychains/login.keychain-db` — `login.keychain-db`, **not** `/Library/Keychains/System.keychain`, **not** `/Library/Keychains/System.keychain-db`. The `-p ssl` flag scopes the trust to SSL/TLS only; X.509 code-signing, email, and other trust purposes are not granted. Stale-cert eviction (`security delete-certificate`) runs first so the operator cannot approve the wrong (old, long-lived) cert.
  - **AC-G7.4:** `security dump-trust-settings` lists the CA under login keychain with `ssl` purpose only; `security find-certificate -c "NFM-4567 Local Broker MITM CA" /Library/Keychains/System.keychain` returns empty.

**Standing-bar language.** Any future NFMD terminating-proxy deployment — [NFM-4567](/NFM/issues/NFM-4567) v2, a staging-runner equivalent, an external-network variant — MUST satisfy all four controls at issuance. "We'll get to hardening later" is not acceptable: the trust store is the gate, and an unconstrained root valid for years is the gate *open*.

**Out of scope (G7 does NOT do).** G7 governs CA issuance and trust enrolment only; it does not redesign the proxy itself (NFM-4567 is the design home for that). It does not authorize widening the proxy allowlist (the existing `NO_PROXY` for `github.com`/`api.github.com` etc. is intentional — those hosts are not DPI-affected; widening requires a fresh CTO ruling). It does not touch `sudo` or the System keychain (the trust step is SecurityAgent-gated either way; widening to system-wide is the threat model G7 is closing, not opening).

## 3. What this ADR deliberately does NOT do

- **No duplicate of [NFM-4265](/NFM/issues/NFM-4265):** that issue (LE, in_progress) fixes the stale `PROD_IMAGE_TAG` env-file landmine and makes host-side compose fail loudly on stale tags. G1/G2 gate *contexts*; NFM-4265 removes *one landmine*. Orthogonal; both ship.
- **No sandbox mandate for `terminal.backend`:** switching the desktop harness to a sandboxed backend is a product-level change to the operator's harness with broad blast radius; G1's scoped blocklist gets the risk reduction at a fraction of the cost.
- **No adversarial-security claim:** these guardrails convert *silent accidental/autonomous-drift* prod mutation into *loud, deliberate, attributed* bypass. A host-user-level actor can ultimately defeat host-level gates; G4 exists for that residual. NFM-4297 raises the effort bar for that actor (gated entries serialize on an exclusive lock, bind `DEPLOY_SHA`/recorded baselines to origin/main reachability, and pin interpreters so a caller's PATH selects nothing — macOS sudo has no secure_path), but the disclaimer stands: this is tamper *resistance*, not tamper *impossibility*.
- **No assumption that system proxy ports are NFMD-owned.** NFM-4225-era "7892 is ours, keep it durable" guidance is deprecated for the prod runner; see §G6 and [NFM-4581](/NFM/issues/NFM-4581). Future runners, plists, and migration scripts must not bind or assume 789x / 790x. If a runner needs terminating TLS, it terminates TLS itself (NFM-4567 pattern) rather than reusing a port it doesn't own.
- **No relitigation of the [NFM-4565](/NFM/issues/NFM-4565) ruling (b):** NFM-4582 (this amendment) captures the *governance tail* — the stale-premise lesson and the durable-monitor design. The ruling itself is final; the runner plist changes from NFM-4567 stand, and the sentinel cron from NFM-4579 Part 2 stands.
- **No unconstrained or long-lived NFMD-issued CA in any host trust store.** Any CA NFMD asks the host to trust (currently [NFM-4567](/NFM/issues/NFM-4567)'s broker-MITM CA; future terminating proxies in NFM-4592's wake) MUST satisfy §G7.1–G7.4 — name-constrained root, 30-day validity, CA key destroyed after leaf issuance, login-keychain/`-p ssl` scope only. The pre-NFM-4592 CA (valid 2026→2028-12-13, unconstrained, key retained) is **not** to be re-enrolled anywhere. If a proxy cannot meet §G7, it does not enrol — it waits for §G7-compliant re-issuance or an architecture that does not require host trust at all.
- **No NFMD CA in the System keychain.** System-keychain trust is host-wide and persists across user logout; an NFMD-issued CA there is the threat model §G7 exists to prevent. Login-keychain with `-p ssl` is the only acceptable scope.
- **No retention of NFMD CA private keys.** Once the leaf is signed and validated end-to-end (NFM-4592 AC4 negative test passes), the CA key, serial file, and any throwaway AC4 material are destroyed. The key has no second use.

## 4. Risks & trade-offs

| Risk | Mitigation |
| --- | --- |
| Blocklist false positives break an urgent human prod fix | Read-only never blocked; sanctioned carve-outs for deploy script + pilot; refusal message names the path; an operator can still run `deploy_prod.sh` directly in seconds. |
| Full command logging leaks secrets in commands | Scope to prod-touching patterns only; `security.redact_secrets: true` already on; log sink access stays host-local. |
| Drift alarm noise during sanctioned deploys | Manifest is written by the sanctioned path at deploy time; check tolerates in-flight deploys (re-check or quiet window). |
| Fail-closed tirith blocks everything on engine breakage | Fail-closed applies to prod-mutation rules only; general policy keeps `tirith_fail_open`. |
| Wrapper shim drift as docker CLI evolves | G2 acceptance is behavioral (direct binary invocation), not implementation-coupled; revisit at docker major upgrades. |
| User-space VPN (e.g. 咍嗒云 v2cloudCore) rebinds 7892 between NFM-4225 probe and NFM-4225 fix, breaking the "durable shim" premise | G6 mandates plist-explicit `HTTPS_PROXY=""` so runner isolation survives the rebind; sentinel cron (NFM-4579 Part 2) detects the divergence within 5 min; terminating TLS proxy (NFM-4567) is the new architecture, not the socat shim. |
| Sentinel cron false pages during user VPN connect/disconnect | Page path is informational-only by design (NFM-4579 Part 2); not a deploy block. False positives indicate a real proxy-vs-listener divergence; investigate the cause, do not silence. |
| Name-constrained root is mis-issued (e.g. missing `critical`, omitting an excluded type that defaults to unconstrained) and the host trusts a root that can mint certs for arbitrary DNS | §G7.1 mandates `openssl x509 -ext` verification before enrolment; the NFM-4592 AC4 negative test with a .NET client probes an out-of-scope name (`example.invalid` / `github.com`) — a non-rejection is an automatic STOP-and-escalate to CTO before any operator prompt. |
| Apple `SecTrust` rejects the `nameConstraints` extension when marked `critical` (parse failure on some macOS versions) | §G7.1 documents the critical-flag fallback: re-issue non-critical and re-run both tests. A root that fails to parse is worse than one that parses and constrains — the operator must know which variant shipped. |
| CA key is destroyed before the NFM-4592 AC4 negative test, breaking the throwaway-leaf mint and leaving the constraint unverified | §G7.3 pins the ordering: NFM-4592 AC4 BEFORE `rm -P ca.key`. The proxy loads a static leaf (no `ca.key` in the hot path) — destruction AFTER verification is mechanically safe. |
| Expired CA silently re-breaks prod deploys (30-day validity bites without warning) | §G7.2 mandates renewal follow-up issue at issuance or fold into the [NFM-4574](/NFM/issues/NFM-4574) SRE monitor rule so expiry pages before deploy goes red. |
| Operator approves the stale (long-lived) CA already sitting in the login keychain untrusted, instead of the new 30-day one | §G7.4 mandates `security delete-certificate -c "NFM-4567 Local Broker MITM CA" ~/Library/Keychains/login.keychain-db` BEFORE the add-trusted-cert step. The two-command operator hand-off ships in NFM-4592 AC7 with this ordering. |
| Re-issued 30-day CA lacks a path back to System keychain if a future NFMD host needs it | By design. §G7.3 + §G7.4 (login-keychain only) is the threat-model boundary; widening is the threat model in reverse and requires a fresh CTO ruling per §3 "No NFMD CA in the System keychain." |

## 5. Acceptance (incident replay test)

The composite test for the whole set: replay the NFM-4264 scenario end-to-end from a fresh Hermes desktop session — grep prod files (allowed), attempt to patch `docker-compose.prod.yml` (blocked at G1 or loud at G3), attempt host-side compose `up -d --build` (blocked at G1; direct-binary retry blocked at G2; any residual mutation detected by G4 within one interval; if anything executed, its full command text is in the log per G3). Attribution cost target: **one grep**, not ~6h.

## 6. Delegation map

| Guardrail | Route | Notes |
| --- | --- | --- |
| G1 + G3 (harness layer) | CTO → CPO child issue (NFM-4267) | Hermes `command_allowlist`/approvals/tirith + gateway logging |
| G2 + G4 (host + repo layer) | CTO → CPO child issue (NFM-4268) | Host gating mechanism + deploy manifest & drift check (cron) |
| G5 policy | This ADR + [NFM-4266](/NFM/issues/NFM-4266) comment | No code; cited by G1 refusal UX |
| G6 (system proxy assumptions, amendment) | This ADR amendment ([NFM-4582](/NFM/issues/NFM-4582)); durable monitor is [NFM-4579](/NFM/issues/NFM-4579) Part 2 sentinel cron; supersedes [NFM-4225](/NFM/issues/NFM-4225) for prod runner scope | Documentation only; no new code. Runner-side isolation is plist-explicit per [NFM-4567](/NFM/issues/NFM-4567). |
| G7 (local-MITM CA hardening standard, amendment) | This ADR amendment ([NFM-4582](/NFM/issues/NFM-4582)); issuance and trust enrolment execute under [NFM-4592](/NFM/issues/NFM-4592) AC1/AC2/AC4/AC5/AC7 | Documentation only; codifies the standing bar for any NFMD terminating-proxy CA. The four controls (name constraints, 30-day validity, CA key destroyed after leaf, login-keychain/`-p ssl` scope) are mandatory, not advisory. |
| Directional separation | This ADR §2 only | No issue until G1+G2 cost data exists |
