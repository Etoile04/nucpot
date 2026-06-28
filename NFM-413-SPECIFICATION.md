# NFM-413: Production Server Provisioning

**Parent:** NFM-412 (blocked)
**Type:** Infrastructure
**Priority:** High (unblocks production deploy)

## Objective

Provision production server for NFMD API deployment.

## Requirements

1. **Server Specifications**
   - OS: Ubuntu 22.04 LTS or compatible
   - RAM: ≥4GB
   - CPU: ≥2 cores
   - Storage: ≥40GB SSD

2. **Services Required**
   - OpenSSH server (for deploy workflow)
   - Python 3.12+
   - PostgreSQL 15+
   - Redis 7+
   - systemd (for service management)

3. **Network Setup**
   - SSH access from GitHub Actions (IP whitelist or key-based)
   - Firewall rules for ports 80, 443, 8000 (API), 6379 (Redis)
   - Domain: nucpot.dpdns.org (already registered)

4. **Systemd Services**
   - `nucpot-api.service` — FastAPI application
   - `nucpot-worker.service` — Celery worker
   - Both managed via systemd, auto-restart on failure

5. **Directory Structure**
   ```
   /var/www/nucpot/
   ├── api/        # Git checkout of nucpot repo (apps/api)
   ├── worker/     # Same checkout (symlink or separate clone)
   └── logs/       # Application logs
   ```

## Deliverables

- [ ] Server provisioned with above specs
- [ ] SSH access configured for deploy user
- [ ] PostgreSQL installed and configured
- [ ] Redis installed and configured
- [ ] systemd unit files created and tested
- [ ] Directory structure created
- [ ] `nucpot` user created with appropriate permissions
- [ ] Firewall configured

## Definition of Done

Server is ready for the first production deployment via the GitHub Actions workflow (NFM-412 commit `a212dd6`).

## Estimated Effort

4-8 hours (depends on hosting provider and existing infrastructure)

## Owner

Lead Engineer / DevOps
