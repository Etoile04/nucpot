# Staging Deployment Pipeline (NFM-111)

A complete, repeatable staging deployment pipeline for the **NFM-DB** platform
(Nuclear Fuel & Materials Properties Database): bring the stack up, run
migrations, gate every deploy behind a health check, and roll back
automatically if a deploy goes bad.

This document is the source of truth referenced by the staging-host setup
ticket (NFM-112). It describes the architecture, one-time setup, day-to-day
deploy/rollback commands, the health gate, smoke tests, and troubleshooting.

---

## 1. Architecture

The staging stack is defined in [`docker-compose.staging.yml`](../../docker-compose.staging.yml)
and runs three containers on a single host (the ThinkStation staging box). All
container names are prefixed `nucpot-staging-*` (the GitHub repo is
`Etoile04/nucpot`), so `docker ps --filter name=nucpot-staging` shows the whole
stack.

| Service | Container              | Built from                       | Host → container | Purpose                              |
|---------|------------------------|----------------------------------|------------------|--------------------------------------|
| `db`    | `nucpot-staging-db`    | `postgres:16`                    | `5432` → `5432`  | Bundled staging database (`nfm_db`)  |
| `api`   | `nucpot-staging-api`   | `docker/staging-api.Dockerfile`  | `8001` → `8000`  | NFM-DB FastAPI (`/api/v1/health`)    |
| `web`   | `nucpot-staging-web`   | `docker/web.Dockerfile`          | `3000` → `3000`  | NFM-DB Next.js front-end             |

The `api` image runs `alembic upgrade head` before serving, so the staging
schema is migrated automatically on every start.

Public ingress (provisioned once via Cloudflare Tunnel — NFM-112):

- `staging.nucpot.dpdns.org` → `http://web:3000`
- `staging-api.nucpot.dpdns.org` → `http://api:8000`

> **Known follow-ups (out of pipeline scope):**
> - **Web API URL:** the Next.js build bakes `NEXT_PUBLIC_API_URL` (default
>   `http://localhost:8000`). For the browser to call the staging API, build the
>   web image with `NEXT_PUBLIC_API_URL=https://staging-api.nucpot.dpdns.org`
>   (a build-time `ARG` on `docker/web.Dockerfile`). The page still loads
>   without it, so it does not block the health gate or smoke tests.
> - **CORS:** `apps/api/src/nfm_db/main.py` currently hard-codes the CORS
>   origin to `http://localhost:3000`. Wire it to `NFM_CORS_ORIGINS` for
>   browser→staging-API calls. Server-side gates (curl/health/smoke) are
>   unaffected.

---

## 2. Prerequisites

On the staging host:

- Docker Engine + `docker compose` v2
- `git`, `curl`, `python3`
- Cloudflared (for the public tunnel)
- A checkout of this repo at `/opt/nucpot-staging/repo`

Locally (to test the pipeline scripts before pushing): Docker, `python3`, and
optionally `shellcheck` to lint [`scripts/staging_deploy.sh`](../../scripts/staging_deploy.sh).

---

## 3. One-time setup

Performed once on the staging host (tracked by NFM-112).

### 3.1 Host prep

```bash
sudo mkdir -p /opt/nucpot-staging && sudo chown "$USER" /opt/nucpot-staging
git clone https://github.com/Etoile04/nucpot.git /opt/nucpot-staging/repo
cd /opt/nucpot-staging/repo
cp docker/.env.staging.example docker/.env.staging
```

Edit `docker/.env.staging`:

```bash
openssl rand -hex 32  # → STAGING_API_SECRET_KEY
openssl rand -hex 24  # → STAGING_POSTGRES_PASSWORD
```

Then set, at minimum:

- `STAGING_API_SECRET_KEY`
- `STAGING_POSTGRES_PASSWORD`
- `STAGING_DATABASE_URL` — replace the password placeholder with the value above
- `STAGING_CORS_ORIGINS=["https://staging.nucpot.dpdns.org"]`

### 3.2 Cloudflare staging tunnel

Create a remotely-managed tunnel in Cloudflare Zero Trust; set
`STAGING_CLOUDFLARE_TUNNEL_TOKEN`. Add dashboard ingress:

- `staging.nucpot.dpdns.org` → `http://web:3000`
- `staging-api.nucpot.dpdns.org` → `http://api:8000`

Create the matching DNS records.

### 3.3 GitHub secrets + environment

Add repository secrets (Settings → Secrets and variables → Actions):

- `STAGING_SSH_HOST`, `STAGING_SSH_USER`, `STAGING_SSH_KEY`
- `GHCR_TOKEN` (only if publishing images to GHCR)

Create a **`staging`** environment (Settings → Environments → New environment).

---

## 4. Day-to-day operations

All operations go through
[`scripts/staging_deploy.sh`](../../scripts/staging_deploy.sh), run from the
repo root on the staging host.

### Deploy

```bash
./scripts/staging_deploy.sh deploy
```

What it does, in order:

1. **Snapshot** the currently-running images as a rollback target
   (`nucpot-staging-{api,web}:prev`).
2. **Build** the staging images (`docker compose build`).
3. **Bring the stack up** (`docker compose up -d --remove-orphans`). The `api`
   container runs `alembic upgrade head` (migrations) then starts uvicorn.
4. **Health gate**: poll `http://127.0.0.1:8001/api/v1/health` until it returns
   `{"status":"ok"}`, for up to `STAGING_HEALTH_TIMEOUT` (default 120s).
5. **On success**: record the last-known-good tag and print status.
6. **On failure**: automatically roll back to the `:prev` images, re-run the
   health gate, and exit non-zero.

### Status

```bash
./scripts/staging_deploy.sh status
```

### Rollback

```bash
./scripts/staging_deploy.sh rollback               # → :prev tag
./scripts/staging_deploy.sh rollback 2026.06.13-1  # → a specific tag
```

### Health gate only

```bash
./scripts/staging_deploy.sh health
```

---

## 5. Health gate + auto-rollback

The gate is the contract between "containers are up" and "staging is healthy".
It requires the staging API to answer `/api/v1/health` with
`{"status":"ok"}` within `STAGING_HEALTH_TIMEOUT` seconds.

- `/api/v1/health` is **native** to the NFM-DB API (`apps/api/.../api/v1/health.py`,
  mounted at the `/api/v1` prefix) and does not touch the database, so it
  reflects process liveness, not DB connectivity.
- Migrations run inside the `api` container *before* uvicorn starts; a failed
  migration prevents the API from serving and fails the gate.
- If the gate fails on a new deploy, the script reverts to the previous images
  and re-validates. If the rollback also fails, the stack is left in place for
  inspection and the script exits non-zero.

---

## 6. Smoke tests

[`scripts/staging_smoke_test.py`](../../scripts/staging_smoke_test.py) runs
three checks and exits non-zero on any failure:

1. **api-health** — `GET /api/v1/health` returns `{"status":"ok"}`.
2. **web-reachable** — the web origin returns HTTP < 500.
3. **container-set** — `nucpot-staging-{db,api,web}` are all running.

```bash
python3 scripts/staging_smoke_test.py
# or against the public hostnames:
python3 scripts/staging_smoke_test.py \
  --api-url https://staging-api.nucpot.dpdns.org/api/v1/health \
  --web-url https://staging.nucpot.dpdns.org/
```

Use `--skip-docker` when running off-host (e.g. from a CI runner without
docker access).

---

## 7. Deploying via GitHub Actions

The **Staging Deploy** workflow
([`.github/workflows/staging-deploy.yml`](../../.github/workflows/staging-deploy.yml))
wraps the above for one-click deploys from the Actions UI. It SSHes to the
staging host, pulls the chosen ref, runs `staging_deploy.sh deploy`, then the
smoke test.

Inputs: `ref` (default `main`), `image_tag` (default `latest`; set a dated tag
to enable named rollback), `skip_smoke`. Requires the secrets and `staging`
environment from §3.3.

---

## 8. Rollback drill

After the first deploy (NFM-112), record a rollback drill:

```bash
./scripts/staging_deploy.sh status            # confirm healthy
./scripts/staging_deploy.sh rollback          # revert to :prev
curl -fsS http://127.0.0.1:8001/api/v1/health # confirm still ok
./scripts/staging_deploy.sh deploy            # roll forward again
```

Record the before/after health in the NFM-112 thread.

---

## 9. Environment reference

All variables live in `docker/.env.staging` (git-ignored). See
[`docker/.env.staging.example`](../../docker/.env.staging.example) for the full
template.

| Variable                        | Default                                                | Notes                                          |
|---------------------------------|--------------------------------------------------------|------------------------------------------------|
| `STAGING_IMAGE_TAG`             | `latest`                                               | Tag for `api`/`web`; enables rollback          |
| `STAGING_API_HOST_PORT`         | `8001`                                                 | Host port for the API                          |
| `STAGING_WEB_HOST_PORT`         | `3000`                                                 | Host port for the web app                      |
| `STAGING_API_SECRET_KEY`        | — (required)                                           | Maps to `NFM_SECRET_KEY`                       |
| `STAGING_POSTGRES_PASSWORD`     | — (required)                                           | Bundled PG16 password                          |
| `STAGING_DATABASE_URL`          | `postgresql+asyncpg://…@nucpot-staging-db:5432/nfm_db` | Maps to `NFM_DATABASE_URL`                     |
| `STAGING_CORS_ORIGINS`          | staging origin                                         | JSON array; maps to `NFM_CORS_ORIGINS`         |
| `STAGING_HEALTH_TIMEOUT`        | `120`                                                  | Seconds the health gate will wait              |
| `STAGING_ROLLBACK_TAG`          | `prev`                                                 | Tag used as the auto-rollback target           |

---

## 10. Troubleshooting

- **Health gate fails** — check `docker compose -f docker-compose.staging.yml
  logs api`. Migration failures (bad `STAGING_DATABASE_URL`) surface here
  before uvicorn starts.
- **"No rollback image"** — the very first deploy has nothing to roll back to.
  Fix the build/config and redeploy.
- **`/api/v1/health` 404** — confirm the deployed `apps/api` still mounts the
  health router at `/api/v1` (see `apps/api/src/nfm_db/main.py`).
- **Browser can't reach the API** — see the known follow-ups in §1
  (`NEXT_PUBLIC_API_URL` build arg + CORS wiring).
