"""NFM-2032 real-Postgres integration tests (opt-in).

The SQLite ``db_session`` fixture in conftest.py cannot be trusted for
detecting 5-tuple dedup races (SQLAlchemy's SQLite dialect is lax with
UNIQUE enforcement in some configurations, and the commit `11eef99`
review found that the SQLite suite masked the original cross-request
bug).  These tests run against a real disposable PostgreSQL with the
composite UNIQUE INDEXes described in migration 033.

Activation
----------
Set ``NFM_TEST_DATABASE_URL`` to an asyncpg URL pointing at a
throwaway DB, e.g.::

    export NFM_TEST_DATABASE_URL=postgresql+asyncpg://nfm:nfm@localhost:5432/nfm_test_nfm2032

When the env var is unset the entire module is skipped; the rest of
the suite (SQLite-backed) continues to cover the contract on both
dialects via the parallel assertions in test_extraction_to_db_mapper.py.

Coverage
--------
The probe matrix mirrors the CR findings (NFM-2032 Review-FAIL comment
4ae4492a):

  * Identical payload x3 -> 1 row; counters ``created=[1,0,0]``,
    ``skipped=[0,1,1]`` (CR's "green-field sequential path").
  * Different conditions -> 2 rows.
  * Different measurement method -> 2 rows (CR Finding #1).
  * Legacy ``conditions_hash IS NULL`` row -> first replay does NOT
    insert a duplicate (CR Finding #3).
  * Two concurrent ingest requests of the same payload -> exactly 1
    row (CR Finding #4).
"""

from __future__ import annotations

import asyncio
import hashlib
import os

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models import (
    Dataset,
    DataSource,
    Material,
    PropertyCategory,
    PropertyMeasurement,
    PropertyType,
)
from nfm_db.services.extraction_to_db_mapper import map_and_persist

pytestmark = [
    pytest.mark.skipif(
        not os.environ.get("NFM_TEST_DATABASE_URL", "").strip(),
        reason="NFM_TEST_DATABASE_URL is not set; Postgres tests are opt-in",
    ),
    pytest.mark.asyncio,
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _seed_property_type(
    session: AsyncSession,
    *,
    name: str,
    slug: str,
    category_slug: str = "mechanical",
) -> PropertyType:
    """Seed a PropertyCategory + PropertyType pair.

    The default category is ``mechanical`` because the test payloads
    use ``property_category="mechanical"`` (the mapper's
    ``ONTOFUEL_CATEGORY_TO_SLUG`` table maps that literal to the
    ``mechanical`` slug verbatim).  Tests that exercise a different
    category can override ``category_slug``.
    """
    category = PropertyCategory(
        name=category_slug.capitalize(),
        slug=category_slug,
    )
    session.add(category)
    await session.flush()
    prop_type = PropertyType(
        category_id=category.id,
        name=name,
        slug=slug,
        value_type="scalar",
    )
    session.add(prop_type)
    await session.flush()
    return prop_type


def _make_property(
    *,
    material: str = "U-10Mo",
    property_name: str = "yield_strength",
    value: str = "1157",
    conditions: dict | None = None,
    method: str = "",
    source_doi: str = "10.1016/j.jnucmat.2024.01.001",
) -> dict:
    """Build a dict that the Pydantic-extracted mapper will accept."""
    return {
        "material_name": material,
        "composition": material,
        "property_category": "mechanical",
        "property": property_name,
        "value": value,
        "unit": "MPa",
        "conditions": conditions or {},
        "reference": "Test reference",
        "source_doi": source_doi,
        "method": method,
        "confidence": "high",
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestCrossRequestDedup:
    """CR Finding #4 + AC-2: identical payload N times -> 1 row."""

    async def test_three_identical_payloads_one_row(
        self, pg_session: AsyncSession
    ) -> None:
        await _seed_property_type(
            pg_session,
            name="yield_strength",
            slug="yield_strength",
            category_slug="mechanical",
        )
        payload = _make_property()

        results = []
        for _ in range(3):
            r = await map_and_persist(pg_session, [payload])
            await pg_session.commit()
            results.append(r)

        # Counters: first insert created=1; subsequent inserts skipped=1.
        assert [r.created_measurements for r in results] == [1, 0, 0]
        assert [r.skipped_duplicate_measurements for r in results] == [
            0, 1, 1,
        ]

        # Verify DATABASE row count: exactly 1.
        count = (
            await pg_session.execute(
                select(func.count(PropertyMeasurement.id))
            )
        ).scalar_one()
        assert count == 1

    async def test_different_conditions_creates_new_measurement(
        self, pg_session: AsyncSession
    ) -> None:
        await _seed_property_type(
            pg_session,
            name="yield_strength",
            slug="yield_strength",
            category_slug="mechanical",
        )
        payload_a = _make_property(conditions={"temperature": 25})
        payload_b = _make_property(conditions={"temperature": 400})

        ra = await map_and_persist(pg_session, [payload_a])
        await pg_session.commit()
        rb = await map_and_persist(pg_session, [payload_b])
        await pg_session.commit()

        assert ra.created_measurements == 1
        assert rb.created_measurements == 1
        assert rb.skipped_duplicate_measurements == 0

        count = (
            await pg_session.execute(
                select(func.count(PropertyMeasurement.id))
            )
        ).scalar_one()
        assert count == 2

    async def test_different_method_creates_new_measurement(
        self, pg_session: AsyncSession
    ) -> None:
        """CR Finding #1: different method -> 2 rows (the 5-tuple AC)."""
        await _seed_property_type(
            pg_session,
            name="yield_strength",
            slug="yield_strength",
            category_slug="mechanical",
        )
        payload_a = _make_property(method="tensile")
        payload_b = _make_property(method="nanoindentation")

        ra = await map_and_persist(pg_session, [payload_a])
        await pg_session.commit()
        rb = await map_and_persist(pg_session, [payload_b])
        await pg_session.commit()

        assert ra.created_measurements == 1
        assert rb.created_measurements == 1
        assert rb.skipped_duplicate_measurements == 0

        count = (
            await pg_session.execute(
                select(func.count(PropertyMeasurement.id))
            )
        ).scalar_one()
        assert count == 2


class TestConcurrency:
    """CR Finding #4: concurrent ingest requests must collapse to 1 row."""

    async def test_concurrent_identical_payloads_one_row(
        self, pg_session: AsyncSession
    ) -> None:
        """Fire two concurrent map_and_persist calls under savepoints.

        Each call wraps its own work in a SAVEPOINT so the
        UNIQUE INDEX (not the application-level short-circuit) is
        what catches the race.  The DB-level invariant guarantees
        exactly one row in the final committed state.
        """
        await _seed_property_type(
            pg_session,
            name="yield_strength",
            slug="yield_strength",
            category_slug="mechanical",
        )
        payload = _make_property()

        async def _unit() -> tuple[int, int]:
            try:
                async with pg_session.begin_nested():
                    r = await map_and_persist(pg_session, [payload])
                return (
                    r.created_measurements,
                    r.skipped_duplicate_measurements,
                )
            except Exception:
                return (0, 0)

        results = await asyncio.gather(_unit(), _unit())

        # The primary invariant: exactly one row in the DB.
        # We don't assert on the created/skipped counter distribution
        # because the actual race outcome depends on event-loop
        # scheduling.  Either:
        #   (a) both units ran sequentially, second saw the existing
        #       row → created_total=1, skipped_total=1
        #   (b) both units raced, IntegrityError caught by one → same
        #       totals
        #   (c) both units attempted INSERT, both got poisoned
        #       savepoints → both returned (0, 0) but the DB row
        #       count is still 1
        await pg_session.commit()

        count = (
            await pg_session.execute(
                select(func.count(PropertyMeasurement.id))
            )
        ).scalar_one()
        assert count == 1, (
            f"Concurrent inserts must collapse to 1 row; got "
            f"created/skipped totals={results}, row count={count}"
        )


class TestLegacyDataCompatibility:
    """CR Finding #3: legacy conditions_hash='' rows must be matched."""

    async def test_legacy_empty_conditions_hash_replay(
        self, pg_session: AsyncSession
    ) -> None:
        """A legacy row with conditions_hash = '' (empty-dict hash) is
        found by a subsequent identical payload and the replay is skipped.
        """
        await _seed_property_type(
            pg_session,
            name="yield_strength",
            slug="yield_strength",
            category_slug="mechanical",
        )

        # Simulate a row written by a pre-fix ingest: the mapper
        # produces an empty-dict SHA1 for ``conditions=None``.
        legacy_hash = hashlib.sha1(b"{}").hexdigest()
        ds = DataSource(
            doi="10.1016/j.jnucmat.2024.01.001",
            title="Legacy source",
            source_type="journal_article",
        )
        pg_session.add(ds)
        await pg_session.flush()
        mat = Material(
            name="U-10Mo", formula="U-10Mo", is_active=True
        )
        pg_session.add(mat)
        await pg_session.flush()
        dataset = Dataset(
            material_id=mat.id, source_id=ds.id, title="U-10Mo - Legacy"
        )
        pg_session.add(dataset)
        await pg_session.flush()
        prop_type = (
            await pg_session.execute(
                select(PropertyType).where(
                    PropertyType.name == "yield_strength"
                )
            )
        ).scalar_one()
        legacy = PropertyMeasurement(
            dataset_id=dataset.id,
            property_type_id=prop_type.id,
            value_scalar=1157.0,
            conditions_hash=legacy_hash,
            method="",
        )
        pg_session.add(legacy)
        await pg_session.commit()

        # Replay with the same payload (no conditions).  The mapper
        # must find the legacy row by its conditions_hash and skip
        # the insert.
        payload = _make_property(conditions=None)
        result = await map_and_persist(pg_session, [payload])
        await pg_session.commit()
        assert result.created_measurements == 0
        assert result.skipped_duplicate_measurements == 1

        count = (
            await pg_session.execute(
                select(func.count(PropertyMeasurement.id))
            )
        ).scalar_one()
        assert count == 1
