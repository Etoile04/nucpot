# Production Deployment Runbook

**Audience:** on-call operators handling the
`wenjiedeMac-Studio` self-hosted production runner when
`.github/workflows/production-deployment.yml` is blocked or a hot
build must be pushed without waiting on CI.

**Source of truth for the contract:** the Production Deployment
workflow at `.github/workflows/production-deployment.yml`. Anything in
this runbook that drifts from the workflow is a bug — fix the runbook,
not the operator.

---

## 1. When to use this runbook

Use it when **one** of the following holds:

1. The CI `pre-flight` job ([NFM-3842](../../issues/NFM-3842))
   reports `github.com:443 unreachable after 10 s` — i.e. the GitHub
   egress path is dead and CI cannot self-recover.
2. The self-hosted runner is otherwise healthy (Docker daemon
   reachable, `~/.ssh/id_ed25519_paperclip_egress` present,
   `~/.ssh/deploy_key` present) but a deploy must happen in the next
   hour and the operator decides not to wait for SRE to repair the
   egress.
3. A human reviewer is exercising an emergency hot-fix that has
   already been merged to `main` and needs the SHA-pinned image in
   front of users before the next CI green.

If **all** of the upstream infrastructure (Docker, compose, network
to `nucpot.dpdns.org`) is broken, escalate to SRE and do **not** try
to bypass this runbook.

---

## 2. Sanctioned local-build recipe

Always rebuild through Docker Compose. **Never** `docker run` a
one-off container — bare `docker run` strips the compose labels the
cost dashboard depends on, so any container started that way is
invisible to billing and won't be cleaned up by `prune.sh`.

The sanctioned command (per [NFM-3818](../../issues/NFM-3818)) is:

```bash
docker compose -f docker-compose.prod.yml build <svc> && \
  docker compose -f docker-compose.prod.yml up -d <svc>
```

### 2.1. Buildable services

`docker-compose.prod.yml` defines six services. `db` and `redis`
pull base images only; the remaining four are rebuildable locally:

| `svc`         | Image tag (with `PROD_IMAGE_TAG`)               | Notes                                                       |
| ------------- | ----------------------------------------------- | ----------------------------------------------------------- |
| `api`         | `nucpot-prod-api:${PROD_IMAGE_TAG:-latest}`     | Shared by `worker` — one build covers both.                 |
| `lightrag`    | `nucpot-prod-lightrag:${PROD_IMAGE_TAG:-latest}` | Standalone RAG sidecar.                                     |
| `worker`      | `nucpot-prod-api:${PROD_IMAGE_TAG:-latest}`     | **Re-tag, do not rebuild** — uses the `api` image.          |
| `web`         | `nucpot-prod-web:${PROD_IMAGE_TAG:-latest}`     | Next.js frontend, built in `apps/web/`.                     |

`db` (pgvector/pgvector:pg16) and `redis` (redis:7-alpine) are
upstream base images — do **not** rebuild locally. They update on
the next CI run after a deliberate base bump.

### 2.2. Tag locally-built images with the SHA CI would have used

CI pins every image to the deploying commit SHA
(`PROD_IMAGE_TAG: ${{ github.sha }}` in `deploy-prod`). Local builds
must use the same tag so:

- A bad local deploy can be rolled back by re-tagging the previous
  SHA, not by rebuilding.
- The local tag and the CI tag are byte-identical, so any container
  started locally can be replaced by the next CI deploy without a
  manual tag dance.

```bash
# Use the head SHA of origin/main — same tag CI will produce for
# the next run.
PROD_IMAGE_TAG="$(git rev-parse origin/main)" docker compose \
  -f docker-compose.prod.yml build api
```

Always read `origin/main`, not `HEAD` — a local branch ahead of
`origin/main` is what `pre-deploy-assert` is designed to catch.

### 2.3. Preserve compose labels for the cost dashboard

`docker run` strips the per-service labels that
`docker-compose.prod.yml` attaches via the `labels:` block. The cost
dashboard reads those labels to attribute container-hours to the
right environment. Restarting a stripped container with `up -d`
re-adds them — so the only safe local path is through
`docker compose … up -d <svc>`.

The labels are also used by `tools/prod-tag-retention/prune.sh` to
distinguish locally-built images from base pulls. A `docker run`
container escapes both the dashboard and the pruner.

### 2.4. Full sequence (canonical example)

```bash
set -euo pipefail
cd ~/Projects/nucpot
git fetch origin main
git reset --hard origin/main

export PROD_IMAGE_TAG="$(git rev-parse HEAD)"
echo "==> Local build with PROD_IMAGE_TAG=${PROD_IMAGE_TAG}"

docker compose -f docker-compose.prod.yml build api web
docker compose -f docker-compose.prod.yml up -d api worker web
```

`worker` is restarted, not rebuilt — it shares the `api` image.

### 2.5. Verification

After a local build, mirror the same checks CI runs:

```bash
# Compose-level health (labels intact, services up)
docker compose -f docker-compose.prod.yml ps

# Tag parity with what CI would build
docker image ls "nucpot-prod-api:${PROD_IMAGE_TAG}"
docker image ls "nucpot-prod-web:${PROD_IMAGE_TAG}"

# Schema-version parity (NFM-2141)
bash tools/migration-on-main-assert/assert.sh \
  --image "nucpot-prod-api:${PROD_IMAGE_TAG}" \
  --base-ref origin/main \
  --repo-root "$(pwd)" \
  --audit-log /tmp/local-deploy-audit.jsonl
```

---

## 3. When CI comes back green, drain the local detour

A local build is a temporary measure. As soon as CI can deploy
again:

1. Confirm `origin/main` HEAD matches the SHA you used locally
   (`git rev-parse origin/main` == the `PROD_IMAGE_TAG` you set).
2. Let the next CI `deploy-prod` run overwrite the locally-built
   containers. CI uses the same `PROD_IMAGE_TAG`, so the tag collision
   is intentional and safe.
3. Do **not** delete locally-built images manually — they age out via
   `tools/prod-tag-retention/prune.sh`, which retains the last 10
   successful `nucpot-prod-*` tags per image.

---

## 4. Anti-patterns (will block cost dashboard / cleanup / rollback)

- `docker run --rm -d …` against an image that compose owns — strips
  labels, invisible to billing, never pruned.
- `docker tag <random-sha> nucpot-prod-api:latest` and then
  `up -d` — overwrites the SHA-pinned image and breaks rollback.
- Building locally without `PROD_IMAGE_TAG` — the default `:latest`
  collides with whatever CI built last and silently serves the wrong
  commit.
- Editing `docker-compose.prod.yml` on the host and not committing it
  back. CI uses the in-repo copy; a drift between host and repo
  means the next CI deploy reverts your local fix.

---

## 5. References

- [NFM-3818](../../issues/NFM-3818) — local-build recipe sanction
  (cost-dashboard label preservation).
- [NFM-3837](../../issues/NFM-3837) — root cause of the GFW egress
  failure this runbook exists to mitigate.
- [NFM-3842](../../issues/NFM-3842) — `pre-flight` probe + SSH
  checkout fix in `.github/workflows/production-deployment.yml`.
- [NFM-2141](../../issues/NFM-2141) — alembic-on-main assertion
  (`tools/migration-on-main-assert/assert.sh`).
- [NFM-2148](../../issues/NFM-2148) — SHA-pinned image provenance
  (`PROD_IMAGE_TAG: ${{ github.sha }}`).
- ADR-NFM-2139 §5 D1 — design rationale for SHA-pinning + the 10-tag
  retention window.