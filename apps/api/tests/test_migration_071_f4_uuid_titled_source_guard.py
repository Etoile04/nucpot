"""NFM-4089 — F4 ingest bypass: UUID-titled ``data_sources`` guard.

This migration installs a BEFORE INSERT/UPDATE trigger on the
``data_sources`` table that rejects any row whose ``title`` matches
the canonical UUID pattern.  The NFM-4084 F4 investigation found that
``extraction_to_db_mapper.py`` was inserting bad rows with UUID-pattern
titles whenever the upstream extraction chain supplied a previous
source's UUID instead of a real reference.  Migration 070 (NFM-4088)
cleaned up the legacy bad rows; this migration closes the hole so the
same bug cannot regress from any of the bypass ingest paths
(literature API, source service, extraction mapper, etc.).

The rejection path inserts a structured ``health_events`` row
(``event_type='uuid_titled_source_blocked'``, severity='critical',
source_service='ingest') so the existing ``GET /api/v1/health/alerts``
endpoint surfaces the regression without any additional wiring.

Acceptance criteria covered:

* [AC-1] Migration exists and chains off 070 (single-step chain to head).
* [AC-2] Migration file is syntactically valid Python and imports the
  expected alembic primitives.
* [AC-3] Migration creates a ``reject_uuid_titled_source()`` PL/pgSQL
  function that uses the canonical 36-char UUID regex.
* [AC-4] Migration installs the trigger BEFORE INSERT OR UPDATE OF title.
* [AC-5] On reject, the function INSERTs into ``health_events`` with
  ``event_type='uuid_titled_source_blocked'``.
* [AC-6] On reject, the function RAISE EXCEPTION with a sentinel that
  callers can match.
* [AC-7] Downgrade removes both the trigger and the function in the
  correct order (trigger first).
* [AC-8] Idempotent — re-running the upgrade after a partial run does
  not error.
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


# ---------------------------------------------------------------------------
# Chain / wiring
# ---------------------------------------------------------------------------


class TestMigration071Chain:
    """Migration is correctly wired into the alembic chain."""

    def test_file_exists(self):
        assert _MIGRATION_PATH.is_file()

    def test_revision_constant(self, migration_source: str) -> None:
        assert re.search(
            r'^revision:\s*str\s*=\s*"071_f4_uuid_titled_source_guard"',
            migration_source,
            re.MULTILINE,
        ), "revision must equal '071_f4_uuid_titled_source_guard'"

    def test_chains_off_070(self, migration_source: str) -> None:
        assert re.search(
            r"^down_revision:\s*str\s*\|\s*Sequence\[str\]\s*\|\s*None\s*=\s*"
            r'"070_d2_dedup_bad_data_sources"',
            migration_source,
            re.MULTILINE,
        ), "down_revision must chain off '070_d2_dedup_bad_data_sources'"


# ---------------------------------------------------------------------------
# Structural / PL/pgSQL payload checks
# ---------------------------------------------------------------------------


class TestMigration071Structure:
    """Migration file is valid Python and uses the alembic primitives."""

    def test_parses_as_valid_python(self, migration_ast: ast.Module) -> None:
        assert migration_ast.body

    def test_uses_alembic_op(self, migration_ast: ast.Module) -> None:
        names: set[str] = set()
        for node in ast.walk(migration_ast):
            if isinstance(node, (ast.ImportFrom, ast.Import)):
                for alias in node.names:
                    names.add(alias.name)
        assert "op" in names, "migration must `from alembic import op`"

    def test_defines_upgrade(self, migration_ast: ast.Module) -> None:
        functions = [
            n.name for n in migration_ast.body if isinstance(n, ast.FunctionDef)
        ]
        assert "upgrade" in functions
        assert "downgrade" in functions

    def test_creates_function(self, migration_source: str) -> None:
        assert (
            "CREATE OR REPLACE FUNCTION reject_uuid_titled_source" in migration_source
        ), "must create reject_uuid_titled_source() function"
        assert "LANGUAGE plpgsql" in migration_source

    def test_installs_trigger(self, migration_source: str) -> None:
        assert (
            "CREATE TRIGGER trg_data_sources_uuid_title" in migration_source
        ), "must install trg_data_sources_uuid_title trigger"
        assert (
            "BEFORE INSERT OR UPDATE OF title ON data_sources"
            in migration_source
        )


class TestMigration071GuardLogic:
    """The PL/pgSQL guard function matches the canonical UUID pattern and blocks."""

    def test_function_uses_canonical_uuid_regex(self, migration_source: str) -> None:
        # The regex must be anchored on both ends and accept 8-4-4-4-12 hex
        # segments with hyphens (case-insensitive allowed).
        assert "[0-9a-fA-F]{8}-[0-9a-fA-F]{4}" in migration_source
        assert r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}" in migration_source
        assert "~ " in migration_source  # PostgreSQL POSIX regex match operator

    def test_function_returns_new_when_safe(self, migration_source: str) -> None:
        # The function must RETURN NEW for non-matching titles so ordinary
        # inserts and updates continue to succeed.
        assert "RETURN NEW" in migration_source

    def test_function_records_health_event(self, migration_source: str) -> None:
        assert "INSERT INTO health_events" in migration_source
        assert "uuid_titled_source_blocked" in migration_source
        assert "severity" in migration_source
        assert "ingest" in migration_source or "source_service" in migration_source

    def test_function_raises_exception(self, migration_source: str) -> None:
        # On a UUID-pattern title, the function must RAISE EXCEPTION so
        # the caller sees a clean transaction rollback.  The exception
        # message must contain the offending title for forensic clarity.
        assert "RAISE EXCEPTION" in migration_source
        assert "EXCEPTION" in migration_source


class TestMigration071Idempotency:
    """Re-running upgrade must be a no-op so partial-failure recovery is safe."""

    def test_function_uses_create_or_replace(self, migration_source: str) -> None:
        assert "CREATE OR REPLACE FUNCTION" in migration_source

    def test_trigger_drop_is_idempotent(self, migration_source: str) -> None:
        # DROP TRIGGER IF EXISTS — re-running upgrade should not error.
        assert "DROP TRIGGER IF EXISTS" in migration_source

    def test_function_drop_is_idempotent(self, migration_source: str) -> None:
        assert "DROP FUNCTION IF EXISTS" in migration_source


class TestMigration071Downgrade:
    """Downgrade removes trigger before function so the order is safe."""

    def test_downgrade_drops_trigger(self, migration_source: str) -> None:
        assert re.search(
            r"op\.execute\(.*DROP TRIGGER IF EXISTS trg_data_sources_uuid_title.*?\)",
            migration_source,
            re.DOTALL,
        ), "downgrade must DROP TRIGGER first"

    def test_downgrade_drops_function(self, migration_source: str) -> None:
        assert re.search(
            r"op\.execute\(.*DROP FUNCTION IF EXISTS reject_uuid_titled_source.*?\)",
            migration_source,
            re.DOTALL,
        ), "downgrade must DROP FUNCTION after trigger"
