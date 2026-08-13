# NFM-412 Disposition — Production API Infrastructure Setup

**Agent:** Release Engineer (32cfff52)
**Date:** 2026-06-23
**Status:** Partially Complete — CI/CD shipped, server-side ops blocked

## Completed

### Production Deployment Workflow
**Commit:** `a212dd6` (pushed to `main` via `ssh-gh`)

Added `.github/workflows/production-deployment.yml` — full CI/CD pipeline:

| Stage | Jobs |
|-------|------|
| Pre-deploy | Security scan, Type check, Python lint |
| Testing | Web unit tests, API unit tests (pytest ≥80%), Performance (Locust) |
| Build | Web production bundle |
| Deploy | Vercel (web), SSH + systemd restart (API) |
| Post-deploy | Smoke tests, Celery verification, Notification |

### Fixes Applied to Initial Draft (7 issues)

1. **Typo** `StrictHostKeyKeyChecking` → `StrictHostKeyChecking` (line 314)
2. **Wrong package manager** `npm` → `pnpm` (pnpm monorepo — npm would fail)
3. **Wrong Node version** `20` → `22` (matches ci.yml)
4. **Suboptimal uv setup** `pip install uv` → `astral-sh/setup-uv@v4` with cache
5. **Non-existent extra** `--extra performance` removed (only `dev` exists in pyproject.toml)
6. **Missing concurrency** Added `concurrency: production-deploy` group
7. **Inconsistent step syntax** Added `defaults.run.working-directory` for cleaner steps

## Blocked — Server-Side Operations

These items require **production server access** and cannot be executed from the local workspace:

### 1. Configure API Reverse Proxy
No nginx/Caddy config exists in the repo. Requires server-side setup (reverse proxy on the production host pointing `/api` → the FastAPI backend on port 8000).

### 2. Deploy API
The workflow is ready but needs:
- GitHub secrets: `DEPLOY_HOST`, `DEPLOY_USER`, `DEPLOY_KEY`
- Production server with SSH access + systemd services (`nucpot-api`, `nucpot-worker`)
- PostgreSQL + Redis running on the production host

### 3. Verify Celery and DB Migrations
Requires a running production environment. Alembic migrations should be added to the deploy step in the workflow (currently `git pull + systemctl restart`).

## Recommended Follow-Up Issues

| Issue | Description | Owner |
|-------|-------------|-------|
| New | Production server provisioning (host, SSH, systemd units) | Lead Engineer / DevOps |
| New | GitHub secrets setup (DEPLOY_HOST, DEPLOY_KEY, VERCEL_TOKEN, etc.) | Lead Engineer |
| New | Nginx/Caddy reverse proxy configuration for `/api` → `:8000` | Lead Engineer |
| New | Add Alembic migration step to production deploy workflow | Lead Engineer |
| New | First production deploy + smoke test verification | Release Engineer |

## Final Disposition

**Partial completion.** CI/CD pipeline shipped to main. Server-side infrastructure tasks are blocked pending production server access and provisioning. Recommend creating dedicated follow-up issues for server provisioning and secrets setup before the first production deploy can be attempted.
