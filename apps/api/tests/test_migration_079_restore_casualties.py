"""Runtime regression tests for migration 079 — NFM-4191.

Validates the restore of the 31 ``property_measurements`` rows (plus
their 30 datasets / collapsed-class sources) that migration 070's
ON DELETE CASCADE dataset deletions took away.

Runs against a real Postgres mirror of prod (the throwaway clone
pattern from ``test_migration_075_restore_placeholder.py``): each test
opens a transaction, runs ``upgrade()``/``downgrade()`` via a bound
``Operations`` context, asserts, and rolls back — leaving the clone
unchanged.

CI safety: on a fresh Postgres service (``NFM_TEST_DATABASE_URL`` points
at an empty database in CI) the 070 backup tables do not exist, so the
fixture **skips** rather than fails — the migration's own fresh-DB guard
makes it a no-op there, and these tests assert data-level behaviour that
only exists on a prod-shaped database.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

pytest.importorskip(
    "psycopg2",
    reason="psycopg2 required for runtime migration tests against Postgres",
)

from sqlalchemy import create_engine, text
from sqlalchemy.exc import StatementError

_MIGRATION_PATH = Path("migrations/versions/079_restore_070_measurement_casualties.py").resolve()

_TEST_DB_URL = os.environ.get(
    "NFM_TEST_DATABASE_URL",
    "postgresql://nfm:nfm@localhost:55439/nfm_db",
)

# NFM-4191 acceptance criterion: the UO2 material's properties endpoint
# must return >= 7 rows again (its 7 backup measurements must be back).
UO2_MATERIAL_ID = "068dc946-9dd9-4a8d-bad0-9f24359b8b87"

_UUID_TITLE_REGEX = "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"

# Mirror of the migration's deterministic class definition.
_CLASS_DATASETS_SQL = f"""
SELECT bk.id
FROM datasets_backup_070 bk
JOIN data_sources_backup_070 s ON s.id = bk.source_id
WHERE bk.id IN (SELECT DISTINCT pm.dataset_id
                FROM property_measurements_backup_070 pm)
  AND (s.title ~ '{_UUID_TITLE_REGEX}'
       OR s.title IN ('Unknown Source', 'Unattributed source (no DOI)'))
"""


def _load_migration_module():
    """Import migration 079 by file path (digit-prefixed module name)."""
    spec = importlib.util.spec_from_file_location("_nfm4191_migration_under_test", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None, (
        f"Could not load migration module from {_MIGRATION_PATH}"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def clone_db():
    """Yield a transactional connection to the prod-mirror clone.

    Skips (does not fail) when the target lacks the 070 backup tables —
    e.g. the fresh CI Postgres service — because the assertions only
    make sense against prod-shaped data.
    """
    engine = create_engine(_TEST_DB_URL, future=True)
    conn = engine.connect()
    trans = conn.begin()
    try:
        row = conn.execute(
            text(
                """
                SELECT to_regclass('public.property_measurements_backup_070') IS NOT NULL
                  AND to_regclass('public.datasets_backup_070') IS NOT NULL
                  AND to_regclass('public.data_sources_backup_070') IS NOT NULL
                AS backup_tables_exist
                """
            )
        ).scalar()
        if not row:
            pytest.skip(
                "070 backup tables absent on NFM_TEST_DATABASE_URL target "
                "(fresh CI database) — migration 079 is a no-op there"
            )
        yield conn
    finally:
        trans.rollback()
        conn.close()
        engine.dispose()


def _run_migration_against_connection(conn, migration_module, func_name: str):
    """Run ``upgrade()``/``downgrade()`` bound to a SQLAlchemy connection."""
    from alembic.operations import Operations
    from alembic.runtime.migration import MigrationContext

    ctx = MigrationContext.configure(conn)
    with Operations.context(ctx):
        getattr(migration_module, func_name)()


def _missing_class_counts(conn):
    """(sources, datasets, measurements) still absent from live tables."""
    return (
        conn.execute(
            text(
                f"""
                SELECT count(*) FROM data_sources_backup_070 b
                WHERE b.id IN (
                    SELECT DISTINCT bk.source_id FROM datasets_backup_070 bk
                    JOIN data_sources_backup_070 s ON s.id = bk.source_id
                    WHERE bk.id IN (SELECT DISTINCT pm.dataset_id
                                    FROM property_measurements_backup_070 pm)
                      AND (s.title ~ '{_UUID_TITLE_REGEX}'
                           OR s.title IN ('Unknown Source', 'Unattributed source (no DOI)'))
                )
                  AND NOT EXISTS (SELECT 1 FROM data_sources cur WHERE cur.id = b.id)
                """
            )
        ).scalar(),
        conn.execute(
            text(
                f"""
                SELECT count(*) FROM datasets_backup_070 bk
                WHERE bk.id IN ({_CLASS_DATASETS_SQL})
                  AND NOT EXISTS (SELECT 1 FROM datasets d WHERE d.id = bk.id)
                """
            )
        ).scalar(),
        conn.execute(
            text(
                f"""
                SELECT count(*) FROM property_measurements_backup_070 b
                WHERE b.dataset_id IN ({_CLASS_DATASETS_SQL})
                  AND NOT EXISTS (
                      SELECT 1 FROM property_measurements pm WHERE pm.id = b.id
                  )
                """
            )
        ).scalar(),
    )


class TestMigration079RestoreCasualties:
    def test_upgrade_restores_every_missing_class_row(self, clone_db):
        """Every still-missing class source/dataset/measurement comes back."""
        migration = _load_migration_module()

        src_missing, ds_missing, meas_missing = _missing_class_counts(clone_db)
        assert (src_missing, ds_missing, meas_missing) > (0, 0, 0), (
            "prod-mirror clone should still be missing the 070 casualties"
        )

        meas_pre = clone_db.execute(text("SELECT count(*) FROM property_measurements")).scalar()

        _run_migration_against_connection(clone_db, migration, "upgrade")

        assert _missing_class_counts(clone_db) == (0, 0, 0), (
            "upgrade must restore every missing class row"
        )
        meas_post = clone_db.execute(text("SELECT count(*) FROM property_measurements")).scalar()
        assert meas_post - meas_pre == meas_missing, (
            f"expected +{meas_missing} measurements, got +{meas_post - meas_pre}"
        )

    def test_upgrade_restores_uo2_measurements(self, clone_db):
        """NFM-4191 AC: UO2 gets its 7 property measurements back."""
        migration = _load_migration_module()

        expected = clone_db.execute(
            text(
                f"""
                SELECT count(*) FROM property_measurements_backup_070 pm
                JOIN datasets_backup_070 d ON d.id = pm.dataset_id
                WHERE d.material_id = '{UO2_MATERIAL_ID}'::uuid
                """
            )
        ).scalar()
        assert expected >= 7, f"backup should hold >=7 UO2 rows, found {expected}"

        before = clone_db.execute(
            text(
                f"""
                SELECT count(*) FROM property_measurements pm
                JOIN datasets d ON d.id = pm.dataset_id
                WHERE d.material_id = '{UO2_MATERIAL_ID}'::uuid
                """
            )
        ).scalar()
        assert before == 0, "precondition: UO2 lost all rows to migration 070"

        _run_migration_against_connection(clone_db, migration, "upgrade")

        after = clone_db.execute(
            text(
                f"""
                SELECT count(*) FROM property_measurements pm
                JOIN datasets d ON d.id = pm.dataset_id
                WHERE d.material_id = '{UO2_MATERIAL_ID}'::uuid
                """
            )
        ).scalar()
        assert after == expected, f"expected {expected} UO2 measurements after upgrade, got {after}"

    def test_upgrade_is_idempotent(self, clone_db):
        """A second upgrade() invocation is a no-op."""
        migration = _load_migration_module()

        _run_migration_against_connection(clone_db, migration, "upgrade")
        counts_first = tuple(
            clone_db.execute(
                text(
                    "SELECT (SELECT count(*) FROM data_sources), "
                    "(SELECT count(*) FROM datasets), "
                    "(SELECT count(*) FROM property_measurements)"
                )
            ).one()
        )

        _run_migration_against_connection(clone_db, migration, "upgrade")
        counts_second = tuple(
            clone_db.execute(
                text(
                    "SELECT (SELECT count(*) FROM data_sources), "
                    "(SELECT count(*) FROM datasets), "
                    "(SELECT count(*) FROM property_measurements)"
                )
            ).one()
        )

        assert counts_second == counts_first, (
            f"idempotent upgrade changed counts: {counts_first} -> {counts_second}"
        )

    def test_upgrade_reenables_uuid_title_guard(self, clone_db):
        """The 071 guard trigger must be active again after upgrade().

        Also proves the UUID-titled source restore itself succeeded
        (those inserts only pass because the migration disabled the
        trigger inside its own transaction).
        """
        migration = _load_migration_module()

        uuid_missing_before = clone_db.execute(
            text(
                f"""
                SELECT count(*) FROM data_sources_backup_070 b
                WHERE b.title ~ '{_UUID_TITLE_REGEX}'
                  AND b.id IN (
                      SELECT DISTINCT bk.source_id FROM datasets_backup_070 bk
                      JOIN data_sources_backup_070 s ON s.id = bk.source_id
                      WHERE bk.id IN (SELECT DISTINCT pm.dataset_id
                                      FROM property_measurements_backup_070 pm)
                        AND (s.title ~ '{_UUID_TITLE_REGEX}'
                             OR s.title IN ('Unknown Source', 'Unattributed source (no DOI)'))
                  )
                  AND NOT EXISTS (SELECT 1 FROM data_sources cur WHERE cur.id = b.id)
                """
            )
        ).scalar()

        _run_migration_against_connection(clone_db, migration, "upgrade")

        uuid_present_after = clone_db.execute(
            text(
                f"""
                SELECT count(*) FROM data_sources b
                WHERE b.title ~ '{_UUID_TITLE_REGEX}'
                  AND b.id IN (
                      SELECT DISTINCT bk.source_id FROM datasets_backup_070 bk
                      JOIN data_sources_backup_070 s ON s.id = bk.source_id
                      WHERE bk.id IN (SELECT DISTINCT pm.dataset_id
                                      FROM property_measurements_backup_070 pm)
                        AND (s.title ~ '{_UUID_TITLE_REGEX}'
                             OR s.title IN ('Unknown Source', 'Unattributed source (no DOI)'))
                  )
                """
            )
        ).scalar()
        assert uuid_present_after >= uuid_missing_before, (
            "UUID-titled class sources should be restored by upgrade()"
        )

        # Probing the guard last: the failed INSERT aborts the
        # transaction, which the fixture rollback then discards.
        with pytest.raises(StatementError, match="uuid_titled_source_blocked"):
            clone_db.execute(
                text(
                    """
                    INSERT INTO data_sources (id, title, source_type, parse_status)
                    VALUES (gen_random_uuid(), '123e4567-e89b-12d3-a456-426614174000',
                            'journal', 'pending')
                    """
                )
            )

    def test_downgrade_removes_entire_class(self, clone_db):
        """downgrade() returns the DB to the 070 post-collapse class state.

        Class-wide semantics (mirroring 075's downgrade): every class
        dataset and its measurements are removed; class sources are
        removed only when no surviving dataset references them.
        """
        migration = _load_migration_module()

        class_datasets_pre = clone_db.execute(
            text(
                f"""
                SELECT count(*) FROM datasets d
                WHERE d.id IN ({_CLASS_DATASETS_SQL})
                """
            )
        ).scalar()
        datasets_pre = clone_db.execute(text("SELECT count(*) FROM datasets")).scalar()
        meas_pre = clone_db.execute(text("SELECT count(*) FROM property_measurements")).scalar()
        class_meas_pre = clone_db.execute(
            text(
                f"""
                SELECT count(*) FROM property_measurements pm
                WHERE pm.dataset_id IN ({_CLASS_DATASETS_SQL})
                """
            )
        ).scalar()

        _run_migration_against_connection(clone_db, migration, "upgrade")
        _run_migration_against_connection(clone_db, migration, "downgrade")

        class_meas_post = clone_db.execute(
            text(
                f"""
                SELECT count(*) FROM property_measurements pm
                WHERE pm.dataset_id IN ({_CLASS_DATASETS_SQL})
                """
            )
        ).scalar()
        class_datasets_post = clone_db.execute(
            text(
                f"""
                SELECT count(*) FROM datasets d
                WHERE d.id IN ({_CLASS_DATASETS_SQL})
                """
            )
        ).scalar()
        datasets_post = clone_db.execute(text("SELECT count(*) FROM datasets")).scalar()
        meas_post = clone_db.execute(text("SELECT count(*) FROM property_measurements")).scalar()

        assert class_meas_post == 0
        assert class_datasets_post == 0
        assert datasets_post == datasets_pre - class_datasets_pre
        assert meas_post == meas_pre - class_meas_pre

    def test_no_out_of_band_writes(self, clone_db):
        """upgrade() performs no UPDATE/DELETE on the three touched tables."""
        migration = _load_migration_module()

        for table in ("data_sources", "datasets", "property_measurements"):
            clone_db.execute(
                text(
                    f"""
                    CREATE OR REPLACE FUNCTION _nfm4191_guard_{table}()
                    RETURNS trigger AS $$
                    BEGIN
                        RAISE EXCEPTION
                            'NFM-4191 guard: unexpected % on {table}', TG_OP
                            USING ERRCODE = 'check_violation';
                    END;
                    $$ LANGUAGE plpgsql
                    """
                )
            )
            clone_db.execute(text(f"DROP TRIGGER IF EXISTS _nfm4191_guard ON {table}"))
            clone_db.execute(
                text(
                    f"""
                    CREATE TRIGGER _nfm4191_guard
                    BEFORE UPDATE OR DELETE ON {table}
                    FOR EACH STATEMENT
                    EXECUTE FUNCTION _nfm4191_guard_{table}()
                    """
                )
            )

        try:
            _run_migration_against_connection(clone_db, migration, "upgrade")
        finally:
            for table in ("data_sources", "datasets", "property_measurements"):
                clone_db.execute(text(f"DROP TRIGGER IF EXISTS _nfm4191_guard ON {table}"))
                clone_db.execute(text(f"DROP FUNCTION IF EXISTS _nfm4191_guard_{table}()"))
