# ADR-024 — Runner-egress terminating-proxy exception lane for the `production` GHA runner (NFM-5582)

| Field | Value |
| --- | --- |
| **Status** | Accepted — staged exception lane: build armed, runner remains on DIRECT egress post-build |
| **Date** | 2026-10-18 |
| **Author** | CTO ([CTO-RULING] comment `5ebae38d` on [NFM-5582](/NFM/issues/NFM-5582), 2026-10-18 05:22Z; post-activation validation addendum `5f1b8a2d`, 05:28Z). Authored and merged via [NFM-5585](/NFM/issues/NFM-5585) under parent [NFM-5583](/NFM/issues/NFM-5583). |
| **Scope** | Egress path of the wenjiedeMac-Studio GHA runner `production` ONLY |
| **Doctrine** | [ADR-018 — De-proxy CI egress](./ADR-018-NFM-4762-deproxy-ci-egress.md) direct-egress remains doctrine for ALL other lanes. This ADR is an exception lane, not a doctrine change. |
| **Untouched** | [ADR-023 — Sentinel egress independence](./ADR-023-NFM-5540-sentinel-egress-independence.md) — its ruling stands unchanged. |
| **See also** | [NFM-5582](/NFM/issues/NFM-5582) (ruling thread), [NFM-5581](/NFM/issues/NFM-5581) (incident ep#9; evidence corpus owner), [NFM-4592](/NFM/issues/NFM-4592) (original hardening bar, inherited verbatim), [NFM-4567](/NFM/issues/NFM-4567) → [NFM-4571](/NFM/issues/NFM-4571) (September attempt, cancelled mid-flight), [NFM-4565](/NFM/issues/NFM-4565) (cancelled-escalation lesson), [NFM-5512](/NFM/issues/NFM-5512) (episode-window doctrine), [ADR-013 — Prod mutation guardrails](./ADR-013-NFM-4266-prod-mutation-guardrails.md) |

---

## 1. Context

### 1.1 Incident class

An on-path middlebox is resetting the .NET runner broker's long-poll connections
(`SocketException 89` / unexpected EOF) on the wenjiedeMac-Studio GHA runner
`production`. Nine episodes over 38 days, with window durations lengthening
monotonically: **17 min → 87 min → ~10 h**. ep#9 limped ~10 h on the broker's
retry loop with only 2 jobs through; one earlier mid-flight kill cost a
terminal-failure rerun. The fingerprint is **ClientHello-sensitive DPI**: same
host, same destination — curl/openssl ClientHello passes (~0.9 s, TLS 1.2 + 1.3
clean) while the .NET broker's ClientHello is reset.

### 1.2 Mechanism match — why a terminating proxy

The only in-our-control fix is to **re-originate TLS with a non-.NET stack** —
exactly what a terminating (MITM) proxy does. A CONNECT tunnel does NOT help
(same ClientHello end-to-end) and was correctly ruled out. This is a
mechanism-matched lever, not a speculative one.

### 1.3 Why now (condensed from the ruling; the ruling is the source of record)

- **R1 — the trend bar has already been met.** Windows are lengthening
  monotonically across three episodes in three days; waiting for ep#10 to
  authorize a build means the next multi-hour-or-dark episode happens with no
  lever in hand.
- **R2 — the NFM-4565 lesson cuts both ways.** The September response was
  mechanistically right: a terminating MITM proxy was authorized, built, and
  partially deployed (NFM-4567 → NFM-4571 reached proxy stand-up on
  `127.0.0.1:8443`) — then cancelled mid-flight when the wedge lifted, and no
  ruling survived; 8 more episodes followed over 38 days. Institutionalize it
  now, or pay the build cost a third time.
- **R3 — mechanism match** (§1.2 above).

### 1.4 Known implementation traps (measured 2026-09-10 — do not relearn)

- Runner 2.337.0 has **no TLS-bypass env var**.
- `SSL_CERT_FILE` is **inert** for the .NET listener on macOS (Apple SecTrust,
  not OpenSSL).
- `sudo` is password-gated, so System-keychain routes are human-gated — the
  login-keychain `-p ssl` path (G2) avoids it.

## 2. Decision

**Option (b) of the [CTO-RULING] on NFM-5582 is AUTHORIZED as a STAGED
exception lane.** Full options record:

| Option | Disposition |
| --- | --- |
| **(a) Accept-and-monitor** | **CONCURRED.** SRE keeps flap-aware monitoring; the 30 s retry loop keeps riding windows until the lever is armed. |
| **(b) Terminating-TLS MITM egress proxy** | **AUTHORIZED — staged exception lane.** Build now, armed, runner stays on direct egress (§4). Scoped to wenjiedeMac-Studio runner `production` ONLY, under guardrails G1–G7 (§3). |
| **(c) Re-register the runner** | **NOT AUTHORIZED** (context only). Evidence is transport-class, not registration; a failed re-register leaves prod undeployable. Revisit only if (b) proves ineffective (proxy ClientHello also reset ⇒ DPI fingerprint widened) AND (d) stalls. Contingency beyond that — egress-relocated runner lane — is a next CTO review, out of scope here. |
| **(d) Upstream/ISP escalation** | **AUTHORIZED.** SRE packages the evidence corpus (probe batteries, flap verdicts, _diag session logs; deterministic ClientHello-dependent reset, single host, single dest-class) and opens the upstream track. Expect slow; it is leverage, not a control lever. Owner: SRE, tracked on NFM-5581; dispatch is operator-gated. |

### 2.1 Exception scope

The exception is a **terminating-TLS MITM egress proxy for the wenjiedeMac-Studio
GHA runner `production` ONLY**. ADR-018 direct-egress remains doctrine for every
other lane; the ADR-023 sentinel ruling is untouched. Guardrail G4 keeps this in
the ADR-018 spirit — a scoped exception lane, not open egress.

## 3. Guardrails G1–G7 — verbatim from the ruling

The hardening bar is inherited from [NFM-4592](/NFM/issues/NFM-4592) verbatim
(it was written for exactly this runner). The text below is the ruling's
authoritative wording; build-time sequencing clarifications from the NFM-5583
build umbrella are appended as indented notes and tighten — never loosen — the
bar.

- **G1** Loopback-only bind (127.0.0.1), host-local; no remote exposure.
- **G2** MITM CA: name constraints on ALL name types (incl. `excluded;IP:0.0.0.0/0`, `excluded;IP:::/0`, `excluded;email:.`, `excluded;URI:.`, `excluded;DirName:/`), validity <=30d, CA private key destroyed after static-leaf issuance, login-keychain `-p ssl` scope only (never System), Apple SecTrust negative-test gate — an out-of-scope leaf MUST be rejected or STOP. Import != trust: verify via `dump-trust-settings`.
  - *Build note (NFM-5583):* destruction of `ca.key` comes AFTER the negative
    test, which needs `ca.key`; "never System" means never the System keychain
    and no `sudo` (the login-keychain `-p ssl` path avoids it); if the
    out-of-scope leaf is NOT rejected, STOP — do not enroll; verify trust via
    `security dump-trust-settings` (+ `-d`), not `find-certificate`.
- **G3** Upstream: full certificate verification, fail-closed. No verify-off anywhere. Negative test (bogus upstream chain => no connect-through) is an acceptance criterion.
- **G4** Dest-class allowlist: GitHub Actions backend dest-class only (`*.github.com`, `*.actions.githubusercontent.com` + broker long-poll endpoints); deny-all-else. This keeps the ADR-018 spirit — a scoped exception lane, not open egress.
- **G5** September-artifact hygiene FIRST: inventory [NFM-4571](/NFM/issues/NFM-4571) leftovers (proxy files, `.proxy` flag, `config.sh` / `.runner` / credentials backups, launchd units, CA remnants) before writing anything new. Reuse what matches the bar; destroy what does not — especially any live `ca.key` or >30d CA minted on 09-10.
- **G6** Kill-switch: documented single-step revert to direct egress, verified in BOTH directions before the lever is declared armed.
- **G7** ADR-024 merged (docs/adr/): exception scope, guardrails, activation triggers, sunset clause — review <=30d after activation; revert to direct egress on (d) resolution or fingerprint retirement.

## 4. Activation posture — build armed, runner stays direct

- **Default state post-build:** proxy deployed, trust enrolled, validated in a
  clean window; the runner remains on **DIRECT** egress (current doctrine).
- **Pre-authorized activation triggers** (no further CTO round-trip needed; SRE
  activates and reports back on [NFM-5582](/NFM/issues/NFM-5582) within 1 h):
  - ep#10 with duration **>=2 h**, OR
  - lane fully dark (**zero dispatch >=2 h**), OR
  - ep#9 **relapse after the lever is armed**.
- The triggers remain armed regardless of any ongoing episode waning or
  lifting (the NFM-4565 lesson: waning is not lifted, lifted is not resolved).
- **DPI-neutralization proof is post-activation, not a build gate:** during an
  active episode (direct .NET ClientHello being reset), the proxied lane must
  sustain long-poll + dispatch **>=30 min**. A clean-window test cannot prove
  this — it is the validation metric.

## 5. Post-activation validation interpretation (CTO addendum `5f1b8a2d`)

The >=30 min sustained metric must distinguish two mechanisms:

1. **(i) Long-poll survival with small payloads** — the metric that matters for
   the broker lane.
2. **(ii) Bulk-transfer stalls** — the second mechanism (connection-age or
   sustained-transfer interference, e.g. `github.com` 200 + 493 KB then
   curl-28), which the broker lane may tolerate.

**Classification rule:** proxy fixes handshakes but long-poll still dies
mid-connection ⇒ **PARTIAL SUCCESS** ⇒ escalate back to the CTO for the
contingency ladder (egress-relocated runner review) — **never a silent
(b)-failed verdict.**

## 6. Sunset clause

- **Review <=30 d after activation.**
- **Revert to direct egress** on upstream/ISP resolution (option (d)) or
  fingerprint retirement.

## 7. Proven no-ops stay closed

Listener restarts, the `useV2Flow` flip, and plist/proxy-env changes are proven
no-ops. **Do not re-try.**

## 8. G2 deviation addendum — system-domain anchor (CTO ruling `28cb62a9`, 2026-10-19)

G2 (§3) pins MITM-CA trust to the login keychain only. Measured on NFM-5587
(W4 AC6, evidence comment `adec11e2`, 2026-10-19): the runner's .NET 8.0.30
Apple-PAL TLS validation does not honor USER-domain trust settings in the
runner's service context — `SecTrust`-equivalent CLI checks pass in both
interactive and launchd contexts while the runner itself rejects the CA
(`UntrustedRoot` from the first request). The login-keychain-only posture is
therefore **unusable for the runner lane on this host**.

The CTO ACCEPTED a scoped deviation (ruling `28cb62a9` on NFM-5587,
2026-10-19 05:28Z, conditions C1–C7):

- **C1 scope:** one system-domain anchor (`sudo security add-trusted-cert
  -d -p ssl -k /Library/Keychains/System.keychain`), THIS CA by fingerprint
  (`08d47215634349565e9d5cffdf65471459a07c8cd0d01aba1929801ed6fc05d4`),
  ssl policy only. No other trust-policy changes; the login entry stays.
- **C2 non-waivable backstops** (unchanged from §3): <=30 d validity,
  keyless after mint, name constraints, allowlist.
- **C3** negative battery re-run THROUGH the system domain before any window.
- **C4 sequencing:** enroll -> headless verify -> minutes-scale cutover with
  clean first handshake -> revert -> only then the >=1 h window.
- **C5 rollback:** wired into the SRE activation runbook §6 (both domains).
- **C6** this addendum, routed via CPO.
- **C7** the deviation is **per-CA-generation** — a re-minted CA requires a
  fresh ruling before any new anchor.

Sunset (§6) and the no-ops list (§7) are unchanged. Full-stand-down removal
of the anchor is operator-owned sudo (runbook §6).

---

*Ruling source of record: [CTO-RULING] comment `5ebae38d` on NFM-5582
(2026-10-18 05:22Z); post-activation validation addendum `5f1b8a2d`
(2026-10-18 05:28Z); G2 deviation ruling `28cb62a9` on NFM-5587
(2026-10-19 05:28Z, conditions C1–C7, addendum §8). Build execution and
acceptance live on NFM-5583 (AC1–AC7); this document satisfies AC2 / G7.*
