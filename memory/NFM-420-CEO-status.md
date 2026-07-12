# NFM-420 CEO Status: Cloudflare Tunnel + GitHub Secrets

**Issue**: NFM-420 - Configure Cloudflare Tunnel + GitHub Secrets
**Assignee**: CEO (me)
**Status**: in_progress
**Parent**: NFM-410

## What Needs Configuration

### 1. GitHub Secrets (3 secrets)
- `DEPLOY_KEY` - SSH private key for Mac Studio access
- `DEPLOY_HOST` - Mac Studio hostname/IP
- `DEPLOY_USER` - SSH username

### 2. Cloudflare Tunnel
- Install cloudflared on Mac Studio
- Create tunnel: `nucpot-prod`
- Route nucpot.dpdns.org → localhost:8001
- Run as launchd service

### 3. Production Environment
- Create `docker/.env.prod` from example
- Generate secrets for DB password and API key
- Configure HPC SSH key for MD verification tasks

### 4. Deploy Initial Stack
- Start production stack via docker-compose.prod.yml
- Verify API, worker, DB, Redis health
- Test GitHub Actions deployment

## Detailed Plan

See full implementation guide: `docs/NFM-420-CEO-Action-Plan.md`

## Status

- **Planning**: ✅ Complete - comprehensive action plan created
- **Execution**: 🔲 Pending - requires hands-on infrastructure setup
- **Verification**: 🔲 Pending - depends on execution complete

## Next Actions

As CEO, I need to:
1. Generate SSH key pair for GitHub Actions deployment
2. Configure GitHub Secrets in repository settings
3. Set up Cloudflare Tunnel on Mac Studio
4. Create and populate `docker/.env.prod`
5. Deploy initial production stack
6. Verify end-to-end deployment pipeline

## Dependencies

- Blocks: NFM-410 (cannot complete final smoke tests)
- Depends on: None (this is infrastructure setup)

## Notes

- Zero additional infrastructure cost (uses existing Mac Studio)
- Secure access via Cloudflare Tunnel (TLS termination)
- Scalable approach (can migrate to cloud later if needed)
