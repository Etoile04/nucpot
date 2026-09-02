"""Runtime regression tests for migration 075 — NFM-4139.

Validates the recast Option B (NFM-4135 verdict) that re-inserts the
18 placeholder ``data_sources`` rows + 10 ``datasets`` rows from
``*_backup_070``.

Why we drive this against a real Postgres clone (not SQLite)
------------------------------------------------------------

Migration 075 executes raw SQL via ``op.get_bind()`` and relies on
Postgres-specific extensions (``jsonb``, ``gen_random_uuid()``,
``uuid`` type). SQLite would either crash or require mocking the
extension layer. Instead, the test connects to the throwaway prod
clone used for AC-2 dry-run (see ``scripts/dryrun_restore_075.py``).

Each test starts a transaction, runs ``upgrade()``, asserts the
expected deltas + UUID pairs, then rolls back — leaving the DB
unchanged.

Test environment
----------------

``NFM_TEST_DATABASE_URL`` env var must point at a Postgres database
that has ``data_sources_backup_070`` and ``datasets_backup_070``
populated with the prod backup state.  The throwaway
``nucpot-prod-clone-nfm4139`` container created by the LE dry-run
flow satisfies this.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import uuid
from pathlib import Path

import pytest

pytest.importorskip(
    "psycopg2",
    reason="psycopg2 required for runtime migration tests against Postgres",
)

from sqlalchemy import create_engine, text

_MIGRATION_PATH = Path(
    "migrations/versions/075_restore_placeholder_sources_datasets.py"
).resolve()

_TEST_DB_URL = os.environ.get(
    "NFM_TEST_DATABASE_URL",
    "postgresql://nfm:nfm@localhost:55432/nfm_db_clone",
)

# NFM-4135 verdict — 10 dataset_ids that must be restored with their
# original source_id (the AC-3 evidence table).
EXPECTED_UUID_PAIRS: list[tuple[str, str]] = [
    ("00a9e563-a785-4f12-bd04-fa6df1df7000", "ed4d0973-63b5-46db-977b-953dc34952dc"),
    ("94a20c7e-e146-42aa-8128-e2cf33daf40d", "c7209fa5-2587-46d3-b3a0-e0bd660b21c2"),
    ("b1f71371-eec7-403a-a31b-72e6f4b1ed7d", "99657011-e844-495e-9d56-3365950ce1eb"),
    ("4bea6c10-3306-4464-83b1-afa634925c5c", "be440b07-4f6b-4b9e-a5c7-892569f4c672"),
    ("5089265e-b610-417b-8f1d-9acfe3d5da0f", "3eadefa3-c955-4332-b020-9a2a05b106b6"),
    ("69c1ce73-2757-410a-ae70-e0f84779c6a8", "c0ad2e84-b367-42b6-98f7-14b8ebb9fab9"),
    ("943e0cdd-8b4f-4f69-9f82-27f50862ae89", "f7131de8-0b35-49ff-b6f9-96fb85cced69"),
    ("a60fa66a-10a5-45c9-9e68-00b83e306ba6", "4530e58a-c8f8-47ce-88aa-2f2fe8269c55"),
    ("af3ff114-c001-4a47-91fb-cda7ce5cbda2", "b32dc25b-ab24-42ec-a86e-e55250fa1acb"),
    ("c7e623df-51c5-45ff-9f7f-1bf19aaba13d", "730f83fc-e611-4ea4-a55b-ca3ba0717a19"),
]


def _load_migration_module():
    """Import migration 075 by file path (digit-prefixed module name)."""
    spec = importlib.util.spec_from_file_location(
        "_nfm4139_migration_under_test", _MIGRATION_PATH
    )
    assert spec is not None and spec.loader is not None, (
        f"Could not load migration module from {_MIGRATION_PATH}"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def clone_db():
    """Yield a SQLAlchemy connection to the throwaway prod clone.

    Rolls back at the end so the test is non-destructive.
    """
    engine = create_engine(_TEST_DB_URL, future=True)
    conn = engine.connect()
    trans = conn.begin()
    try:
        # Verify the test target has the preconditions (backup tables
        # populated).
        row = conn.execute(
            text(
                "SELECT count(*) FROM data_sources_backup_070 "
                "WHERE title IN ('Unknown Source', 'Unattributed source (no DOI)')"
            )
        ).scalar()
        assert row == 18, (
            f"expected 18 placeholder rows in backup, found {row}; "
            "is the throwaway prod clone correctly populated?"
        )
        yield conn
    finally:
        trans.rollback()
        conn.close()
        engine.dispose()


def _run_migration_against_connection(conn, migration_module, func_name: str):
    """Run ``migration_module.upgrade()`` or ``.downgrade()`` against a SQLAlchemy Connection.

    Uses ``MigrationContext`` + ``Operations`` to bind the migration's
    ``op`` proxy to the connection (the same pattern the alembic
    ``env.py`` uses for online mode).
    """
    from alembic.operations import Operations
    from alembic.runtime.migration import MigrationContext

    ctx = MigrationContext.configure(conn)
    with Operations.context(ctx):
        getattr(migration_module, func_name)()


class TestMigration075RestorePlaceholder:
    """Verify migration 075 implements the recast Option B correctly."""

    def test_upgrade_inserts_18_placeholder_sources(self, clone_db):
        """AC-2: upgrade() inserts exactly +18 placeholder data_sources rows."""
        migration = _load_migration_module()

        ds_pre = clone_db.execute(text("SELECT count(*) FROM data_sources")).scalar()

        _run_migration_against_connection(clone_db, migration, "upgrade")

        ds_post = clone_db.execute(text("SELECT count(*) FROM data_sources")).scalar()
        placeholder_post = clone_db.execute(
            text(
                "SELECT count(*) FROM data_sources "
                "WHERE title IN ('Unknown Source', 'Unattributed source (no DOI)')"
            )
        ).scalar()

        assert ds_post - ds_pre == 18, (
            f"expected +18 data_sources, got +{ds_post - ds_pre}"
        )
        assert placeholder_post >= 18, (
            f"expected at least 18 placeholder rows after upgrade, got {placeholder_post}"
        )

    def test_upgrade_inserts_10_datasets_with_original_source_ids(self, clone_db):
        """AC-3: upgrade() restores each of the 10 dataset_ids with its original source_id."""
        migration = _load_migration_module()

        _run_migration_against_connection(clone_db, migration, "upgrade")

        datasets_post = clone_db.execute(
            text("SELECT count(*) FROM datasets")
        ).scalar()
        # SQLAlchemy needs explicit cast for uuid[] — use `= ANY(:ids)`
        # with a list of strings.
        rows = clone_db.execute(
            text(
                """
                SELECT d.id, d.source_id
                FROM datasets d
                JOIN data_sources ds ON ds.id = d.source_id
                WHERE ds.title IN ('Unknown Source', 'Unattributed source (no DOI)')
                  AND d.id IN (
                    SELECT bk.id FROM datasets_backup_070 bk
                    WHERE bk.source_id IN (
                      SELECT id FROM data_sources_backup_070
                      WHERE title IN ('Unknown Source', 'Unattributed source (no DOI)')
                    )
                  )
                """
            )
        ).fetchall()

        # 188 (current prod datasets) + 10 (restored) = 198.
        assert datasets_post >= 198, (
            f"expected at least 198 datasets after upgrade, got {datasets_post}"
        )
        restored_pairs = {(row[0], row[1]) for row in rows}
        for ds_id_str, src_id_str in EXPECTED_UUID_PAIRS:
            expected = (uuid.UUID(ds_id_str), uuid.UUID(src_id_str))
            assert expected in restored_pairs, (
                f"dataset {ds_id_str} did not get its original source_id {src_id_str}; "
                f"restored pairs for placeholder datasets: {restored_pairs}"
            )

    def test_upgrade_is_idempotent(self, clone_db):
        """AC-1: upgrade() is idempotent — a second invocation affects 0 rows."""
        migration = _load_migration_module()

        # First run — non-destructive verification only.
        _run_migration_against_connection(clone_db, migration, "upgrade")

        ds_after_first = clone_db.execute(
            text("SELECT count(*) FROM data_sources")
        ).scalar()
        datasets_after_first = clone_db.execute(
            text("SELECT count(*) FROM datasets")
        ).scalar()

        # Second run — must be a no-op.
        _run_migration_against_connection(clone_db, migration, "upgrade")

        ds_after_second = clone_db.execute(
            text("SELECT count(*) FROM data_sources")
        ).scalar()
        datasets_after_second = clone_db.execute(
            text("SELECT count(*) FROM datasets")
        ).scalar()

        assert ds_after_second == ds_after_first, (
            f"idempotent upgrade changed data_sources: "
            f"{ds_after_first} → {ds_after_second}"
        )
        assert datasets_after_second == datasets_after_first, (
            f"idempotent upgrade changed datasets: "
            f"{datasets_after_first} → {datasets_after_second}"
        )

    def test_downgrade_reverses_restore(self, clone_db):
        """downgrade() removes only the rows we just inserted (defensive)."""
        migration = _load_migration_module()

        # Pre-state baseline.
        ds_pre = clone_db.execute(
            text("SELECT count(*) FROM data_sources")
        ).scalar()
        datasets_pre = clone_db.execute(
            text("SELECT count(*) FROM datasets")
        ).scalar()

        _run_migration_against_connection(clone_db, migration, "upgrade")
        _run_migration_against_connection(clone_db, migration, "downgrade")

        ds_post = clone_db.execute(
            text("SELECT count(*) FROM data_sources")
        ).scalar()
        datasets_post = clone_db.execute(
            text("SELECT count(*) FROM datasets")
        ).scalar()

        # The downgrade should return to pre-state (rollback-friendly).
        assert ds_post == ds_pre, (
            f"downgrade changed data_sources from {ds_pre} to {ds_post}"
        )
        assert datasets_post == datasets_pre, (
            f"downgrade changed datasets from {datasets_pre} to {datasets_post}"
        )

    def test_no_out_of_band_writes(self, clone_db):
        """AC-4: upgrade() performs no UPDATE/DELETE on tables outside the two INSERT blocks.

        We install a Postgres guard trigger that raises on any UPDATE or
        DELETE against ``data_sources`` / ``datasets``.  ``INSERT`` is
        allowed.  If the migration attempts any out-of-band write, the
        trigger fires and the test fails immediately.
        """
        migration = _load_migration_module()

        # Install guard triggers before running the migration.
        for table in ("data_sources", "datasets"):
            clone_db.execute(
                text(
                    f"""
                    CREATE OR REPLACE FUNCTION _guard_no_modify_{table}()
                    RETURNS trigger AS $$
                    BEGIN
                        RAISE EXCEPTION
                            'NFM-4139 guard: unexpected % on {table}', TG_OP
                            USING ERRCODE = 'check_violation';
                    END;
                    $$ LANGUAGE plpgsql
                    """
                )
            )
            clone_db.execute(text(f"DROP TRIGGER IF EXISTS _guard_trigger ON {table}"))
            clone_db.execute(
                text(
                    f"""
                    CREATE TRIGGER _guard_trigger
                    BEFORE UPDATE OR DELETE ON {table}
                    FOR EACH STATEMENT
                    EXECUTE FUNCTION _guard_no_modify_{table}()
                    """
                )
            )

        try:
            # upgrade() will INSERT, which does NOT trigger the guard
            # (BEFORE UPDATE OR DELETE).
            _run_migration_against_connection(clone_db, migration, "upgrade")
        finally:
            # Always clean up the guard triggers — even on test failure.
            for table in ("data_sources", "datasets"):
                clone_db.execute(
                    text(f"DROP TRIGGER IF EXISTS _guard_trigger ON {table}")
                )
                clone_db.execute(
                    text(f"DROP FUNCTION IF EXISTS _guard_no_modify_{table}()")
                )
