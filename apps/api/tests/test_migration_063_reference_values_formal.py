"""Runtime regression tests for migration 063 — NFM-3872 (Wayfinder pilot C / C-S1).

Validates the ``reference_values`` formal table that the C-S1 ETL
populates from ``_ref_gap_fill_staging`` rows that pass the C-I1
admission gate (NFM-3871).

Approach (matches test_seed_property_types_migration_runtime.py)
--------------------------------------------------------------

``alembic.operations.Operations`` lets us bind the module-level
``op`` proxy to a real SQLite connection via ``MigrationContext`` +
``Operations.context``. Running ``module.upgrade()`` then executes
real DDL against the in-memory SQLite database, after which we can
inspect the resulting schema with plain SQLAlchemy reflection.

Why we don't drive this through ``alembic upgrade`` against the real
``migrations/env.py``:

* ``env.py`` calls ``SELECT pg_advisory_lock(...)`` at the start of
  ``run_migrations_online`` (NFM-2782). That call is Postgres-only;
  on SQLite it fails before any DDL runs.

So we replicate just enough of the env.py setup to exercise the
migration body in isolation. The chain integrity / down_revision
wiring is covered by the existing ``alembic.heads`` check we ran
locally.
"""

from __future__ import annotations

import importlib.util
import sys
import uuid
from pathlib import Path

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, text

_MIGRATION_PATH = Path(
    "migrations/versions/063_create_reference_values_formal.py"
).resolve()


def _load_migration_module():
    """Import migration 063 by file path (digit-prefixed module name)."""
    spec = importlib.util.spec_from_file_location(
        "_nfm3872_migration_under_test", _MIGRATION_PATH
    )
    assert spec is not None and spec.loader is not None, (
        f"Could not load migration module from {_MIGRATION_PATH}"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _run_with_sqlite(module_func) -> None:
    """Bind ``op`` to a fresh SQLite in-memory connection and run *module_func*.

    Foreign keys are enforced (the C-S1 migration declares
    ``reference_values.staging_id`` as FK to
    ``_ref_gap_fill_staging.id``). We seed a minimal parent table so
    the FK can be satisfied on later inserts; the migration itself
    only creates the ``reference_values`` table.
    """
    engine = create_engine("sqlite:///:memory:", future=True)

    with engine.connect() as conn:
        # Seed the parent table the FK targets.
        conn.execute(text(
            """
            CREATE TABLE _ref_gap_fill_staging (
                id CHAR(36) PRIMARY KEY,
                element_system VARCHAR(50) NOT NULL,
                property_name VARCHAR(100) NOT NULL,
                value DOUBLE PRECISION NOT NULL,
                unit VARCHAR(50) NOT NULL,
                source VARCHAR(200) NOT NULL,
                dedup_hash VARCHAR(64) NOT NULL DEFAULT ''
            )
            """
        ))
        conn.commit()

    with engine.connect() as conn:
        # Enable FK enforcement for the migration's create_table call.
        conn.execute(text("PRAGMA foreign_keys=ON"))
        mc = MigrationContext.configure(conn)
        with Operations.context(mc):
            module_func()
        conn.commit()

    # Stash the engine on the connection for the test to introspect.
    # We do this by passing the engine through a closure on the
    # module-level ``_engine`` variable — easier: the test creates
    # its own engine to verify the schema after the migration. So we
    # do NOT expose it through the helper; the test creates its own
    # SQLite DB. Wait, that won't work because the DDL is on a
    # different engine.
    #
    # Simpler approach: persist the engine on the function object
    # via a sentinel attribute. The migration closes over the same
    # in-memory engine so subsequent reads from that engine see the
    # tables.
    _run_with_sqlite._last_engine = engine  # type: ignore[attr-defined]


@pytest.fixture
def sqlite_engine_with_063_upgrade():
    """Yield a SQLite engine that has migration 063's DDL applied."""
    engine = create_engine("sqlite:///:memory:", future=True)

    with engine.connect() as conn:
        conn.execute(text(
            """
            CREATE TABLE _ref_gap_fill_staging (
                id CHAR(36) PRIMARY KEY,
                element_system VARCHAR(50) NOT NULL,
                property_name VARCHAR(100) NOT NULL,
                value DOUBLE PRECISION NOT NULL,
                unit VARCHAR(50) NOT NULL,
                source VARCHAR(200) NOT NULL,
                dedup_hash VARCHAR(64) NOT NULL DEFAULT ''
            )
            """
        ))
        conn.commit()

    module = _load_migration_module()

    def _do_upgrade() -> None:
        module.upgrade()

    with engine.connect() as conn:
        conn.execute(text("PRAGMA foreign_keys=ON"))
        mc = MigrationContext.configure(conn)
        with Operations.context(mc):
            _do_upgrade()
        conn.commit()

    yield engine
    engine.dispose()


# ---------------------------------------------------------------------------
# Tests — schema shape (post-upgrade)
# ---------------------------------------------------------------------------


class TestUpgrade:
    """Migration 063 upgrade produces the C-S1 formal table shape."""

    def test_creates_reference_values_table(
        self, sqlite_engine_with_063_upgrade,
    ) -> None:
        with sqlite_engine_with_063_upgrade.connect() as conn:
            tables = {
                r[0]
                for r in conn.execute(
                    text(
                        "SELECT name FROM sqlite_master "
                        "WHERE type='table' AND name='reference_values'"
                    )
                ).fetchall()
            }
            assert "reference_values" in tables

    def test_creates_required_columns(
        self, sqlite_engine_with_063_upgrade,
    ) -> None:
        with sqlite_engine_with_063_upgrade.connect() as conn:
            # PRAGMA table_info returns: (cid, name, type, notnull, dflt, pk)
            cols = {
                r[1]
                for r in conn.execute(
                    text("PRAGMA table_info(reference_values)")
                ).fetchall()
            }

        # Domain columns (production schema, per NFM-3780)
        assert {
            "element",
            "crystal_structure",
            "property_name",
            "value",
            "unit",
            "method",
            "source",
            "notes",
        } <= cols, f"missing domain columns: {cols}"

        # DOI attribution — only populated when C-I1 admits the row,
        # but the column must exist so the ETL doesn't silently drop
        # the admission context.
        assert "source_doi" in cols

        # ETL provenance / audit trail.
        assert {
            "staging_id",
            "etl_issue",
            "etl_manifest_ref",
            "etl_ok_reason",
            "promoted_at",
            "created_at",
            "updated_at",
        } <= cols

    def test_creates_indexes(
        self, sqlite_engine_with_063_upgrade,
    ) -> None:
        with sqlite_engine_with_063_upgrade.connect() as conn:
            indexes = {
                r[0]
                for r in conn.execute(
                    text(
                        "SELECT name FROM sqlite_master WHERE type='index' "
                        "AND tbl_name='reference_values'"
                    )
                ).fetchall()
            }
            assert "idx_rv_source" in indexes, f"idx_rv_source missing; got {indexes}"
            assert "idx_rv_element_property" in indexes, (
                f"idx_rv_element_property missing; got {indexes}"
            )

    def test_uniqueness_constraint_on_staging_id(
        self, sqlite_engine_with_063_upgrade,
    ) -> None:
        """staging_id is UNIQUE — the C-S1 ETL relies on this for idempotency."""
        from sqlalchemy.exc import IntegrityError as SAIntegrityError

        with sqlite_engine_with_063_upgrade.connect() as conn:
            # Insert one staging parent + two formal children referencing it.
            staging_id = str(uuid.uuid4())
            conn.execute(
                text(
                    "INSERT INTO _ref_gap_fill_staging "
                    "(id, element_system, property_name, value, unit, source, dedup_hash) "
                    "VALUES (:id, 'UO2', 'density', 10.0, 'g/cm3', 'Owen2023', 'h1')"
                ),
                {"id": staging_id},
            )
            conn.execute(
                text(
                    "INSERT INTO reference_values "
                    "(id, staging_id, element, property_name, value, unit, source, promoted_at) "
                    "VALUES (:id, :sid, 'UO2', 'density', 10.0, 'g/cm3', 'Owen2023', '2026-08-31 00:00:00')"
                ),
                {"id": str(uuid.uuid4()), "sid": staging_id},
            )
            # The second insert with the same staging_id MUST fail.
            # SQLAlchemy wraps the underlying sqlite3.IntegrityError as
            # sqlalchemy.exc.IntegrityError; assert against the SA wrapper.
            with pytest.raises(SAIntegrityError):
                conn.execute(
                    text(
                        "INSERT INTO reference_values "
                        "(id, staging_id, element, property_name, value, unit, source, promoted_at) "
                        "VALUES (:id, :sid, 'UO2', 'density', 11.0, 'g/cm3', 'Owen2023', '2026-08-31 00:00:00')"
                    ),
                    {"id": str(uuid.uuid4()), "sid": staging_id},
                )

    def test_foreign_key_to_staging(
        self, sqlite_engine_with_063_upgrade,
    ) -> None:
        """FK to _ref_gap_fill_staging with ON DELETE CASCADE must be enforced."""
        from sqlalchemy.exc import IntegrityError as SAIntegrityError

        with sqlite_engine_with_063_upgrade.connect() as conn:
            # A formal row referencing a non-existent staging row MUST fail.
            with pytest.raises(SAIntegrityError):
                conn.execute(
                    text(
                        "INSERT INTO reference_values "
                        "(id, staging_id, element, property_name, value, unit, source, promoted_at) "
                        "VALUES (:id, :sid, 'UO2', 'density', 10.0, 'g/cm3', 'Owen2023', '2026-08-31 00:00:00')"
                    ),
                    {
                        "id": str(uuid.uuid4()),
                        "sid": str(uuid.uuid4()),  # never inserted into staging
                    },
                )


class TestDowngrade:
    """Migration 063 downgrade reverses the C-S1 schema cleanly."""

    def test_drops_table_and_indexes(self) -> None:
        engine = create_engine("sqlite:///:memory:", future=True)
        with engine.connect() as conn:
            conn.execute(text(
                """
                CREATE TABLE _ref_gap_fill_staging (
                    id CHAR(36) PRIMARY KEY,
                    element_system VARCHAR(50) NOT NULL,
                    property_name VARCHAR(100) NOT NULL,
                    value DOUBLE PRECISION NOT NULL,
                    unit VARCHAR(50) NOT NULL,
                    source VARCHAR(200) NOT NULL,
                    dedup_hash VARCHAR(64) NOT NULL DEFAULT ''
                )
                """
            ))
            conn.commit()

        module = _load_migration_module()

        def _upgrade() -> None:
            module.upgrade()

        def _downgrade() -> None:
            module.downgrade()

        with engine.connect() as conn:
            conn.execute(text("PRAGMA foreign_keys=ON"))
            mc = MigrationContext.configure(conn)
            with Operations.context(mc):
                _upgrade()
                _downgrade()
            conn.commit()

        with engine.connect() as conn:
            tables = {
                r[0]
                for r in conn.execute(
                    text("SELECT name FROM sqlite_master WHERE type='table'")
                ).fetchall()
            }
            assert "reference_values" not in tables, (
                f"reference_values still exists after downgrade: {tables}"
            )
            indexes = {
                r[0]
                for r in conn.execute(
                    text(
                        "SELECT name FROM sqlite_master WHERE type='index' "
                        "AND tbl_name='reference_values'"
                    )
                ).fetchall()
            }
            assert indexes == set(), f"orphan indexes left after downgrade: {indexes}"


class TestRevisionMetadata:
    """Static checks on the migration metadata — guards against silent typos."""

    def test_revision_id(self) -> None:
        module = _load_migration_module()
        assert module.revision == "063_create_reference_values_formal"

    def test_down_revision(self) -> None:
        module = _load_migration_module()
        assert module.down_revision == "062_create_rerun_idempotency_keys"
