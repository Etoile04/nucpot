"""Static + substitution tests for migration 073 — NFM-4122.

NFM-4122 closes NFM-4106 acceptance criterion 1 by creating the
least-privilege ``nfm_preview`` login role on the production database.

The runtime behaviour of this migration cannot be exercised on SQLite:
``CREATE ROLE`` is PostgreSQL-specific. The end-to-end verification
(``docker exec nucpot-prod-api-preview alembic upgrade head`` fails,
``alembic_version`` is unchanged) lives in
``docs/runbooks/prod-deploy.md`` §6.5.1 — it runs against the real
prod database after this PR merges and the deploy path applies the
migration.

What we cover here:

* ``TestRevisionMetadata`` — revision id, down_revision, branch_labels,
  depends_on. Catches the "no head" / "two heads" regressions the
  pre-deploy-assert job would also catch (but with a much noisier
  failure mode, after a build).
* ``TestPasswordSubstitution`` — the canonical SQL file at
  ``apps/api/migrations/sql/create_nfm_preview_role.sql`` is the
  source of truth for both the manual psql path and the alembic
  path. The alembic path loads it at upgrade time and substitutes
  ``:'NFMD_PREVIEW_DB_PASSWORD'`` with the env-var value. These
  tests catch any drift in the substitution logic.
* ``TestBootstrapSqlStructure`` — the SQL file contains every step
  documented in the issue's "Suggested implementation" section, in
  the right order, with the load-bearing
  ``REVOKE ... ON alembic_version`` step that closes criterion 1.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

_MIGRATION_PATH = (
    Path(__file__).resolve().parent.parent
    / "migrations"
    / "versions"
    / "073_create_nfm_preview_role.py"
)
_SQL_PATH = (
    Path(__file__).resolve().parent.parent
    / "migrations"
    / "sql"
    / "create_nfm_preview_role.sql"
)


def _load_migration_module():
    spec = importlib.util.spec_from_file_location(
        "migration_073", _MIGRATION_PATH
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestRevisionMetadata:
    """Static checks on the migration's revision metadata.

    pre-deploy-assert's "exactly one head" check is the load-bearing
    invariant; this test catches the case where the migration lands
    with the wrong ``down_revision`` (creates a second head) without
    waiting for CI.
    """

    def test_revision_id_is_073(self) -> None:
        module = _load_migration_module()
        assert module.revision == "073_create_nfm_preview_role"

    def test_down_revision_is_072(self) -> None:
        module = _load_migration_module()
        # 072 is the current chain head. If the chain reorders and 072
        # is no longer the head, this test fails loudly — better to
        # update the test than to silently create a second head.
        assert module.down_revision == "072_material_kg_bridge_coverage"

    def test_no_branch_labels_or_depends_on(self) -> None:
        module = _load_migration_module()
        assert module.branch_labels is None
        assert module.depends_on is None

    def test_single_chain_head(self) -> None:
        """Ensure there is exactly one alembic head.

        Originally asserted 073 as the head (NFM-4122). Migration 075
        (NFM-4139) chained after 073, then 076 (NFM-4159 attribution
        view), then 077 (NFM-4159 datasets.source_id nullable) extended
        it further.  077 is the current head.
        pre-deploy-assert checks this in CI, but a sub-second check here
        keeps the PR signal clean — a "two heads" failure here is a
        red-flag stop-the-line, not a 6-minute build.
        """
        # Use the repo's alembic ScriptDirectory directly so this test
        # does not depend on the app's runtime config (which requires a
        # live database URL).
        from alembic.script import ScriptDirectory

        migrations_dir = (
            _MIGRATION_PATH.parents[1]
        )  # .../apps/api/migrations
        sd = ScriptDirectory(str(migrations_dir))
        heads = list(sd.get_heads())
        assert heads == ["077_datasets_source_id_nullable"], (
            f"alembic heads is {heads}; pre-deploy-assert would block the "
            f"deploy. Update the new migration's down_revision to point at "
            f"the actual chain head."
        )


class TestPasswordSubstitution:
    """The alembic path loads the canonical SQL and substitutes the password.

    The substitution must (a) be present, (b) be properly escaped against
    SQL injection, and (c) fail loudly when the env var is unset — the
    deploy workflow's `${NFMD_PREVIEW_DB_PASSWORD:?...}` reference in
    docker-compose.prod.yml is the OUTER guard rail; this is the INNER
    one. A silent CREATE ROLE with an empty / placeholder password
    would defeat the entire purpose of the migration.
    """

    def test_sql_file_has_password_reference(self) -> None:
        assert _SQL_PATH.exists(), (
            f"Canonical bootstrap SQL missing at {_SQL_PATH}; the alembic "
            f"migration cannot apply without it."
        )
        content = _SQL_PATH.read_text(encoding="utf-8")
        assert ":'NFMD_PREVIEW_DB_PASSWORD'" in content, (
            "SQL file is missing the psql variable reference; the alembic "
            "loader cannot substitute the password."
        )

    def test_loader_substitutes_password(self, monkeypatch) -> None:
        """Run the migration's loader with a known password and verify
        the result contains the password (escaped) and no longer has
        the psql variable reference.
        """
        monkeypatch.setenv("NFMD_PREVIEW_DB_PASSWORD", "hunter2")
        module = _load_migration_module()

        rendered = module._load_bootstrap_sql_for_alembic()

        assert ":'NFMD_PREVIEW_DB_PASSWORD'" not in rendered, (
            "Loader did not substitute the psql variable reference."
        )
        assert "'hunter2'" in rendered, (
            "Loader did not inject the password value as a SQL string "
            "literal."
        )

    def test_loader_escapes_single_quotes(self, monkeypatch) -> None:
        """A password containing single quotes must not break the SQL.

        A generated password from a secret manager should not contain
        single quotes in practice, but a sloppy escape path is a SQL
        injection vector regardless.
        """
        # 'hunter2'; DROP ROLE nfm_preview; --
        tricky = "hunter2'; DROP ROLE nfm_preview; --"
        monkeypatch.setenv("NFMD_PREVIEW_DB_PASSWORD", tricky)
        module = _load_migration_module()

        rendered = module._load_bootstrap_sql_for_alembic()

        # Single quote must be doubled per SQL standard.
        expected_escaped = tricky.replace("'", "''")
        assert f"'{expected_escaped}'" in rendered
        # The original (un-escaped) form must NOT appear in the rendered
        # SQL — that would mean the escape pass was skipped.
        assert tricky not in rendered

    def test_loader_escapes_backslashes(self, monkeypatch) -> None:
        """Backslashes must be doubled (C-style escape strings).

        PostgreSQL's ``standard_conforming_strings`` is on by default in
        modern versions, so a backslash in a string literal is literal
        — not an escape character. But the SQL standard escape
        (backslash -> double-backslash) is still the conservative
        thing to do, and the loader's escape pass must handle it.
        """
        monkeypatch.setenv("NFMD_PREVIEW_DB_PASSWORD", "a\\b")
        module = _load_migration_module()

        rendered = module._load_bootstrap_sql_for_alembic()

        # The literal rendered into SQL must be 'a\\b' (i.e. backslash
        # doubled) — the PostgreSQL parser sees one backslash.
        assert "'a\\\\b'" in rendered

    def test_loader_fails_loudly_without_env_var(self, monkeypatch) -> None:
        """A missing env var must abort, not create a role with no password.

        This is the load-bearing guard for the case where
        docker/.env.prod was updated but the deploy shell did not
        re-source it (or the operator forgot to set the variable).
        Silent fallback to no password would let anyone with the
        ``nfm`` password connect as ``nfm_preview`` too — a privilege
        escalation.
        """
        monkeypatch.delenv("NFMD_PREVIEW_DB_PASSWORD", raising=False)
        module = _load_migration_module()

        with pytest.raises(RuntimeError) as excinfo:
            module._load_bootstrap_sql_for_alembic()
        assert "NFMD_PREVIEW_DB_PASSWORD" in str(excinfo.value)

    def test_loader_strips_psql_meta_commands(self, monkeypatch) -> None:
        """The manual psql path uses ``\\set ON_ERROR_STOP on``.

        That is a psql CLIENT command, not SQL — SQLAlchemy ``text()``
        would reject it. The loader must strip psql meta-commands
        before executing.
        """
        monkeypatch.setenv("NFMD_PREVIEW_DB_PASSWORD", "hunter2")
        module = _load_migration_module()

        rendered = module._load_bootstrap_sql_for_alembic()

        # No psql meta-commands should leak through.
        assert not re.search(r"^\s*\\[a-zA-Z]", rendered, re.MULTILINE), (
            "Loader left psql meta-commands in the rendered SQL; "
            "SQLAlchemy will fail with a syntax error."
        )

    def test_loader_raises_if_sql_missing_password_reference(
        self, monkeypatch, tmp_path
    ) -> None:
        """Defensive guard: if someone refactors the SQL and removes the
        ``:'NFMD_PREVIEW_DB_PASSWORD'`` reference, the loader must
        raise rather than silently emit a role without a password.
        """
        monkeypatch.setenv("NFMD_PREVIEW_DB_PASSWORD", "hunter2")

        # Monkey-patch the SQL path to point at a version missing the
        # password reference.
        broken_sql = tmp_path / "create_nfm_preview_role.sql"
        broken_sql.write_text(
            "CREATE ROLE nfm_preview LOGIN PASSWORD 'placeholder';\n",
            encoding="utf-8",
        )
        module = _load_migration_module()
        monkeypatch.setattr(module, "_SQL_REL_PATH", broken_sql)

        with pytest.raises(RuntimeError) as excinfo:
            module._load_bootstrap_sql_for_alembic()
        assert "NFMD_PREVIEW_DB_PASSWORD" in str(excinfo.value)


class TestBootstrapSqlStructure:
    """Static checks on the canonical bootstrap SQL.

    These are the steps that close NFM-4106 acceptance criterion 1. If
    any are removed, the bypass comes back. The tests are intentionally
    text-based (not parsed) so the SQL stays readable for the next
    operator who has to rotate the password at 2am.
    """

    @pytest.fixture
    def sql_content(self) -> str:
        return _SQL_PATH.read_text(encoding="utf-8")

    def test_creates_role_with_login(self, sql_content: str) -> None:
        assert re.search(
            r"CREATE ROLE nfm_preview\s+LOGIN\s+PASSWORD",
            sql_content,
            re.IGNORECASE,
        ), "Role creation statement is missing or malformed."

    def test_role_creation_is_idempotent(self, sql_content: str) -> None:
        """The CREATE ROLE must be guarded by an IF NOT EXISTS check.

        Without the guard, re-running the migration (e.g. from the
        alembic path after the manual psql bootstrap already ran) would
        fail with ``role already exists`` and abort the deploy.
        assert_pattern matches a DO block that checks pg_roles.
        """
        assert "IF NOT EXISTS" in sql_content, (
            "Role creation is not idempotent — re-running the migration "
            "would fail with 'role already exists'."
        )
        assert "pg_roles" in sql_content, (
            "Idempotency check does not consult pg_roles — the guard "
            "is meaningless without it."
        )

    def test_grants_connect_on_database(self, sql_content: str) -> None:
        assert re.search(
            r"GRANT CONNECT\s+ON DATABASE\s+nfm_db\s+TO nfm_preview",
            sql_content,
            re.IGNORECASE,
        )

    def test_grants_usage_on_schema_public(self, sql_content: str) -> None:
        # Omitting GRANT CREATE on schema public is what blocks DDL —
        # assert the USAGE grant is present and there is NO CREATE grant.
        assert re.search(
            r"GRANT USAGE\s+ON SCHEMA\s+public\s+TO nfm_preview",
            sql_content,
            re.IGNORECASE,
        )
        # CREATE on schema would defeat the point of the role. This is
        # the NFM-4122 invariant; the absence is the assertion.
        assert not re.search(
            r"GRANT CREATE\s+ON SCHEMA\s+public\s+TO nfm_preview",
            sql_content,
            re.IGNORECASE,
        ), (
            "Role has CREATE on schema public — DDL is permitted, which "
            "defeats the entire purpose of NFM-4122."
        )

    def test_grants_dml_on_existing_tables(self, sql_content: str) -> None:
        assert re.search(
            r"GRANT SELECT,\s*INSERT,\s*UPDATE,\s*DELETE\s+"
            r"ON ALL TABLES IN SCHEMA\s+public\s+TO nfm_preview",
            sql_content,
            re.IGNORECASE,
        )

    def test_grants_select_only_on_alembic_version(
        self, sql_content: str
    ) -> None:
        """The load-bearing step: alembic_version must be SELECT only.

        Without this, ``alembic upgrade head`` would succeed in stamping
        and NFM-4106 criterion 1 would still fail.
        """
        # REVOKE writes from the table — covers the INSERT/UPDATE/
        # DELETE/TRUNCATE the prior ALL TABLES grant would otherwise
        # confer.
        assert re.search(
            r"REVOKE\s+(INSERT|UPDATE|DELETE|TRUNCATE)[^;]*"
            r"ON TABLE\s+alembic_version\s+FROM nfm_preview",
            sql_content,
            re.IGNORECASE,
        ), (
            "alembic_version is not write-revoked — the bypass is "
            "still open. NFM-4106 acceptance criterion 1 is not closed."
        )
        # SELECT must be explicitly granted back (REVOKE removes the
        # ALL TABLES grant that previously included SELECT).
        assert re.search(
            r"GRANT SELECT\s+ON TABLE\s+alembic_version\s+TO nfm_preview",
            sql_content,
            re.IGNORECASE,
        )

    def test_default_privileges_for_nfm_owned_objects(
        self, sql_content: str
    ) -> None:
        """Future tables created by ``nfm`` must auto-grant to nfm_preview.

        Without this, a migration that adds a new table after the role
        is created would leave nfm_preview blind to that table.
        """
        assert re.search(
            r"ALTER DEFAULT PRIVILEGES\s+FOR ROLE\s+nfm\s+IN SCHEMA\s+public\s+"
            r"GRANT SELECT,\s*INSERT,\s*UPDATE,\s*DELETE\s+"
            r"ON TABLES TO nfm_preview",
            sql_content,
            re.IGNORECASE,
        )

    def test_refuses_non_superuser(self, sql_content: str) -> None:
        """The script must abort if run as a non-superuser.

        CREATE ROLE and ALTER DEFAULT PRIVILEGES require superuser.
        Catching the failure here produces a self-diagnosing message
        instead of a confusing "permission denied to create role"
        half-way through.
        """
        assert "rolsuper" in sql_content, (
            "Script does not check rolsuper — non-superuser callers "
            "will get a confusing permission error mid-script."
        )
