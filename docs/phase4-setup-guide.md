# Phase 4 Deployment & Operations Guide

## 1. Architecture Overview

| Component | Technology | Location |
|-----------|-----------|----------|
| Frontend | Next.js | Vercel → [https://nucpot.dpdns.org](https://nucpot.dpdns.org) |
| Verify service | Docker (FastAPI) | ThinkStation `z203@100.70.30.21`, port 8001 |
| Cloudflare Tunnel | cloudflared | `nucpot-verify` → [https://verify.nucpot.dpdns.org](https://verify.nucpot.dpdns.org) |
| Database (app) | PostgreSQL | ThinkStation Docker (`nucpot-nucpot-db-1`, db=nucpot, user=nucpot) |
| Database (NFMD) | Supabase | Mac Studio Docker (localhost:54321) |

```
┌─────────────┐     Cloudflare Tunnel      ┌──────────────────┐
│  Vercel      │◄──────────────────────────►│  ThinkStation    │
│  (Frontend)  │     verify.nucpot.dpdns.org│  Docker Compose  │
│  nucpot.dpdns.org                    │  :8001 verify svc  │
└──────┬──────┘                            │  :5432 PostgreSQL │
       │                                    └──────────────────┘
       │ Supabase API
       ▼
┌─────────────┐
│  Mac Studio  │
│  Supabase    │
│  localhost:54321 │
└─────────────┘
```

## 2. CI/CD Pipeline

### GitHub Actions Workflows

| Workflow | Trigger | Steps | Purpose |
|----------|---------|-------|---------|
| **CI** | Push/PR to `main` | lint → tsc → build → test | Full validation gate |
| **Deploy Check** | PR to `main` only | lint + tsc (parallel) | Fast feedback on PRs |
| **Vercel Deploy** | Push to `main` (auto) | Vercel build & deploy | Production deployment |

### Pre-push Hook (Local)

Installed automatically via `npm run prepare` (runs on `postinstall`):

```bash
# Installed by husky → runs tsc --noEmit before every push
# If TypeScript errors exist, push is blocked

# To install manually:
npm run prepare
```

### Deployment Flow

1. Developer pushes to feature branch → PR created
2. **Deploy Check** runs (lint + tsc, parallel, ~30s)
3. Developer merges PR → **CI** runs (full suite)
4. On `main` → Vercel auto-deploys to production

## 3. ThinkStation Service Management

All services use **user-level systemd** — no `sudo` required.

### Cloudflared Tunnel

```bash
# Status
systemctl --user status cloudflared-nucpot

# Start (after killing any manual cloudflared process)
systemctl --user start cloudflared-nucpot

# Stop
systemctl --user stop cloudflared-nucpot

# Restart (e.g., after config change)
systemctl --user restart cloudflared-nucpot

# Logs (follow)
journalctl --user -u cloudflared-nucpot -f
```

### Verify Service (Docker Compose)

```bash
# Status
systemctl --user status nucpot-autovc

# Start
systemctl --user start nucpot-autovc

# Stop
systemctl --user stop nucpot-autovc

# Logs (follow)
journalctl --user -u nucpot-autovc -f

# Container-level logs
docker logs nucpot-verify-service-1
```

### Enabling on Boot

```bash
# Enable services to start automatically on login
systemctl --user enable cloudflared-nucpot
systemctl --user enable nucpot-autovc

# Enable lingering (keeps user services running after logout)
sudo loginctl enable-linger z203
```

## 4. Deploying Updates

### Verify Service

```bash
# SSH to ThinkStation
ssh z203@100.70.30.21

# Pull latest code and rebuild
cd ~/projects/nucpot-autovc
git pull
docker compose build
docker compose up -d
```

### Cloudflared Configuration

```bash
# Copy new config
cp new-config.yml ~/.cloudflared/config.yml

# Restart tunnel
systemctl --user restart cloudflared-nucpot
```

### Frontend

Frontend is auto-deployed by Vercel on push to `main`. No manual steps needed.

Ensure these environment variables are set in Vercel dashboard:

- `NEXT_PUBLIC_SUPABASE_URL`
- `NEXT_PUBLIC_SUPABASE_ANON_KEY`

## 5. Database

### Connecting

```bash
# From ThinkStation (Docker)
docker exec -it nucpot-nucpot-db-1 psql -U nucpot -d nucpot

# From remote (via SSH tunnel)
ssh -L 5432:localhost:5432 z203@100.70.30.21
psql -h localhost -U nucpot -d nucpot
```

### Reference Values Table

```sql
-- Query reference data
SELECT element_system, phase, property, value, unit, temperature
FROM reference_values
ORDER BY element_system, phase, property;

-- Example: elastic constants for BCC U-Mo
SELECT * FROM reference_values
WHERE element_system = 'U-Mo' AND phase = 'BCC';
```

### Schema

| Column | Type | Description |
|--------|------|-------------|
| `element_system` | `varchar` | U, Mo, Zr, Fe, Nb, U-Mo, U-Zr |
| `phase` | `varchar` | BCC, HCP, FCC, fluorite, orthorhombic |
| `property` | `varchar` | lattice_constant, C11, C12, C44, bulk_modulus, etc. |
| `value` | `double precision` | Numerical value |
| `unit` | `varchar` | Å, GPa, eV |
| `temperature` | `double precision` | Temperature in Kelvin (default: 300) |

### Supabase (NFMD) — Mac Studio

```bash
# Access via local Supabase Docker
docker exec -it supabase-db psql -U postgres -d postgres

# Or connect via Supabase Studio
open http://localhost:54323
```

## 6. Troubleshooting

### Vercel Build Fails

1. Check GitHub Actions CI — if CI fails, fix the errors first
2. If CI passes but Vercel fails → check environment variables in Vercel dashboard
3. Check Vercel build logs for Node.js version mismatch

### Verify Service Down

```bash
# On ThinkStation: check if service responds
curl http://localhost:8001/api/health

# If unhealthy, check service status
systemctl --user status nucpot-autovc

# Check container logs
docker logs nucpot-verify-service-1

# Restart
systemctl --user restart nucpot-autovc
```

### Cloudflare Tunnel Down

```bash
# Check service status
systemctl --user status cloudflared-nucpot

# Check logs for DNS/connection errors
journalctl --user -u cloudflared-nucpot -f

# Verify credentials exist
ls ~/.cloudflared/credentials.json

# Restart
systemctl --user restart cloudflared-nucpot
```

### Pre-push Hook Blocks Push

```bash
# Run type-check manually to see errors
npx tsc --noEmit

# Fix TypeScript errors, then push normally

# Emergency bypass (NOT recommended — skips type safety)
git push --no-verify
```

### Container Won't Start

```bash
# Check Docker is running
docker ps

# Inspect container logs
docker logs nucpot-verify-service-1

# Check compose config
cd ~/projects/nucpot-autovc
docker compose config

# Rebuild from scratch
docker compose down
docker compose build --no-cache
docker compose up -d
```

### Database Connection Issues

```bash
# Check PostgreSQL is running
docker ps | grep nucpot-db

# Test connection
docker exec -it nucpot-nucpot-db-1 psql -U nucpot -d nucpot -c "SELECT 1;"

# Check if port is open
ss -tlnp | grep 5432
```

## 7. Secrets

| Location | Secret | Notes |
|----------|--------|-------|
| **GitHub** (repo secrets) | `NEXT_PUBLIC_SUPABASE_URL` | Public (client-side), prefixed `NEXT_PUBLIC_` |
| **GitHub** (repo secrets) | `NEXT_PUBLIC_SUPABASE_ANON_KEY` | Public (client-side) |
| **Vercel** (env vars) | `NEXT_PUBLIC_SUPABASE_URL` | Mirrored from GitHub |
| **Vercel** (env vars) | `NEXT_PUBLIC_SUPABASE_ANON_KEY` | Mirrored from GitHub |
| **ThinkStation** | `~/.cloudflared/credentials.json` | Auto-generated by `cloudflared tunnel login` |
| **ThinkStation** | `~/projects/nucpot-autovc/.env` | `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` |
| **Mac Studio** | `.env.local` (project root) | Supabase local dev credentials |

### Rotating Secrets

```bash
# Cloudflare tunnel credentials (if compromised)
cloudflared tunnel logout
rm ~/.cloudflared/credentials.json
cloudflared tunnel login nucpot-verify
systemctl --user restart cloudflared-nucpot

# Supabase service role key (regenerate in Supabase dashboard)
# Then update .env on ThinkStation and Vercel
```
