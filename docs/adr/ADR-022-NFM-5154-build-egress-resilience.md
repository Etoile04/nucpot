# ADR-022 — Build-egress resilience: pre-baked GHCR base, pip cache mounts, fail-don't-cancel (NFM-5154)

| Field | Value |
| --- | --- |
| **Status** | Accepted |
| **Date** | 2026-09-23 |
| **Author** | CTO (architecture sign-off; routed from [NFM-5153](/NFM/issues/NFM-5153) SRE lane) |
| **Scope** | Runner Docker build path (`docker/*.Dockerfile`) + network legs in CI/deploy workflows |
| **Upholds** | [ADR-018 — De-proxy CI egress](./ADR-018-NFM-4762-deproxy-ci-egress.md) (no proxy re-introduction) |
| **See also** | [NFM-4931](/NFM/issues/NFM-4931) (apt half-open, 09-17), [NFM-5153](/NFM/issues/NFM-5153) (brownout cluster, 09-23), [NFM-5151](/NFM/issues/NFM-5151) (cancelled-conclusion triage blind spot) |

---

## 1. Context

### 1.1 Failure pattern (2 clusters in 6 days, prod service never impacted)

| Date | Event | Outcome |
| --- | --- | --- |
| 2026-09-17 ([NFM-4931](/NFM/issues/NFM-4931)) | apt half-open connection hung ~22 min mid-deploy | Fixed by apt timeouts (30s) + 5 retries + mirror ladder |
| 2026-09-23 ([NFM-5153](/NFM/issues/NFM-5153)) | apt-ladder stall, main CI run 35831060524 | 15-min job timeout → **cancelled** |
| 2026-09-23 | apt-ladder stall, PR CI Docker Build Verification run 35831037652 | 15-min job timeout → **cancelled** |
| 2026-09-23 | pip ladder fully exhausted, prod deploy build run 35831060526 | ReadTimeoutError on files.pythonhosted.org after tuna + direct legs → **failed** (rerun-recoverable) |

Live SRE probes 08:29–08:35Z during the 09-23 cluster: westbound flows via Fastly
(`pypi.org`, `files.pythonhosted.org`, `deb.debian.org`) and `github.com:443`
degraded/flapping (0/4 HTTPS + 0/6 TCP), while **`api.github.com` and `ghcr.io`
stayed green throughout** and the CN mirror (tuna) was healthy.

### 1.2 Current state on `origin/main` (grounding)

- `docker/prod-api.Dockerfile`: `FROM python:3.12-slim`; NFM-4931 apt conf
  (`Acquire::http(s)::Timeout 30`, `Retries 5`); 2-mirror × 3-attempt apt ladder
  (tuna → deb.debian.org) + proxy-bypass leg installing
  `gcc libpq-dev libcurl4-openssl-dev curl ca-certificates`; pip ladder
  tuna×2 (`--retries 10`, timeout 120s) → pypi direct (`--retries 15`, 180s);
  same ladder for a defensive `xgboost` layer.
- `docker/lightrag.Dockerfile`, `docker/staging-api.Dockerfile`: mirror-ladder
  apt + pip legs of the same shape; `docker/e2e-hub.Dockerfile`,
  `docker/e2e-resource.Dockerfile`: tuna pip legs.
- Workflow-host apt legs outside Docker builds: `ci.yml` L244,
  `test-api.yml` L157 (`sudo apt-get install postgresql-client`).
- Job-level `timeout-minutes` caps (`ci.yml` L185 10m / L280 15m;
  `production-deployment.yml` L1147 15m) turn an exhausted ladder into a
  **`cancelled`** conclusion — which bypasses CI Failure Triage (the
  [NFM-5151](/NFM/issues/NFM-5151) blind spot).

### 1.3 Decision constraints

- Retry ladders bound the damage but each exhaustion burns 12–15 min of runner
  time plus a red X or a silent cancel.
- ADR-018: CI egress is direct; the retired 7897 proxy was measured at 5.5×
  slowdown with zero capability. Any proposal to reintroduce a proxy must beat
  that record.
- The runner is a single self-hosted host (CN); ghcr.io is provably green under
  the exact brownout conditions that break Fastly-fronted endpoints.

## 2. Decision

### D1 — ADOPT: pre-baked GHCR base image (highest leverage)

Nightly job rebuilds `ghcr.io/etoile04/nucpot-build-base` containing the apt
build dependencies; consumer Dockerfiles `FROM` it. **Routine builds stop
calling apt-get entirely**, eliminating the apt-stall class (both 09-23
cancels + the NFM-4931 class).

- New `docker/build-base.Dockerfile`: `FROM python:3.12-slim` (digest-pinned
  upstream), carrying the NFM-4931 apt conf and the union of apt build deps
  across consumers (`gcc libpq-dev libcurl4-openssl-dev curl ca-certificates`
  + lightrag's set, audited at implementation).
- New `.github/workflows/base-image.yml`: nightly schedule, off-peak CN local
  time, **off-round cron minute** (e.g. `23 19 * * *` UTC = 03:23 CST);
  builds, pushes with provenance attestations, tags `nightly-YYYYMMDD`
  (immutable record) and promotes the mutable `stable` tag **only on green
  builds**.
- Consumers (`prod-api`, `staging-api`, `lightrag`; audit `web`, `e2e-*`)
  switch to `FROM ghcr.io/etoile04/nucpot-build-base:stable` and delete their
  apt ladders.
- Failure containment: a failed nightly does **not** break builds — `stable`
  is sticky and apt deps change rarely. Alert after 3 consecutive nightly
  failures (SRE lane). Dependency changes land via PR editing
  `build-base.Dockerfile`, then roll out on the next green nightly.
- Registry posture: ghcr.io was green through the entire brownout, and moving
  the base pull there removes routine `docker.io` (python:3.12-slim) registry
  dependence — the nightly job absorbs that pull once per night into the
  runner's layer cache.

### D2 — ADOPT: pip BuildKit cache mounts

Replace `pip install --no-cache-dir` with
`RUN --mount=type=cache,target=/root/.cache/pip pip install …` in all consumer
Dockerfiles. Wheel sets persist in the runner's BuildKit cache across builds;
during brownouts, cached wheels serve installs with zero network. Only
genuinely new packages hit the network. Keep the tuna→pypi ladder as the
second line (cheap once cache absorbs the steady state). Requires BuildKit on
every build path (buildx in Docker Build Verification; deploy build must set
`DOCKER_BUILDKIT=1` / buildx — verify at implementation).

### D3 — ADOPT: fail-don't-cancel at stall caps (NFM-5151 remediation)

Wrap every remaining network leg (workflow-host apt legs at `ci.yml` L244 /
`test-api.yml` L157, and any pip legs that remain post-D1/D2) in a step-level
GNU `timeout` so exhaustion **exits non-zero**: the step fails, the job
conclusion becomes `failure` (red X), and CI Failure Triage fires. Job-level
`timeout-minutes` is retained only as a backstop, set ≥ 1.5× the sum of step
caps so it never fires first.

Decided remediation path for the [NFM-5151](/NFM/issues/NFM-5151) blind spot:
**make stalls fail, not cancel.** Optional non-blocking hardening for a
separate follow-up: teach CI Failure Triage to also surface `cancelled`
conclusions (catches non-stall cancels).

### D4 — REJECT: proxy re-introduction (upholds ADR-018)

The brownout hit the CN mirror and the direct legs at different moments; a
proxy adds a single point of failure and measured 5.5× latency, buying no
redundancy against this failure signature. Egress stays direct.

### D5 — KEEP: retry ladders as second line

The existing apt/pip ladders stay. Post D1+D2 they rarely fire; post D3 their
exhaustion is triage-visible instead of silently cancelled.

## 3. Consequences

- The apt-stall build-failure class is eliminated from routine builds; the
  12–15 min runner-time burn per brownout event goes with it. Brownout
  exposure for apt moves to the nightly rebuild window, which is retryable
  with a sticky `stable` tag and no user-visible blast radius.
- Workflow-host apt legs (not Docker builds) remain network legs; D3 bounds
  and triage-visibilizes them. Prebaking the runner host itself is explicitly
  out of scope (noted for a future ADR if host-level stalls recur).
- Cost: one GHCR image + a nightly build of a few minutes. Supply-chain
  surface: the base image becomes a protected artifact — pushed only by the
  sanctioned workflow, provenance-attested, digest-pinned upstream.
- Reproducibility trade: consumer builds track a moving `stable` tag. Accepted
  because `nightly-YYYYMMDD` tags give an immutable audit trail and any build
  can be pinned to a date tag for bisection; `git log` of
  `build-base.Dockerfile` plus the date tag reconstructs any historical base.
- Verification gates (implementation AC): after landing, `docker build` of
  `prod-api` contains **zero apt-get invocations**; a forced pip-failure in
  Docker Build Verification produces conclusion `failure`, not `cancelled`.

## 4. Implementation

Delegated to CPO (→ Lead Engineer implementation, CI/Infra review chain,
Release Engineer merge) as a child issue of [NFM-5154](/NFM/issues/NFM-5154).
This ADR is the contract; the child issue carries scope, file paths, and
acceptance criteria.
