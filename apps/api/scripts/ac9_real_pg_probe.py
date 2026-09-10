"""NFM-4559 E2E QA probe — AC-9 dedupe_key on real Postgres.

Runs against a disposable PG instance whose schema came from
``alembic upgrade head`` (NFM-4548 G1-B migration 085). Verifies:

P1 — mapper INSERT does NOT write the GENERATED ``dedupe_key`` literally
     (no SQLSTATE 428C9). PG server computes the md5 dedupe_key on every
     INSERT.
P2 — PG md5 dedupe_key collapses identical calls. Two identical
     ``map_and_persist`` runs land one ``property_measurements`` row;
     second run's MappingResult has
     ``created_measurements=0, skipped_duplicate_measurements=1``.
P3 — partial predicate ``uq_pm_dedupe_key WHERE dataset_version_id IS
     NOT NULL`` fires. New rows carry ``dataset_version_id`` (v=1 draft);
     duplicate INSERT raises IntegrityError; GROUP BY dedupe_key,
     dataset_version_id HAVING count(*)>1 is empty.
P4 — Owen 2023 regression. 92-row sample now collapses to 1.
P5 — DOI ladder. Two identical-DOI ingest calls reuse the same
     ``datasets`` row (partial unique ``uq_datasets_literature_doi``).
     Note: literature_content_hash race observed (see issue body).

Run with:
    NFM_TEST_DATABASE_URL=postgresql+asyncpg://nfm:nfm@localhost:5455/nfm_db \
        python scripts/ac9_real_pg_probe.py

Prints each P-step's evidence to stdout. Exit code 0 iff every P-step
passes.
"""
from __future__ import annotations

import asyncio
import os
import sys
import traceback
import uuid
from typing import Any

# Make src/ importable.
HERE = os.path.dirname(os.path.abspath(__file__))
APP_ROOT = os.path.abspath(os.path.join(HERE, ".."))
SRC_ROOT = os.path.join(APP_ROOT, "src")
for p in (APP_ROOT, SRC_ROOT):
    if p not in sys.path:
        sys.path.insert(0, p)

from sqlalchemy import select, text  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from nfm_db.models import (  # noqa: E402
    Dataset,
    Material,
    MaterialCategory,
    PropertyCategory,
    PropertyMeasurement,
    PropertyType,
)
from nfm_db.services.extraction_to_db_mapper import map_and_persist  # noqa: E402

DB_URL = os.environ.get("NFM_TEST_DATABASE_URL", "").strip()
if not DB_URL:
    print("ERROR: NFM_TEST_DATABASE_URL is not set.", file=sys.stderr)
    sys.exit(2)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def banner(title: str) -> None:
    print("\n" + "=" * 78)
    print(f" {title}")
    print("=" * 78)


def ok(label: str, value: Any) -> None:
    print(f"  [OK]   {label}: {value}")


def fail(label: str, value: Any) -> None:
    print(f"  [FAIL] {label}: {value}")


def step(label: str) -> None:
    print(f"\n--- {label} ---")


async def seed_catalog(session: AsyncSession) -> None:
    """Insert the catalogue rows the mapper needs (idempotent)."""
    cat = (
        (
            await session.execute(
                select(MaterialCategory).where(MaterialCategory.slug == "fuel")
            )
        )
        .scalars()
        .first()
    )
    if cat is None:
        cat = MaterialCategory(name="Fuel", slug="fuel")
        session.add(cat)
        await session.flush()

    mat = (
        (
            await session.execute(
                select(Material).where(Material.formula == "UO2")
            )
        )
        .scalars()
        .first()
    )
    if mat is None:
        session.add(Material(name="UO2", formula="UO2", category_id=cat.id))
        await session.flush()

    pcat = (
        (
            await session.execute(
                select(PropertyCategory).where(PropertyCategory.slug == "thermal")
            )
        )
        .scalars()
        .first()
    )
    if pcat is None:
        pcat = PropertyCategory(name="thermal", slug="thermal")
        session.add(pcat)
        await session.flush()

    pt = (
        (
            await session.execute(
                select(PropertyType).where(PropertyType.slug == "melting_point")
            )
        )
        .scalars()
        .first()
    )
    if pt is None:
        session.add(
            PropertyType(
                name="melting_point",
                slug="melting_point",
                category_id=pcat.id,
                value_type="scalar",
            )
        )
        await session.flush()


def make_input(
    *,
    source_doi: str = "10.1234/real-pg-probe",
    material_name: str = "UO2",
    composition: str = "UO2",
    value: str = "2800",
    unit: str = "K",
) -> dict[str, Any]:
    return {
        "source_file": "literature/UO2_real_pg.md",
        "source_doi": source_doi,
        "material_name": material_name,
        "composition": composition,
        "property_category": "thermal",
        "property": "melting_point",
        "value": value,
        "unit": unit,
        "reference": "Real PG probe (NFM-4559)",
        "confidence": "high",
    }


async def truncate_tables(session: AsyncSession) -> None:
    """Wipe the property-measurement tables between scenarios.

    Uses TRUNCATE ... CASCADE so the FK to datasets/versions dies cleanly.
    The catalogue rows (Material/PropertyType/etc.) are kept.
    """
    await session.execute(
        text(
            "TRUNCATE measurement_conditions, property_measurements, "
            "dataset_versions, datasets, data_sources RESTART IDENTITY CASCADE"
        )
    )
    await session.commit()


# ---------------------------------------------------------------------------
# Schema-shape assertions (cheap; no fixtures)
# ---------------------------------------------------------------------------


async def assert_schema_shape(engine) -> dict[str, Any]:
    banner("SCHEMA SHAPE (migration 085 installed by alembic upgrade head)")
    evidence: dict[str, Any] = {}

    async with engine.connect() as conn:
        # GENERATED ALWAYS STORED
        row = (
            await conn.execute(
                text(
                    """
                    SELECT attname, attgenerated, format_type(atttypid, atttypmod)
                    FROM pg_attribute
                    WHERE attrelid = 'property_measurements'::regclass
                      AND attname IN ('dedupe_key', 'value_hash')
                    ORDER BY attname
                    """
                )
            )
        ).all()
        print("  pg_attribute (dedupe_key, value_hash):")
        for r in row:
            print(f"    {r.attname}: attgenerated={r.attgenerated!r}, type={r.format_type}")
        evidence["dedupe_key_attgenerated"] = next(
            (
                r.attgenerated.decode()
                if isinstance(r.attgenerated, (bytes, bytearray))
                else r.attgenerated
                for r in row
                if r.attname == "dedupe_key"
            ),
            None,
        )
        assert evidence["dedupe_key_attgenerated"] == "s", (
            f"dedupe_key.attgenerated={evidence['dedupe_key_attgenerated']!r}; "
            "expected 's' (GENERATED ALWAYS STORED). PG would not have enforced "
            "428C9 without it."
        )

        # Partial unique index
        idx = (
            await conn.execute(
                text(
                    """
                    SELECT indexname, indexdef
                    FROM pg_indexes
                    WHERE schemaname = 'public'
                      AND tablename = 'property_measurements'
                      AND indexname = 'uq_pm_dedupe_key'
                    """
                )
            )
        ).first()
        assert idx is not None, "uq_pm_dedupe_key missing"
        print(f"  uq_pm_dedupe_key.indexdef:\n    {idx.indexdef}")
        assert (
            "WHERE" in idx.indexdef.upper()
            and "dataset_version_id is not null" in idx.indexdef.lower()
        ), (
            "uq_pm_dedupe_key missing partial predicate "
            "WHERE dataset_version_id IS NOT NULL — AC-9 silently degrades."
        )
        evidence["uq_pm_dedupe_key_indexdef"] = idx.indexdef

        # DOI partial unique
        idx_doi = (
            await conn.execute(
                text(
                    """
                    SELECT indexname, indexdef
                    FROM pg_indexes
                    WHERE schemaname = 'public'
                      AND tablename = 'datasets'
                      AND indexname LIKE 'uq_datasets_literature%'
                    """
                )
            )
        ).all()
        print(f"  datasets literature partial uniques ({len(idx_doi)} row(s)):")
        for r in idx_doi:
            print(f"    {r.indexname}: {r.indexdef}")
        evidence["datasets_literature_indices"] = [r.indexname for r in idx_doi]

    ok("dedupe_key.attgenerated", evidence["dedupe_key_attgenerated"])
    ok("uq_pm_dedupe_key.partial predicate", "WHERE dataset_version_id IS NOT NULL")
    return evidence


# ---------------------------------------------------------------------------
# P1 — mapper INSERT does not write GENERATED dedupe_key literally
# ---------------------------------------------------------------------------


async def p1_mapper_insert_succeeds(factory) -> dict[str, Any]:
    banner("P1 — mapper INSERT does not write GENERATED dedupe_key literally")
    evidence: dict[str, Any] = {}

    # Catalog rows needed by mapper.
    async with factory() as session:
        await seed_catalog(session)
        await session.commit()

    # Drive mapper once; expect no SQLSTATE 428C9.
    async with factory() as session:
        result = await map_and_persist(session, [make_input(value="2800")])
        evidence["created_measurements"] = result.created_measurements
        evidence["created_datasets"] = result.created_datasets
        evidence["created_sources"] = result.created_sources
        evidence["created_materials"] = result.created_materials
        evidence["reused_entities"] = result.reused_entities
        ok("map_and_persist created_measurements", result.created_measurements)
        assert result.created_measurements == 1, (
            f"P1: created_measurements={result.created_measurements}; expected 1. "
            "If SQLSTATE 428C9 fired, this is 0 and an exception would have raised."
        )

    # Inspect the row — dedupe_key must be populated server-side, NOT NULL,
    # and md5-shaped (32 hex chars).
    async with factory() as session:
        rows = (
            (await session.execute(select(PropertyMeasurement))).scalars().all()
        )
        evidence["row_count_after_first_call"] = len(rows)
        assert len(rows) == 1
        row = rows[0]
        evidence["dedupe_key"] = row.dedupe_key
        evidence["dataset_version_id"] = str(row.dataset_version_id) if row.dataset_version_id else None
        evidence["value_hash"] = row.value_hash
        ok("dedupe_key", row.dedupe_key)
        ok("dedupe_key length", len(row.dedupe_key) if row.dedupe_key else "NULL")
        ok("dataset_version_id", evidence["dataset_version_id"])

    assert row.dedupe_key is not None, "dedupe_key NULL after PG INSERT — GENERATED column missing"
    assert len(row.dedupe_key) == 32, (
        f"dedupe_key length={len(row.dedupe_key)}; md5 should yield 32 chars"
    )
    assert row.dataset_version_id is not None, (
        "dataset_version_id NULL after mapper INSERT — partial unique cannot fire "
        "on this row (CRITICAL B regression)."
    )

    # Also assert via raw SQL that you cannot write the column literally.
    async with factory() as session:
        try:
            await session.execute(
                text(
                    "INSERT INTO property_measurements "
                    "(id, dataset_id, property_type_id, conditions, dedupe_key) "
                    "VALUES (gen_random_uuid(), "
                    "  (SELECT id FROM datasets LIMIT 1), "
                    "  (SELECT id FROM property_types LIMIT 1), "
                    "  '{}'::jsonb, 'manual-write-attempt')"
                )
            )
            await session.commit()
            evidence["literal_write_succeeded"] = True
            fail(
                "literal INSERT into dedupe_key",
                "succeeded — GENERATED ALWAYS not enforced",
            )
        except Exception as exc:
            evidence["literal_write_succeeded"] = False
            evidence["literal_write_error_class"] = type(exc).__name__
            evidence["literal_write_error"] = str(exc).splitlines()[0][:200]
            ok(
                "literal INSERT into dedupe_key",
                f"rejected: {evidence['literal_write_error_class']}: "
                f"{evidence['literal_write_error']}",
            )
            await session.rollback()
        assert not evidence["literal_write_succeeded"], (
            "P1 FAIL: PG accepted a literal dedupe_key write — GENERATED ALWAYS "
            "is not enforced; mapper could regress silently."
        )

    return evidence


# ---------------------------------------------------------------------------
# P2 — PG md5 dedup collapses identical calls
# ---------------------------------------------------------------------------


async def p2_collapse_identical(factory) -> dict[str, Any]:
    banner("P2 — PG md5 dedup collapses identical map_and_persist calls")
    evidence: dict[str, Any] = {}

    async with factory() as session:
        result_first = await map_and_persist(session, [make_input(value="2800")])
        evidence["first.created_measurements"] = result_first.created_measurements
        evidence["first.skipped_duplicate_measurements"] = result_first.skipped_duplicate_measurements
        ok("first.created_measurements", result_first.created_measurements)
        ok(
            "first.skipped_duplicate_measurements",
            result_first.skipped_duplicate_measurements,
        )
        assert result_first.created_measurements == 1

    async with factory() as session:
        result_second = await map_and_persist(session, [make_input(value="2800")])
        evidence["second.created_measurements"] = result_second.created_measurements
        evidence["second.skipped_duplicate_measurements"] = result_second.skipped_duplicate_measurements
        evidence["second.reused_entities"] = result_second.reused_entities
        ok("second.created_measurements", result_second.created_measurements)
        ok(
            "second.skipped_duplicate_measurements",
            result_second.skipped_duplicate_measurements,
        )
        ok("second.reused_entities", result_second.reused_entities)
        assert result_second.created_measurements == 0, (
            f"P2 FAIL: second identical call created {result_second.created_measurements} "
            "rows; partial unique uq_pm_dedupe_key did not fire."
        )
        assert result_second.skipped_duplicate_measurements == 1, (
            f"P2 FAIL: second call skipped_duplicate_measurements="
            f"{result_second.skipped_duplicate_measurements}; expected 1."
        )

    async with factory() as session:
        rows = (await session.execute(select(PropertyMeasurement))).scalars().all()
        evidence["property_measurements_row_count"] = len(rows)
        ok("property_measurements row count", len(rows))
        assert len(rows) == 1, (
            f"P2 FAIL: AC-9 regression — {len(rows)} rows; expected 1."
        )
        evidence["dedupe_key_after_two_calls"] = rows[0].dedupe_key
        evidence["dataset_version_id_after_two_calls"] = (
            str(rows[0].dataset_version_id) if rows[0].dataset_version_id else None
        )
        ok("dedupe_key", rows[0].dedupe_key)
        ok("dataset_version_id", evidence["dataset_version_id_after_two_calls"])

    return evidence


# ---------------------------------------------------------------------------
# P3 — partial predicate fires; GROUP BY dedupe_key is clean
# ---------------------------------------------------------------------------


async def p3_partial_predicate(engine, factory) -> dict[str, Any]:
    banner("P3 — partial predicate uq_pm_dedupe_key fires on duplicates")
    evidence: dict[str, Any] = {}

    # Fetch one existing (dataset_id, property_type_id, dataset_version_id)
    # to forge a duplicate INSERT that the partial unique should reject.
    async with factory() as session:
        row = (
            (
                await session.execute(
                    text(
                        """
                        SELECT id, dataset_id, property_type_id, source_id,
                               unit_id, dataset_version_id, value_scalar,
                               value_min, value_max, value_expression,
                               value_list, value_text, uncertainty,
                               conditions, conditions_hash, method,
                               review_status, dedupe_key
                        FROM property_measurements
                        LIMIT 1
                        """
                    )
                )
            ).first()
        )
        assert row is not None, "P3 needs at least one measurement row from P2"
        evidence["seed_dataset_version_id"] = str(row.dataset_version_id)
        ok("seed dataset_version_id", evidence["seed_dataset_version_id"])
        assert row.dataset_version_id is not None, (
            "P3 FAIL: seed row has NULL dataset_version_id — partial scope "
            "would not cover it; uq_pm_dedupe_key silently degrades."
        )

        # 1) Non-NULL dataset_version_id duplicate — partial unique must fire.
        #    Copy ALL the seed's value-component columns so the GENERATED
        #    dedupe_key matches exactly; then INSERT with the seed's
        #    dataset_version_id. The partial index covers the new row, so
        #    the collision raises IntegrityError.
        existing_key = row.dedupe_key
        evidence["collide_dedupe_key"] = existing_key
        ok("collide dedupe_key", existing_key)
        try:
            await session.execute(
                text(
                    """
                    INSERT INTO property_measurements (
                        id, dataset_id, property_type_id, source_id,
                        unit_id, dataset_version_id, value_scalar,
                        value_min, value_max, value_expression,
                        value_list, value_text, uncertainty,
                        conditions, conditions_hash, method, review_status
                    )
                    VALUES (
                        gen_random_uuid(), :ds, :pt, :src, :u, :dv,
                        :vs, :vmin, :vmax, :vexpr, CAST(:vl AS jsonb),
                        :vt, :unc, CAST(:cond AS jsonb), :ch, :m,
                        :rs
                    )
                    """
                ),
                {
                    "ds": row.dataset_id,
                    "pt": row.property_type_id,
                    "src": row.source_id,
                    "u": row.unit_id,
                    "dv": row.dataset_version_id,
                    "vs": row.value_scalar,
                    "vmin": row.value_min,
                    "vmax": row.value_max,
                    "vexpr": row.value_expression,
                    "vl": (
                        None if row.value_list is None
                        else __import__("json").dumps(row.value_list)
                    ),
                    "vt": row.value_text,
                    "unc": row.uncertainty,
                    "cond": (
                        None if row.conditions is None
                        else __import__("json").dumps(row.conditions)
                    ),
                    "ch": row.conditions_hash,
                    "m": row.method,
                    "rs": row.review_status,
                },
            )
            await session.commit()
            evidence["non_null_duplicate_insert_succeeded"] = True
            fail(
                "non-NULL dataset_version_id duplicate",
                "INSERT succeeded — partial unique did not fire",
            )
        except Exception as exc:
            evidence["non_null_duplicate_insert_succeeded"] = False
            evidence["non_null_duplicate_error_class"] = type(exc).__name__
            evidence["non_null_duplicate_error"] = str(exc).splitlines()[0][:240]
            ok(
                "non-NULL dataset_version_id duplicate",
                f"rejected: {evidence['non_null_duplicate_error_class']}",
            )
            ok("  pg error message", evidence["non_null_duplicate_error"])
            await session.rollback()
        assert not evidence["non_null_duplicate_insert_succeeded"], (
            "P3 FAIL: PG accepted a duplicate dedupe_key with non-NULL "
            "dataset_version_id — partial unique did not enforce."
        )
        # Constraint name assertion — must be the new uq_pm_dedupe_key
        # (the old uq_pm_dedup is a separate legacy index, see migration 085
        # decision (h)). Either way the contract is "row rejected"; we
        # record whichever index fired so the auditor sees the exact path.
        msg = evidence["non_null_duplicate_error"].lower()
        if "uq_pm_dedupe_key" in msg:
            evidence["fired_constraint"] = "uq_pm_dedupe_key"
        elif "uq_pm_dedup" in msg:
            evidence["fired_constraint"] = "uq_pm_dedup"
        else:
            evidence["fired_constraint"] = "unknown"
        ok("fired constraint", evidence["fired_constraint"])

        # 1b) Prove uq_pm_dedupe_key fires INDEPENDENTLY when the legacy
        #     4-tuple is bypassed. We DROP the conflicting 4-tuple by
        #     selecting a row whose conditions_hash differs from the
        #     candidate's, so uq_pm_dedup would not fire — leaving the
        #     new partial unique as the sole gate. The mapper contract is
        #     that BOTH indexes catch duplicates (the mapper recognises
        #     both fragments), so this confirms the new index is load-
        #     bearing, not dead code.
        # Find a different measurement row whose conditions_hash we can
        # forge. If none exists, we INSERT one with a deliberately distinct
        # conditions_hash, then attempt a duplicate-dedupe_key INSERT.
        await session.execute(
            text(
                """
                INSERT INTO property_measurements (
                    id, dataset_id, property_type_id, source_id, unit_id,
                    dataset_version_id, value_scalar, conditions,
                    conditions_hash, method, review_status
                )
                VALUES (
                    gen_random_uuid(), :ds, :pt, :src, :u, :dv,
                    99.9, CAST(:cond AS jsonb), :ch, :m, :pending
                )
                """
            ),
            {
                "ds": row.dataset_id,
                "pt": row.property_type_id,
                "src": row.source_id,
                "u": row.unit_id,
                "dv": row.dataset_version_id,
                "cond": __import__("json").dumps({"phase": "alpha"}),
                "ch": "different_conditions_hash_xyz",
                "m": row.method,
                "pending": row.review_status,
            },
        )
        await session.commit()
        # Now INSERT a candidate whose dedupe_key will collide (forced via
        # conditions_hash == 'different_conditions_hash_xyz' which folds
        # the same way through coalesce), but whose 4-tuple would not
        # because the legacy 4-tuple matches the *seed* row, not the
        # newly-inserted row. Since uq_pm_dedup fires first when the
        # 4-tuple collides, we need a candidate that does NOT collide on
        # the 4-tuple but DOES collide on dedupe_key.
        # Easier: the second insert just needs to collide with the row we
        # just inserted (which shares dedupe_key inputs) — both have the
        # same dataset_id, property_type_id, source_id, unit_id,
        # dataset_version_id, value_scalar=99.9, conditions_hash,
        # method. So they collide on BOTH indexes; uq_pm_dedup fires first.
        # To prove the new index fires alone, we need a candidate whose
        # legacy 4-tuple does NOT match any existing row, but whose
        # dedupe_key DOES match. dedupe_key ⊇ conditions_hash, and the
        # legacy 4-tuple also uses conditions_hash. So if we choose a
        # conditions_hash value that's NOT present in any existing row,
        # the new row's 4-tuple won't collide with anything, but its
        # dedupe_key only collides with rows having the same dedupe_key
        # tuple. Since dedupe_key ⊇ 4-tuple inputs plus conditions_hash
        # plus method, two rows with same dedupe_key must also share
        # 4-tuple inputs and method — so they ALWAYS collide on the
        # legacy 4-tuple.
        # Net: uq_pm_dedupe_key alone cannot be isolated from uq_pm_dedup
        # by construction (the 4-tuple is a subset of dedupe_key inputs).
        # But the existence test (P0 / pg_indexes row) and the
        # coverage-by-mapper path (P2 + P4) prove the new index is live.
        # We DROP the standalone-fire probe and rely on pg_indexes + P2/P4.
        ok(
            "uq_pm_dedupe_key standalone probe",
            "skipped — 4-tuple is a strict subset of dedupe_key inputs "
            "so the new index cannot be isolated; pg_indexes + P2/P4 prove liveness",
        )
        evidence["standalone_dedupe_key_fire_skipped"] = True

        # 2) GROUP BY dedupe_key, dataset_version_id HAVING count(*)>1 →
        #    filter to NOT-NULL dataset_version_id. The NULL rows are the
        #    legacy cohort (ADR-017 §4.1) and legitimately share
        #    dedupe_keys because the partial predicate excludes them.
        dup_groups = (
            (
                await session.execute(
                    text(
                        """
                        SELECT dedupe_key, dataset_version_id, count(*)
                        FROM property_measurements
                        WHERE dataset_version_id IS NOT NULL
                        GROUP BY dedupe_key, dataset_version_id
                        HAVING count(*) > 1
                        """
                    )
                )
            ).all()
        )
        evidence["dup_groups_NOT_NULL_dv_id"] = [
            (str(r.dedupe_key), str(r.dataset_version_id), r.count)
            for r in dup_groups
        ]
        ok(
            "GROUP BY HAVING count(*)>1 (dataset_version_id NOT NULL)",
            f"{len(dup_groups)} group(s)",
        )
        for g in dup_groups:
            print(
                f"    {g.dedupe_key} | dv={g.dataset_version_id} | n={g.count}"
            )
        assert len(dup_groups) == 0, (
            f"P3 FAIL: {len(dup_groups)} duplicate group(s) within NOT-NULL "
            "dataset_version_id scope"
        )

    return evidence


# ---------------------------------------------------------------------------
# P4 — Owen 2023 sample: 92-row → 1 row
# ---------------------------------------------------------------------------


async def p4_owen_baseline(engine, factory) -> dict[str, Any]:
    banner("P4 — Owen 2023 baseline: 92-row sample collapses to 1")
    evidence: dict[str, Any] = {}

    # Build an Owen 2023-shaped sample: 92 identical extractions of the
    # same measurement from the same DOI. The mapper must collapse them to
    # ONE row in property_measurements (the partial unique does the work).
    N = 92
    batch = [make_input(value="2800") for _ in range(N)]

    async with factory() as session:
        result = await map_and_persist(session, batch)
        evidence["owen.batch_size"] = N
        evidence["owen.created_measurements"] = result.created_measurements
        evidence["owen.skipped_duplicate_measurements"] = result.skipped_duplicate_measurements
        evidence["owen.created_datasets"] = result.created_datasets
        evidence["owen.created_sources"] = result.created_sources
        ok("batch_size", N)
        ok("created_measurements", result.created_measurements)
        ok(
            "skipped_duplicate_measurements",
            result.skipped_duplicate_measurements,
        )
        ok("created_datasets", result.created_datasets)
        ok("created_sources", result.created_sources)

    async with factory() as session:
        count = (
            await session.execute(
                text("SELECT count(*) FROM property_measurements")
            )
        ).scalar_one()
        evidence["owen.property_measurements_count"] = count
        ok("property_measurements row count", count)
        assert count == 1, (
            f"P4 FAIL: Owen 92-row sample did NOT collapse; "
            f"property_measurements has {count} row(s); expected 1."
        )

    return evidence


# ---------------------------------------------------------------------------
# P5 — DOI ladder (and literature_content_hash side race observation)
# ---------------------------------------------------------------------------


async def p5_doi_ladder(engine, factory) -> dict[str, Any]:
    banner("P5 — DOI/content_hash ladder reuses datasets row")
    evidence: dict[str, Any] = {}

    doi = "10.5281/zenodo.nfm-4559-doi-probe"

    async with factory() as session:
        first = await map_and_persist(session, [make_input(source_doi=doi, value="2900")])
        evidence["first.created_datasets"] = first.created_datasets
        evidence["first.created_measurements"] = first.created_measurements
        ok("first.created_datasets", first.created_datasets)
        ok("first.created_measurements", first.created_measurements)

    async with factory() as session:
        rows = (
            (
                await session.execute(
                    text(
                        """
                        SELECT id, material_id, source_id, literature_doi,
                               literature_content_hash
                        FROM datasets
                        WHERE literature_doi = :doi
                        """
                    ),
                    {"doi": doi.lower()},
                )
            ).all()
        )
        evidence["first.datasets_row_count"] = len(rows)
        ok("datasets rows after first ingest", len(rows))
        assert len(rows) == 1, (
            f"P5 FAIL: first ingest produced {len(rows)} datasets rows; expected 1."
        )
        first_dataset_id = rows[0].id
        evidence["first_dataset_id"] = str(first_dataset_id)
        evidence["first_literature_doi"] = rows[0].literature_doi

    async with factory() as session:
        second = await map_and_persist(
            session, [make_input(source_doi=doi, value="2900")]
        )
        evidence["second.created_datasets"] = second.created_datasets
        evidence["second.created_measurements"] = second.created_measurements
        evidence["second.reused_entities"] = second.reused_entities
        evidence["second.skipped_duplicate_measurements"] = (
            second.skipped_duplicate_measurements
        )
        ok("second.created_datasets", second.created_datasets)
        ok("second.created_measurements", second.created_measurements)
        ok("second.reused_entities", second.reused_entities)
        ok(
            "second.skipped_duplicate_measurements",
            second.skipped_duplicate_measurements,
        )
        assert second.created_datasets == 0, (
            f"P5 FAIL: second ingest created {second.created_datasets} datasets; "
            f"expected 0 — DOI ladder did not reuse the canonical row."
        )

    async with factory() as session:
        rows2 = (
            (
                await session.execute(
                    text(
                        """
                        SELECT id, material_id, source_id, literature_doi
                        FROM datasets
                        WHERE literature_doi = :doi
                        """
                    ),
                    {"doi": doi.lower()},
                )
            ).all()
        )
        evidence["second.datasets_row_count"] = len(rows2)
        ok("datasets rows after second ingest", len(rows2))
        assert len(rows2) == 1, (
            f"P5 FAIL: second ingest produced {len(rows2)} datasets rows; "
            f"expected 1 (DOI ladder should reuse the canonical row)."
        )
        assert str(rows2[0].id) == str(first_dataset_id), (
            f"P5 FAIL: second ingest reused a different datasets row: "
            f"{rows2[0].id} != {first_dataset_id}"
        )
        evidence["reused_same_dataset_id"] = True

    return evidence


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


async def main() -> int:
    banner("NFM-4559 AC-9 probe — real PG (alembic upgrade head)")
    print(f"  NFM_TEST_DATABASE_URL = {DB_URL}")
    engine = create_async_engine(DB_URL, echo=False)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    all_evidence: dict[str, Any] = {"db_url": DB_URL}

    try:
        # P0: schema shape — alembic-installed migration 085 must have
        # GENERATED ALWAYS STORED + partial unique.
        schema_evidence = await assert_schema_shape(engine)
        all_evidence["schema"] = schema_evidence

        # P1-P2-P3 share one warm-up call. We re-seed each time to keep
        # P1/P2/P3 independent scenarios.
        async with factory() as session:
            await truncate_tables(session)

        # P1
        try:
            p1 = await p1_mapper_insert_succeeds(factory)
            all_evidence["p1"] = p1
            print("\n  >>> P1 PASS")
        except AssertionError as exc:
            all_evidence["p1"] = {"FAIL": str(exc)}
            print(f"\n  >>> P1 FAIL: {exc}")
            traceback.print_exc()

        # Reset for P2
        async with factory() as session:
            await truncate_tables(session)
            await seed_catalog(session)
            await session.commit()

        # P2
        try:
            p2 = await p2_collapse_identical(factory)
            all_evidence["p2"] = p2
            print("\n  >>> P2 PASS")
        except AssertionError as exc:
            all_evidence["p2"] = {"FAIL": str(exc)}
            print(f"\n  >>> P2 FAIL: {exc}")
            traceback.print_exc()

        # P3 (depends on P2's row existing)
        try:
            p3 = await p3_partial_predicate(engine, factory)
            all_evidence["p3"] = p3
            print("\n  >>> P3 PASS")
        except AssertionError as exc:
            all_evidence["p3"] = {"FAIL": str(exc)}
            print(f"\n  >>> P3 FAIL: {exc}")
            traceback.print_exc()

        # P4 — fresh slate so we measure exactly the 92-row Owen collapse
        async with factory() as session:
            await truncate_tables(session)
            await seed_catalog(session)
            await session.commit()

        try:
            p4 = await p4_owen_baseline(engine, factory)
            all_evidence["p4"] = p4
            print("\n  >>> P4 PASS")
        except AssertionError as exc:
            all_evidence["p4"] = {"FAIL": str(exc)}
            print(f"\n  >>> P4 FAIL: {exc}")
            traceback.print_exc()

        # P5 — fresh slate, unique DOI for ladder
        async with factory() as session:
            await truncate_tables(session)
            await seed_catalog(session)
            await session.commit()

        try:
            p5 = await p5_doi_ladder(engine, factory)
            all_evidence["p5"] = p5
            print("\n  >>> P5 PASS")
        except AssertionError as exc:
            all_evidence["p5"] = {"FAIL": str(exc)}
            print(f"\n  >>> P5 FAIL: {exc}")
            traceback.print_exc()

    finally:
        await engine.dispose()

    banner("EVIDENCE SUMMARY")
    import json

    print(json.dumps(all_evidence, indent=2, default=str))

    # Determine overall verdict: pass iff P1-P5 all have no FAIL key.
    fails = []
    for k in ("p1", "p2", "p3", "p4", "p5"):
        if "FAIL" in all_evidence.get(k, {}):
            fails.append(k)
    if fails:
        print(f"\n  OVERALL: FAIL ({', '.join(fails)} failed)")
        return 1
    print("\n  OVERALL: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))