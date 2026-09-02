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
path (this file) loads that SQL at upgrade time, substitutes the
password from ``NFMD_PREVIEW_DB_PASSWORD`` (sourced from the host's
``docker/.env.prod`` via the deploy workflow, or from the CI
``schema-drift-guard`` job's env block since NFM-4169), and splits
the resulting multi-statement script into single-statement
``op.execute()`` calls so asyncpg's prepared-statement protocol can
carry each one. This keeps the two delivery paths (manual psql +
alembic upgrade head) bit-identical at the SQL level and ensures the
role grants cannot drift between the manual bootstrap and the eventual
automatic application.

NFM-4169 — asyncpg prepared-statement guard
===========================================

asyncpg uses server-side prepared statements (one SQL statement per
round-trip). The canonical bootstrap script is a psql-style
multi-statement file (``\\set``, three ``DO $$`` blocks, several
``GRANT`` statements, an ``ALTER DEFAULT PRIVILEGES`` block) that
asyncpg rejects with::

    asyncpg.exceptions.PostgresSyntaxError:
        cannot insert multiple commands into a prepared statement

Fix: ``split_bootstrap_sql_statements()`` below chunks the rendered
SQL on top-level semicolons (skipping ``$$ ... $$`` dollar-quoted
blocks, single-quoted strings with ``''`` escape, ``/* ... */`` block
comments, and ``--`` line comments) and ``upgrade()`` then issues one
``op.execute(sa.text(...))`` per statement. The structural identity
of every individual statement is unchanged from the manual-psql path;
we only rewrap so each fits one asyncpg prepared statement.

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

# NFM-4169: dollar-quote token (``$tag$`` where ``tag`` is empty or
# ``[A-Za-z_][A-Za-z0-9_]*``). PostgreSQL's dollar-quoted string literals
# may begin ``$$``, ``$tag$``, ``$body$``, etc.; the splitter tracks
# each open tag and treats its closing instance as the body's end.
_DOLLAR_QUOTE_OPEN_RE = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)?\$")


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


def split_bootstrap_sql_statements(rendered_sql: str) -> list[str]:
    """Split a multi-statement PostgreSQL script into single statements.

    NFM-4169 — asyncpg prepared-statement guard. asyncpg issues one
    prepared statement per round-trip and rejects
    ``cannot insert multiple commands into a prepared statement`` when
    the server-attached script contains more than one statement. This
    splitter walks the rendered bootstrap SQL, tracking state to skip
    over:

    * ``$$ ... $$`` (and ``$tag$ ... $tag$``) dollar-quoted blocks — the
      canonical 073 bootstrap has three ``DO`` blocks whose bodies are
      full of ``;`` that are NOT statement terminators.
    * Single-quoted string literals ``'...'`` with the standard
      ``''`` escape for embedded apostrophes.
    * Double-quoted identifiers ``"..."``.
    * ``/* ... */`` block comments.
    * ``-- ...`` line comments.

    Each emitted statement has its trailing ``;`` removed; the caller
    passes each through ``sa.text(...)`` and ``op.execute(...)`` which
    handle the bare-statement form.

    Empty / whitespace-only chunks are dropped (a trailing ``\\n;\\n``
    after the last statement would otherwise yield an empty string
    that asyncpg rejects with a syntax error).
    """
    statements: list[str] = []
    buf: list[str] = []

    i = 0
    n = len(rendered_sql)
    # ``None`` means "no dollar-quote open"; a non-None value is the
    # tag for the currently-open dollar-quote ("" for bare ``$$``).
    in_dollar: str | None = None
    in_single = False  # inside '...'
    in_double = False  # inside "..."
    in_line_comment = False  # inside -- ... \n
    in_block_comment = False  # inside /* ... */

    def _flush() -> None:
        chunk = "".join(buf).strip()
        if chunk:
            # Strip trailing semicolon — op.execute(sa.text(...)) does
            # not require it, and emitting bare statements keeps the
            # caller symmetric between this loader and the regular
            # alembic ops (which never end in ``;``).
            if chunk.endswith(";"):
                chunk = chunk[:-1].rstrip()
            if chunk:
                statements.append(chunk)
        buf.clear()

    while i < n:
        ch = rendered_sql[i]
        nxt = rendered_sql[i + 1] if i + 1 < n else ""

        # Inside a ``$$ ... $$`` block, just buffer until the
        # matching closer. Skip ALL other lexical checks so the body
        # of a DO block (full of ``;``) doesn't fragment.
        if in_dollar is not None:
            m = _DOLLAR_QUOTE_OPEN_RE.match(rendered_sql, i)
            if m:
                expected_close = "$" + in_dollar + "$"
                if m.group(0) == expected_close:
                    buf.append(m.group(0))
                    in_dollar = None
                    i += len(m.group(0))
                    continue
            buf.append(ch)
            i += 1
            continue

        # Inside a block comment, look for ``*/``.
        if in_block_comment:
            buf.append(ch)
            if ch == "*" and nxt == "/":
                buf.append(nxt)
                in_block_comment = False
                i += 2
                continue
            i += 1
            continue

        # Inside a line comment, look for newline.
        if in_line_comment:
            buf.append(ch)
            if ch == "\n":
                in_line_comment = False
            i += 1
            continue

        # Inside a single-quoted string, look for ``''`` (escape) or
        # the closing ``'``.
        if in_single:
            buf.append(ch)
            if ch == "'":
                if nxt == "'":
                    buf.append(nxt)
                    i += 2
                    continue
                in_single = False
            i += 1
            continue

        # Inside a double-quoted identifier, look for the closing ``"``.
        if in_double:
            buf.append(ch)
            if ch == '"':
                in_double = False
            i += 1
            continue

        # --- Outside any lexical region. ---

        # ``--`` line comment opens.
        if ch == "-" and nxt == "-":
            in_line_comment = True
            buf.append(ch)
            buf.append(nxt)
            i += 2
            continue

        # ``/*`` block comment opens.
        if ch == "/" and nxt == "*":
            in_block_comment = True
            buf.append(ch)
            buf.append(nxt)
            i += 2
            continue

        # Single-quoted string opens.
        if ch == "'":
            in_single = True
            buf.append(ch)
            i += 1
            continue

        # Double-quoted identifier opens.
        if ch == '"':
            in_double = True
            buf.append(ch)
            i += 1
            continue

        # Dollar-quote opens — ``$$`` or ``$tag$``.
        if ch == "$":
            m = _DOLLAR_QUOTE_OPEN_RE.match(rendered_sql, i)
            if m:
                tag = m.group(1) or ""
                buf.append(m.group(0))
                in_dollar = tag
                i += len(m.group(0))
                continue
            # Bare ``$`` outside dollar-quotes is rare in PostgreSQL
            # but legal in some operator contexts. Append it as a
            # plain character and continue.
            buf.append(ch)
            i += 1
            continue

        # Statement boundary: top-level ``;``.
        if ch == ";":
            buf.append(ch)
            _flush()
            i += 1
            continue

        buf.append(ch)
        i += 1

    _flush()

    if in_dollar is not None or in_single or in_double or in_block_comment:
        raise RuntimeError(
            f"split_bootstrap_sql_statements: unbalanced lexical region "
            f"(dollar={in_dollar!r} single={in_single} double={in_double} "
            f"block_comment={in_block_comment}). The bootstrap SQL is "
            f"malformed."
        )

    return statements


def upgrade() -> None:
    """Create the nfm_preview role and apply the least-privilege grants.

    Idempotent: re-running is a no-op (the SQL guards every step with
    ``IF NOT EXISTS`` or equivalent).

    NFM-4169 — the rendered SQL is split by
    ``split_bootstrap_sql_statements()`` and each statement issued as
    a separate ``op.execute(sa.text(...))`` so asyncpg's
    prepared-statement protocol accepts every chunk as a single
    command.
    """
    rendered = _load_bootstrap_sql_for_alembic()
    for stmt in split_bootstrap_sql_statements(rendered):
        op.execute(sa.text(stmt))


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

    NFM-4169 — downgrade is a single block of DDL with no ``DO``/``IF``
    regions or dollar-quoted bodies, so it can be issued as one
    ``op.execute(sa.text(...))`` without the splitter path. (asyncpg
    only complains when the SQL has more than one top-level
    statement; the downgrade happens to be one statement + a
    trailing semicolon.)
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