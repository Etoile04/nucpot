"""073 — Create the ``nfm_preview`` least-privilege login role (NFM-4122).

Closes NFM-4106 acceptance criterion 1: a ``docker exec
nucpot-prod-api-preview alembic upgrade head`` against ``nucpot-prod-db``
must not advance ``alembic_version``.

NFM-4106 (merged as ``9d2441428``) shipped the
``NFMD_PROD_MIGRATION_PERMITTED=1`` flag guard on the
release-engineering deploy path. It did NOT close criterion 1 because
the guard is not in alembic's own path — only in the
``prod_migrate.sh`` entrypoint override — and the ``nfm`` role on
``nucpot-prod-db`` is a confirmed superuser
(``rolsuper=t, rolcreatedb=t, rolcreaterole=t, rolbypassrls=t``).
A bare ``alembic upgrade head`` from any container that knows the
``nfm`` password still runs as DDL.

This migration implements NFM-4106 option 2: a separate, least-privilege
login role for preview / QA containers that point at the production
database. The role has DML on the public schema (so QA agents can
exercise the app) but no DDL and no write access to ``alembic_version``,
so ``alembic upgrade head`` cannot stamp and the migration fails at the
INSERT into ``alembic_version``.

The canonical source of truth for the role creation / grants is
``apps/api/migrations/sql/create_nfm_preview_role.sql``. The alembic
path (this file) loads that SQL at upgrade time and substitutes the
password from ``NFMD_PREVIEW_DB_PASSWORD`` (sourced from the host's
``docker/.env.prod`` via the deploy workflow). This keeps the two
delivery paths (manual psql + alembic upgrade head) bit-identical and
ensures the role grants cannot drift between the manual bootstrap and
the eventual automatic application.

Revises: 072_material_kg_bridge_coverage
Create Date: 2026-09-02
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import sqlalchemy as sa
from alembic import op

revision: str = "073_create_nfm_preview_role"
down_revision: str | None = "072_material_kg_bridge_coverage"
branch_labels: str | None = None
depends_on: str | None = None


# Path to the canonical bootstrap SQL, relative to this migration file.
# Both the manual psql path (`psql -f create_nfm_preview_role.sql`) and the
# alembic path (`alembic upgrade head` → this migration → load + execute the
# same file) read from this one source.
_SQL_REL_PATH = Path(__file__).parent.parent / "sql" / "create_nfm_preview_role.sql"

# psql meta-commands (`\set ON_ERROR_STOP on`, `\set ...`, `\echo ...`, etc.)
# are NOT SQL — they are psql client commands and would cause a syntax error
# when executed via SQLAlchemy ``text()``. Strip them when loading the SQL
# into the alembic execution path. The manual psql path keeps them.
_PSQL_META_RE = re.compile(r"^\s*\\[a-zA-Z][^\n]*\n", re.MULTILINE)


def _load_bootstrap_sql_for_alembic() -> str:
    """Read the canonical bootstrap SQL and substitute the password.

    The canonical SQL file uses psql's ``:'NFMD_PREVIEW_DB_PASSWORD'``
    string-variable substitution so the password never lives in a
    committed file. The alembic path replaces that one reference with a
    properly-escaped SQL string literal sourced from the same env var,
    so the SQL byte-for-byte matches what the manual path executes
    (modulo the password value and the stripped psql meta-commands).
    """
    pw = os.environ.get("NFMD_PREVIEW_DB_PASSWORD")
    if not pw:
        raise RuntimeError(
            "NFMD_PREVIEW_DB_PASSWORD is not set; the 073 migration cannot "
            "create the nfm_preview role without it. Add the variable to "
            "docker/.env.prod on the production host — the deploy workflow "
            "passes it through to the migration container automatically. See "
            "docs/runbooks/prod-deploy.md §6.5 for the rotation procedure."
        )

    raw = _SQL_REL_PATH.read_text(encoding="utf-8")
    raw = _PSQL_META_RE.sub("", raw)

    # SQL standard string-literal escaping: backslash -> double-backslash,
    # single-quote -> doubled single-quote. The PL/pgSQL ``format(... %L,
    # '<escaped_pw>')`` then renders the literal correctly.
    escaped_pw = pw.replace("\\", "\\\\").replace("'", "''")

    if ":'NFMD_PREVIEW_DB_PASSWORD'" not in raw:
        raise RuntimeError(
            "create_nfm_preview_role.sql is missing the "
            ":'NFMD_PREVIEW_DB_PASSWORD' reference — the alembic loader "
            "cannot substitute the password. Refusing to run to avoid "
            "silently creating the role with an unset password."
        )

    return raw.replace(":'NFMD_PREVIEW_DB_PASSWORD'", f"'{escaped_pw}'")


def upgrade() -> None:
    """Create the nfm_preview role and apply the least-privilege grants.

    Idempotent: re-running is a no-op (the SQL guards every step with
    ``IF NOT EXISTS`` or equivalent).
    """
    op.execute(sa.text(_load_bootstrap_sql_for_alembic()))


def downgrade() -> None:
    """Inverse of every step in create_nfm_preview_role.sql.

    Drops the default-privilege entries first (so future ``nfm``-owned
    objects do not auto-grant to ``nfm_preview`` between this downgrade
    and a later upgrade), then ``DROP OWNED BY nfm_preview`` (which
    revokes any object ownership the role may have picked up — there
    should be none in steady state, but the cascade is cheap and
    defensive), then ``DROP ROLE nfm_preview``.

    Reversing the GRANTs explicitly (rather than relying on DROP OWNED)
    documents the intent for anyone reading the downgrade.
    """
    op.execute(
        sa.text(
            """
            REVOKE ALL PRIVILEGES ON DATABASE nfm_db FROM nfm_preview;
            REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public
              FROM nfm_preview;
            REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public
              FROM nfm_preview;
            REVOKE ALL PRIVILEGES ON SCHEMA public FROM nfm_preview;
            ALTER DEFAULT PRIVILEGES FOR ROLE nfm IN SCHEMA public
              REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES
              FROM nfm_preview;
            ALTER DEFAULT PRIVILEGES FOR ROLE nfm IN SCHEMA public
              REVOKE USAGE, SELECT ON SEQUENCES FROM nfm_preview;
            DROP OWNED BY nfm_preview;
            DROP ROLE nfm_preview;
            """
        )
    )