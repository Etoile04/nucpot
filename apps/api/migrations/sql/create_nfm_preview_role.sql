-- =============================================================================
-- NFM-4122 — bootstrap script: create the `nfm_preview` login role
--             on the production database (idempotent).
--
-- This is the MANUAL bootstrap path that runs BEFORE the equivalent alembic
-- migration (073_create_nfm_preview_role.py) lands via the regular deploy.
-- While the 070 embargo is in place, the prod alembic chain is blocked at
-- 069, so the 073 migration cannot apply. This script lets an authorised
-- operator create the role right now, so the NFM-4106 acceptance criterion 1
-- ("running `alembic upgrade head` inside a preview/QA container cannot
-- advance `alembic_version` on `nucpot-prod-db`") can be verified.
--
-- The script is IDEMPOTENT: re-running it produces the same end state. The
-- 073 alembic migration runs the same GRANT set when it eventually applies
-- (after 070/071/072 lift), and the role-creation step short-circuits on the
-- `IF NOT EXISTS` guard, so manual bootstrap does not conflict with the
-- eventual alembic application.
--
-- =============================================================================
-- Why this exists
-- =============================================================================
--
-- NFM-4106 landed a prod-migration pre-flight guard (PR #1098, merged as
-- 9d2441428) that hardens the **release-engineering deploy path**:
-- scripts/prod_migrate.sh runs check_prod_migration.py before `alembic upgrade
-- head`, gated on NFMD_PROD_MIGRATION_PERMITTED=1 and writing a JSONL audit
-- row per invocation.
--
-- That satisfied 3 of NFM-4106's 4 acceptance criteria. **It did not satisfy
-- the first and most important one**, and NFM-4122 closes it honestly:
--
--   > Running `alembic upgrade head` inside a preview/QA container cannot
--   > advance `alembic_version` on `nucpot-prod-db`.
--
-- NFM-4106 itself predicted this: option (3) "alone is still bypassable by a
-- determined `alembic` invocation", and "(1) or (2) is preferred".
--
-- Option (2) is what this script implements: a separate, least-privilege
-- login role for preview/QA containers, with no DDL rights and no write
-- access to `alembic_version`.
--
-- =============================================================================
-- Authorisation
-- =============================================================================
--
-- Apply as a superuser. Today `nfm` is the only login role on `nfm_db` and
-- it is `rolsuper=t`. Apply with (the password is supplied via a psql
-- variable so it never lives in a committed file or appears in shell
-- history beyond the one-line invocation):
--
--   docker exec -i nucpot-prod-db psql -U nfm -d nfm_db \
--     -v NFMD_PREVIEW_DB_PASSWORD="$NFMD_PREVIEW_DB_PASSWORD" \
--     -v ON_ERROR_STOP=1 \
--     -f - < apps/api/migrations/sql/create_nfm_preview_role.sql
--
-- `NFMD_PREVIEW_DB_PASSWORD` is sourced from the host's `docker/.env.prod`
-- (added by the NFM-4122 PR). If the variable is unset, psql aborts on the
-- first :'var' reference and the transaction rolls back.
--
-- =============================================================================
-- Verification (NFM-4106 acceptance criterion 1)
-- =============================================================================
--
-- After applying this script AND repointing the preview container's
-- `NFM_DATABASE_URL` at the new role, the bypass must be closed:
--
--   1. `docker exec nucpot-prod-api-preview alembic upgrade head`
--      -> fails with `permission denied for table alembic_version` (or
--      equivalent).
--   2. `docker exec nucpot-prod-db psql -U nfm -d nfm_db \
--         -c "SELECT version_num FROM alembic_version;"`
--      -> still reads `069_add_v050_f8_property_types`.
--
-- =============================================================================
-- Rollback
-- =============================================================================
--
--   REVOKE ALL PRIVILEGES ON DATABASE nfm_db FROM nfm_preview;
--   REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM nfm_preview;
--   REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM nfm_preview;
--   REVOKE ALL PRIVILEGES ON SCHEMA public FROM nfm_preview;
--   ALTER DEFAULT PRIVILEGES FOR ROLE nfm IN SCHEMA public
--     REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM nfm_preview;
--   ALTER DEFAULT PRIVILEGES FOR ROLE nfm IN SCHEMA public
--     REVOKE USAGE, SELECT ON SEQUENCES FROM nfm_preview;
--   DROP OWNED BY nfm_preview;
--   DROP ROLE nfm_preview;
--
-- (The above is the inverse of every step below; drop_owned cascades to any
-- objects owned by the role, of which there should be none in steady state.)
-- =============================================================================

\set ON_ERROR_STOP on

-- -----------------------------------------------------------------------------
-- 0. Sanity: refuse to run unless the caller is a superuser. The CREATE ROLE
-- and ALTER DEFAULT PRIVILEGES statements below require superuser; catching
-- the failure here produces a self-diagnosing message instead of a confusing
-- "permission denied to create role" mid-script.
-- -----------------------------------------------------------------------------
DO $$
BEGIN
  IF NOT (SELECT rolsuper FROM pg_roles WHERE rolname = current_user) THEN
    RAISE EXCEPTION 'NFM-4122: this script must be applied as a superuser (current user: %)', current_user;
  END IF;
END
$$;

-- -----------------------------------------------------------------------------
-- 1. Create the role (idempotent). The password is supplied via psql
-- variable `NFMD_PREVIEW_DB_PASSWORD` so it never lives in a committed file.
-- We use EXECUTE format() with %L so the password is properly quoted; this
-- handles passwords containing single quotes, backslashes, or other SQL-
-- special characters safely.
-- -----------------------------------------------------------------------------
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'nfm_preview') THEN
    EXECUTE format(
      'CREATE ROLE nfm_preview LOGIN PASSWORD %L',
      :'NFMD_PREVIEW_DB_PASSWORD'
    );
    RAISE NOTICE 'NFM-4122: created role nfm_preview';
  ELSE
    RAISE NOTICE 'NFM-4122: role nfm_preview already exists, skipping CREATE';
  END IF;
END
$$;

-- -----------------------------------------------------------------------------
-- 2. Database-level CONNECT. No CREATE on the database (a role with CREATE
-- on the database can create new schemas; we want the role to be unable to
-- create ANY new schema).
-- -----------------------------------------------------------------------------
GRANT CONNECT ON DATABASE nfm_db TO nfm_preview;

-- -----------------------------------------------------------------------------
-- 3. Schema-level USAGE. Omitting GRANT CREATE ON SCHEMA public is what
-- blocks CREATE TABLE / ALTER TABLE / CREATE INDEX / etc. — i.e. DDL.
-- -----------------------------------------------------------------------------
GRANT USAGE ON SCHEMA public TO nfm_preview;

-- -----------------------------------------------------------------------------
-- 4. DML on all existing tables. SELECT/INSERT/UPDATE/DELETE so QA agents
-- can exercise the app (read + write to data tables, but never to schema).
-- -----------------------------------------------------------------------------
GRANT SELECT, INSERT, UPDATE, DELETE
  ON ALL TABLES IN SCHEMA public
  TO nfm_preview;

-- -----------------------------------------------------------------------------
-- 5. Sequence USAGE + SELECT. nextval() (used by INSERTs into serial columns)
-- requires USAGE; currval() requires SELECT.
-- -----------------------------------------------------------------------------
GRANT USAGE, SELECT
  ON ALL SEQUENCES IN SCHEMA public
  TO nfm_preview;

-- -----------------------------------------------------------------------------
-- 6. Default privileges for future objects. Without this, a table created by
-- `nfm` (the deploy role) AFTER this script runs would not be visible to
-- `nfm_preview`. The `FOR ROLE nfm` clause scopes the default to objects
-- created by `nfm` (the only role that creates DDL in prod). Future
-- migrations run as `nfm` (via env.py's NFM_DATABASE_URL with
-- ${PROD_POSTGRES_USER:-nfm}), so this covers them.
-- -----------------------------------------------------------------------------
ALTER DEFAULT PRIVILEGES FOR ROLE nfm IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO nfm_preview;

ALTER DEFAULT PRIVILEGES FOR ROLE nfm IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO nfm_preview;

-- -----------------------------------------------------------------------------
-- 7. alembic_version: SELECT only. This is the load-bearing step: even if a
-- QA agent runs `alembic upgrade head`, the role cannot INSERT/UPDATE/
-- DELETE/TRUNCATE the version row, so alembic cannot stamp and the migration
-- fails. REVOKE is explicit (the previous GRANTs on ALL TABLES would
-- otherwise cover alembic_version).
-- -----------------------------------------------------------------------------
REVOKE INSERT, UPDATE, DELETE, TRUNCATE
  ON TABLE alembic_version
  FROM nfm_preview;

GRANT SELECT
  ON TABLE alembic_version
  TO nfm_preview;

-- -----------------------------------------------------------------------------
-- 8. Final self-check. Surface the granted surface area in the apply log so
-- the operator can eyeball it before moving on.
-- -----------------------------------------------------------------------------
DO $$
DECLARE
  table_grants TEXT;
  seq_grants TEXT;
  av_perms TEXT;
BEGIN
  SELECT string_agg(
      privilege_type || ' ON ' || table_name,
      ', ' ORDER BY privilege_type, table_name
    )
    INTO table_grants
    FROM information_schema.role_table_grants
    WHERE grantee = 'nfm_preview'
      AND table_schema = 'public';

  SELECT string_agg(
      privilege_type || ' ON ' || object_name,
      ', ' ORDER BY privilege_type, object_name
    )
    INTO seq_grants
    FROM (
      SELECT privilege_type, object_name
        FROM information_schema.role_usage_grants
        WHERE grantee = 'nfm_preview'
          AND object_schema = 'public'
          AND object_type = 'SEQUENCE'
    ) s;

  SELECT string_agg(privilege_type, ', ' ORDER BY privilege_type)
    INTO av_perms
    FROM information_schema.role_table_grants
    WHERE grantee = 'nfm_preview'
      AND table_schema = 'public'
      AND table_name = 'alembic_version';

  RAISE NOTICE 'NFM-4122: nfm_preview table grants: %', COALESCE(table_grants, '(none)');
  RAISE NOTICE 'NFM-4122: nfm_preview sequence grants: %', COALESCE(seq_grants, '(none)');
  RAISE NOTICE 'NFM-4122: nfm_preview alembic_version perms: %', COALESCE(av_perms, '(none — role does not see alembic_version)');
END
$$;