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
- [NFM-4106](../../issues/NFM-4106) — prod-migration pre-flight guard
  (`apps/api/scripts/check_prod_migration.py`, the subject of §6
  below).

---

## 6. Prod-migration pre-flight guard (NFM-4106)

### 6.1. Why this exists

Before NFM-4106, a QA / preview container pointed at `nucpot-prod-db`
(e.g. `nucpot-prod-api:preview-nfm4087-…`) could run `alembic upgrade
head` and the database would advance — the prod API container's CMD
is uvicorn-only (NFM-2146), but the image and the `nucpot-prod-db`
DSN are shared. The CTO embargo on applying migration 070 to prod
(see [NFM-4092](../../issues/NFM-4092)) was a *social* control: any
operator with shell access could accidentally bypass it.

The NFM-4106 guard turns the embargo into a *structural* control by
refusing `alembic upgrade head` unless the caller sets the literal
flag `NFMD_PROD_MIGRATION_PERMITTED=1`. That flag is **never** present
in any committed env file — it is set only by `scripts/prod_migrate.sh`
and by `.github/workflows/production-deployment.yml`, both of which
are the only authorised invocation paths.

### 6.2. What the guard checks

`apps/api/scripts/check_prod_migration.py` runs inside the ephemeral
prod-api container, BEFORE `alembic upgrade head`. It returns:

| Exit | Meaning                                                         |
| ---- | --------------------------------------------------------------- |
| 0    | Authorised AND image is at or ahead of DB. Safe to migrate.    |
| 1    | DB revision is not in the image — image is older than DB.       |
| 2    | Configuration / IO error (missing DB URL, missing migrations). |
| 3    | `NFMD_PROD_MIGRATION_PERMITTED` unset or not `"1"`. **Refused.** |

The 3-branch is the new structural control. A QA agent who runs
`docker exec nucpot-prod-api-preview alembic upgrade head` against
`nucpot-prod-db` now gets exit 3 with a self-diagnosing block
explaining (a) which env flag they would have needed to set,
(b) that they should ask SRE for a scratch DB instead, and
(c) where the audit row landed.

### 6.3. Audit log

Every invocation writes a single JSONL row to:

```
/var/log/nfmd/prod-migrations.log
```

inside the named volume `nucpot-prod-migration-audit`, mounted on the
`api` service (and therefore on both the long-running
`nucpot-prod-api` container and the ephemeral `docker compose run --rm`
migration container, plus preview/QA containers via the
`docker-compose.preview.yml` overlay). Read it with:

```bash
docker exec nucpot-prod-api cat /var/log/nfmd/prod-migrations.log
# or, if the api container is down:
docker run --rm -v nucpot-prod-migration-audit:/var/log/nfmd \
  busybox cat /var/log/nfmd/prod-migrations.log
```

History note: before this volume existed (deploys up to and including
`14e16f5e4`, 2026-09-02), the ephemeral migration container was
destroyed by `--rm` along with its audit row; the only surviving copy
of those rows is the `AUDIT {…}` stderr line each one mirrored into
the GitHub Actions deploy-job log. If that path is unwritable (e.g.
local dev without Docker), the row falls back to
`/tmp/prod-migration-audit.log` and a `WARNING` line is written to
stderr. The volume is the grep target — a fallback row will NOT be
visible there.

Row schema (every field except `ts` and `outcome` is optional in
runbook queries):

```json
{
  "ts": "2026-09-02T15:30:00+00:00",
  "outcome": "ok" | "permission_denied" | "image_older_than_db"
           | "config_error" | "db_connectivity_error" | "ok_fresh_db",
  "image_tag": "abc1234",
  "image_revision_count": 71,
  "image_head_revisions": ["070_d2_dedup_bad_data_sources"],
  "db_revision": "069_add_v050_f8_property_types",
  "permission_granted": true,
  "operator": "ci-9876543210",
  "container_hostname": "nucpot-prod-api",
  "refusal_reason": null,
  "script": "check_prod_migration.py",
  "issue": "NFM-4106"
}
```

### 6.4. Who may set the flag

**Only the two authorised invocation paths.** No committed env file
(`docker/.env.prod`, `docker/.env.prod.example`, `apps/api/.env`,
etc.) carries `NFMD_PROD_MIGRATION_PERMITTED=1`. If you find it in a
committed env file, that is a regression — file a follow-up issue
and roll it back.

The two authorised paths:

1. **`scripts/prod_migrate.sh`** — invoked by
   `scripts/deploy_prod.sh`, called from the CI workflow's
   `deploy-prod` job and from a local hot-fix runbook follow (see
   §2 above). The script passes `-e NFMD_PROD_MIGRATION_PERMITTED=1`
   to the `docker compose run` invocation and wires
   `NFMD_OPERATOR=$NFMD_OPERATOR` (defaults to
   `local-$(whoami)` for ad-hoc runs).
2. **`.github/workflows/production-deployment.yml`** — the `deploy-prod`
   job calls `scripts/deploy_prod.sh`, which calls `prod_migrate.sh`,
   which carries the flag through. The CI operator identifier is
   `ci-${GITHUB_RUN_ID}`.

### 6.5. How a QA agent gets a mutable database instead

**Do not point your preview container at `nucpot-prod-db` with the
`nfm` superuser role.** That is the entire class of problem NFM-4106
and NFM-4122 close, and the guard + role separation together will
refuse you with exit 3 + an audit row + a permission error at the
INSERT into `alembic_version`.

NFM-4122 introduces the **`nfm_preview`** login role: DML on the
`public` schema (so QA agents can exercise the app), `SELECT`-only
on `alembic_version` (so `alembic upgrade head` cannot stamp), no
DDL. Preview / QA containers that need to talk to prod data MUST
connect as `nfm_preview`, never as `nfm`.

For QA / preview work that needs to mutate the **schema** (e.g.
running `alembic upgrade head` against a candidate migration), the
supported path is:

1. Restore a prod snapshot into a scratch database
   (`nucpot-prod-db-scratch-<ticket>`). The SRE runbook
   `docs/runbooks/scratch-db-restore.md` (when present) describes
   the restore procedure.
2. Start the preview container with `NFM_DATABASE_URL` pointed at
   the scratch DB, NOT at `nucpot-prod-db`. The guard will accept
   that call (the flag is still required, but the blast radius is
   a throwaway snapshot, not the live prod data).

#### 6.5.1. `nfm_preview` role (NFM-4122)

| Property              | Value                                            |
| --------------------- | ------------------------------------------------ |
| Login role            | `nfm_preview`                                    |
| Database              | `nfm_db` (production)                            |
| Existing-table DML    | `SELECT, INSERT, UPDATE, DELETE` on `public.*`   |
| Existing-sequence     | `USAGE, SELECT` on `public.*`                    |
| Default privileges    | DML on future objects created by `nfm` in `public` |
| `alembic_version`     | `SELECT` only                                    |
| DDL rights            | **None** — no `CREATE` on database or schema     |

A bare `docker exec nucpot-prod-api-preview alembic upgrade head`
against `nucpot-prod-db` fails when alembic tries to stamp
`alembic_version` (verified live 2026-09-03: the
`UPDATE alembic_version SET version_num='080_...'` at the end of the
run raises `ERROR: permission denied for table alembic_version`, and
transactional DDL rolls the whole migration back), closing
NFM-4106 acceptance criterion 1.

##### Creating the role

The role is created by the alembic migration
`apps/api/migrations/versions/073_create_nfm_preview_role.py`,
which runs after the 070 embargo lifts (the chain head is
`080_kg_orphan_bridge_u10mo_u3si_puo2` as of 2026-09-03; 073 depends on
072). The canonical DDL — including all GRANTs, the
`ALTER DEFAULT PRIVILEGES` set, and the `alembic_version` read-only
revocation — lives at
`apps/api/migrations/sql/create_nfm_preview_role.sql`. Both the
alembic path and the manual psql path read from that one file, so
the two cannot drift.

##### Bootstrapping the role BEFORE the 070 embargo lifts

> **Historical (2026-09-02):** the embargo has since been lifted —
> prod applied 070–079 (NFM-4191 / PR #1117) and sits at
> `079_restore_070_measurement_casualties`. The manual bootstrap below
> is no longer required; the psql path remains valid disaster recovery.

The 070 embargo keeps prod at alembic head `069_add_v050_f8_property_types`
until NFM-4104 lifts it. During that window, the 073 migration
cannot apply (the chain would attempt to apply 070/071/072 first,
which the embargo blocks). To unblock the NFM-4106 acceptance
criterion 1 verification NOW, an authorised operator can bootstrap
the role manually via psql — the SQL is idempotent, so the eventual
alembic migration will be a no-op when it lands:

```bash
docker exec -i nucpot-prod-db psql -U nfm -d nfm_db \
  -v NFMD_PREVIEW_DB_PASSWORD="$NFMD_PREVIEW_DB_PASSWORD" \
  -v ON_ERROR_STOP=1 \
  -f - < apps/api/migrations/sql/create_nfm_preview_role.sql
```

`NFMD_PREVIEW_DB_PASSWORD` MUST be sourced from
`docker/.env.prod` (added by the NFM-4122 PR); the
`${NFMD_PREVIEW_DB_PASSWORD:?...}` reference in `docker-compose.prod.yml`
will fail the deploy if the variable is unset, which is the
load-bearing guard rail.

##### Starting a preview container with `nfm_preview`

The supported overlay is `docker-compose.preview.yml`. It overrides
the `api` and `web` services into preview containers
(`nucpot-prod-api-preview` / `nucpot-prod-web-preview`) that connect
as `nfm_preview`:

```bash
docker compose \
  -f docker-compose.prod.yml \
  -f docker-compose.preview.yml \
  --env-file docker/.env.prod \
  up -d --no-deps --force-recreate api web
```

**`up` takes SERVICE names (`api`, `web`), not `container_name`
values** — `nucpot-prod-api-preview` is the resulting container, not
a compose service, and passing it to `up` errors out.

`--no-deps` prevents compose from starting the prod-only LightRAG
sidecar if it is not already running. The overlay does NOT define
the `db` / `redis` / `lightrag` services — it inherits those from
`docker-compose.prod.yml`.

Overlay mechanics (NFM-4202), so nobody re-adds the old workarounds:

- **Host ports.** The overlay `!override`s `ports`, so the preview
  stack publishes ONLY `${PREVIEW_API_HOST_PORT:-8008}` (api) and
  `${PREVIEW_WEB_HOST_PORT:-3030}` (web). Compose merges `ports` by
  published-port key, so without the override the preview api ALSO
  tries to bind prod's `${PROD_API_HOST_PORT:-8001}` and fails with
  `Bind for 0.0.0.0:8001 failed: port is already allocated` while
  the prod stack is up. Do NOT work around this by moving
  `PROD_API_HOST_PORT` — that just moves prod off its port.
- **Network.** The overlay attaches to the prod stack's bridge
  network (`nucpot-prod_prod`) as an EXTERNAL network, so
  `nucpot-prod-db` / `nucpot-prod-redis` resolve without a manual
  `docker network connect`. Corollary: **the prod stack must be up**
  (or at least its network must exist) before starting a preview
  stack — compose will refuse with `network nucpot-prod_prod not
  found` otherwise. The preview containers also claim the
  `api` / `web` service aliases on that shared network; that is
  dormant because every prod service pins the explicit
  `nucpot-prod-*` container names in its env.
- **Image.** `PROD_IMAGE_TAG` (shell env or `docker/.env.prod`)
  selects the `nucpot-prod-api` / `nucpot-prod-web` image tag;
  unset means `latest`, which is NOT guaranteed to exist for both
  images — pin a deploy tag (what `docker inspect nucpot-prod-api
  --format '{{.Config.Image}}'` shows) when in doubt.

##### Rotating the password

```bash
docker exec nucpot-prod-db psql -U nfm -d nfm_db -v ON_ERROR_STOP=1 <<SQL
ALTER ROLE nfm_preview WITH PASSWORD :'NFMD_PREVIEW_DB_PASSWORD';
SQL
```

…then update `NFMD_PREVIEW_DB_PASSWORD` in `docker/.env.prod` and
restart any running preview containers (compose does not pick up
DSN env changes on a `docker restart` alone — the new container
needs `docker compose ... up -d --force-recreate`).

##### Verification (NFM-4106 acceptance criterion 1, run end-to-end)

```bash
# 1. Confirm role exists and has the right grants.
docker exec nucpot-prod-db psql -U nfm -d nfm_db -c \
  "SELECT rolname, rolsuper, rolcreatedb FROM pg_roles WHERE rolname='nfm_preview';"
# Expect: rolname=nfm_preview, rolsuper=f, rolcreatedb=f

# 2. Confirm nfm_preview can read but not write alembic_version.
docker exec nucpot-prod-db psql -U nfm_preview -d nfm_db -c \
  "SELECT version_num FROM alembic_version;"
# Expect: prod's current head — 079_restore_070_measurement_casualties
# as of 2026-09-03 (the embargo lifted 2026-09-02, NFM-4191); must
# equal what the nfm-role query returns

docker exec nucpot-prod-db psql -U nfm_preview -d nfm_db -c \
  "INSERT INTO alembic_version (version_num) VALUES ('070_d2_dedup_bad_data_sources');"
# Expect: ERROR: permission denied for table alembic_version

# 3. The actual bypass test. From the preview container, alembic
#    upgrade head must fail, and prod's alembic_version must be
#    unchanged afterwards.
docker exec nucpot-prod-api-preview alembic upgrade head
# Expect: permission-denied error (no stamp). NOTE: the preview
# image must contain at least one migration NEWER than the DB's
# current version, otherwise nothing is pending and this exits 0
# as a no-op (hit live 2026-09-03 with an image whose head matched
# prod). Pin PROD_IMAGE_TAG to a post-head build for this test.

docker exec nucpot-prod-db psql -U nfm -d nfm_db -c \
  "SELECT version_num FROM alembic_version;"
# Expect: unchanged — the same version as the pre-attempt query
```

For read-only smoke tests against prod, use the staging API image
(`nucpot-prod-api-staging:<tag>`) which is wired to a separate
staging DB and has its own `check_staging_revision.py` guard
(NFM-4066).

### 6.6. Bypassing the guard (intentional emergency)

A determined operator with shell access can still set the flag —
the audit row makes that visible, but does not prevent it. If you
must invoke `alembic upgrade head` against prod outside the deploy
workflow (e.g. midnight hotfix when CI is unreachable):

```bash
# Document your handle in the audit row.
export NFMD_OPERATOR="oncall-$(whoami)"

# Invoke the guard. The flag must equal the literal string "1".
docker compose -f docker-compose.prod.yml run --rm -T --no-deps \
  -e NFMD_PROD_MIGRATION_PERMITTED=1 \
  -e NFMD_OPERATOR="$NFMD_OPERATOR" \
  -e PROD_IMAGE_TAG="$PROD_IMAGE_TAG" \
  -e NFMD_DEPLOY_LOCK_KEY="$NFMD_DEPLOY_LOCK_KEY" \
  --entrypoint "sh" api \
  -c "python /usr/local/bin/check_prod_migration.py && alembic upgrade head"
```

Every such invocation is audit-logged with your operator identifier
and the image tag. The on-call SRE can grep
`/var/log/nfmd/prod-migrations.log` for any operator handle and
reconstruct the migration timeline.

### 6.7. Companion: see also

- `apps/api/scripts/check_prod_migration.py` — the guard script.
- `apps/api/scripts/tests/test_check_prod_migration.py` — 19 unit tests
  covering all five exit-code branches, DSN normalization, audit row
  schema, and the `prod_migrate.sh` / `prod-api.Dockerfile` wiring.
- `apps/api/scripts/check_staging_revision.py` — the staging analog
  (NFM-4066). The prod guard follows the same shape but adds the
  permission gate.