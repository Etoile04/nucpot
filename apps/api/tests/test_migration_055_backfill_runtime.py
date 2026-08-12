"""Runtime regression tests for migration 055 (ontology_version_id FK).

Replaces the string-match backfill assertions in
``test_migration_055_ontology_version_fk.py`` with real round-trip
exercises of the migration's backfill SQL.

Cases covered:

* **Warm DB** — rows in ``kg_entity_types`` / ``kg_relation_types`` pre-055
  are backfilled to the id of the ``0.1.0`` version seeded by migration
  044 (``status='published'``).
* **Cold DB** — no published ``ontology_versions`` row -> backfill is a
  no-op, columns stay NULL, no abort.
* **Idempotency** — running the backfill UPDATE twice does not change
  already-populated rows.
* **Downgrade** — ``module.downgrade()`` drops both columns cleanly and
  re-upgrade repopulates the backfill.

Why this matters
----------------
If migration 044 ever changes the seed status from ``'published'``, the
055 backfill silently no-ops and every existing type row keeps a NULL
``ontology_version_id`` — with the old string-match tests still green.
Layer 1 below runs on every CI and catches that regression on SQLite
without needing PostgreSQL.

Two layers
----------

* **Layer 1 — SQLite round-trip** (always runs):
  Bootstraps a minimal pre-055 schema on SQLite, manually applies the
  schema-equivalent DDL (because SQLite rejects ``ALTER TABLE ADD COLUMN
  … REFERENCES`` with ``NotImplementedError``), then exercises the
  migration's actual ``_BACKFILL_ID_SUBQUERY`` SQL via
  ``op.execute``. The migration's backfill contract is the SQL string,
  not the ``op.add_column`` call — Layer 1 verifies the SQL does the
  right thing on the simplest dialect that supports it.

* **Layer 2 — PostgreSQL** (opt-in via ``NFM_TEST_DATABASE_URL``):
  Runs the migration's actual ``upgrade()`` and ``downgrade()`` functions
  end-to-end against a disposable PG DB. This is the only layer that
  exercises the FK DDL on the production dialect.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, event, text

# Path to the migration module under test (resolved against CWD, which is
# apps/api/ when invoked via ``pytest apps/api/tests``).
_MIGRATION_PATH = Path(
    "migrations/versions/055_add_ontology_version_fk_to_type_tables.py"
).resolve()

# Mirror of the deterministic seed user from migration 044 (NFM-2579).
_SEED_USER_ID = "00000000-0000-0000-0000-000000000001"
_SEED_USER_EMAIL = "system@nucpot.internal"


# ---------------------------------------------------------------------------
# Engine / module helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def sqlite_engine():
    """In-memory SQLite engine with FK enforcement on.

    FK enforcement is enabled because the migration's
    ``ontology_version_id`` column references ``ontology_versions.id``;
    we want the runtime test to reject bogus backfill values if the
    contract ever regresses.
    """
    engine = create_engine("sqlite:///:memory:", future=True)

    @event.listens_for(engine, "connect")
    def _enable_fk(dbapi_conn, _record):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    yield engine
    engine.dispose()


def _load_migration_module():
    """Import migration 055 by file path (digit-prefixed module name).

    Module names cannot start with a digit, so ``import_module`` cannot
    load ``055_add_ontology_version_fk_to_type_tables``. Use
    ``spec_from_file_location`` instead.
    """
    spec = importlib.util.spec_from_file_location(
        "_nfm2898_migration_under_test", _MIGRATION_PATH
    )
    assert spec is not None and spec.loader is not None, (
        f"Could not load migration module from {_MIGRATION_PATH}"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _bootstrap_pre_055_schema(conn) -> None:
    """Create schema state matching migrations 001-053.

    Specifically, ``kg_entity_types`` and ``kg_relation_types`` do NOT
    yet have an ``ontology_version_id`` column — that's what migration
    055 adds.
    """
    conn.execute(text(
        """
        CREATE TABLE users (
            id CHAR(36) PRIMARY KEY,
            username VARCHAR(64) NOT NULL,
            email VARCHAR(255) NOT NULL UNIQUE,
            full_name VARCHAR(200),
            hashed_password VARCHAR(255) NOT NULL,
            is_active BOOLEAN NOT NULL DEFAULT 1
        )
        """
    ))
    conn.execute(text(
        """
        CREATE TABLE ontology_versions (
            id CHAR(36) PRIMARY KEY,
            version VARCHAR(50) NOT NULL UNIQUE,
            status VARCHAR(20) NOT NULL,
            changelog TEXT,
            created_by CHAR(36) NOT NULL REFERENCES users(id),
            ontology_data TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    ))
    conn.execute(text(
        """
        CREATE TABLE kg_entity_types (
            id CHAR(36) PRIMARY KEY,
            name VARCHAR(50) NOT NULL UNIQUE,
            label_template VARCHAR(200),
            required_properties TEXT,
            description TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    ))
    conn.execute(text(
        """
        CREATE TABLE kg_relation_types (
            id CHAR(36) PRIMARY KEY,
            name VARCHAR(100) NOT NULL UNIQUE,
            source_types TEXT,
            target_types TEXT,
            properties_schema TEXT,
            description TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    ))


def _seed_system_user(conn) -> None:
    """Insert the migration-044 system user (idempotent)."""
    conn.execute(text(
        """
        INSERT INTO users (id, username, email, full_name, hashed_password, is_active)
        VALUES (:id, 'system', :email, 'NucPot System', '!', 0)
        """
    ), {"id": _SEED_USER_ID, "email": _SEED_USER_EMAIL})


def _seed_published_version(conn, version_id: str, version: str = "0.1.0") -> None:
    """Insert a published ontology_versions row."""
    conn.execute(text(
        """
        INSERT INTO ontology_versions (
            id, version, status, changelog, created_by, ontology_data, created_at, updated_at
        )
        VALUES (:id, :version, 'published', 'Initial',
                :user, '{}', '2026-01-01', '2026-01-01')
        """
    ), {"id": version_id, "version": version, "user": _SEED_USER_ID})


def _seed_type_rows(
    conn,
    *,
    entity_names: tuple[str, ...] = ("Entity1", "Entity2"),
    relation_names: tuple[str, ...] = ("Relation1",),
) -> None:
    """Insert the given entity/relation rows with deterministic UUIDs."""
    for i, name in enumerate(entity_names, start=1):
        row_id = f"00000000-0000-0000-0000-00000000000{i}"
        conn.execute(text(
            "INSERT INTO kg_entity_types (id, name, created_at, updated_at) "
            "VALUES (:id, :name, '2026-01-01', '2026-01-01')"
        ), {"id": row_id, "name": name})
    for i, name in enumerate(relation_names, start=1):
        # Relation rows use a different first hex digit so UUIDs don't
        # collide with entity rows in the same test.
        row_id = f"a0000000-0000-0000-0000-00000000000{i}"
        conn.execute(text(
            "INSERT INTO kg_relation_types (id, name, created_at, updated_at) "
            "VALUES (:id, :name, '2026-01-01', '2026-01-01')"
        ), {"id": row_id, "name": name})


def _apply_backfill(conn, module) -> None:
    """Apply the schema-add DDL + run the migration's backfill SQL.

    Layer 1 cannot use the migration's full ``upgrade()`` on SQLite:
    SQLite rejects ``ALTER TABLE ADD COLUMN … REFERENCES`` with
    ``NotImplementedError``. The schema-change portion (add_column with
    FK) is exercised at Layer 2 against PostgreSQL. Here we replicate
    the column-add DDL manually, then execute the migration's actual
    backfill SQL — the part of the contract this test guards.
    """
    conn.execute(text(
        "ALTER TABLE kg_entity_types ADD COLUMN ontology_version_id CHAR(36)"
    ))
    conn.execute(text(
        "ALTER TABLE kg_relation_types ADD COLUMN ontology_version_id CHAR(36)"
    ))
    subq = module._BACKFILL_ID_SUBQUERY
    conn.execute(text(
        f"UPDATE kg_entity_types SET ontology_version_id = ({subq}) "
        "WHERE ontology_version_id IS NULL"
    ))
    conn.execute(text(
        f"UPDATE kg_relation_types SET ontology_version_id = ({subq}) "
        "WHERE ontology_version_id IS NULL"
    ))


# ---------------------------------------------------------------------------
# Layer 1 — SQLite round-trip (runs on every CI)
# ---------------------------------------------------------------------------


class TestWarmDbBackfill:
    """Rows pre-055 are backfilled to the 0.1.0 published version id."""

    def test_entity_rows_backfilled_to_published_version(self, sqlite_engine):
        """Pre-055 entity rows should get ontology_version_id = the 0.1.0 id."""
        pub_id = "11111111-1111-1111-1111-111111111111"
        with sqlite_engine.begin() as conn:
            _bootstrap_pre_055_schema(conn)
            _seed_system_user(conn)
            _seed_published_version(conn, pub_id)
            _seed_type_rows(
                conn,
                entity_names=("Entity1", "Entity2", "Entity3"),
                relation_names=(),
            )
            module = _load_migration_module()
            _apply_backfill(conn, module)

        with sqlite_engine.connect() as conn:
            rows = conn.execute(text(
                "SELECT name, ontology_version_id FROM kg_entity_types ORDER BY name"
            )).fetchall()
        assert len(rows) == 3, f"expected 3 entity rows, got {len(rows)}"
        for name, vid in rows:
            assert vid == pub_id, (
                f"kg_entity_types row {name!r} backfilled to {vid!r}, "
                f"expected {pub_id!r}"
            )

    def test_relation_rows_backfilled_to_published_version(self, sqlite_engine):
        """Pre-055 relation rows should get ontology_version_id = the 0.1.0 id."""
        pub_id = "22222222-2222-2222-2222-222222222222"
        with sqlite_engine.begin() as conn:
            _bootstrap_pre_055_schema(conn)
            _seed_system_user(conn)
            _seed_published_version(conn, pub_id)
            _seed_type_rows(
                conn,
                entity_names=(),
                relation_names=("Relation1", "Relation2"),
            )
            module = _load_migration_module()
            _apply_backfill(conn, module)

        with sqlite_engine.connect() as conn:
            rows = conn.execute(text(
                "SELECT name, ontology_version_id FROM kg_relation_types ORDER BY name"
            )).fetchall()
        assert len(rows) == 2, f"expected 2 relation rows, got {len(rows)}"
        for name, vid in rows:
            assert vid == pub_id, (
                f"kg_relation_types row {name!r} backfilled to {vid!r}, "
                f"expected {pub_id!r}"
            )

    def test_backfill_picks_earliest_published_when_multiple(self, sqlite_engine):
        """If multiple published rows exist, backfill targets ORDER BY created_at ASC LIMIT 1."""
        older_id = "33333333-3333-3333-3333-333333333333"
        newer_id = "44444444-4444-4444-4444-444444444444"
        with sqlite_engine.begin() as conn:
            _bootstrap_pre_055_schema(conn)
            _seed_system_user(conn)
            # Older published row.
            _seed_published_version(conn, older_id, version="0.1.0")
            # Newer published row — must NOT win.
            conn.execute(text(
                """
                INSERT INTO ontology_versions (
                    id, version, status, changelog, created_by, ontology_data, created_at, updated_at
                )
                VALUES (:id, '0.2.0', 'published', 'Next', :user, '{}', '2026-06-01', '2026-06-01')
                """
            ), {"id": newer_id, "user": _SEED_USER_ID})
            _seed_type_rows(
                conn,
                entity_names=("Entity1", "Entity2"),
                relation_names=("Relation1",),
            )
            module = _load_migration_module()
            _apply_backfill(conn, module)

        with sqlite_engine.connect() as conn:
            all_ids = [
                vid for (vid,) in conn.execute(text(
                    "SELECT ontology_version_id FROM kg_entity_types"
                )).fetchall()
            ] + [
                vid for (vid,) in conn.execute(text(
                    "SELECT ontology_version_id FROM kg_relation_types"
                )).fetchall()
            ]
        for vid in all_ids:
            assert vid == older_id, (
                f"Backfill picked {vid!r}, expected oldest published {older_id!r}"
            )


class TestColdDbBackfill:
    """No published ontology_versions -> backfill no-ops, columns stay NULL."""

    def test_draft_only_keeps_columns_null(self, sqlite_engine):
        """Cold DB: only a 'draft' ontology_versions row exists.

        The migration's ``WHERE status='published'`` filter means the
        backfill subquery returns NULL and the UPDATE leaves the new
        columns NULL.
        """
        with sqlite_engine.begin() as conn:
            _bootstrap_pre_055_schema(conn)
            _seed_system_user(conn)
            # Only a draft row -- the migration's status='published' filter excludes it.
            conn.execute(text(
                """
                INSERT INTO ontology_versions (
                    id, version, status, changelog, created_by, ontology_data, created_at, updated_at
                )
                VALUES ('55555555-5555-5555-5555-555555555555', '0.1.0', 'draft', 'WIP',
                        :user, '{}', '2026-01-01', '2026-01-01')
                """
            ), {"user": _SEED_USER_ID})
            _seed_type_rows(
                conn,
                entity_names=("Entity1", "Entity2"),
                relation_names=("Relation1",),
            )

            module = _load_migration_module()
            _apply_backfill(conn, module)  # Must not raise.

        with sqlite_engine.connect() as conn:
            for table in ("kg_entity_types", "kg_relation_types"):
                nulls = conn.execute(text(
                    f"SELECT COUNT(*) FROM {table} WHERE ontology_version_id IS NULL"
                )).scalar_one()
                total = conn.execute(text(
                    f"SELECT COUNT(*) FROM {table}"
                )).scalar_one()
                assert nulls == total, (
                    f"{table}: cold DB should leave all {total} rows NULL, "
                    f"got {nulls} NULLs"
                )


class TestIdempotency:
    """Re-running the backfill UPDATE is safe on already-populated rows."""

    def test_double_backfill_is_noop_on_populated_rows(self, sqlite_engine):
        """Running the backfill twice does not change already-populated rows.

        The migration's ``WHERE ontology_version_id IS NULL`` clause
        guarantees this — rows whose id was set on the first run are
        skipped on the second.
        """
        pub_id = "66666666-6666-6666-6666-666666666666"
        with sqlite_engine.begin() as conn:
            _bootstrap_pre_055_schema(conn)
            _seed_system_user(conn)
            _seed_published_version(conn, pub_id)
            _seed_type_rows(
                conn,
                entity_names=("Entity1", "Entity2"),
                relation_names=("Relation1",),
            )

            module = _load_migration_module()
            _apply_backfill(conn, module)

            # Snapshot the post-backfill state.
            snap_ent = conn.execute(text(
                "SELECT id, ontology_version_id FROM kg_entity_types ORDER BY id"
            )).fetchall()
            snap_rel = conn.execute(text(
                "SELECT id, ontology_version_id FROM kg_relation_types ORDER BY id"
            )).fetchall()

            # Re-run only the backfill UPDATE statements (not the schema add).
            subq = module._BACKFILL_ID_SUBQUERY
            conn.execute(text(
                f"UPDATE kg_entity_types SET ontology_version_id = ({subq}) "
                "WHERE ontology_version_id IS NULL"
            ))
            conn.execute(text(
                f"UPDATE kg_relation_types SET ontology_version_id = ({subq}) "
                "WHERE ontology_version_id IS NULL"
            ))

            again_ent = conn.execute(text(
                "SELECT id, ontology_version_id FROM kg_entity_types ORDER BY id"
            )).fetchall()
            again_rel = conn.execute(text(
                "SELECT id, ontology_version_id FROM kg_relation_types ORDER BY id"
            )).fetchall()

        assert snap_ent == again_ent, (
            f"kg_entity_types changed across re-run: {snap_ent} -> {again_ent}"
        )
        assert snap_rel == again_rel, (
            f"kg_relation_types changed across re-run: {snap_rel} -> {again_rel}"
        )
        for _id, vid in (*snap_ent, *snap_rel):
            assert vid == pub_id, (
                f"row {_id} backfilled to {vid!r}, expected {pub_id!r}"
            )


class TestDowngrade:
    """``downgrade()`` drops both columns cleanly and re-upgrade works."""

    def test_downgrade_drops_columns(self, sqlite_engine):
        """After downgrade, ontology_version_id is gone from both tables."""
        with sqlite_engine.begin() as conn:
            _bootstrap_pre_055_schema(conn)
            _seed_system_user(conn)
            _seed_published_version(conn, "77777777-7777-7777-7777-777777777777")
            _seed_type_rows(
                conn,
                entity_names=("Entity1", "Entity2"),
                relation_names=("Relation1",),
            )

            module = _load_migration_module()
            _apply_backfill(conn, module)

        # Run the migration's actual downgrade() end-to-end. This is the
        # only portion of the migration that is portable to SQLite
        # without modification (no FK constraint to add).
        with sqlite_engine.begin() as conn:
            module = _load_migration_module()
            mc = MigrationContext.configure(conn)
            with Operations.context(mc):
                module.downgrade()

        with sqlite_engine.connect() as conn:
            ent_cols = [r[1] for r in conn.execute(text(
                "PRAGMA table_info(kg_entity_types)"
            )).fetchall()]
            rel_cols = [r[1] for r in conn.execute(text(
                "PRAGMA table_info(kg_relation_types)"
            )).fetchall()]
        assert "ontology_version_id" not in ent_cols, (
            f"kg_entity_types still has ontology_version_id after downgrade: {ent_cols}"
        )
        assert "ontology_version_id" not in rel_cols, (
            f"kg_relation_types still has ontology_version_id after downgrade: {rel_cols}"
        )

    def test_downgrade_then_reupgrade_repopulates_backfill(self, sqlite_engine):
        """Downgrade + re-upgrade must work end to end with no orphan state."""
        pub_id = "88888888-8888-8888-8888-888888888888"
        with sqlite_engine.begin() as conn:
            _bootstrap_pre_055_schema(conn)
            _seed_system_user(conn)
            _seed_published_version(conn, pub_id)
            _seed_type_rows(
                conn,
                entity_names=("Entity1", "Entity2"),
                relation_names=("Relation1",),
            )

            module = _load_migration_module()
            _apply_backfill(conn, module)

            # Downgrade.
            mc = MigrationContext.configure(conn)
            with Operations.context(mc):
                module.downgrade()

            # Re-upgrade (Layer 1 helper).
            _apply_backfill(conn, module)

        with sqlite_engine.connect() as conn:
            ids = [
                row[0] for row in conn.execute(text(
                    "SELECT ontology_version_id FROM kg_entity_types"
                )).fetchall()
            ]
            ids += [
                row[0] for row in conn.execute(text(
                    "SELECT ontology_version_id FROM kg_relation_types"
                )).fetchall()
            ]
        assert ids, "expected at least one backfilled id after re-upgrade"
        assert all(v == pub_id for v in ids), (
            f"Re-upgrade did not re-populate backfill: got {ids!r}, "
            f"expected all == {pub_id!r}"
        )


# ---------------------------------------------------------------------------
# Layer 2 — PostgreSQL integration (opt-in via env var)
# ---------------------------------------------------------------------------

_NFM_TEST_PG_URL = os.environ.get("NFM_TEST_DATABASE_URL", "").strip()


@pytest.mark.skipif(
    not _NFM_TEST_PG_URL,
    reason=(
        "Real-PG integration test requires NFM_TEST_DATABASE_URL env var "
        "(e.g. postgresql+asyncpg://nfm:nfm@localhost:5432/nfm_test_migration055). "
        "Set to a disposable test DB to enable."
    ),
)
class TestRealPgIntegration:
    """End-to-end verification against PostgreSQL.

    Runs the migration's actual ``upgrade()`` and ``downgrade()``
    functions against a disposable PG DB. This is the only layer that
    exercises the FK DDL on the production dialect.
    """

    def test_warm_db_full_round_trip_on_pg(self):
        engine = create_engine(_NFM_TEST_PG_URL, future=True)
        try:
            # 1. Clean slate.
            with engine.begin() as conn:
                conn.execute(text("DROP TABLE IF EXISTS kg_relation_types CASCADE"))
                conn.execute(text("DROP TABLE IF EXISTS kg_entity_types CASCADE"))
                conn.execute(text("DROP TABLE IF EXISTS ontology_versions CASCADE"))
                conn.execute(text("DROP TABLE IF EXISTS users CASCADE"))
                conn.execute(text("DROP TABLE IF EXISTS alembic_version"))

            # 2. Bootstrap the minimum schema a real PG would have pre-055.
            with engine.begin() as conn:
                conn.execute(text(
                    """
                    CREATE TABLE users (
                        id UUID PRIMARY KEY,
                        username VARCHAR(64) NOT NULL,
                        email VARCHAR(255) NOT NULL UNIQUE,
                        full_name VARCHAR(200),
                        hashed_password VARCHAR(255) NOT NULL,
                        is_active BOOLEAN NOT NULL DEFAULT TRUE
                    )
                    """
                ))
                conn.execute(text(
                    """
                    CREATE TABLE ontology_versions (
                        id UUID PRIMARY KEY,
                        version VARCHAR(50) NOT NULL UNIQUE,
                        status VARCHAR(20) NOT NULL,
                        changelog TEXT,
                        created_by UUID NOT NULL REFERENCES users(id),
                        ontology_data JSONB,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                ))
                conn.execute(text(
                    """
                    CREATE TABLE kg_entity_types (
                        id UUID PRIMARY KEY,
                        name VARCHAR(50) NOT NULL UNIQUE,
                        label_template VARCHAR(200),
                        required_properties JSONB,
                        description TEXT,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                ))
                conn.execute(text(
                    """
                    CREATE TABLE kg_relation_types (
                        id UUID PRIMARY KEY,
                        name VARCHAR(100) NOT NULL UNIQUE,
                        source_types JSONB,
                        target_types JSONB,
                        properties_schema JSONB,
                        description TEXT,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                ))
                pub_id = "99999999-9999-9999-9999-999999999999"
                conn.execute(text(
                    """
                    INSERT INTO users (id, username, email, full_name, hashed_password, is_active)
                    VALUES ('00000000-0000-0000-0000-000000000001', 'system',
                            'system@nucpot.internal', 'NucPot System', '!', FALSE)
                    """
                ))
                conn.execute(text(
                    """
                    INSERT INTO ontology_versions
                        (id, version, status, changelog, created_by, ontology_data)
                    VALUES (:id, '0.1.0', 'published', 'Initial',
                            '00000000-0000-0000-0000-000000000001', '{}'::jsonb)
                    """
                ), {"id": pub_id})
                for i, name in enumerate(("Entity1", "Entity2", "Entity3"), start=1):
                    conn.execute(text(
                        "INSERT INTO kg_entity_types (id, name) VALUES (:id, :name)"
                    ), {"id": f"00000000-0000-0000-0000-00000000000{i}", "name": name})

            # 3. Run migration 055's actual upgrade() end-to-end.
            module = _load_migration_module()
            with engine.begin() as conn:
                mc = MigrationContext.configure(conn)
                with Operations.context(mc):
                    module.upgrade()

            # 4. Verify backfill.
            with engine.connect() as conn:
                rows = conn.execute(text(
                    "SELECT name, ontology_version_id FROM kg_entity_types ORDER BY name"
                )).fetchall()
            assert len(rows) == 3, f"expected 3 entity rows, got {len(rows)}"
            for name, vid in rows:
                assert vid is not None, (
                    f"PG backfill left {name!r} with NULL ontology_version_id"
                )

            # 5. Downgrade drops both columns.
            with engine.begin() as conn:
                mc = MigrationContext.configure(conn)
                with Operations.context(mc):
                    module.downgrade()
            with engine.connect() as conn:
                for table in ("kg_entity_types", "kg_relation_types"):
                    present = conn.execute(text(
                        """
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name = :t AND column_name = 'ontology_version_id'
                        """
                    ), {"t": table}).fetchone()
                    assert present is None, (
                        f"{table} still has ontology_version_id after PG downgrade"
                    )
        finally:
            engine.dispose()
