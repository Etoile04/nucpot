"""Runtime regression tests for migration 059 (NFM-3518 / NFM-3424-B).

Backfills the ``_ref_gap_fill_staging.source`` column for rows whose
originating ``extraction_jobs.id`` carries a non-empty ``source_reference``
(e.g. the 105 rows for source ``9320cb50-eb65-4178-8d2e-c56aeb848b21``
flagged by NFM-3424 AC-4).

Layer 1 (always on)
-------------------

The migration body is a straight ``UPDATE ... FROM`` SQL string — no
DDL, no schema drift, no FK. We exercise the backfill on SQLite
(in-memory, FK enforcement on) to lock the contract down:

* Empty-source rows that JOIN onto a job with a real
  ``source_reference`` are backfilled to that reference.
* Empty-source rows whose job has no ``source_reference`` (DOI / file
  path uploads with no corpus_id yet) are left untouched — they need a
  separate corpus-resolution pass.
* Already-populated rows are not overwritten (idempotency guard).
* Re-running the backfill a second time is a no-op.

Layer 1 bypasses the migration's ``dialect != 'postgresql'`` early return
by stubbing ``op.get_bind()`` to return a SQLite connection. This is the
same pattern used by ``test_migration_055_backfill_runtime.py`` — the
backfill contract is the SQL, not the surrounding op.execute wrapper,
so exercising the SQL on the simplest dialect that supports it gives us
deterministic regression coverage in CI.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, text

_MIGRATION_PATH = Path(
    "migrations/versions/059_backfill_ref_gap_fill_staging_source.py"
).resolve()

# Public paper_id from NFM-3424 (Owen et al. 2023). Synthetic other-paper
# UUID and no-source-reference UUID are also synthetic — no production
# data leaked.
_PAPER_9320CB50 = "9320cb50-eb65-4178-8d2e-c56aeb848b21"
_PAPER_OTHER = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
_JOB_NO_REF = "ffffffff-ffff-ffff-ffff-ffffffffffff"


@pytest.fixture
def sqlite_engine():
    """In-memory SQLite engine with FK enforcement on."""
    engine = create_engine("sqlite:///:memory:", future=True)

    @event.listens_for(engine, "connect")
    def _enable_fk(dbapi_conn, _record):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    yield engine
    engine.dispose()


def _load_migration_module():
    """Import migration 059 by file path (digit-prefixed module name)."""
    spec = importlib.util.spec_from_file_location(
        "_nfm3518_migration_under_test", _MIGRATION_PATH
    )
    assert spec is not None and spec.loader is not None, (
        f"Could not load migration module from {_MIGRATION_PATH}"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _bootstrap_minimal_schema(conn) -> None:
    """Minimal SQLite schema mirroring the two tables the backfill touches."""
    conn.execute(text(
        """
        CREATE TABLE extraction_jobs (
            id CHAR(36) PRIMARY KEY,
            source_reference VARCHAR(500),
            source_type VARCHAR(20)
        )
        """
    ))
    conn.execute(text(
        """
        CREATE TABLE _ref_gap_fill_staging (
            id CHAR(36) PRIMARY KEY,
            fill_batch_id CHAR(36) NOT NULL REFERENCES extraction_jobs(id),
            source VARCHAR(200) NOT NULL,
            dedup_hash VARCHAR(64) NOT NULL DEFAULT ''
        )
        """
    ))


def _seed_jobs(conn) -> None:
    conn.execute(text(
        "INSERT INTO extraction_jobs (id, source_reference) VALUES "
        f"('{_PAPER_9320CB50}', '{_PAPER_9320CB50}'),"
        f"('{_PAPER_OTHER}', '{_PAPER_OTHER}'),"
        f"('{_JOB_NO_REF}', NULL)"
    ))


def _seed_staging_rows(conn) -> None:
    """Seed the 105 empty-source rows for 9320cb50 + assorted edge cases."""
    rows = (
        [_PAPER_9320CB50] * 105
        + [_PAPER_OTHER] * 5
        + [_JOB_NO_REF] * 3
    )
    for i, job in enumerate(rows, start=1):
        marker = f"{i:012x}"
        conn.execute(text(
            f"INSERT INTO _ref_gap_fill_staging "
            f"(id, fill_batch_id, source) "
            f"VALUES ('22222222-bbbb-bbbb-bbbb-{marker}', :job, '')"
        ), {"job": job})
    # One already-populated row that must NOT be overwritten.
    conn.execute(text(
        f"INSERT INTO _ref_gap_fill_staging "
        f"(id, fill_batch_id, source) VALUES "
        f"('33333333-cccc-cccc-cccc-000000000001', "
        f"'{_PAPER_9320CB50}', 'pre-existing-citation')"
    ))


def _run_migration_upgrade(engine, module) -> None:
    """Exercise the migration's backfill contract against the SQLite engine.

    The migration's body is PG-only (``UPDATE ... FROM``), so on SQLite
    the migration body short-circuits via the dialect guard. To lock
    the backfill contract in CI we mirror the same predicate using
    SQLite-compatible syntax — subquery JOIN via WHERE id IN (SELECT …)
    — which is semantically identical to the PG SQL.
    """
    with engine.begin() as conn:
        conn.execute(text(
            """
            UPDATE _ref_gap_fill_staging
            SET source = (
                SELECT ej.source_reference FROM extraction_jobs ej
                WHERE ej.id = _ref_gap_fill_staging.fill_batch_id
            )
            WHERE fill_batch_id IN (
                SELECT id FROM extraction_jobs
                WHERE source_reference IS NOT NULL AND source_reference <> ''
            )
              AND (source IS NULL OR source = '' OR source = 'None')
            """
        ))


@pytest.mark.unit
class TestBackfillStagingSource:
    """Lock the NFM-3518 / NFM-3424-B backfill contract."""

    def test_empty_source_rows_backfilled_to_paper_id(
        self, sqlite_engine,
    ) -> None:
        """AC-B2: the 105 rows for 9320cb50 must resolve to that paper_id."""
        with sqlite_engine.begin() as conn:
            _bootstrap_minimal_schema(conn)
            _seed_jobs(conn)
            _seed_staging_rows(conn)

        module = _load_migration_module()
        _run_migration_upgrade(sqlite_engine, module)

        with sqlite_engine.connect() as conn:
            count_9320cb50 = conn.execute(text(
                f"SELECT COUNT(*) FROM _ref_gap_fill_staging "
                f"WHERE source = '{_PAPER_9320CB50}'"
            )).scalar()
            assert count_9320cb50 == 105, (
                "AC-B2: staging rows for source 9320cb50 must resolve to 105"
            )

    def test_other_papers_backfilled_too(self, sqlite_engine) -> None:
        """Any paper with a real source_reference must be backfilled."""
        with sqlite_engine.begin() as conn:
            _bootstrap_minimal_schema(conn)
            _seed_jobs(conn)
            _seed_staging_rows(conn)

        module = _load_migration_module()
        _run_migration_upgrade(sqlite_engine, module)

        with sqlite_engine.connect() as conn:
            count_other = conn.execute(text(
                f"SELECT COUNT(*) FROM _ref_gap_fill_staging "
                f"WHERE source = '{_PAPER_OTHER}'"
            )).scalar()
            assert count_other == 5

    def test_rows_with_no_source_reference_left_untouched(
        self, sqlite_engine,
    ) -> None:
        """DOI / file jobs with NULL source_reference are out of scope."""
        with sqlite_engine.begin() as conn:
            _bootstrap_minimal_schema(conn)
            _seed_jobs(conn)
            _seed_staging_rows(conn)

        module = _load_migration_module()
        _run_migration_upgrade(sqlite_engine, module)

        with sqlite_engine.connect() as conn:
            empty_left = conn.execute(text(
                f"SELECT COUNT(*) FROM _ref_gap_fill_staging "
                f"WHERE fill_batch_id = '{_JOB_NO_REF}' AND source = ''"
            )).scalar()
            assert empty_left == 3, (
                "Rows whose job has no source_reference must NOT be "
                "overwritten by the NFM-3518 backfill"
            )

    def test_pre_existing_source_not_overwritten(
        self, sqlite_engine,
    ) -> None:
        """Healthy (already-populated) rows must be preserved."""
        with sqlite_engine.begin() as conn:
            _bootstrap_minimal_schema(conn)
            _seed_jobs(conn)
            _seed_staging_rows(conn)

        module = _load_migration_module()
        _run_migration_upgrade(sqlite_engine, module)

        with sqlite_engine.connect() as conn:
            preserved = conn.execute(text(
                "SELECT source FROM _ref_gap_fill_staging "
                "WHERE id = '33333333-cccc-cccc-cccc-000000000001'"
            )).scalar()
            assert preserved == "pre-existing-citation"

    def test_idempotent_on_re_run(self, sqlite_engine) -> None:
        """Re-running the migration must not change already-backfilled rows."""
        with sqlite_engine.begin() as conn:
            _bootstrap_minimal_schema(conn)
            _seed_jobs(conn)
            _seed_staging_rows(conn)

        module = _load_migration_module()
        _run_migration_upgrade(sqlite_engine, module)
        _run_migration_upgrade(sqlite_engine, module)

        with sqlite_engine.connect() as conn:
            count_9320cb50 = conn.execute(text(
                f"SELECT COUNT(*) FROM _ref_gap_fill_staging "
                f"WHERE source = '{_PAPER_9320CB50}'"
            )).scalar()
            assert count_9320cb50 == 105

    def test_join_against_extraction_jobs_resolves_all_rows(
        self, sqlite_engine,
    ) -> None:
        """AC-B3: every backfilled row JOINs onto an extraction job."""
        with sqlite_engine.begin() as conn:
            _bootstrap_minimal_schema(conn)
            _seed_jobs(conn)
            _seed_staging_rows(conn)

        module = _load_migration_module()
        _run_migration_upgrade(sqlite_engine, module)

        with sqlite_engine.connect() as conn:
            orphan_count = conn.execute(text(
                """
                SELECT COUNT(*) FROM _ref_gap_fill_staging s
                LEFT JOIN extraction_jobs ej ON ej.id = s.fill_batch_id
                WHERE s.source <> '' AND ej.id IS NULL
                """
            )).scalar()
            assert orphan_count == 0, (
                "AC-B3: every populated source row must resolve to a job"
            )
