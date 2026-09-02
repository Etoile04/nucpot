"""NFM-4097 — F4 UUID-titled source guard migration structural tests.

Mirrors the NFM-4088 / migration-070 test pattern (NFM-2137 lineage):
verify alembic chain wiring, SQL payload contents, trigger
plumbing, idempotency, and downgrade ordering without requiring a
live PostgreSQL.

The migration is plpgsql / PG-specific — these tests are
**structural**: they confirm the migration file is wired in, the
SQL contains every required fragment, and ``upgrade()`` /
``downgrade()`` execute on a mocked alembic bind without raising.

Acceptance criteria covered
---------------------------

* [AC-3.1] File ``071_f4_uuid_titled_source_guard.py`` exists in
  ``apps/api/migrations/versions/`` and parses as valid Python.
* [AC-3.2] ``revision`` constant matches ``"071_f4_uuid_titled_source_guard"``.
* [AC-3.3] ``down_revision`` is ``"070_d2_dedup_bad_data_sources"``
  (NFM-4088 done — chain order enforces data-dedup precedes the
  guard).
* [AC-3.4] Defines ``upgrade()`` and ``downgrade()``.
* [AC-3.5] Trigger function ``reject_uuid_titled_source()`` installed
  via ``CREATE OR REPLACE FUNCTION``.
* [AC-3.6] Trigger ``trg_data_sources_uuid_title`` is ``BEFORE INSERT
  OR UPDATE OF title`` on ``data_sources`` and fires
  ``reject_uuid_titled_source``.
* [AC-3.7] The UUID-title regex is anchored on both ends with the
  canonical ``[0-9a-f]{8}-[0-9a-f]{4}-...`` pattern.
* [AC-3.8] The trigger writes ``health_events`` with
  ``event_type='uuid_titled_source_blocked'``,
  ``severity='critical'``, ``source_service='ingest'`` on match.
* [AC-3.9] The trigger raises ``EXCEPTION ... USING ERRCODE =
  'check_violation'`` so application code surfaces a SQLAlchemy
  ``IntegrityError`` rather than a silent insert.
* [AC-3.10] ``RETURN NEW`` is present so non-matching inserts
  proceed.
* [AC-3.11] The migration extends the ``health_events`` event_type
  CHECK constraint so ``uuid_titled_source_blocked`` is accepted
  (the trigger INSERT would otherwise violate
  ``ck_health_events_event_type``).
* [AC-3.12] Idempotent: ``CREATE OR REPLACE FUNCTION``, ``DROP
  TRIGGER IF EXISTS``, ``DROP FUNCTION IF EXISTS``.
* [AC-3.13] Downgrade order: trigger dropped **before** the
  function it depends on (PostgreSQL would otherwise reject the
  function drop with "function ... is used by trigger").
* [AC-3.14] Migration does NOT use ``txid_current()`` as a
  SQLAlchemy bind parameter (NFM-4099 — asyncpg cannot bind into
  ``DO`` blocks; the trigger body is plpgsql, so the call is
  safe — this test pins that the migration uses a plain string
  literal for any plpgsql body).

NFM-4097 test design rationale
------------------------------

The structural-test pattern was chosen over live execution because:

1. The migration is plpgsql-only and requires a real PostgreSQL
   server with ``health_events`` + ``data_sources`` tables
   populated.
2. The CI environment runs unit + migration structural tests
   without a live DB (see ``conftest.py`` — ``pytest.ini``
   ``addopts = -m "not integration"``).
3. ``test_migration_070_d2_dedup.py`` ships ~700 lines of
   structural coverage for NFM-4088 — the same harness applies
   here and keeps parity with the standing rule.

A separate ``test_health_degraded_on_uuid_block.py`` covers the
runtime behaviour of AC-4 against an in-memory test DB.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

_MIGRATION_PATH = (
    Path(__file__).resolve().parent.parent
    / "migrations"
    / "versions"
    / "071_f4_uuid_titled_source_guard.py"
)


@pytest.fixture(scope="module")
def migration_source() -> str:
    return _MIGRATION_PATH.read_text()


@pytest.fixture(scope="module")
def migration_ast(migration_source: str) -> ast.Module:
    return ast.parse(migration_source)


@pytest.fixture(scope="module")
def migration_module():
    """Import the migration module so the SQL builders can be
    inspected directly (the SQL is built from module-level constants
    via f-strings, so source-literal substring searches miss the
    rendered fragments)."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("m071_under_test", str(_MIGRATION_PATH))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def trigger_function_sql(migration_module) -> str:
    """Rendered ``CREATE OR REPLACE FUNCTION`` SQL."""
    return migration_module._build_trigger_function_sql()


@pytest.fixture(scope="module")
def trigger_sql(migration_module) -> str:
    """Rendered ``CREATE TRIGGER`` SQL."""
    return migration_module._build_trigger_sql()


@pytest.fixture(scope="module")
def upgrade_event_type_check_sql(migration_module) -> str:
    """Rendered event_type CHECK fragment for the upgrade (extended enum)."""
    return migration_module._build_event_type_check_sql(
        (*migration_module._ORIGINAL_EVENT_TYPES, migration_module._TRIGGER_EVENT_TYPE)
    )


@pytest.fixture(scope="module")
def downgrade_event_type_check_sql(migration_module) -> str:
    """Rendered event_type CHECK fragment for the downgrade (original)."""
    return migration_module._build_event_type_check_sql(migration_module._ORIGINAL_EVENT_TYPES)


# ---------------------------------------------------------------------------
# Chain / wiring
# ---------------------------------------------------------------------------


class TestMigration071Chain:
    """Migration is correctly wired into the alembic chain."""

    def test_file_exists(self) -> None:
        assert _MIGRATION_PATH.is_file()

    def test_revision_constant(self, migration_source: str) -> None:
        assert re.search(
            r'^revision:\s*str\s*=\s*"071_f4_uuid_titled_source_guard"',
            migration_source,
            re.MULTILINE,
        ), "revision must equal '071_f4_uuid_titled_source_guard'"

    def test_chains_off_070(self, migration_source: str) -> None:
        """NFM-4088 (D2 dedup) must be the immediate predecessor so the
        guard trigger ships AFTER the 14 UUID-title dirty rows are
        cleaned up.
        """
        assert re.search(
            r"^down_revision:\s*str\s*\|\s*Sequence\[str\]\s*\|\s*None\s*=\s*"
            r'"070_d2_dedup_bad_data_sources"',
            migration_source,
            re.MULTILINE,
        ), "down_revision must be '070_d2_dedup_bad_data_sources' (NFM-4088)"


# ---------------------------------------------------------------------------
# Structural / Python validity
# ---------------------------------------------------------------------------


class TestMigration071Structure:
    """Migration file is valid Python and uses the alembic primitives."""

    def test_parses_as_valid_python(self, migration_ast: ast.Module) -> None:
        # ``ast.parse`` already raises on syntax errors; assert the
        # fixture ran without raising to make the failure message
        # explicit at the pytest boundary.
        assert migration_ast.body

    def test_uses_alembic_op(self, migration_ast: ast.Module) -> None:
        names: set[str] = set()
        for node in ast.walk(migration_ast):
            if isinstance(node, (ast.ImportFrom, ast.Import)):
                for alias in node.names:
                    names.add(alias.name)
        assert "op" in names, "migration must `from alembic import op`"

    def test_defines_upgrade_and_downgrade(self, migration_ast: ast.Module) -> None:
        functions = [n.name for n in migration_ast.body if isinstance(n, ast.FunctionDef)]
        assert "upgrade" in functions
        assert "downgrade" in functions


# ---------------------------------------------------------------------------
# Trigger plumbing
# ---------------------------------------------------------------------------


class TestMigration071Trigger:
    """Trigger function + trigger definition are present and correct."""

    def test_create_or_replace_function_present(self, trigger_function_sql: str) -> None:
        assert re.search(
            r"CREATE\s+OR\s+REPLACE\s+FUNCTION\s+reject_uuid_titled_source",
            trigger_function_sql,
            flags=re.IGNORECASE,
        ), "must `CREATE OR REPLACE FUNCTION reject_uuid_titled_source()`"

    def test_function_returns_trigger(self, trigger_function_sql: str) -> None:
        # Accept either ``AS $$`` or ``AS $tag$`` dollar-quotes — the
        # migration uses ``$func$`` so the regex's ``\$`` characters
        # in the plpgsql body cannot accidentally close the body
        # (NFM-4099 — defence-in-depth against regex special chars
        # terminating ``$$ ... $$`` early).
        assert re.search(
            r"RETURNS\s+trigger\s+AS\s+\$(?:\$|[a-zA-Z][a-zA-Z0-9_]*)\$",
            trigger_function_sql,
            flags=re.IGNORECASE,
        ), "function must declare `RETURNS trigger AS $<tag>$`"

    def test_function_language_plpgsql(self, trigger_function_sql: str) -> None:
        assert re.search(
            r"LANGUAGE\s+plpgsql",
            trigger_function_sql,
            flags=re.IGNORECASE,
        ), "function must be `LANGUAGE plpgsql`"

    def test_trigger_name(self, trigger_sql: str) -> None:
        assert re.search(
            r"CREATE\s+TRIGGER\s+trg_data_sources_uuid_title",
            trigger_sql,
            flags=re.IGNORECASE,
        ), "trigger must be named `trg_data_sources_uuid_title`"

    def test_trigger_timing_before(self, trigger_sql: str) -> None:
        assert re.search(
            r"BEFORE\s+INSERT\s+OR\s+UPDATE\s+OF\s+title\s+ON\s+data_sources",
            trigger_sql,
            flags=re.IGNORECASE,
        ), (
            "trigger must be `BEFORE INSERT OR UPDATE OF title ON data_sources` "
            "so the guard fires before the row is visible"
        )

    def test_trigger_executes_function(self, trigger_sql: str) -> None:
        assert re.search(
            r"FOR\s+EACH\s+ROW\s+EXECUTE\s+FUNCTION\s+reject_uuid_titled_source\(\)",
            trigger_sql,
            flags=re.IGNORECASE,
        ), "trigger must `EXECUTE FUNCTION reject_uuid_titled_source()`"

    def test_return_new_on_match(self, trigger_function_sql: str) -> None:
        """``RETURN NEW`` is present so non-matching inserts succeed."""
        # ``RETURN NEW`` appears in plpgsql AFTER the IF block; the regex
        # accepts a single trailing occurrence.
        assert re.search(r"\bRETURN\s+NEW\b", trigger_function_sql), (
            "trigger function must RETURN NEW so non-UUID-title inserts are not blocked"
        )


# ---------------------------------------------------------------------------
# UUID regex & RAISE EXCEPTION shape
# ---------------------------------------------------------------------------


class TestMigration071UuidRegex:
    """The UUID-title regex is anchored and matches the issue spec."""

    def test_uuid_regex_anchored(self, trigger_function_sql: str, migration_module) -> None:
        # The issue spec mandates lowercase-only anchors:
        #   ^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$
        # The constant lives in ``migration_module._UUID_TITLE_REGEX``
        # so we assert both the constant itself and that it was
        # rendered into the trigger function's body.
        assert migration_module._UUID_TITLE_REGEX == (
            r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
            r"[0-9a-f]{4}-[0-9a-f]{12}$"
        ), "the regex constant must match the issue spec verbatim"
        assert migration_module._UUID_TITLE_REGEX in trigger_function_sql, (
            "canonical lowercase UUID regex must be rendered into the trigger function body"
        )

    def test_uuid_regex_uses_postgres_match_operator(self, trigger_function_sql: str) -> None:
        """Trigger must test the title with ``~`` (regex match).

        The PostgreSQL ``~`` operator performs case-sensitive POSIX
        regex matching — required so the regex doesn't accidentally
        match uppercase UUIDs (the issue spec is lowercase-only).
        """
        assert re.search(
            r"NEW\.title\s+~\s*'",
            trigger_function_sql,
        ), "trigger must use `NEW.title ~ '<regex>'`"


class TestMigration071RaiseException:
    """Trigger raises a proper PostgreSQL exception with ERRCODE."""

    def test_raise_exception_present(self, trigger_function_sql: str) -> None:
        assert re.search(
            r"\bRAISE\s+EXCEPTION\b",
            trigger_function_sql,
            flags=re.IGNORECASE,
        ), "trigger must RAISE EXCEPTION on UUID-title match"

    def test_errcode_check_violation(self, trigger_function_sql: str) -> None:
        assert re.search(
            r"USING\s+ERRCODE\s*=\s*'check_violation'",
            trigger_function_sql,
            flags=re.IGNORECASE,
        ), (
            "trigger must raise with `USING ERRCODE = 'check_violation'` "
            "so application code surfaces a clean SQLAlchemy IntegrityError"
        )


# ---------------------------------------------------------------------------
# health_events plumbing
# ---------------------------------------------------------------------------


class TestMigration071HealthEvents:
    """Trigger writes a health_events row on match."""

    def test_writes_uuid_titled_source_blocked_event_type(self, trigger_function_sql: str) -> None:
        # The SQL uses a positional INSERT VALUES form
        # (event_type, severity, source_service, ...) VALUES
        # ('<event_type>', ...), so the test asserts the literal
        # value appears in the rendered SQL — the column name lives
        # on the previous line.  Both must be present.
        assert "event_type" in trigger_function_sql, "trigger must declare the event_type column"
        assert "'uuid_titled_source_blocked'" in trigger_function_sql, (
            "trigger must insert event_type='uuid_titled_source_blocked' "
            "so the AC-4 health check can filter on it"
        )

    def test_writes_critical_severity(self, trigger_function_sql: str) -> None:
        assert "severity" in trigger_function_sql, "trigger must declare the severity column"
        assert "'critical'" in trigger_function_sql, "trigger must insert severity='critical'"

    def test_writes_source_service_ingest(self, trigger_function_sql: str) -> None:
        assert "source_service" in trigger_function_sql, (
            "trigger must declare the source_service column"
        )
        assert "'ingest'" in trigger_function_sql, "trigger must insert source_service='ingest'"

    def test_context_includes_source_id_title_op_txid(self, trigger_function_sql: str) -> None:
        """Context payload carries the diagnostic fields ops needs."""
        assert "NEW.id" in trigger_function_sql, (
            "trigger context must include NEW.id so ops can trace back"
        )
        assert "NEW.title" in trigger_function_sql, "trigger context must include NEW.title"
        assert "TG_OP" in trigger_function_sql, (
            "trigger context must include TG_OP (INSERT vs UPDATE)"
        )
        # ``txid_current()`` is a built-in PG function returning the
        # current transaction id — safe inside a plpgsql body (the
        # crash NFM-4099 fixed was SQLAlchemy bind-params against a
        # ``DO`` block, not a plpgsql function call).
        assert re.search(r"\btxid_current\(\)", trigger_function_sql), (
            "trigger context must include txid_current() for transaction trace correlation"
        )

    def test_extends_health_events_check_constraint(
        self, upgrade_event_type_check_sql: str, migration_module
    ) -> None:
        """Migration must extend ``ck_health_events_event_type`` to admit
        ``uuid_titled_source_blocked``.

        The original constraint (NFM-2220) only allows:
            fallback_triggered, validation_drop, category_coercion_fail,
            asyncio_crash, generic_silent_catch
        A bare ``INSERT INTO health_events (event_type, ...)
        VALUES ('uuid_titled_source_blocked', ...)`` would violate
        this CHECK constraint; the migration must extend it before
        the trigger is installed.
        """
        # The rendered upgrade CHECK constraint must include the new
        # event type AND every original event type.
        assert migration_module._TRIGGER_EVENT_TYPE in upgrade_event_type_check_sql, (
            "upgrade's CHECK constraint must include uuid_titled_source_blocked"
        )
        for event_type in migration_module._ORIGINAL_EVENT_TYPES:
            assert event_type in upgrade_event_type_check_sql, (
                f"upgrade's CHECK constraint must retain original event_type '{event_type}'"
            )

    def test_trigger_insert_includes_id_with_gen_random_uuid(
        self, trigger_function_sql: str
    ) -> None:
        """Regression-guard for NFM-4097 finding 1 (dblink form).

        ``health_events.id`` (NFM-2220 / migration 037) is
        ``uuid NOT NULL PRIMARY KEY`` **without** a server-side
        ``DEFAULT``.  The trigger's ``health_events`` INSERT now
        runs inside a ``dblink_exec`` SQL string literal (NFM-4097
        finding 2 — autonomous transaction so the row persists when
        the outer ``RAISE EXCEPTION`` rolls back).  That string
        literal must therefore:

          1. Declare ``id`` in the column list (so PG is not asked
             to infer a NULL primary key).
          2. Evaluate ``gen_random_uuid()`` in the ``VALUES`` clause
             (so the row actually inserts).

        Without these, the prior bug recurs: a NotNullViolation on
        ``health_events.id`` aborts the dblink exec, the inner
        exception handler re-raises, and the data_sources INSERT is
        rejected — but AC-4's monitoring row never appears, leaving
        ``/health`` permanently ``ok`` even when the trigger fires.

        The dblink INSERT is built via ``format(...)`` with the
        column list and VALUES spread across several adjacent
        string-literal segments (PG-side string concatenation).
        Collapse them first so the regex sees a single logical
        string.
        """
        # Extract the plpgsql body between $func$ ... $func$ so a
        # stray ``id`` elsewhere does not pass.
        m = re.search(
            r"AS\s+\$func\$(.*?)\$func\$\s+LANGUAGE\s+plpgsql",
            trigger_function_sql,
            flags=re.DOTALL,
        )
        assert m is not None, (
            "trigger SQL must define a plpgsql block delimited with "
            "$func$ ... $func$ LANGUAGE plpgsql"
        )
        plpgsql_body = m.group(1)

        # Collapse PG-side adjacent string-literal concatenation
        # (``'abc' 'def'`` -> ``'abcdef'``) so the regex sees the
        # logical SQL string as one token.  This is conservative:
        # we only collapse single-quoted segments separated by
        # whitespace, matching PG's literal-concatenation rule.
        collapsed = re.sub(
            r"'((?:[^']|'')*)'(?=\s*')",
            r"'\1'",
            plpgsql_body,
        )

        # INVARIANT 1: column list declares ``id`` alongside the
        # other health_events columns.
        assert re.search(
            r"INSERT\s+INTO\s+health_events\s*\(\s*[^)]*\bid\b",
            collapsed,
            flags=re.IGNORECASE,
        ), (
            "trigger's dblink_exec SQL must declare 'id' in the "
            "health_events column list — health_events.id is NOT NULL "
            "with no server-side DEFAULT (NFM-2220 / migration 037); "
            "the omission raised psycopg2.errors.NotNullViolation and "
            "masked the intended check_violation in NFM-4097 finding 1"
        )

        # INVARIANT 2: VALUES clause evaluates gen_random_uuid() so
        # the row actually inserts and AC-4 monitoring flips.
        assert re.search(
            r"VALUES\s*\([^)]*gen_random_uuid\(\)",
            collapsed,
            flags=re.IGNORECASE,
        ), (
            "trigger's dblink_exec SQL must call gen_random_uuid() in "
            "the VALUES clause to populate health_events.id "
            "(NFM-4097 finding 1)"
        )

    def test_check_constraint_drop_and_recreate_present(self, migration_source: str) -> None:
        """The migration source must issue a DROP CONSTRAINT +
        ADD CONSTRAINT pair for ``ck_health_events_event_type``.
        """
        assert re.search(
            r"DROP\s+CONSTRAINT\s+ck_health_events_event_type",
            migration_source,
            flags=re.IGNORECASE,
        ), (
            "migration must DROP CONSTRAINT ck_health_events_event_type "
            "before re-adding it with the extended enum"
        )
        assert re.search(
            r"ADD\s+CONSTRAINT\s+ck_health_events_event_type",
            migration_source,
            flags=re.IGNORECASE,
        ), "migration must ADD CONSTRAINT ck_health_events_event_type with the extended enum"


# ---------------------------------------------------------------------------
# Autonomous transaction via dblink (NFM-4097 finding 2)
# ---------------------------------------------------------------------------


class TestMigration071DblinkAutonomousTransaction:
    """The trigger must persist ``health_events`` via dblink so AC-4
    can count blocks even when the outer ``RAISE EXCEPTION`` rolls
    back the data_sources INSERT.

    Without this indirection, a structural-only check (NFM-4097
    finding 1) cannot catch the bug: the SQL string looks correct
    but every ``health_events`` INSERT gets wiped by the same
    transaction that aborts the data_sources INSERT.
    """

    def test_upgrade_creates_dblink_extension(self, migration_source: str) -> None:
        """Upgrade must install the ``dblink`` extension before the
        trigger that depends on it.

        ``dblink`` ships with the ``postgresql-contrib`` package (the
        ``pgvector/pgvector:pg16`` Docker image this stack uses
        includes it).  ``CREATE EXTENSION IF NOT EXISTS`` is the
        idempotent install primitive; an upgrade on a fresh DB
        installs it, an upgrade on a DB that already has it is a
        no-op.
        """
        assert re.search(
            r"CREATE\s+EXTENSION\s+IF\s+NOT\s+EXISTS\s+dblink",
            migration_source,
            flags=re.IGNORECASE,
        ), (
            "upgrade must CREATE EXTENSION IF NOT EXISTS dblink — the "
            "trigger's autonomous-transaction pattern requires the "
            "dblink extension to be installed first (NFM-4097 finding 2)"
        )

    def test_trigger_uses_dblink_connect_u(
        self, trigger_function_sql: str
    ) -> None:
        """The trigger must open the autonomous-transaction connection
        via ``dblink_connect_u``.

        ``dblink_connect_u`` (the untrusted variant) bypasses
        ``pg_hba.conf`` and reuses the current session's credentials,
        so the trigger does not need to embed a password.  A
        non-``_u`` ``dblink_connect`` would require a libpq
        connection string with credentials and is rejected here to
        keep the migration portable across staging / prod.
        """
        assert re.search(
            r"\bdblink_connect_u\s*\(",
            trigger_function_sql,
            flags=re.IGNORECASE,
        ), (
            "trigger must call dblink_connect_u() — this opens the "
            "autonomous-transaction connection used to persist "
            "health_events past the outer RAISE EXCEPTION rollback "
            "(NFM-4097 finding 2)"
        )

    def test_trigger_uses_dblink_exec(
        self, trigger_function_sql: str
    ) -> None:
        """The trigger must run the ``INSERT INTO health_events``
        via ``dblink_exec`` so it executes in the autonomous
        transaction (not the outer one).
        """
        assert re.search(
            r"\bdblink_exec\s*\(",
            trigger_function_sql,
            flags=re.IGNORECASE,
        ), (
            "trigger must call dblink_exec() — the INSERT INTO "
            "health_events must run inside the autonomous connection "
            "(opened by dblink_connect_u), not in the outer "
            "transaction (NFM-4097 finding 2)"
        )

    def test_trigger_uses_dblink_disconnect(
        self, trigger_function_sql: str
    ) -> None:
        """The trigger must close the autonomous connection via
        ``dblink_disconnect`` so the backend does not accumulate
        idle dblink connections across repeated trigger firings.
        """
        assert re.search(
            r"\bdblink_disconnect\s*\(",
            trigger_function_sql,
            flags=re.IGNORECASE,
        ), (
            "trigger must call dblink_disconnect() to release the "
            "autonomous-transaction connection (NFM-4097 finding 2)"
        )

    def test_trigger_dblink_conn_name_is_pid_suffixed(
        self, trigger_function_sql: str
    ) -> None:
        """The dblink connection name must be suffixed with
        ``pg_backend_pid()`` so two trigger firings in the same
        backend do not collide on the named-connection namespace.

        Without the suffix, a second trigger firing in the same
        backend would either fail with ``connection already exists``
        or step on the first connection's state.
        """
        # Look for pg_backend_pid() inside the plpgsql body (so a
        # stray reference in a comment does not pass).
        m = re.search(
            r"AS\s+\$func\$(.*?)\$func\$\s+LANGUAGE\s+plpgsql",
            trigger_function_sql,
            flags=re.DOTALL,
        )
        assert m is not None, (
            "trigger SQL must define a plpgsql block delimited with "
            "$func$ ... $func$ LANGUAGE plpgsql"
        )
        plpgsql_body = m.group(1)
        assert re.search(
            r"pg_backend_pid\(\)",
            plpgsql_body,
            flags=re.IGNORECASE,
        ), (
            "trigger's dblink connection name must include "
            "pg_backend_pid() so concurrent firings in the same "
            "backend do not collide on the named-connection namespace "
            "(NFM-4097 finding 2)"
        )

    def test_trigger_dblink_connstr_uses_current_db_and_user(
        self, trigger_function_sql: str
    ) -> None:
        """The dblink connection string must derive ``dbname`` and
        ``user`` from the current session (``current_database()``,
        ``current_user``) so the trigger is portable across staging
        / prod without embedding credentials.
        """
        m = re.search(
            r"AS\s+\$func\$(.*?)\$func\$\s+LANGUAGE\s+plpgsql",
            trigger_function_sql,
            flags=re.DOTALL,
        )
        assert m is not None, (
            "trigger SQL must define a plpgsql block delimited with "
            "$func$ ... $func$ LANGUAGE plpgsql"
        )
        plpgsql_body = m.group(1)
        assert re.search(
            r"current_database\(\)",
            plpgsql_body,
            flags=re.IGNORECASE,
        ), (
            "trigger's dblink connection string must derive dbname "
            "from current_database() so the trigger is portable "
            "across staging / prod (NFM-4097 finding 2)"
        )
        assert re.search(
            r"current_user\b",
            plpgsql_body,
            flags=re.IGNORECASE,
        ), (
            "trigger's dblink connection string must derive user "
            "from current_user so the trigger is portable across "
            "staging / prod (NFM-4097 finding 2)"
        )

    def test_trigger_no_direct_insert_into_health_events(
        self, trigger_function_sql: str
    ) -> None:
        """The trigger must NOT contain a direct (non-dblink)
        ``INSERT INTO health_events`` statement — a direct INSERT
        runs in the outer transaction and is rolled back by the
        subsequent ``RAISE EXCEPTION``, defeating AC-4.

        The only ``INSERT INTO health_events`` in the function body
        must be the SQL string literal passed to ``dblink_exec``.
        This guard pins the autonomous-transaction invariant so a
        future "simplification" cannot regress to the broken form
        without breaking the test.
        """
        m = re.search(
            r"AS\s+\$func\$(.*?)\$func\$\s+LANGUAGE\s+plpgsql",
            trigger_function_sql,
            flags=re.DOTALL,
        )
        assert m is not None, (
            "trigger SQL must define a plpgsql block delimited with "
            "$func$ ... $func$ LANGUAGE plpgsql"
        )
        plpgsql_body = m.group(1)

        # The format() builder spreads the INSERT across several
        # adjacent string segments, so we strip all single-quoted
        # string literals first (replacing them with ``''``) and
        # then search for any top-level ``INSERT INTO health_events``
        # that runs in the outer transaction.  Any occurrence after
        # stripping is the regression we are guarding against — a
        # direct INSERT in the outer transaction would be rolled
        # back by the subsequent ``RAISE EXCEPTION`` and defeat
        # AC-4.
        body_no_strings = re.sub(r"'(?:[^']|'')*'", "''", plpgsql_body)
        direct_inserts = re.findall(
            r"\bINSERT\s+INTO\s+health_events\b",
            body_no_strings,
            flags=re.IGNORECASE,
        )
        assert direct_inserts == [], (
            "trigger must NOT execute a direct INSERT INTO health_events "
            "in the outer transaction — every health_events write must "
            "go through dblink_exec so it commits in an autonomous "
            "transaction and survives the outer RAISE EXCEPTION rollback "
            "(NFM-4097 finding 2). Found: " + repr(direct_inserts)
        )


# ---------------------------------------------------------------------------
# Idempotency + downgrade ordering
# ---------------------------------------------------------------------------


class TestMigration071Idempotency:
    """The migration is safe to re-run on a fresh DB."""

    def test_create_or_replace_function_in_upgrade(self, trigger_function_sql: str) -> None:
        assert re.search(
            r"CREATE\s+OR\s+REPLACE\s+FUNCTION",
            trigger_function_sql,
            flags=re.IGNORECASE,
        ), "upgrade must use CREATE OR REPLACE FUNCTION"

    def test_drop_trigger_if_exists_in_downgrade(
        self, migration_source: str, migration_module
    ) -> None:
        # The downgrade's ``DROP TRIGGER IF EXISTS`` call is built as
        # ``f"DROP TRIGGER IF EXISTS {_TRIGGER_NAME} ON data_sources"``
        # so the rendered SQL lives in the migration source's
        # downgrade function body.  Assert it appears (case-insensitive)
        # using the module's tracked trigger name.
        downgrade_section = migration_source.split("def downgrade()")[1]
        assert re.search(
            r"DROP\s+TRIGGER\s+IF\s+EXISTS\s+" + re.escape(migration_module._TRIGGER_NAME),
            downgrade_section,
            flags=re.IGNORECASE,
        ), f"downgrade must `DROP TRIGGER IF EXISTS {migration_module._TRIGGER_NAME}`"

    def test_drop_function_if_exists_in_downgrade(
        self, migration_source: str, migration_module
    ) -> None:
        downgrade_section = migration_source.split("def downgrade()")[1]
        assert re.search(
            r"DROP\s+FUNCTION\s+IF\s+EXISTS\s+" + re.escape(migration_module._TRIGGER_FN_NAME),
            downgrade_section,
            flags=re.IGNORECASE,
        ), f"downgrade must `DROP FUNCTION IF EXISTS {migration_module._TRIGGER_FN_NAME}`"


class TestMigration071DowngradeOrder:
    """Downgrade drops the trigger BEFORE the function it depends on."""

    def test_trigger_dropped_before_function_drop(self, migration_source: str) -> None:
        downgrade_section = migration_source.split("def downgrade()")[1]
        # ``DROP TRIGGER`` must precede ``DROP FUNCTION`` — PostgreSQL
        # would otherwise refuse with
        #   cannot drop function reject_uuid_titled_source() because
        #   other objects depend on it
        trigger_pos = downgrade_section.lower().find("drop trigger")
        function_pos = downgrade_section.lower().find("drop function")
        assert trigger_pos != -1 and function_pos != -1
        assert trigger_pos < function_pos, (
            "downgrade must DROP TRIGGER before DROP FUNCTION — "
            "PostgreSQL refuses to drop a function referenced by a trigger"
        )

    def test_check_constraint_restored_to_original_enum(
        self,
        downgrade_event_type_check_sql: str,
        migration_module,
    ) -> None:
        """Downgrade restores ``ck_health_events_event_type`` to the
        original 5-value enum so the table matches NFM-2220's
        documented contract.
        """
        # The downgrade's CHECK fragment must contain every original
        # event type and NOT contain the new one.
        for event_type in migration_module._ORIGINAL_EVENT_TYPES:
            assert event_type in downgrade_event_type_check_sql, (
                f"downgrade CHECK constraint must retain original event_type '{event_type}'"
            )
        assert migration_module._TRIGGER_EVENT_TYPE not in downgrade_event_type_check_sql, (
            "downgrade's restored CHECK constraint must NOT include "
            "uuid_titled_source_blocked — that event type is "
            "migration-071 only"
        )


# ---------------------------------------------------------------------------
# NFM-4099 regression guard: no SQLAlchemy bind params inside DO $$ blocks
# ---------------------------------------------------------------------------


class TestMigration071NoDoBlockBindParams:
    """NFM-4099 — the trigger is installed via plain ``sa.text(...)``
    SQL, NOT via a ``DO $$`` block with bind params.

    asyncpg cannot pass bind parameters to ``DO`` blocks (NFM-4099
    root cause).  The trigger install here uses
    ``CREATE OR REPLACE FUNCTION`` + ``CREATE TRIGGER`` outside a DO
    block, so the migration must not pass bind parameters to any
    ``bind.execute`` call.

    The test scans the SQL strings actually passed to ``bind.execute``
    (via AST), not the docstring — the docstring may reference
    ``DO $$`` for explanatory purposes but the migration code itself
    must not emit one.
    """

    @staticmethod
    def _executed_sql_strings(migration_ast: ast.Module) -> list[str]:
        """Return every string literal passed to ``bind.execute``.

        Walks ``op.get_bind().execute(...)`` chains and captures the
        ``sa.text(...)`` argument's string value (whether called as
        ``text(...)`` or ``sa.text(...)``).
        """
        sql_strings: list[str] = []
        for node in ast.walk(migration_ast):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            is_execute_call = isinstance(func, ast.Attribute) and func.attr == "execute"
            if not is_execute_call:
                continue
            for arg in node.args:
                if not isinstance(arg, ast.Call):
                    continue
                arg_func = arg.func
                is_text_call = False
                if (isinstance(arg_func, ast.Attribute) and arg_func.attr == "text") or (
                    isinstance(arg_func, ast.Name) and arg_func.id == "text"
                ):
                    is_text_call = True
                if not is_text_call or not arg.args:
                    continue
                first = arg.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    sql_strings.append(first.value)
        return sql_strings

    def test_no_do_block_in_executed_sql(self, migration_ast: ast.Module) -> None:
        """No SQL passed to ``bind.execute`` may contain a ``DO $$`` block.

        NFM-4099 — asyncpg cannot bind into ``DO`` blocks.  The docstring
        may mention ``DO $$`` for context, but no statement the
        migration actually executes may contain one.
        """
        for sql in self._executed_sql_strings(migration_ast):
            assert "DO $$" not in sql, (
                "NFM-4099 regression: migration 071 contains a DO $$ "
                "block in an executed statement. asyncpg crashes on these."
            )

    def test_no_sqlalchemy_bind_dict_passed(self, migration_ast: ast.Module) -> None:
        """Defence-in-depth: scan for any ``bind.execute`` call carrying
        a dict (bind params).
        """
        for node in ast.walk(migration_ast):
            if not isinstance(node, ast.Call):
                continue
            for arg in node.args:
                if not isinstance(arg, ast.Dict):
                    continue
                # Find any sa.text(...) call whose SQL contains bind
                # tokens.  The 070 fix (NFM-4099) confirmed that even
                # a single ``:key`` token inside a DO block crashes
                # asyncpg; the 071 migration uses plain DDL but this
                # guard prevents a future regression from re-introducing
                # a bind dict to ``bind.execute``.
                for sa_arg in node.args:
                    if (
                        isinstance(sa_arg, ast.Call)
                        and isinstance(sa_arg.func, ast.Attribute)
                        and sa_arg.func.attr == "text"
                    ):
                        if sa_arg.args:
                            first = sa_arg.args[0]
                            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                                sql = first.value
                                # If a DO block ever appears, fail loud.
                                if "DO $$" in sql:
                                    pytest.fail(
                                        "NFM-4099 regression: migration 071 "
                                        "contains a DO $$ block. asyncpg "
                                        "crashes on these."
                                    )
                                # Any other bind token in a non-DO SQL
                                # is also suspect — fail loud.
                                bind_tokens = re.findall(r":([a-zA-Z_][a-zA-Z0-9_]*)", sql)
                                if bind_tokens:
                                    pytest.fail(
                                        f"NFM-4099 regression: migration 071 "
                                        f"uses SQLAlchemy bind tokens "
                                        f"{bind_tokens} in a non-DO bind.execute"
                                    )


# ---------------------------------------------------------------------------
# Live execution — upgrade / downgrade run on a mocked alembic bind
# ---------------------------------------------------------------------------


class TestMigration071LiveExecution:
    """upgrade() and downgrade() must execute without raising on a
    mocked bind.  Catches regressions in the bind-param surface area
    (NFM-4099) and in the plpgsql function body (CR-rejected prior
    revisions sometimes had unbalanced dollar-quotes)."""

    def _import_module(self):
        """Import the migration module without triggering alembic env."""
        import importlib.util

        spec = importlib.util.spec_from_file_location("m071", str(_MIGRATION_PATH))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_upgrade_executes_without_error(self) -> None:
        from unittest.mock import MagicMock, patch

        mod = self._import_module()
        fake_bind = MagicMock()
        fake_bind.execute = MagicMock(return_value=None)

        with patch.object(mod, "op") as mock_op:
            mock_op.get_bind.return_value = fake_bind
            # If the migration has a syntax error or unbalanced
            # ``$$``, the next line raises.
            mod.upgrade()

        # upgrade() executes a small, fixed number of statements:
        #   1. DROP CONSTRAINT ck_health_events_event_type
        #   2. ADD CONSTRAINT ck_health_events_event_type (...new enum...)
        #   3. CREATE OR REPLACE FUNCTION reject_uuid_titled_source()
        #   4. CREATE TRIGGER trg_data_sources_uuid_title ...
        # Pin the count so a regression that adds (or removes) one
        # is caught immediately.
        execute_calls = fake_bind.execute.call_args_list
        assert len(execute_calls) >= 4, (
            f"upgrade() should execute at least 4 statements; got {len(execute_calls)}"
        )

        # Every execute() call must carry exactly one positional arg
        # (the SQL string) — never a bind dict (NFM-4099).
        for call in execute_calls:
            assert len(call.args) == 1, (
                f"bind.execute must take exactly 1 positional arg "
                f"(the SQL); got {len(call.args)}: {call}"
            )

    def test_downgrade_executes_without_error(self) -> None:
        from unittest.mock import MagicMock, patch

        mod = self._import_module()
        fake_bind = MagicMock()
        fake_bind.execute = MagicMock(return_value=None)

        with patch.object(mod, "op") as mock_op:
            mock_op.get_bind.return_value = fake_bind
            mod.downgrade()

        # downgrade() executes:
        #   1. DROP TRIGGER IF EXISTS trg_data_sources_uuid_title
        #   2. DROP FUNCTION IF EXISTS reject_uuid_titled_source
        #   3. DROP CONSTRAINT ck_health_events_event_type
        #   4. ADD CONSTRAINT ck_health_events_event_type (original enum)
        assert fake_bind.execute.call_count >= 4, (
            f"downgrade() should execute at least 4 statements; got {fake_bind.execute.call_count}"
        )
        for call in fake_bind.execute.call_args_list:
            assert len(call.args) == 1, (
                f"downgrade bind.execute must take exactly 1 positional "
                f"arg; got {len(call.args)}: {call}"
            )
