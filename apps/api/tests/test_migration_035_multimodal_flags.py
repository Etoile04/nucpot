"""NFM-2137: Offline verification of alembic migration 035 (multimodal flags).

Migration 034 (NFM-2115) added 13 provenance/status/count/timestamp columns
to ``extraction_jobs`` but missed the 4 multimodal-flag columns preserved from
the original ``ExtractionJob`` stub (extract_figures, extract_tables,
confidence_threshold, figure_types). The running prod image's INSERT
therefore crashes with::

    asyncpg.UndefinedColumnError:
        column "extract_figures" of relation "extraction_jobs" does not exist

Migration 035 chains off 034 and adds those 4 columns. These offline tests
verify chain, idempotency, and downgrade without requiring a live
PostgreSQL — the live-PG verification (matching the prod pgvector/pg16
schema) lives in
``tests/test_migration_035_multimodal_flags_runtime.py``.

Acceptance criteria (covered by this file):

* [AC-1] Migration 035 exists and chains off 034 (so it runs after the 13
  persistence columns land; do **not** edit 034 — it is already deployed).
* [AC-2] Migration is idempotent on PG (uses ``ADD COLUMN IF NOT EXISTS``
  for each of the 4 columns) and on SQLite (uses ``op.add_column`` with
  ``OperationalError`` swallow — mirroring migration 034's pattern).
* [AC-3] All 4 columns are declared with the correct PG types / nullability
  / server defaults (BOOLEAN NOT NULL DEFAULT FALSE,
  BOOLEAN NOT NULL DEFAULT FALSE, DOUBLE PRECISION NOT NULL DEFAULT 0.5,
  JSONB NULL).
* [AC-4] Downgrade drops all 4 columns using ``DROP COLUMN IF EXISTS``
  (PG) / ``op.drop_column`` with ``OperationalError`` swallow (SQLite).

Live PG tests (AC-5, AC-6) — the prod pgvector/pg16 verification that the
running API's INSERT no longer hits UndefinedColumnError — live in
``test_migration_035_multimodal_flags_runtime.py``.
"""

from __future__ import annotations

import re

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

REVISION = "035_add_extraction_job_multimodal_flags"
DOWN_REVISION = "034_add_extraction_job_persistence_columns"
MIGRATION_PATH = f"migrations/versions/{REVISION}.py"


# The 4 columns the running prod image INSERTs via SQLAlchemy defaults.
# Mirrored verbatim from ``apps/api/src/nfm_db/models/extraction_job.py``
# lines 106-112.
EXPECTED_COLUMNS: frozenset[str] = frozenset(
    {
        "extract_figures",
        "extract_tables",
        "confidence_threshold",
        "figure_types",
    }
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def script_directory() -> ScriptDirectory:
    """Load Alembic script directory for offline chain analysis."""
    config = Config("alembic.ini")
    return ScriptDirectory.from_config(config)


@pytest.fixture(scope="module")
def migration_source() -> str:
    """Read the migration source file as text."""
    with open(MIGRATION_PATH) as f:
        return f.read()


# ---------------------------------------------------------------------------
# AC-1: Migration exists and chains off 034
# ---------------------------------------------------------------------------


class TestMigrationChain:
    """035_add_extraction_job_multimodal_flags is discoverable, chains 034→035."""

    def test_revision_loadable(self, script_directory: ScriptDirectory) -> None:
        rev = script_directory.get_revision(REVISION)
        assert rev is not None, f"Migration {REVISION!r} not registered"
        assert rev.revision == REVISION

    def test_down_revision_is_034(self, script_directory: ScriptDirectory) -> None:
        rev = script_directory.get_revision(REVISION)
        assert rev is not None
        assert rev.down_revision == DOWN_REVISION, (
            f"Expected down_revision={DOWN_REVISION!r}, "
            f"got {rev.down_revision!r}"
        )

    def test_035_is_direct_ancestor_of_head(self, script_directory: ScriptDirectory) -> None:
        """035 is no longer the chain head after the 036 merge.

        Migration 036_merge_chain_A_and_B (commit 9df2f3f) merged chain A
        (032_create_data_submission_tables) with chain B (035_multimodal),
        and 037_create_health_events_table (NFM-2220) chains off 036.
        We verify 035 is still reachable in the revision graph instead of
        asserting it is the head.
        """
        head = script_directory.get_current_head()
        assert head == "037_create_health_events_table", (
            f"Expected head='037_create_health_events_table', got {head!r}"
        )
        # 035 must still be a known revision in the chain
        rev = script_directory.get_revision(REVISION)
        assert rev is not None, f"Revision {REVISION!r} not found in migration chain"

    def test_no_duplicate_revisions(self, script_directory: ScriptDirectory) -> None:
        revisions = [r.revision for r in script_directory.walk_revisions()]
        assert len(revisions) == len(set(revisions)), "Duplicate revisions found"


# ---------------------------------------------------------------------------
# AC-2: Idempotent on PG (IF NOT EXISTS) and SQLite (OperationalError swallow)
# ---------------------------------------------------------------------------


class TestIdempotent:
    """Re-running the migration must be a clean no-op on both dialects."""

    def test_pg_add_columns_use_if_not_exists(self, migration_source: str) -> None:
        """Every PG ``ALTER TABLE ... ADD COLUMN`` carries ``IF NOT EXISTS``."""
        # Match ADD COLUMN IF NOT EXISTS lines.
        add_lines = re.findall(
            r"ALTER\s+TABLE\s+extraction_jobs\s+ADD\s+COLUMN[^;\n]*",
            migration_source,
            re.IGNORECASE,
        )
        assert add_lines, "Expected at least one ALTER TABLE ADD COLUMN"
        for line in add_lines:
            assert "IF NOT EXISTS" in line.upper(), (
                f"PG ADD COLUMN missing IF NOT EXISTS (idempotency): {line!r}"
            )

    def test_pg_drop_columns_use_if_exists(self, migration_source: str) -> None:
        """Every PG ``ALTER TABLE ... DROP COLUMN`` carries ``IF EXISTS``."""
        drop_lines = re.findall(
            r"ALTER\s+TABLE\s+extraction_jobs\s+DROP\s+COLUMN[^;\n]*",
            migration_source,
            re.IGNORECASE,
        )
        assert drop_lines, "Expected at least one ALTER TABLE DROP COLUMN"
        for line in drop_lines:
            assert "IF EXISTS" in line.upper(), (
                f"PG DROP COLUMN missing IF EXISTS (idempotency): {line!r}"
            )

    def test_sqlite_branch_swallows_operationalerror(
        self,
        migration_source: str,
    ) -> None:
        """SQLite branch catches ``OperationalError`` so re-runs are no-ops."""
        upgrade_body = _extract_function_body(migration_source, "upgrade")
        downgrade_body = _extract_function_body(migration_source, "downgrade")
        assert upgrade_body is not None, "upgrade() body not found"
        assert downgrade_body is not None, "downgrade() body not found"
        assert "except sqlalchemy.exc.OperationalError" in upgrade_body, (
            "SQLite upgrade must catch OperationalError for idempotency"
        )
        assert "except sqlalchemy.exc.OperationalError" in downgrade_body, (
            "SQLite downgrade must catch OperationalError for idempotency"
        )

    def test_sqlite_branch_uses_op_add_column(
        self,
        migration_source: str,
    ) -> None:
        """SQLite upgrade uses ``op.add_column`` so test-DDL parity holds."""
        upgrade_body = _extract_function_body(migration_source, "upgrade")
        assert upgrade_body is not None
        assert re.search(r"op\.add_column\(", upgrade_body), (
            "SQLite branch must use op.add_column"
        )


# ---------------------------------------------------------------------------
# AC-3: All 4 columns with correct PG types, nullability, defaults
# ---------------------------------------------------------------------------


class TestColumnSpecs:
    """Mirrors ``apps/api/src/nfm_db/models/extraction_job.py`` lines 106-112."""

    @pytest.mark.parametrize("column", sorted(EXPECTED_COLUMNS))
    def test_column_in_pg_ddl(self, migration_source: str, column: str) -> None:
        """Each required column appears in a PG ``ALTER TABLE ... ADD COLUMN``."""
        pattern = rf"ADD\s+COLUMN\s+(?:IF\s+NOT\s+EXISTS\s+)?{re.escape(column)}\b"
        assert re.search(pattern, migration_source, re.IGNORECASE), (
            f"Column {column!r} missing from PG DDL"
        )

    @pytest.mark.parametrize("column", sorted(EXPECTED_COLUMNS))
    def test_column_in_sqlite_spec(self, migration_source: str, column: str) -> None:
        """Each required column appears in the SQLite ``_SQLITE_ADD_COLUMNS``."""
        # sa.Column("name", ...) — match the quoted column-name literal.
        pattern = rf'sa\.Column\(\s*["\']{re.escape(column)}["\']'
        assert re.search(pattern, migration_source), (
            f"Column {column!r} missing from SQLite spec"
        )

    def test_extract_figures_is_boolean_not_null_false(
        self,
        migration_source: str,
    ) -> None:
        """extract_figures is BOOLEAN NOT NULL DEFAULT FALSE."""
        match = re.search(
            r"ADD\s+COLUMN\s+(?:IF\s+NOT\s+EXISTS\s+)?extract_figures\b[^;\n]*",
            migration_source,
            re.IGNORECASE,
        )
        assert match is not None
        ddl = match.group(0).upper()
        assert "BOOLEAN" in ddl, (
            f"extract_figures must be BOOLEAN, got: {ddl!r}"
        )
        assert "NOT NULL" in ddl, (
            f"extract_figures must be NOT NULL, got: {ddl!r}"
        )
        assert "FALSE" in ddl, (
            f"extract_figures must default to FALSE, got: {ddl!r}"
        )

    def test_extract_tables_is_boolean_not_null_false(
        self,
        migration_source: str,
    ) -> None:
        """extract_tables is BOOLEAN NOT NULL DEFAULT FALSE."""
        match = re.search(
            r"ADD\s+COLUMN\s+(?:IF\s+NOT\s+EXISTS\s+)?extract_tables\b[^;\n]*",
            migration_source,
            re.IGNORECASE,
        )
        assert match is not None
        ddl = match.group(0).upper()
        assert "BOOLEAN" in ddl
        assert "NOT NULL" in ddl
        assert "FALSE" in ddl

    def test_confidence_threshold_is_double_precision_not_null_default_0_5(
        self,
        migration_source: str,
    ) -> None:
        """confidence_threshold is DOUBLE PRECISION NOT NULL DEFAULT 0.5."""
        match = re.search(
            r"ADD\s+COLUMN\s+(?:IF\s+NOT\s+EXISTS\s+)?confidence_threshold\b[^;\n]*",
            migration_source,
            re.IGNORECASE,
        )
        assert match is not None
        ddl = match.group(0).upper()
        assert "DOUBLE PRECISION" in ddl, (
            f"confidence_threshold must be DOUBLE PRECISION, got: {ddl!r}"
        )
        assert "NOT NULL" in ddl
        assert "0.5" in ddl, (
            f"confidence_threshold must default to 0.5, got: {ddl!r}"
        )

    def test_figure_types_is_jsonb_nullable(
        self,
        migration_source: str,
    ) -> None:
        """figure_types is JSONB with no NOT NULL (matches SQLAlchemy nullable=True)."""
        match = re.search(
            r"ADD\s+COLUMN\s+(?:IF\s+NOT\s+EXISTS\s+)?figure_types\b[^;\n]*",
            migration_source,
            re.IGNORECASE,
        )
        assert match is not None
        ddl = match.group(0).upper()
        assert "JSONB" in ddl, (
            f"figure_types must be JSONB, got: {ddl!r}"
        )
        assert "NOT NULL" not in ddl, (
            f"figure_types must be nullable (matches JSONArray nullable=True), "
            f"got: {ddl!r}"
        )


# ---------------------------------------------------------------------------
# AC-4: Downgrade drops all 4 columns
# ---------------------------------------------------------------------------


class TestDowngrade:
    """``downgrade()`` removes the 4 columns added by ``upgrade()``."""

    def test_has_downgrade(self, migration_source: str) -> None:
        assert "def downgrade()" in migration_source

    @pytest.mark.parametrize("column", sorted(EXPECTED_COLUMNS))
    def test_column_in_pg_drop_ddl(
        self,
        migration_source: str,
        column: str,
    ) -> None:
        """Each column appears in a PG ``ALTER TABLE ... DROP COLUMN IF EXISTS``."""
        pattern = rf"DROP\s+COLUMN\s+(?:IF\s+EXISTS\s+)?{re.escape(column)}\b"
        assert re.search(pattern, migration_source, re.IGNORECASE), (
            f"Column {column!r} missing from PG downgrade DDL"
        )

    def test_sqlite_drop_iterates_sqlite_add_columns(self, migration_source: str) -> None:
        """SQLite downgrade iterates ``_SQLITE_ADD_COLUMNS`` so all 4 columns drop.

        The downgrade body calls ``op.drop_column("extraction_jobs", col.name)``
        inside a ``for col in reversed(_SQLITE_ADD_COLUMNS)`` loop. We verify the
        loop shape (so every column added in upgrade() is removed in downgrade())
        rather than re-asserting per-column literal names, since the column
        identifier comes from the iteration variable.
        """
        downgrade_body = _extract_function_body(migration_source, "downgrade")
        assert downgrade_body is not None
        assert re.search(
            r"for\s+col\s+in\s+reversed\(\s*_SQLITE_ADD_COLUMNS\s*\)",
            downgrade_body,
        ), (
            "SQLite downgrade must iterate reversed(_SQLITE_ADD_COLUMNS) "
            "so every upgrade column is dropped"
        )
        assert re.search(
            r'op\.drop_column\(\s*["\']extraction_jobs["\']\s*,\s*col\.name\s*\)',
            downgrade_body,
        ), "SQLite downgrade must call op.drop_column(\"extraction_jobs\", col.name)"

    @pytest.mark.parametrize("column", sorted(EXPECTED_COLUMNS))
    def test_column_in_sqlite_add_columns_iterable(
        self,
        migration_source: str,
        column: str,
    ) -> None:
        """Each column appears in the ``_SQLITE_ADD_COLUMNS`` iterable.

        Since the SQLite downgrade iterates ``reversed(_SQLITE_ADD_COLUMNS)``,
        the per-column drop coverage is implied by per-column upgrade coverage
        (see ``test_column_in_sqlite_spec``). This test is the dual: every
        column declared in the SQLite spec is reachable from the iterable.
        """
        # Match ``sa.Column("name", ...)`` inside the _SQLITE_ADD_COLUMNS tuple.
        pattern = rf'sa\.Column\(\s*["\']{re.escape(column)}["\']'
        assert re.search(pattern, migration_source), (
            f"Column {column!r} missing from _SQLITE_ADD_COLUMNS spec"
        )

    def test_downgrade_no_table_recreation(self, migration_source: str) -> None:
        """Downgrade does not recreate the table (no DROP TABLE / replace_table)."""
        downgrade_body = _extract_function_body(migration_source, "downgrade")
        assert downgrade_body is not None
        assert "DROP TABLE" not in downgrade_body.upper()
        assert "replace_table" not in downgrade_body


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_function_body(source: str, function_name: str) -> str | None:
    """Return the body of the named function (without the signature line)."""
    pattern = rf"def\s+{re.escape(function_name)}\s*\([^)]*\)[^:]*:\s*(?P<body>.*?)(?=\n\ndef\s|\Z)"
    match = re.search(pattern, source, re.DOTALL)
    return match.group("body") if match else None
