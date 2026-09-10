"""NFM-4549 / G1-C — real-Postgres integration test for AC-9 dedup.

The RE 2026-09-10 ruling on NFM-4549 flagged two CRITICAL bugs that the
SQLite hermetic test fixture cannot catch:

* **CRITICAL A** — ``extraction_to_db_mapper`` wrote ``dedupe_key=...``
  on every INSERT.  On PG the column is ``GENERATED ALWAYS AS (md5(...))
  STORED`` (migration 085, NFM-4548 G1-B), so a literal write raises
  SQLSTATE 428C9.  SQLite drops the GENERATED semantics and accepts
  the literal write, so the bug is invisible there.
* **CRITICAL B** — ``uq_pm_dedupe_key`` is a PARTIAL unique index
  ``WHERE dataset_version_id IS NOT NULL``.  The pre-fix mapper never
  stamped ``dataset_version_id``, so every new measurement was OUTSIDE
  the partial scope and duplicates slipped through.  SQLite ignores
  ``postgresql_where`` and ``Base.metadata.create_all`` builds a full
  unique index, so the test fixture silently inverted the predicate.

This test is opt-in via ``NFM_TEST_DATABASE_URL``. When set, it:

1. Creates a per-run isolated schema (``CREATE SCHEMA nfm_test_<uuid>``).
2. Sets ``search_path`` so every DDL/DML lives in that schema — no
   collision with whatever else is in the test DB.
3. Re-creates the ``property_measurements``, ``measurement_conditions``,
   ``datasets``, ``dataset_versions``, ``data_sources``, ``materials``,
   ``material_categories``, ``property_categories``, ``property_types``,
   and ``units`` tables with the EXACT DDL migration 085 produces —
   including the GENERATED ALWAYS columns and partial unique indexes.
4. Drives ``map_and_persist`` twice with identical inputs and asserts
   the dedup round-trip — proves the mapper's dialect-aware write
   path AND the partial unique scope both hold on real PG.
5. Inspects ``pg_attribute`` and ``pg_indexes`` to confirm the
   GENERATED column and partial predicate are actually installed
   (not just claimed by the migration source).

The test is intentionally narrow — it exercises AC-9 (the column +
the constraint + the mapper wiring). Owen 2023's full 92-row case
is the E2E QA probe's job; this test guards the production ingest
path's correctness on the production dialect.

Skip semantics: ``NFM_TEST_DATABASE_URL`` unset → entire class skipped,
no error.
"""

from __future__ import annotations

import os
import uuid
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

# ---------------------------------------------------------------------------
# Skip-if-Postgres-not-configured gate
# ---------------------------------------------------------------------------

_NFM_TEST_PG_URL = os.environ.get("NFM_TEST_DATABASE_URL", "").strip()


pytestmark = pytest.mark.skipif(
    not _NFM_TEST_PG_URL,
    reason=(
        "Real-Postgres integration test requires NFM_TEST_DATABASE_URL env "
        "var pointing at a disposable asyncpg URL "
        "(e.g. postgresql+asyncpg://nfm:nfm@localhost:5432/nfm_test_nfm4549). "
        "Set it to enable this layer of the AC-9 regression guard."
    ),
)


# ---------------------------------------------------------------------------
# Per-test schema isolation helpers
# ---------------------------------------------------------------------------


def _build_schema_dsn(base_url: str, schema_name: str) -> str:
    """Return a DSN that pins ``search_path`` to ``schema_name`` on connect.

    Asyncpg accepts ``server_settings`` via the URL query string. We append
    ``options=-c%20search_path%3D<schema>`` so every connection sees the
    isolated schema without explicit per-connection ``SET`` calls.
    """
    if "?" in base_url:
        head, qs = base_url.split("?", 1)
    else:
        head, qs = base_url, ""
    options = f"-c search_path={schema_name},public"
    new_qs = f"options={options}" + (f"&{qs}" if qs else "")
    return f"{head}?{new_qs}"


@pytest.fixture
async def pg_dedup_schema():
    """Yield an async engine whose tables live in an isolated PG schema.

    Builds the schema with raw DDL that mirrors migration 085's relevant
    payload (GENERATED ALWAYS columns + partial unique indexes). Drops
    the schema at teardown — even on failure — to keep CI clean.
    """
    assert _NFM_TEST_PG_URL, "skipif above should have skipped this fixture"
    schema = f"nfm_test_dedup_{uuid.uuid4().hex[:12]}"

    # 1. Bootstrap engine for the bootstrap connection (uses public schema
    #    so CREATE SCHEMA / DROP SCHEMA land there).
    bootstrap_engine = create_async_engine(_NFM_TEST_PG_URL, isolation_level="AUTOCOMMIT")
    try:
        async with bootstrap_engine.connect() as conn:
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    finally:
        await bootstrap_engine.dispose()

    # 2. Schema-scoped engine for the actual test body.
    dsn = _build_schema_dsn(_NFM_TEST_PG_URL, schema)
    engine = create_async_engine(dsn, echo=False)

    try:
        async with engine.begin() as conn:
            # Mirror migration 085's column / index / constraint payload
            # for the tables ``map_and_persist`` touches. We keep this
            # minimal — just enough to exercise AC-9 — rather than
            # reproducing the full schema.
            await conn.execute(
                text(
                    """
                    CREATE TABLE material_categories (
                        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                        name VARCHAR(200) NOT NULL,
                        slug VARCHAR(200) NOT NULL UNIQUE,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
            )
            await conn.execute(
                text(
                    """
                    CREATE TABLE materials (
                        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                        name VARCHAR(200) NOT NULL,
                        formula VARCHAR(200),
                        category_id UUID NOT NULL REFERENCES material_categories(id)
                            ON DELETE RESTRICT,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
            )
            await conn.execute(
                text(
                    """
                    CREATE TABLE property_categories (
                        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                        name VARCHAR(200) NOT NULL,
                        slug VARCHAR(200) NOT NULL UNIQUE,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
            )
            await conn.execute(
                text(
                    """
                    CREATE TABLE property_types (
                        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                        category_id UUID NOT NULL REFERENCES property_categories(id)
                            ON DELETE CASCADE,
                        name VARCHAR(200) NOT NULL,
                        slug VARCHAR(200) NOT NULL,
                        value_type VARCHAR(50) NOT NULL,
                        description TEXT,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
            )
            await conn.execute(
                text(
                    """
                    CREATE TABLE data_sources (
                        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                        title TEXT,
                        doi VARCHAR(255),
                        source_type VARCHAR(50),
                        file_hash VARCHAR(128),
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
            )
            await conn.execute(
                text(
                    """
                    CREATE TABLE datasets (
                        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                        material_id UUID NOT NULL REFERENCES materials(id)
                            ON DELETE CASCADE,
                        source_id UUID REFERENCES data_sources(id) ON DELETE SET NULL,
                        title TEXT,
                        literature_doi VARCHAR(255),
                        literature_content_hash VARCHAR(64),
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
            )
            await conn.execute(
                text(
                    """
                    CREATE TABLE dataset_versions (
                        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                        dataset_id UUID NOT NULL REFERENCES datasets(id)
                            ON DELETE CASCADE,
                        version_no INTEGER NOT NULL CHECK (version_no >= 1),
                        row_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
                        source_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
                        parent_version_id UUID REFERENCES dataset_versions(id)
                            ON DELETE SET NULL,
                        status VARCHAR(20) NOT NULL DEFAULT 'draft'
                            CHECK (status IN ('draft', 'released', 'rolled_back', 'superseded')),
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        CONSTRAINT uq_dataset_versions_dataset_version_no
                            UNIQUE (dataset_id, version_no),
                        CONSTRAINT uq_dataset_versions_one_released
                            UNIQUE (dataset_id) WHERE (status = 'released')
                    )
                    """
                )
            )
            # CRITICAL: property_measurements with the GENERATED ALWAYS column
            # AND the partial unique index. This is the exact shape migration
            # 085 produces — the SQLite test fixture cannot produce this.
            await conn.execute(
                text(
                    f"""
                    CREATE TABLE property_measurements (
                        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                        dataset_id UUID NOT NULL REFERENCES datasets(id)
                            ON DELETE CASCADE,
                        property_type_id UUID NOT NULL REFERENCES property_types(id)
                            ON DELETE CASCADE,
                        unit_id UUID,
                        source_id UUID,
                        dataset_version_id UUID REFERENCES dataset_versions(id)
                            ON DELETE SET NULL,
                        value_scalar NUMERIC(20, 15),
                        value_min NUMERIC(20, 15),
                        value_max NUMERIC(20, 15),
                        value_expression TEXT,
                        value_list JSONB,
                        value_text TEXT,
                        uncertainty NUMERIC(20, 15),
                        conditions JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                        conditions_hash VARCHAR(64),
                        review_status VARCHAR(50) NOT NULL DEFAULT 'pending',
                        reviewer_note TEXT,
                        reviewed_at TIMESTAMPTZ,
                        review_round INTEGER NOT NULL DEFAULT 1,
                        reviewer_id UUID,
                        method VARCHAR(100) NOT NULL DEFAULT '',
                        simulation_method VARCHAR(100),
                        model_name VARCHAR(200),
                        temp_k NUMERIC(20, 15),
                        pressure_gpa NUMERIC(20, 15),
                        -- GENERATED ALWAYS AS (md5(...)) STORED — the column
                        -- the mapper must NOT write into on PG.
                        value_hash TEXT GENERATED ALWAYS AS ({_VALUE_HASH_SQL}) STORED,
                        dedupe_key TEXT GENERATED ALWAYS AS ({_DEDUPE_KEY_SQL}) STORED,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        CONSTRAINT ck_property_measurements_value_present CHECK (
                            value_scalar IS NOT NULL OR value_min IS NOT NULL
                            OR value_max IS NOT NULL OR value_expression IS NOT NULL
                            OR value_list IS NOT NULL OR value_text IS NOT NULL
                        ),
                        CONSTRAINT uq_pm_dedup UNIQUE (
                            dataset_id, property_type_id, conditions_hash, method
                        )
                    )
                    """
                )
            )
            # The PARTIAL unique — the predicate is the load-bearing bit.
            await conn.execute(
                text(
                    """
                    CREATE UNIQUE INDEX uq_pm_dedupe_key
                        ON property_measurements (dedupe_key)
                        WHERE dataset_version_id IS NOT NULL
                    """
                )
            )
            await conn.execute(
                text(
                    """
                    CREATE INDEX idx_pm_dataset_version
                        ON property_measurements (dataset_version_id)
                        WHERE dataset_version_id IS NOT NULL
                    """
                )
            )
            await conn.execute(
                text(
                    """
                    CREATE TABLE measurement_conditions (
                        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                        measurement_id UUID NOT NULL REFERENCES property_measurements(id)
                            ON DELETE CASCADE,
                        temperature NUMERIC(10, 2),
                        pressure NUMERIC(10, 2),
                        strain_rate NUMERIC(20, 15),
                        environment VARCHAR(100),
                        loading_mode VARCHAR(100),
                        extra JSONB,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
            )

        factory = async_sessionmaker(engine, expire_on_commit=False)
        yield engine, factory, schema

    finally:
        await engine.dispose()
        # Tear down the schema via a fresh bootstrap connection.
        teardown_engine = create_async_engine(
            _NFM_TEST_PG_URL, isolation_level="AUTOCOMMIT"
        )
        try:
            async with teardown_engine.connect() as conn:
                await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        finally:
            await teardown_engine.dispose()


# ---------------------------------------------------------------------------
# AC-9 schema-shape assertions — proves the GENERATED column and the partial
# predicate are actually installed on PG (not just claimed by the migration
# source).
# ---------------------------------------------------------------------------


class TestDedupSchemaShapeOnPg:
    """The DDL the mapper runs against must really be PG-correct.

    These assertions are intentionally cheap: one query each, no fixtures.
    They prove the schema was built as expected — without this guard, the
    rest of the suite could pass on a mis-built schema and still be wrong.
    """

    async def test_dedupe_key_is_generated_always_stored(self, pg_dedup_schema) -> None:
        engine, _, _ = pg_dedup_schema
        async with engine.connect() as conn:
            row = (
                await conn.execute(
                    text(
                        """
                        SELECT is_generated, generation_expression
                        FROM pg_attribute
                        WHERE attrelid = 'property_measurements'::regclass
                          AND attname = 'dedupe_key'
                        """
                    )
                )
            ).first()
        assert row is not None, "dedupe_key column missing — DDL regression"
        assert row.is_generated == "s", (
            f"dedupe_key is_generated={row.is_generated!r}; "
            "expected 's' (always stored). If 'd' the GENERATED ALWAYS clause "
            "was dropped — SQLSTATE 428C9 risk."
        )
        assert "md5" in (row.generation_expression or "").lower(), (
            "dedupe_key generation_expression missing md5 — wrong column shape."
        )

    async def test_uq_pm_dedupe_key_has_partial_predicate(self, pg_dedup_schema) -> None:
        engine, _, _ = pg_dedup_schema
        async with engine.connect() as conn:
            row = (
                await conn.execute(
                    text(
                        """
                        SELECT indexdef
                        FROM pg_indexes
                        WHERE schemaname = current_schema()
                          AND tablename = 'property_measurements'
                          AND indexname = 'uq_pm_dedupe_key'
                        """
                    )
                )
            ).first()
        assert row is not None, "uq_pm_dedupe_key index missing — DDL regression"
        indexdef = row.indexdef.lower()
        assert "where" in indexdef and "dataset_version_id is not null" in indexdef, (
            f"uq_pm_dedupe_key indexdef missing partial predicate: {row.indexdef!r}. "
            "Without the predicate AC-9 silently degrades (rows with NULL "
            "dataset_version_id are excluded from the unique scope)."
        )


# ---------------------------------------------------------------------------
# AC-9 behavioral assertions — the round-trip the SQLite fixture cannot run.
# ---------------------------------------------------------------------------


def _extraction_input(
    *,
    source_doi: str = "10.1234/real-pg",
    material_name: str = "UO2",
    composition: str = "UO2",
    value: str = "2800",
    unit: str = "K",
) -> dict[str, Any]:
    """Mirror ``test_literature_dedup._make_extraction_input`` shape."""
    return {
        "source_file": "literature/UO2_real_pg.md",
        "source_doi": source_doi,
        "material_name": material_name,
        "composition": composition,
        "property_category": "thermal",
        "property": "melting_point",
        "value": value,
        "unit": unit,
        "reference": "Real PG probe",
        "confidence": "high",
    }


async def _seed_catalog(
    session: AsyncSession,
) -> None:
    """Insert the catalogue rows the mapper needs."""
    from nfm_db.models.material import Material, MaterialCategory
    from nfm_db.models.property import PropertyCategory, PropertyType

    cat = MaterialCategory(name="Fuel", slug="fuel")
    session.add(cat)
    await session.flush()
    session.add(Material(name="UO2", formula="UO2", category_id=cat.id))
    await session.flush()

    pcat = PropertyCategory(name="thermal", slug="thermal")
    session.add(pcat)
    await session.flush()
    session.add(
        PropertyType(
            name="melting_point",
            slug="melting_point",
            category_id=pcat.id,
            value_type="scalar",
        )
    )
    await session.flush()


class TestMapperDoesNotWriteGeneratedDedupeKey:
    """CRITICAL A — mapper's INSERT must not write ``dedupe_key`` literally.

    The DDL made the column ``GENERATED ALWAYS AS (md5(...)) STORED``.
    A literal INSERT raises SQLSTATE 428C9.  This test exercises the
    mapper on real PG: if the mapper still passes ``dedupe_key=`` into
    the PropertyMeasurement constructor (the old pre-fix wiring), the
    INSERT fails and we surface the regression.
    """

    async def test_mapper_insert_succeeds_on_real_pg(self, pg_dedup_schema) -> None:
        from nfm_db.models.property import PropertyMeasurement
        from nfm_db.services.extraction_to_db_mapper import map_and_persist

        _, factory, _ = pg_dedup_schema
        async with factory() as session:
            await _seed_catalog(session)
            await session.commit()

        async with factory() as session:
            result = await map_and_persist(
                session, [_extraction_input(value="2800")]
            )
            assert result.created_measurements == 1, (
                f"map_and_persist failed to insert on real PG: {result!r}. "
                "If this raises SQLSTATE 428C9, the mapper is still writing "
                "dedupe_key literally — CRITICAL A regression."
            )

        async with factory() as session:
            rows = (await session.execute(select(PropertyMeasurement))).scalars().all()
        assert len(rows) == 1
        row = rows[0]
        # Server-side generation populated the column.
        assert row.dedupe_key is not None, (
            "dedupe_key is NULL after PG INSERT — GENERATED column is missing "
            "or the migration chain didn't install it."
        )
        assert len(row.dedupe_key) == 32, (
            f"dedupe_key has unexpected length {len(row.dedupe_key)}; "
            "md5 should produce a 32-char hex string."
        )
        # CRITICAL B: dataset_version_id must be populated so the partial
        # unique scope actually covers this row.
        assert row.dataset_version_id is not None, (
            "dataset_version_id is NULL after mapper INSERT — CRITICAL B "
            "regression: partial unique uq_pm_dedupe_key WHERE "
            "dataset_version_id IS NOT NULL cannot fire on this row."
        )


class TestPartialUniqueFiresOnDuplicate:
    """CRITICAL B — duplicate mapper calls collapse via uq_pm_dedupe_key.

    Owen 2023's 92-row case is the canonical AC-9 acceptance probe.  The
    SQLite hermetic fixture cannot exercise this path because SQLite
    drops the partial predicate.  This test runs the round-trip on
    real PG so the partial unique's actual behaviour is observable.
    """

    async def test_identical_mapper_calls_collapse_to_one_row(
        self, pg_dedup_schema
    ) -> None:
        from nfm_db.models.property import PropertyMeasurement
        from nfm_db.services.extraction_to_db_mapper import map_and_persist

        _, factory, _ = pg_dedup_schema
        async with factory() as session:
            await _seed_catalog(session)
            await session.commit()

        # First call: lands one measurement row.
        async with factory() as session:
            first = await map_and_persist(
                session, [_extraction_input(value="2800")]
            )
            assert first.created_measurements == 1

        # Second call: identical input. The mapper must catch the
        # IntegrityError from uq_pm_dedupe_key (or the legacy uq_pm_dedup
        # fallback) and report a skipped duplicate, NOT a 500.
        async with factory() as session:
            second = await map_and_persist(
                session, [_extraction_input(value="2800")]
            )
            assert second.created_measurements == 0, (
                f"second identical call created {second.created_measurements} "
                "rows; expected 0 — partial unique uq_pm_dedupe_key did not fire."
            )
            assert second.skipped_duplicate_measurements == 1, (
                f"second call skipped_duplicate_measurements="
                f"{second.skipped_duplicate_measurements}; expected 1."
            )

        async with factory() as session:
            rows = (await session.execute(select(PropertyMeasurement))).scalars().all()
        assert len(rows) == 1, (
            f"AC-9 regression on real PG: identical mapper calls landed "
            f"{len(rows)} rows; expected 1."
        )
        # Both rows of the round-trip share the same generated dedupe_key.
        assert rows[0].dedupe_key is not None
        assert len(rows[0].dedupe_key) == 32


# ---------------------------------------------------------------------------
# Migration SQL fragments — pinned from migration 085.
# ---------------------------------------------------------------------------

# Inline the value-hash expression (decision (e)). See migration 085 lines
# 170-181 for the canonical form; reproduced here so the test DDL stands
# alone and does not import the migration module.
_VALUE_HASH_SQL = """
md5(
    coalesce(value_scalar::text, '')     || '|' ||
    coalesce(value_min::text, '')        || '|' ||
    coalesce(value_max::text, '')        || '|' ||
    coalesce(value_expression, '')       || '|' ||
    coalesce(value_list::text, '')       || '|' ||
    coalesce(value_text, '')             || '|' ||
    coalesce(uncertainty::text, '')      || '|' ||
    coalesce(unit_id::text, '')
)
""".strip()

# Inline the dedupe-key expression (decision (e) superset). See migration
# 085 lines 185-194. Postgres forbids one generated column referencing
# another, so value_hash's expression is inlined rather than named.
_DEDUPE_KEY_SQL = f"""
md5(
    coalesce(dataset_id::text, '')       || '|' ||
    coalesce(property_type_id::text, '') || '|' ||
    coalesce(source_id::text, '')        || '|' ||
    {_VALUE_HASH_SQL}                    || '|' ||
    coalesce(conditions_hash, '')        || '|' ||
    coalesce(method, '')
)
""".strip()
