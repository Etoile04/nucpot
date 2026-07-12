# NFM-420 CEO Action Plan: Cloudflare Tunnel + GitHub Secrets

## Objective

Configure production deployment infrastructure for NFM-410 production deployment.

## Architecture

**Production Stack**: Mac Studio local deployment + Cloudflare Tunnel reverse proxy

```
┌─────────────────┐     Cloudflare      ┌──────────────┐
│ External User  │ ───────────────────▶ │ Cloudflare   │
└─────────────────┘   Tunnel (TLS)     │    Tunnel     │
                                         └───────┬──────┘
                                                 │
                                                 ▼
                                   ┌─────────────────────────┐
                                   │   Mac Studio (Local)    │
                                   │ ┌─────────────────────┐ │
                                   │ │ Docker Compose Stack │ │
                                   │ │ - API (port 8001)    │ │
                                   │ │ - Worker             │ │
                                   │ │ - PostgreSQL (5433)  │ │
                                   │ │ - Redis (6380)       │ │
                                   │ └─────────────────────┘ │
                                   └─────────────────────────┘
```

## Required Configurations

### 1. GitHub Secrets (Repository Settings)

Navigate to: Repository → Settings → Secrets and variables → Actions

Add the following secrets:

| Secret Name | Description | How to Generate |
|-------------|-------------|-----------------|
| `DEPLOY_KEY` | SSH private key for Mac Studio access | `ssh-keygen -t ed25519 -C "github-actions" -f ~/.ssh/nucpot_deploy` |
| `DEPLOY_HOST` | Mac Studio hostname/IP address | Local Mac IP (e.g., `192.168.1.100`) or Dynamic DNS |
| `DEPLOY_USER` | SSH username on Mac Studio | Typically `lwj04` or your macOS username |

**SSH Key Setup**:
```bash
# Generate SSH key pair
ssh-keygen -t ed25519 -C "github-actions" -f ~/.ssh/nucpot_deploy

# Add public key to Mac Studio ~/.ssh/authorized_keys
cat ~/.ssh/nucpot_deploy.pub >> ~/.ssh/authorized_keys

# Copy private key content for GitHub Secret DEPLOY_KEY
cat ~/.ssh/nucpot_deploy
```

### 2. Cloudflare Tunnel Configuration

#### Prerequisites
- Cloudflare account with domain `dpdns.org`
- `nucpot.dpdns.org` DNS record configured

#### Tunnel Setup

```bash
# Install cloudflared on Mac Studio
brew install cloudflared

# Authenticate with Cloudflare
cloudflared tunnel login

# Create tunnel
cloudflared tunnel create nucpot-prod

# Configure tunnel routing
cloudflared tunnel route dns nucpot-prod nucpot.dpdns.org

# Create tunnel configuration file
mkdir -p ~/.cloudflared
cat > ~/.cloudflared/config.yml << 'EOF'
tunnel: <TUNNEL_ID_FROM_ABOVE>
credentials-file: /Users/lwj04/.cloudflared/<TUNNEL_ID>.json

ingress:
  - hostname: nucpot.dpdns.org
    service: http://localhost:8001
  - service: http_status:404
EOF

# Run tunnel as service (launchd)
cloudflared service install
sudo launchctl start com.cloudflare.cloudflared
```

**Verify Tunnel**:
```bash
# Test from external network (not local Mac)
curl https://nucpot.dpdns.org/api/health
# Should respond with health check JSON
```

### 3. Production Environment Variables

Create `docker/.env.prod` from example:

```bash
cp docker/.env.prod.example docker/.env.prod
```

**Generate Secrets**:
```bash
# Generate strong random passwords
openssl rand -base64 32  # For PROD_POSTGRES_PASSWORD
openssl rand -base64 32  # For PROD_API_SECRET_KEY
```

**Fill `docker/.env.prod`**:
```bash
# PostgreSQL
PROD_POSTGRES_USER=nfm
PROD_POSTGRES_PASSWORD=<GENERATED_PASSWORD>
PROD_POSTGRES_DB=nfm_db
PROD_DB_HOST_PORT=5433

# API
PROD_API_SECRET_KEY=<GENERATED_SECRET_KEY>
PROD_API_HOST_PORT=8001
PROD_CORS_ORIGINS=["https://nucpot.dpdns.org"]
PROD_ACCESS_TOKEN_EXPIRE_MINUTES=30

# Worker
PROD_WORKER_CONCURRENCY=1

# HPC (Guangzhou Supercomputing) - Optional for Phase 1
NFM_HPC_PRIMARY_HOST=
NFM_HPC_PRIMARY_USER=

# Redis
PROD_REDIS_HOST_PORT=6380
```

**HPC SSH Key Setup** (for MD verification tasks):
```bash
# Mount HPC SSH key as Docker secret
docker secret create hpc_ssh_key ~/.ssh/hpc_guangzhou_key

# Or for docker-compose without swarm (mount as volume)
# Add to docker-compose.prod.yml:
# volumes:
#   - ~/.ssh/hpc_guangzhou_key:/run/secrets/hpc_ssh_key:ro
```

### 4. Deploy Initial Stack

```bash
# On Mac Studio
cd ~/nucpot
git pull origin main

# Start production stack
docker compose -f docker-compose.prod.yml --env-file docker/.env.prod up -d --build

# Wait for services to start (40s for API health check)
sleep 45

# Verify services
docker compose -f docker-compose.prod.yml ps
docker exec nucpot-prod-api curl -f http://localhost:8000/api/v1/health
docker exec nucpot-prod-worker celery -A nfm_db.services.celery_app:celery_app inspect active
```

### 5. Verify GitHub Actions Deployment

Trigger a manual deployment:
```bash
# Via GitHub UI: Actions → Production Deployment → Run workflow
# Or via gh CLI:
gh workflow run production-deployment.yml
```

Check deployment logs in GitHub Actions for successful:
- SSH connection to Mac Studio
- Docker compose rebuild
- Health checks passing
- External smoke tests passing

## Verification Checklist

Once all configurations are complete:

- [ ] GitHub Secrets configured (DEPLOY_KEY, DEPLOY_HOST, DEPLOY_USER)
- [ ] Cloudflare Tunnel routes nucpot.dpdns.org → localhost:8001
- [ ] `docker/.env.prod` exists with all secrets
- [ ] Production stack running on Mac Studio (`docker compose ps`)
- [ ] API health check responds: `curl https://nucpot.dpdns.org/api/health`
- [ ] Web app loads: `curl https://nucpot.dpdns.org/`
- [ ] Celery workers healthy: `docker exec nucpot-prod-worker celery inspect active`
- [ ] GitHub Actions can SSH and deploy successfully

## Unblocking NFM-410

When NFM-420 is complete:
1. Mark NFM-420 as `done`
2. Update NFM-410 blockers: "NFM-420 complete - waiting on NFM-421 database migration"
3. NFM-410 can proceed with final smoke tests once NFM-421 completes

## Notes

- **Security**: All secrets stored in GitHub Secrets (never in code)
- **TLS**: Cloudflare Tunnel provides TLS termination
- **Zero Cost**: Leverages existing Mac Studio, no cloud infrastructure needed
- **Scalable**: Can migrate to cloud deployment later if needed
