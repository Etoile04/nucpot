# ADR-022 — Build-egress resilience: pre-baked GHCR base, pip cache mounts, fail-don't-cancel (NFM-5154)

| Field | Value |
| --- | --- |
| **Status** | Accepted (amended 2026-09-23 — D2 re-scoped to BuildKit-verified build paths; [NFM-5169](/NFM/issues/NFM-5169); amended 2026-09-24 — D1 publishes multi-arch, [NFM-5203](/NFM/issues/NFM-5203); amended 2026-09-24 — D5 pip ladders span distinct mirrors, [NFM-5209](/NFM/issues/NFM-5209)) |
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

### 1.2 Current state on `origin/main` at decision time (grounding; pip ladders since amended by D5/NFM-5209)

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

**Amended 2026-09-24 ([NFM-5203](/NFM/issues/NFM-5203)):** the nightly
publishes a **multi-arch manifest list** — `platforms:
linux/amd64,linux/arm64`, with `docker/setup-qemu-action` registered before
the build so the arm64 leg executes under emulation on the amd64 hosted
runner. `stable` is resolved by BOTH amd64 CI consumers and the arm64
production deploy host (classic builder, `DOCKER_BUILDKIT=0`); the original
builder-native-arch publish served amd64 only, so the deploy host's `FROM`
failed with "no matching manifest for linux/arm64/v8" (2026-09-23 Production
Deployment hard-down, 4 red runs — CI stayed green because its consumers are
amd64). The build timeout backstop was raised 15m → 25m to cover the
QEMU-emulated arm64 apt legs. Pinned by guard tests in
`scripts/tests/test_build_base_consumers.py` (`REQUIRED_BASE_PLATFORMS`).

### D2 — ADOPT (amended 2026-09-23, [NFM-5169](/NFM/issues/NFM-5169)): pip BuildKit cache mounts — BuildKit-verified build paths only

**Original scope (superseded):** replace `pip install --no-cache-dir` with
`RUN --mount=type=cache,target=/root/.cache/pip pip install …` in all consumer
Dockerfiles; BuildKit on every build path ("verify at implementation").

**Verified constraint (2026-09-23, deploy host — the verification came back
negative, two ways):**

1. `DOCKER_BUILDKIT=1 docker build` through the G2 RO gate
   (`unix:///var/run/nfm-g2/docker-ro.sock`) routes buildx to the
   docker-container driver, whose boot container the gate rejects:
   `container config rejected: Privileged=true. nfm-g2 (NFM-4270 / ADR-013
   G5)`.
2. The classic builder (`DOCKER_BUILDKIT=0`, pinned in
   `production-deployment.yml` L516 candidate build, `scripts/deploy_prod.sh`
   ×3, `scripts/staging_deploy.sh`) hard-errors on `RUN --mount` ("the
   --mount option requires BuildKit").

**Amended scope (normative rule):** BuildKit-only syntax (`RUN --mount=…`,
heredocs, `RUN --ssh`) is permitted ONLY in Dockerfiles whose *every* build
path has BuildKit verified available (today: CI jobs on ubuntu-latest with
setup-buildx-action). A Dockerfile is a single artifact consumed by all its
build paths — one non-BuildKit path vetoes BuildKit syntax for the whole
file. Dockerfiles with any deploy-host build path MUST remain
classic-builder-clean.

- `prod-api` and `staging-api` are on deploy-host paths (candidate build +
  deploy scripts) → keep `pip install --no-cache-dir` + the distinct-mirror
  pip ladder (D5) as the pip resilience line. `lightrag` / `web` / `e2e-*`
  follow the same rule per the [NFM-5159](/NFM/issues/NFM-5159) build-matrix
  audit.
- Wheel-cache benefit concentrates on CI (every push/PR); deploy-host builds
  are low-frequency, so cache warmth there is marginal against the cost below.
- D1 (base `FROM`, apt-ladder deletion) and D3 (fail-don't-cancel) are
  unaffected on all paths.

**Rejected alternative (NFM-5169 Option B):** commissioning an nfm-g2
(root-owned) gate change to admit buildkit's privileged boot container, or
sanctioned buildx provisioning on the deploy host (incl. the NFM-848
locked-keychain constraint). Rejected now: G5 is a deliberate non-privilege
invariant on the prod deploy host; weakening it buys pip-cache warmth on
low-frequency deploy builds whose residual pip risk is already bounded by D5
and triage-visibilized by D3. This is the same category of trade as rejected
D4 — boundary weakening for resilience convenience.

**Re-open trigger:** ≥2 deploy-host pip-ladder exhaustions in a rolling
30 days post-D1 landing, or any single deploy build burning >15 min on pip
legs → commission Option B as a separately-costed infra track (gate owner +
security review + NFM-848 constraint; rootless buildx provisioning preferred
over privileged boot-container admission if it preserves the G5 non-privilege
invariant). Until then the trigger is measured, not speculated.

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

**Amended 2026-09-24 ([NFM-5209](/NFM/issues/NFM-5209)):** a pip retry
ladder's legs must span **distinct mirror indexes** — repeating the same
mirror (the pre-amendment tuna×2 shape) is no fallback: a single mirror
outage exhausted the whole ladder during the 2026-09-24 candidate-build
brownout (tuna hard-403'd the setuptools≥75.0 wheel, run 36003157519; the
pypi.org-direct leg then died on the CN-egress read-timeout, run
35991349533). Contract for `prod-api` / `lightrag` pip ladders: ≥ 2
distinct CN mirror indexes (tuna → aliyun), every leg bounded
(`--default-timeout` + `--retries`), exactly one un-indexed pypi.org-direct
leg as last resort. Pinned by
`test_pip_ladders_span_distinct_mirror_indexes` in
`scripts/tests/test_build_base_consumers.py`.

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
