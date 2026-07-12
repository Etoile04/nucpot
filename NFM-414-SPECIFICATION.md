# NFM-414: GitHub Secrets Configuration for Production Deploy

**Parent:** NFM-412 (blocked)
**Type:** Configuration
**Priority:** High (unblocks production deploy)
**Depends on:** NFM-413 (server must exist first)

## Objective

Configure GitHub repository secrets for production deployment workflow.

## Required Secrets

The `.github/workflows/production-deployment.yml` workflow requires these secrets:

### API Deploy Secrets
| Secret Name | Description | Example |
|--------------|-------------|---------|
| `DEPLOY_HOST` | Production server hostname/IP | `prod-server.example.com` or `192.168.1.100` |
| `DEPLOY_USER` | SSH user for deploy | `nucpot-deploy` |
| `DEPLOY_KEY` | SSH private key for deploy user | PEM-formatted private key |

### Web Deploy Secrets
| Secret Name | Description | Example |
|--------------|-------------|---------|
| `VERCEL_TOKEN` | Vercel API token for web deploy | `eyJhbGciOiJ...` |
| `VERCEL_ORG_ID` | Vercel organization ID | `team_abcd1234` |
| `VERCEL_PROJECT_ID` | Vercel project ID | `prj_abcd1234` |

## Setup Instructions

1. **Generate SSH Key Pair**
   ```bash
   ssh-keygen -t ed25519 -C "github-actions-nucpot" -f ~/.ssh/nucpot_deploy
   # Add public key to deploy user's authorized_keys on server
   # Copy private key content for DEPLOY_KEY secret
   ```

2. **Configure GitHub Secrets**
   - Navigate to: GitHub repo → Settings → Secrets and variables → Actions
   - Click "New repository secret" for each secret above
   - Paste the corresponding values

3. **Verify Vercel Configuration** (if using Vercel)
   - Ensure Vercel project is connected to GitHub repo
   - Obtain VERCEL_TOKEN from Vercel account settings
   - Obtain ORG_ID and PROJECT_ID from Vercel project settings

## Verification

After configuration, verify by:
1. Triggering `production-deployment.yml` manually via `workflow_dispatch`
2. Checking that secrets are accessible (no "secret not found" errors)
3. Verifying SSH connection succeeds in deploy-api job

## Security Notes

- **DEPLOY_KEY**: Use a dedicated deploy user with minimal permissions (only git pull + sudo systemctl restart)
- **VERCEL_TOKEN**: Use token with minimal scope (only deploy permissions)
- **Rotate keys**: If a key is compromised, rotate immediately and update the secret
- **Audit log**: GitHub Actions provides audit logs for secret access

## Definition of Done

All secrets configured and verified via a test run of the production deployment workflow.

## Estimated Effort

1-2 hours

## Owner

Lead Engineer / CTO (for approval and secret management)
