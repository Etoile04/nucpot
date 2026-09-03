#!/usr/bin/env bash
# ============================================================================
# NFM-3777 / issue #1050 — deploy_prod.sh
#
# This is the deploy-prod body, extracted from the mega-heredoc that lived
# inline in .github/workflows/production-deployment.yml. The heredoc was
# retired after FIVE silent-cutover incidents (2026-08-18 .. 2026-08-31):
# streamed-heredoc stdin can be swallowed mid-script by inner commands
# (notably `docker compose run ... < /dev/null` in prod_migrate.sh, and
# empty-variable command mangling), leaving the tail of the deploy — cutover,
# assertions, health gates — silently skipped while the job stayed green.
#
# Execution contract (from the workflow step):
#   scp scripts/deploy_prod.sh  host:/tmp/nfm-deploy-prod.sh
#   ssh host "PROXY_PORT=<port> DEPLOY_SHA=<github.sha> bash /tmp/nfm-deploy-prod.sh"
#
# The script runs FROM DISK on the remote host — stdin is never the script
# transport, so nothing inside can eat the rest of the deploy. All inputs are
# environment variables validated with :? (empty → immediate loud failure,
# fixing the empty-variable command-mangling class seen in issue #1050).
#
# Requires (validated up front): PROXY_PORT, DEPLOY_SHA.
# Exit codes: 1 general; 71-74 reserved by tools/post-deploy-cutover-assert.
# ============================================================================
set -euo pipefail

# ssh host "cmd" runs a NON-LOGIN zsh: only /etc/zshenv + ~/.zshenv are
# sourced, so /usr/local/bin (docker) is missing from PATH. The old heredoc
# transport accidentally worked because a login shell sources .zprofile.
export PATH="/usr/local/bin:/usr/local/sbin:/opt/homebrew/bin:$PATH"

# --- Input validation (empty variable = loud failure, never a mangled cmd) --
: "${DEPLOY_SHA:?DEPLOY_SHA (github.sha) not provided — refusing to deploy}"
: "${PROXY_PORT:=7897}"


# NFM-4272 / ADR-013 §2 G4b — deploy lockfile. The drift-checker cron
# (scripts/check_deploy_drift.py, per runbook §8) diffs live container
# digests against the deploy manifest recorded at the END of this script.
# While a sanctioned deploy RUNS, live state legitimately diverges from the
# previous manifest; this lockfile tells the checker to stand down (fresh
# lock ⇒ deploy in progress, no drift issue filed — AC-G4b.2). Removed on
# ANY exit including failures: a crashed deploy left prod diverged and that
# MUST alarm. A lock orphaned by a host crash goes stale and is ignored by
# the checker after --max-lock-age (default 2h; cold build is ~30 min).
NFM_DEPLOY_LOCK="${NFM_DEPLOY_LOCK:-$HOME/.nfmd/prod-deploy.lock}"
mkdir -p "$(dirname "$NFM_DEPLOY_LOCK")"
printf '{"pid": %s, "deploy_sha": "%s", "started": "%s"}\n' "$$" "${DEPLOY_SHA}" \
  "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$NFM_DEPLOY_LOCK"
trap 'rm -f "$NFM_DEPLOY_LOCK"' EXIT

# NFM-3328: fail-fast semantics. Prior deploys slid past failed steps (build
# errors, `docker compose` unavailable) into green health checks against
# stale containers — four consecutive false-success deploys (2026-08-18..19).
# Without -e only the LAST command's exit code propagates; every intermediate
# failure was swallowed. (set -euo pipefail is at the top of this script.)

cd ~/Projects/nucpot

export HTTP_PROXY="http://127.0.0.1:${PROXY_PORT}" HTTPS_PROXY="http://127.0.0.1:${PROXY_PORT}"
# github.com must go DIRECT: the proxy route for it is dead (verified 5/5
# fail via proxy, 5/5 pass direct). SSH sessions do not inherit job env.
export NO_PROXY=localhost,127.0.0.1,github.com,.githubusercontent.com,.githubassets.com
export no_proxy="$NO_PROXY"

# NFM-848: DOCKER_CONFIG redirects the CLI-plugin search path away from the
# credential helper that breaks daemon-side metadata resolution.
export DOCKER_CONFIG=/tmp/nfm848-no-cred-docker-config
mkdir -p /tmp/nfm848-no-cred-docker-config/cli-plugins
printf '{}' > /tmp/nfm848-no-cred-docker-config/config.json

# NFM-3777/#1050: stale-marker poisoning (seen 2026-08-18 and in NFM-3845
# UPDATE 4) — a leftover /tmp/nfmd_prod_health_passed from a PRIOR deploy made
# the emit fragment report "health-gate-first-poll-passed true" for a deploy
# that never cut over. Scrub it at the top of every deploy so the marker can
# only ever attest to THIS run's health gate.
rm -f /tmp/nfmd_prod_health_passed

# NFM-3328: an otherwise-empty DOCKER_CONFIG dir SILENTLY REMOVES
# `docker compose` (verified empirically: `docker compose version` →
# "unknown command"). Symlink the host's compose plugin into the override
# dir to keep compose available without restoring the keychain.
ln -sf ~/.docker/cli-plugins/docker-compose \
  /tmp/nfm848-no-cred-docker-config/cli-plugins/docker-compose
docker compose version

# NFM-2148 / ADR-NFM-2139 §5 D1: pin every image to the deploying commit SHA
# so a bad deploy can be rolled back by re-tagging, not by rebuilding.
export PROD_IMAGE_TAG="${DEPLOY_SHA}"
echo "==> Deploying with PROD_IMAGE_TAG=${PROD_IMAGE_TAG}"

for i in 1 2 3 4 5; do
  git fetch origin main && break
  [ "$i" = 5 ] && { echo "FATAL: git fetch failed 5x"; exit 1; }
  echo "git fetch failed (attempt $i), retrying in 15s..."; sleep 15
done
git reset --hard origin/main

# Build the 3 distinct images (D1, NFM-2148). The api image is shared by the
# api and worker services (docker-compose.prod.yml), so one build tag covers
# both. --build is BANNED below in the compose step — it would re-tag the new
# image as `latest` and destroy the prior SHA-tagged image, breaking rollback.
# pre-deploy-assert (D2, NFM-2149) has already validated the DB↔code alembic
# match against the candidate image before this point.
# --no-cache on all builds (NFM-2376 root cause: stale layer cache).
echo "==> Building nucpot-prod-api:${PROD_IMAGE_TAG}"
# NFM-2502: clear proxy for Docker build (apt/pip use CN mirrors directly)
# NFM-848: BUILDKIT=0 — daemon-side metadata resolution, no keychain
HTTP_PROXY= HTTPS_PROXY= DOCKER_BUILDKIT=0 \
  docker build --no-cache -t "nucpot-prod-api:${PROD_IMAGE_TAG}" -f docker/prod-api.Dockerfile .

echo "==> Building nucpot-prod-lightrag:${PROD_IMAGE_TAG}"
DOCKER_BUILDKIT=0 \
  docker build --no-cache -t "nucpot-prod-lightrag:${PROD_IMAGE_TAG}" \
    -f docker/lightrag.Dockerfile --build-arg LIGHTRAG_VERSION=1.5.4 .

echo "==> Building nucpot-prod-web:${PROD_IMAGE_TAG}"
DOCKER_BUILDKIT=0 \
  docker build --no-cache -t "nucpot-prod-web:${PROD_IMAGE_TAG}" \
    -f docker/web.Dockerfile \
    --build-arg API_SERVER_URL=http://nucpot-prod-api:8000 \
    --build-arg LIGHTRAG_WEBUI_URL=http://nucpot-prod-lightrag:9621 .

# NFM-2146 / ADR-NFM-2139 §5 D3 (revised by NFM-2196): deploy-time migration
# runs BEFORE the new containers come up. The Postgres advisory lock
# (key 7423912) is acquired by env.py on the migration's own async connection
# (apps/api/migrations/env.py: run_async_migrations), so concurrent migrators
# (cron jobs, manual ssh, parallel CI) block at the SQL level until the
# in-flight migration disconnects. A migration failure aborts the deploy
# before any traffic is cut over — failure-mode shift from "502 on boot" to
# "failed deploy step" (alertable, retryable). See scripts/prod_migrate.sh
# for the orchestration and apps/api/migrations/env.py for the lock primitive.
./scripts/prod_migrate.sh

# NFM-3320 AC-1/AC-2 — post-deploy cutover assertion. The 2026-08-18 incident
# showed that `docker compose ... up -d` can return success while leaving the
# old containers in place. We snapshot every nucpot-prod-* service container
# before `up -d` (Image ID + Created timestamp) and re-assert after, refusing
# to declare a green deploy unless each running container's Image ID matches a
# SHA-tagged image that was actually built this run, AND its Created timestamp
# moved forward. Distinct exit codes (71/72/73/74) let the workflow branch
# (skipped/failure) on the failure type. The snapshot directory lives on the
# host because the deploy-prod job runs on the Mac Studio self-hosted runner
# (loopback SSH) and /tmp survives both phases of the deploy.
#
# NFM-3777 note: with this script running from disk, stdin is no longer the
# script transport, so the historical `docker compose run < /dev/null`
# stdin-swallow cannot eat the cutover segment. Belt-and-braces: keep stdin
# detached anyway and emit anchors for log-based diagnosis.
exec 0</dev/null
echo "==> DEPLOY SCRIPT CUTOVER START (anchor for NFM-3777 diagnosis)"
export NFM_CUTOVER_SNAPSHOT_DIR="/tmp/nfm-cutover-${PROD_IMAGE_TAG}"
rm -rf "${NFM_CUTOVER_SNAPSHOT_DIR}"
echo "==> Capturing BEFORE snapshot of nucpot-prod-* containers..."
bash tools/post-deploy-cutover-assert/assert.sh \
  --phase before \
  --snapshot-dir "${NFM_CUTOVER_SNAPSHOT_DIR}"

# Restart ALL containers using the SHA-tagged images. PROD_IMAGE_TAG resolves
# each service's `image:` to the matching SHA-tagged image we just built.
# Schema authority is unchanged: Alembic-only via apps/api/migrations/,
# applied deploy-time by the step above (NFM-2146), not on container boot (D3).
docker compose -f docker-compose.prod.yml --env-file docker/.env.prod up -d

# Wait for services to start
sleep 20

# NFM-3320 AC-1/AC-2 (continued) — AFTER-phase assertion. Hard-fails the
# deploy on image-digest mismatch (exit 71), no-recreate (exit 72), missing
# expected tag (exit 73), or missing service container (exit 74). Must run
# BEFORE the curl health checks — curl against old (still-healthy) containers
# returns 200 and would mask a failed cutover (the original 2026-08-18 bug).
echo "==> Asserting post-deploy cutover (expected tag: ${PROD_IMAGE_TAG})..."
bash tools/post-deploy-cutover-assert/assert.sh \
  --phase after \
  --expected-tag "${PROD_IMAGE_TAG}" \
  --snapshot-dir "${NFM_CUTOVER_SNAPSHOT_DIR}" \
  --distinct-exit 71

# Internal health checks — the first curl -f is the production equivalent of
# staging's first-poll gate (ADR-KR3-A2 §Failure-mode 6). We write a marker
# file on success so the "Emit prod deploy-event fragment" post-step can read
# the outcome without re-parsing this script's output. The deploy-prod job
# runs on a self-hosted runner on the same Mac Studio host, so the runner's
# /tmp is the host's /tmp.
echo "Checking API health..."
rm -f /tmp/nfmd_prod_health_passed
if curl -f http://localhost:8001/api/v1/health; then
  touch /tmp/nfmd_prod_health_passed
else
  exit 1
fi

echo "Checking Web health..."
curl -f http://localhost:3000/ || exit 1

# NFM-2148 / ADR-NFM-2139 §5 D1 retention: keep the most-recent 10
# nucpot-prod-* tags per repository in the local daemon. The new SHA we just
# built is always newest, so it is never pruned.
echo "==> Pruning nucpot-prod-* tags older than the last 10 per repository"
for REPO in nucpot-prod-api nucpot-prod-lightrag nucpot-prod-web; do
  OLD_IDS=$(docker images --format '{{.Repository}}|{{.Tag}}|{{.ID}}|{{.CreatedAt}}' \
    | grep "^${REPO}|" \
    | sort -t'|' -k4,4 -r \
    | tail -n +11 \
    | cut -d'|' -f3 \
    | sort -u)
  if [ -n "$OLD_IDS" ]; then
    echo "    removing old ${REPO} tags: $(echo "$OLD_IDS" | tr '\n' ' ')"
    echo "$OLD_IDS" | xargs -r docker image rm -f
  else
    echo "    ${REPO}: nothing to prune"
  fi
done

# NFM-3448: candidate-tag retention. The pre-deploy-assert job writes
# nucpot-prod-*:candidate-<sha> at every build; keep the most-recent 3 per
# repository (see NFM-3447 disk pressure).
echo "==> Pruning candidate-* tags older than the last 3 per repository (NFM-3448)"
for REPO in nucpot-prod-api nucpot-prod-lightrag nucpot-prod-web; do
  bash tools/prod-tag-retention/prune.sh --repo "$REPO" --keep 3
done
echo "==> Post-prune nucpot-prod-* tag count: $(docker images --format '{{.Repository}}:{{.Tag}}' | grep -c '^nucpot-prod-')"
echo "==> DEPLOY SCRIPT CUTOVER END (anchor for NFM-3777 diagnosis)"
echo "DEPLOY_SCRIPT_COMPLETED_OK sha=${DEPLOY_SHA}"
