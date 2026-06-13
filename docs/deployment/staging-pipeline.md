# Staging Deployment Pipeline (NFM-111)

The staging pipeline builds and deploys the NFM / NucPot stack to an isolated
staging environment, verifies it with a health gate + smoke test, and
auto-rolls back if the new revision is unhealthy. Staging mirrors the production
topology (FastAPI API, Next.js web, PostgreSQL, Redis) but runs under a separate
project name, network, and data volume.

> Spec: [NFM-111](/NFM/issues/NFM-111) · Host setup + first deploy: [NFM-112](/NFM/issues/NFM-112) · Publish: [NFM-113](/NFM/issues/NFM-113)

## Architecture

```
                   Cloudflare Zero Trust tunnel (staging.nucpot.dpdns.org)
                                       │
        ┌──────────────────────────────┼──────────────────────────────┐
        ▼                              ▼                              ▼
   web (Next.js :3000)        api (FastAPI :8000)              redis (:6379)
        │                              │
        │                              ▼
        └────────────────────►  postgres (:5432)  ◄── migrator (alembic upgrade head)
```

- **web** — Next.js standalone build (`docker/web.Dockerfile`), port 3000.
- **api** — FastAPI `nfm_db.main:app` (`docker/api.Dockerfile`), port 8000,
  health at `GET /api/health` → `200` with `ok` in the body.
- **postgres** — `postgres:16`, staging database `nfm_db`.
- **redis** — `redis:7-alpine` cache.
- **migrator** — one-shot `alembic upgrade head`, runs before app services.

## Files

| File | Purpose |
| --- | --- |
| `docker/.env.staging.example` | Non-secret template for staging config + secret placeholders. Copy to `docker/.env.staging`. |
| `docker/docker-compose.staging.yml` | Staging stack (project `nfm-staging`). |
| `scripts/staging_deploy.sh` | `deploy` / `status` / `rollback` with health gate + auto-rollback. |
| `scripts/staging_smoke_test.py` | Stdlib smoke test against the public staging URLs. |
| `.github/workflows/staging-deploy.yml` | "Staging Deploy" CI: build → deploy → smoke → rollback. |
| `docs/deployment/staging-pipeline.md` | This document. |

## Prerequisites (provisioned by NFM-112 / board)

These are tracked on [NFM-112](/NFM/issues/NFM-112) and are **not** required to
merge this pipeline — only to run a real deploy:

- **Staging host** reachable over SSH with `docker` + compose v2 installed.
- **GitHub `staging` environment** secrets:
  `STAGING_SSH_HOST`, `STAGING_SSH_PORT`, `STAGING_SSH_USER`, `STAGING_SSH_KEY`,
  `STAGING_WEB_URL`, `STAGING_API_URL`, `GHCR_TOKEN` (PAT with `write:packages`).
- **Cloudflare tunnel** ingresses:
  `staging.nucpot.dpdns.org` → `web:3000`,
  `staging-api.nucpot.dpdns.org` → `api:8000`.
- **`docker/.env.staging`** on the host, filled from the example (real secrets).

## Configuration

All knobs are environment-driven (no host values hardcoded). Copy the template
and edit:

```bash
cp docker/.env.staging.example docker/.env.staging
$EDITOR docker/.env.staging     # fill POSTGRES_PASSWORD, OSS keys, tunnel token, …
```

Key variables:

| Variable | Default | Notes |
| --- | --- | --- |
| `STAGING_WEB_URL` | `https://staging.nucpot.dpdns.org` | Health gate + smoke target. |
| `STAGING_API_URL` | `https://staging-api.nucpot.dpdns.org` | Health gate + smoke target. |
| `NFM_API_IMAGE` / `NFM_WEB_IMAGE` | _(empty → build locally)_ | Set to versioned registry tags for reliable rollback. |
| `STAGING_HEALTH_TIMEOUT_SECONDS` | `180` | Max wait for healthy after `up`. |
| `STAGING_ROLLBACK_ON_FAILURE` | `true` | Auto-rollback when the health gate fails. |

## Deploy / status / rollback

Run from the repo root (`scripts/staging_deploy.sh` resolves `REPO_ROOT`
automatically):

```bash
# Build from source, run migrations, bring up the stack, verify health.
./scripts/staging_deploy.sh deploy

# Show compose ps + live web/api health.
./scripts/staging_deploy.sh status

# Restore the last-known-good revision (recorded at the prior successful deploy).
./scripts/staging_deploy.sh rollback
```

### Health gate + auto-rollback

`deploy` runs in this order:

1. **Capture** the currently-running image refs into `.staging-state/deploy.state`.
2. **Migrate** — `docker compose run --rm migrator` (alembic upgrade head).
3. **Bring up** — `docker compose up -d` (build from source, or `--pull always`
   when `NFM_API_IMAGE`/`NFM_WEB_IMAGE` are set).
4. **Health gate** — poll `STAGING_API_URL/api/health` and `STAGING_WEB_URL/`
   until both return `200` or `STAGING_HEALTH_TIMEOUT_SECONDS` elapses.
5. On success — promote the current image refs to
   `.staging-state/last-known-good.env` and report status.
6. On failure — if `STAGING_ROLLBACK_ON_FAILURE=true`, restore the
   last-known-good image refs, re-verify, and exit non-zero. If disabled, the
   stack is left in its current (unhealthy) state with a clear warning.

### Reliable rollback

Versioned image tags (`NFM_API_IMAGE=ghcr.io/etoile04/nucpot-api:staging-<sha>`)
give a true image-level rollback: `rollback` restores the previous tag. When the
stack is built locally (no versioned tag), rollback restarts the previous
containers as a best-effort and prints a warning recommending versioned registry
tags.

## CI workflow (`.github/workflows/staging-deploy.yml`)

Triggers: push to `staging` branch or a `v*-staging` tag, or manual dispatch.

1. **build** (optional) — build + push `nucpot-api` / `nucpot-web` to GHCR,
   tagged `staging-<sha>` and `staging`. Skip with `skip_build` to build on the
   host instead.
2. **deploy** — SSH to the staging host, `git reset --hard <sha>`, export the
   image tags, run `staging_deploy.sh deploy`.
3. **smoke** — run `staging_smoke_test.py` against the staging URLs.
4. **rollback** — `if: failure()` — SSH and run `staging_deploy.sh rollback`.

`concurrency: staging-deploy` with `cancel-in-progress: false` serializes deploys.

## Smoke test

Stdlib-only; runs in CI, on the host, or locally:

```bash
python3 scripts/staging_smoke_test.py \
  --web-url https://staging.nucpot.dpdns.org \
  --api-url https://staging-api.nucpot.dpdns.org
```

Checks: API `/api/health` (P0), API `/api/potentials` (P1), web `/` (P0),
web `/browse` (P1). Exits `0` when all pass, `1` on any failure. Writes
`smoke_passed` / `smoke_total` / `smoke_failed` to `$GITHUB_OUTPUT` in Actions.

## Rollback drill

Once staging is live, verify the rollback path is real:

```bash
# 1. Deploy a known-good revision.
./scripts/staging_deploy.sh deploy

# 2. Introduce a deliberate regression (e.g. bad image tag) and deploy.
NFM_API_IMAGE=ghcr.io/etoile04/nucpot-api:broken ./scripts/staging_deploy.sh deploy
#    → health gate fails → auto-rollback fires → staging serves last-known-good.

# 3. Confirm staging is healthy again.
./scripts/staging_deploy.sh status
```

## Troubleshooting

- **`Refusing to deploy: … CHANGE_ME placeholders`** — fill real secrets in
  `docker/.env.staging`.
- **Health gate fails after `up`** — check `docker compose logs api web`;
  common causes: migrations did not run (run `staging_deploy.sh deploy`, not raw
  `up`), wrong `NFM_DATABASE_URL`, or Cloudflare tunnel not pointing at the
  services.
- **`No last-known-good state to roll back to`** — the first deploy has nothing
  to roll back to; this is expected.
- **Workflow does not run** — the `staging` GitHub environment and its secrets
  are provisioned by NFM-112; until then the workflow is reviewed code, not
  executing.
