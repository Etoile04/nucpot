"""NFM-4555 — G1-G Owen 2023 重抽试点(新数据集 + 校核 + 合并 + 存量对照).

Drives the four-step pilot against a disposable PG instance whose schema
came from ``alembic upgrade head`` (NFM-4548 G1-B migration 085 + NFM-4549
G1-C mapper). Verifies:

P1 — 新数据集 (per-literature private dataset, ADR-017 §2.1):
     ``map_and_persist`` with the Owen 2023 DOI mints ONE ``datasets``
     row carrying ``literature_doi='10.1234/owen-2023-amorphous-uo2'``
     (normalised) and a fresh ``dataset_versions v1 'draft'``.

P2 — 校核 (G1-A skill path stub, spec §5):
     A second ``map_and_persist`` call with the G1-A skill-adapter
     output shape (13→20 field adapter payload) lands new measurements
     stamped with ``dataset_version_id`` so the partial unique
     ``uq_pm_dedupe_key`` actually fires.

P3 — 合并 (ADR-017 §2.2 snapshot merge):
     Two ``map_and_persist`` calls with identical inputs collapse to
     ONE row; the duplicate INSERT is rejected by
     ``uq_pm_dedupe_key``; ``GROUP BY dedupe_key HAVING count(*)>1``
     on rows where ``dataset_version_id IS NOT NULL`` is empty.

P4 — 存量对照 (migration 085 decision (g)):
     92 legacy rows stamped with ``dataset_version_id=NULL`` survive
     the pilot untouched. A second identical ``map_and_persist`` call
     on the new path creates ZERO new rows because the partial unique
     index is scoped to rows where ``dataset_version_id IS NOT NULL``,
     and the legacy 92 are out of that scope. This is the
     "存量 92 行保留对照" guarantee from spec §5 + ADR-017 §4.1.

P5 — AC-2 (Calhoun/Zhu 数值域外):
     A 0-row ``map_and_persist`` call (no numeric extractions) leaves
     ``property_measurements`` empty for the new dataset. This is the
     "qualitative facts enter KG, not property_measurements" guarantee
     from spec §6 — the pipeline does not invent numeric rows where
     none exist.

The script is a **pilot recipe** — it demonstrates the pattern RE/SRE
will run on the integrated G1-A+B+C+D code at NFM-4556 merge time. It
exercises steps 1, 3, 4 against G1-B/G1-C code (already in main); step
2 is a stub that documents the G1-A skill-adapter call site.

Run with:
    NFM_TEST_DATABASE_URL=postgresql+asyncpg://nfm:nfm@localhost:5455/nfm_db \
        python scripts/nfm-4555-owen2023-pilot.py

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
    DataSource,
    Dataset,
    DatasetVersion,
    Material,
    MaterialCategory,
    PropertyCategory,
    PropertyType,
)
from nfm_db.services.extraction_to_db_mapper import map_and_persist  # noqa: E402

DB_URL = os.environ.get("NFM_TEST_DATABASE_URL", "").strip()
if not DB_URL:
    print("ERROR: NFM_TEST_DATABASE_URL is not set.", file=sys.stderr)
    sys.exit(2)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Owen 2023 dataset slug — spec §5 names this exact slug.
OWEN2023_DATASET_SLUG = "Owen2023-amorphous-UO2"
#: Owen 2023 DOI placeholder for the pilot.  Real DOI comes from
#: ``data_sources.doi`` after RE pulls the production source via the
#: integration ticket (NFM-4556).
OWEN2023_DOI = "10.5281/zenodo.nfm-4555-owen2023-pilot"
#: Number of legacy rows the spec mandates retain as a comparison cohort
#: (spec §5, ADR-017 §4.1, migration 085 decision (g)).
LEGACY_OWEN_ROWS = 92


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
    source_doi: str = OWEN2023_DOI,
    material_name: str = "UO2",
    composition: str = "UO2",
    value: str = "2800",
    unit: str = "K",
    conditions: dict[str, Any] | None = None,
    method: str = "",
) -> dict[str, Any]:
    """Build a single extraction item shaped like the G1-A skill-adapter
    output (13→20 field adapter payload, spec §3.2).

    ``conditions`` and ``method`` are forwarded to the mapper's
    ``conditions_hash`` so callers can build rows that share value /
    unit / source but differ in measurement conditions (the
    ADR-017 §2.6 "有条件差异保留各行" rule)."""
    return {
        "source_file": "literature/Owen2023-amorphous-UO2.md",
        "source_doi": source_doi,
        "material_name": material_name,
        "composition": composition,
        "property_category": "thermal",
        "property": "melting_point",
        "value": value,
        "unit": unit,
        "reference": "Owen et al. 2023, amorphous UO2 (NFM-4555 pilot)",
        "confidence": "high",
        "conditions": dict(conditions or {}),
        "method": method,
    }


async def truncate_tables(session: AsyncSession) -> None:
    """Wipe the property-measurement tables between scenarios.

    Idempotent — uses ``TRUNCATE ... RESTART IDENTITY CASCADE`` so the
    test never leaves a half-wiped schema.
    """
    await session.execute(
        text(
            "TRUNCATE TABLE property_measurements, measurement_conditions, "
            "datasets, dataset_versions, data_sources, materials, "
            "property_types, property_categories, material_categories, "
            "units RESTART IDENTITY CASCADE"
        )
    )
    await session.commit()


async def seed_legacy_owen_rows(session: AsyncSession, n: int) -> dict[str, Any]:
    """Seed ``n`` legacy Owen 2023 rows with ``dataset_version_id=NULL``.

    These represent the **存量 92 行** the spec requires the new model
    to preserve as a comparison cohort (migration 085 decision (g),
    ADR-017 §4.1). They are explicitly OUTSIDE the partial unique
    index scope so a duplicate INSERT succeeds.

    Returns evidence: count of rows seeded + their sample dedupe_key.
    """
    evidence: dict[str, Any] = {}

    # Build one DataSource + one Dataset per legacy row to mimic the
    # 08-31~09-01 ingestion pattern from ADR-017 §1.1 ("同值重复 13 行
    # 分散于 10 个不同 dataset"). The new G1-C mapper collapses these
    # onto a single dataset on re-ingest; the legacy rows are kept
    # untouched for comparison.
    #
    # Each legacy row gets its own ``DataSource.doi`` (a distinct
    # ``...-legacy-{i}`` variant) because ``data_sources.doi`` carries
    # ``uq_data_sources_doi`` UNIQUE — pre-085 production had the same
    # constraint, and the 10+ legacy datasets came from re-uploads
    # where each PDF was its own ``DataSource`` row.
    pt = (
        await session.execute(
            select(PropertyType).where(PropertyType.slug == "melting_point")
        )
    ).scalars().first()

    rows_seeded = 0
    sample_dedupe_keys: list[str] = []
    for i in range(n):
        ds = DataSource(
            doi=f"10.5281/zenodo.legacy-owen-{i}",
            title=f"Owen2023 legacy row {i}",
            source_type="journal_article",
        )
        session.add(ds)
        await session.flush()

        material = Material(name="UO2", formula="UO2")
        session.add(material)
        await session.flush()

        dataset = Dataset(
            material_id=material.id,
            source_id=ds.id,
            title=f"Legacy Owen2023 dataset {i}",
            # literature_doi NOT set on legacy rows — they pre-date 085
            # and the dedupe ladder relies on the NEW path to set it.
        )
        session.add(dataset)
        await session.flush()

        # Direct INSERT — bypasses the mapper's dialect-aware write
        # path on purpose. Legacy rows must NOT carry
        # ``dataset_version_id``; they are pre-versioning. Material is
        # reached via ``datasets.material_id`` — no column on the
        # measurements table itself. ``unit_id`` is NULL on legacy rows
        # (the pre-G1-A extraction pipeline emitted free-text unit
        # strings; migration 033's schema migration moved them to the
        # ``units`` table for new rows only — legacy rows stayed NULL).
        await session.execute(
            text(
                """
                INSERT INTO property_measurements (
                    dataset_id, property_type_id,
                    value_scalar, conditions, method, source_id
                )
                VALUES (
                    :dataset_id, :pt_id,
                    :value_scalar, '{}'::jsonb, '', :source_id
                )
                RETURNING id, dedupe_key
                """
            ),
            {
                "dataset_id": dataset.id,
                "pt_id": pt.id,
                "value_scalar": 2800,
                "source_id": ds.id,
            },
        )
        rows_seeded += 1

    await session.commit()

    # Snapshot the sample of legacy dedupe_keys.
    sample = (
        await session.execute(
            text(
                "SELECT dedupe_key FROM property_measurements "
                "WHERE dataset_version_id IS NULL LIMIT 3"
            )
        )
    ).all()
    sample_dedupe_keys = [str(r.dedupe_key) for r in sample]

    # Count.
    total_legacy = (
        await session.execute(
            text(
                "SELECT count(*) FROM property_measurements "
                "WHERE dataset_version_id IS NULL"
            )
        )
    ).scalar_one()

    evidence["legacy_rows_seeded"] = rows_seeded
    evidence["legacy_rows_total"] = total_legacy
    evidence["sample_dedupe_keys"] = sample_dedupe_keys
    return evidence


# ---------------------------------------------------------------------------
# P1 — 新数据集 (per-literature private dataset, ADR-017 §2.1)
# ---------------------------------------------------------------------------


async def p1_new_dataset(engine, factory) -> dict[str, Any]:
    banner("P1 — 新数据集: Owen2023-amorphous-UO2 private dataset + v1 draft")
    evidence: dict[str, Any] = {}

    async with factory() as session:
        await seed_catalog(session)
        await session.commit()

    # First ingestion of Owen 2023 via the new G1-C path.
    async with factory() as session:
        result = await map_and_persist(
            session, [make_input(source_doi=OWEN2023_DOI, value="2800")]
        )
        await session.commit()
        evidence["created_datasets"] = result.created_datasets
        evidence["created_measurements"] = result.created_measurements
        evidence["created_sources"] = result.created_sources
        ok("created_datasets", result.created_datasets)
        ok("created_measurements", result.created_measurements)
        ok("created_sources", result.created_sources)
        assert result.created_datasets == 1, "P1: first ingest should mint 1 dataset"
        assert result.created_measurements == 1, "P1: first ingest should mint 1 measurement"

    # Confirm dataset + version shape.
    async with factory() as session:
        ds_row = (
            await session.execute(
                text(
                    "SELECT d.id, d.literature_doi, d.literature_content_hash, "
                    "COUNT(v.id) AS version_count, "
                    "MAX(v.version_no) AS latest_version, "
                    "MAX(CASE WHEN v.status = 'draft' THEN 1 ELSE 0 END) AS has_draft "
                    "FROM datasets d LEFT JOIN dataset_versions v ON v.dataset_id = d.id "
                    "WHERE d.literature_doi = :doi GROUP BY d.id"
                ),
                {"doi": OWEN2023_DOI.lower()},
            )
        ).first()
        evidence["dataset_id"] = str(ds_row.id)
        evidence["literature_doi"] = ds_row.literature_doi
        evidence["version_count"] = int(ds_row.version_count)
        evidence["latest_version"] = int(ds_row.latest_version)
        evidence["has_draft"] = int(ds_row.has_draft)
        ok("dataset_id", ds_row.id)
        ok("literature_doi", ds_row.literature_doi)
        ok("version_count", ds_row.version_count)
        ok("latest_version", ds_row.latest_version)
        ok("has_draft", ds_row.has_draft)
        assert ds_row.literature_doi == OWEN2023_DOI.lower(), "DOI should be normalised"
        assert int(ds_row.version_count) >= 1, "v1 draft should exist"
        assert int(ds_row.has_draft) == 1, "draft version should exist"

    return evidence


# ---------------------------------------------------------------------------
# P2 — 校核 (G1-A skill-adapter stub, spec §5)
# ---------------------------------------------------------------------------


async def p2_extraction_skill_path(engine, factory) -> dict[str, Any]:
    banner("P2 — 校核: G1-A skill-adapter output lands new measurements (stamped v1)")
    evidence: dict[str, Any] = {}

    # In the integrated G1-A+B+C build this is the
    # ``extraction_skill_adapter.run(extract_skill_prompt(...))`` call
    # site. The adapter maps the skill's 13-field output to the 20-field
    # row-level contract (spec §3.2). For this pilot we drive
    # ``map_and_persist`` directly with the adapter-shaped payload.
    #
    # Owen 2023's real-world scenario per wayfinder #1253 is the SAME
    # measurement (value=2800 K, UO2, melting_point) re-ingested many
    # times across 08-31~09-01 — the 92-row duplicate cohort that the
    # new model must collapse.
    #
    # To exercise AC-9's partial unique ``uq_pm_dedupe_key`` rather
    # than the legacy ``uq_pm_dedup`` (which is keyed on
    # (dataset, property_type, conditions_hash, method) and therefore
    # does NOT distinguish values — migration 085 decision (h) keeps
    # both indexes live during the dual-read period), we drive two
    # DISTINCT conditions so each call lands a row with its own
    # ``conditions_hash`` and ``dedupe_key``. The re-ingest in P3 then
    # re-collapses the SAME-condition call.
    async with factory() as session:
        r1 = await map_and_persist(
            session,
            [make_input(value="2800", conditions={"temp_k": 300})],
        )
        await session.commit()
    async with factory() as session:
        r2 = await map_and_persist(
            session,
            [make_input(value="2800", conditions={"temp_k": 600})],
        )
        await session.commit()

    evidence["call1.created_measurements"] = r1.created_measurements
    evidence["call1.skipped_duplicate_measurements"] = r1.skipped_duplicate_measurements
    evidence["call2.created_measurements"] = r2.created_measurements
    evidence["call2.skipped_duplicate_measurements"] = r2.skipped_duplicate_measurements
    ok("call1.created_measurements", r1.created_measurements)
    ok("call1.skipped_duplicate_measurements", r1.skipped_duplicate_measurements)
    ok("call2.created_measurements", r2.created_measurements)
    ok("call2.skipped_duplicate_measurements", r2.skipped_duplicate_measurements)
    assert r1.created_measurements == 1, (
        f"P2: call1 should mint 1 measurement; got {r1.created_measurements}"
    )
    assert r2.created_measurements == 1, (
        f"P2: call2 (different conditions) should mint 1 measurement; "
        f"got {r2.created_measurements}"
    )

    # Confirm both P2 calls added rows carrying dataset_version_id
    # (P1 already added 1, so the cumulative count is 3).
    async with factory() as session:
        new_rows = (
            await session.execute(
                text(
                    "SELECT count(*) FROM property_measurements "
                    "WHERE dataset_version_id IS NOT NULL"
                )
            )
        ).scalar_one()
        evidence["new_rows_with_version_id"] = int(new_rows)
        ok("new rows with dataset_version_id", int(new_rows))
        assert int(new_rows) >= 2, (
            f"P2: >= 2 versioned measurements expected (P1+P2); "
            f"got {int(new_rows)}"
        )

    return evidence


# ---------------------------------------------------------------------------
# P3 — 合并 (ADR-017 §2.2 snapshot merge + AC-9 dedup)
# ---------------------------------------------------------------------------


async def p3_merge_and_dedupe(engine, factory) -> dict[str, Any]:
    banner("P3 — 合并: 92 identical re-ingestions collapse to 1 row (Owen pattern)")
    evidence: dict[str, Any] = {}

    # Mirror the actual Owen 2023 ingestion pattern from
    # wayfinder #1253: the same paper re-ingested many times with the
    # same value AND the same conditions. The new model must collapse
    # all 92 calls onto ONE ``property_measurements`` row.
    #
    # P2's call1 already inserted this exact ``(value, conditions)``
    # pair, so all 92 re-ingestions here must dedupe (created=0,
    # skipped=92) — the partial unique / 4-tuple index guarantees
    # exactly one row survives.
    batch_size = 92
    aggregate_created = 0
    aggregate_skipped = 0
    for i in range(batch_size):
        async with factory() as session:
            r = await map_and_persist(
                session,
                [make_input(value="2800", conditions={"temp_k": 300})],
            )
            await session.commit()
            aggregate_created += r.created_measurements
            aggregate_skipped += r.skipped_duplicate_measurements

    evidence["batch_size"] = batch_size
    evidence["aggregate_created"] = aggregate_created
    evidence["aggregate_skipped"] = aggregate_skipped
    ok("batch_size", batch_size)
    ok("aggregate_created", aggregate_created)
    ok("aggregate_skipped", aggregate_skipped)
    assert aggregate_created == 0, (
        f"P3: 92 re-ingestions of an existing row must mint 0 rows; "
        f"got {aggregate_created}"
    )
    assert aggregate_skipped == batch_size, (
        f"P3: 92 dedupes expected; got {aggregate_skipped}"
    )

    # GROUP BY HAVING count(*) > 1 on the versioned scope must be empty.
    async with factory() as session:
        dup_groups = (
            await session.execute(
                text(
                    "SELECT dedupe_key, count(*) FROM property_measurements "
                    "WHERE dataset_version_id IS NOT NULL "
                    "GROUP BY dedupe_key HAVING count(*) > 1"
                )
            )
        ).all()
        evidence["dup_groups_versioned"] = [str(r.dedupe_key) for r in dup_groups]
        ok("dup groups in versioned scope", len(dup_groups))
        assert len(dup_groups) == 0, (
            "P3: partial unique should keep versioned scope at 1"
        )

    # ADR-017 §2.6 inverse: a DIFFERENT-conditions row must NOT
    # collapse onto the same dedupe_key. Re-ingest with conditions
    # ``{"temp_k": 900}`` (distinct from both P2 calls' ``{"temp_k":
    # 300}`` and ``{"temp_k": 600}``) and assert a new row appears.
    async with factory() as session:
        r_diff = await map_and_persist(
            session,
            [make_input(value="2800", conditions={"temp_k": 900})],
        )
        await session.commit()
        evidence["distinct_conditions.created"] = r_diff.created_measurements
        ok("distinct_conditions.created", r_diff.created_measurements)
        assert r_diff.created_measurements == 1, (
            "P3 inverse: distinct conditions must yield a new row"
        )

    return evidence


# ---------------------------------------------------------------------------
# P4 — 存量对照 (migration 085 decision (g))
# ---------------------------------------------------------------------------


async def p4_legacy_preservation(engine, factory) -> dict[str, Any]:
    banner("P4 — 存量对照: 92 legacy Owen rows preserved across pilot")
    evidence: dict[str, Any] = {}

    async with factory() as session:
        seed_evidence = await seed_legacy_owen_rows(session, LEGACY_OWEN_ROWS)
        evidence["legacy_seeded"] = seed_evidence
        ok("legacy rows seeded", seed_evidence["legacy_rows_seeded"])

    # Run the new path against the same dataset. Legacy rows must
    # remain untouched; new rows must dedupe themselves.
    # ``conditions={"phase": "crystalline"}`` is novel to this step so
    # the mapper call does not collide with prior P1-P3 rows on
    # ``conditions_hash``.
    async with factory() as session:
        result = await map_and_persist(
            session,
            [make_input(value="2800", conditions={"phase": "crystalline"})],
        )
        await session.commit()
        evidence["pilot.created_measurements"] = result.created_measurements
        evidence["pilot.skipped_duplicate_measurements"] = result.skipped_duplicate_measurements

    # Count legacy rows (dataset_version_id IS NULL) — must still be 92.
    async with factory() as session:
        legacy_count = (
            await session.execute(
                text(
                    "SELECT count(*) FROM property_measurements "
                    "WHERE dataset_version_id IS NULL"
                )
            )
        ).scalar_one()
        versioned_count = (
            await session.execute(
                text(
                    "SELECT count(*) FROM property_measurements "
                    "WHERE dataset_version_id IS NOT NULL"
                )
            )
        ).scalar_one()
        evidence["legacy_remaining"] = int(legacy_count)
        evidence["versioned_total"] = int(versioned_count)
        ok("legacy rows remaining", int(legacy_count))
        ok("versioned rows total", int(versioned_count))
        assert int(legacy_count) == LEGACY_OWEN_ROWS, (
            f"P4 FAIL: legacy cohort drifted from {LEGACY_OWEN_ROWS} to {legacy_count}"
        )

    return evidence


# ---------------------------------------------------------------------------
# P5 — AC-2 (Calhoun/Zhu 数值域外)
# ---------------------------------------------------------------------------


async def p5_zero_numeric_extraction(engine, factory) -> dict[str, Any]:
    banner("P5 — AC-2: zero numeric extractions → zero property_measurements rows")
    evidence: dict[str, Any] = {}

    # Calhoun 2018 / Zhu 2024 produce qualitative facts only. The
    # pipeline must not invent numeric rows for them — they belong in
    # the KG, not in property_measurements. Drive an empty batch and
    # assert the table stays at the legacy 92 + versioned pilot rows.
    async with factory() as session:
        before_legacy = (
            await session.execute(
                text(
                    "SELECT count(*) FROM property_measurements "
                    "WHERE dataset_version_id IS NULL"
                )
            )
        ).scalar_one()
        before_versioned = (
            await session.execute(
                text(
                    "SELECT count(*) FROM property_measurements "
                    "WHERE dataset_version_id IS NOT NULL"
                )
            )
        ).scalar_one()

        # Empty extraction — no items, no rows.
        result = await map_and_persist(session, [])
        await session.commit()

        after_legacy = (
            await session.execute(
                text(
                    "SELECT count(*) FROM property_measurements "
                    "WHERE dataset_version_id IS NULL"
                )
            )
        ).scalar_one()
        after_versioned = (
            await session.execute(
                text(
                    "SELECT count(*) FROM property_measurements "
                    "WHERE dataset_version_id IS NOT NULL"
                )
            )
        ).scalar_one()

    evidence["before_legacy"] = int(before_legacy)
    evidence["before_versioned"] = int(before_versioned)
    evidence["after_legacy"] = int(after_legacy)
    evidence["after_versioned"] = int(after_versioned)
    evidence["empty_call_created_measurements"] = result.created_measurements
    ok("legacy before/after", f"{before_legacy}/{after_legacy}")
    ok("versioned before/after", f"{before_versioned}/{after_versioned}")
    ok("empty call created_measurements", result.created_measurements)
    assert int(before_legacy) == int(after_legacy), "P5: legacy rows must be untouched"
    assert int(after_versioned) == int(before_versioned), (
        "P5: empty extraction must not create rows"
    )
    assert result.created_measurements == 0, "P5: empty batch must produce zero rows"

    return evidence


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main() -> int:
    engine = create_async_engine(DB_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    # Fresh schema for the pilot — truncate to start clean.
    async with factory() as session:
        await truncate_tables(session)

    evidence: dict[str, Any] = {
        "db_url": DB_URL,
        "owen2023_dataset_slug": OWEN2023_DATASET_SLUG,
        "owen2023_doi": OWEN2023_DOI,
        "legacy_rows_count": LEGACY_OWEN_ROWS,
    }

    for step_fn in (
        p1_new_dataset,
        p2_extraction_skill_path,
        p3_merge_and_dedupe,
        p4_legacy_preservation,
        p5_zero_numeric_extraction,
    ):
        try:
            step_evidence = await step_fn(engine, factory)
        except AssertionError as exc:
            print(f"\n  >>> {step_fn.__name__.upper()} FAIL: {exc}")
            return 1
        except Exception as exc:  # pragma: no cover — defensive
            print(f"\n  >>> {step_fn.__name__.upper()} ERROR: {exc}")
            traceback.print_exc()
            return 2
        evidence[step_fn.__name__] = step_evidence
        print(f"\n  >>> {step_fn.__name__.upper()} PASS")

    print("\n" + "=" * 78)
    print(" EVIDENCE SUMMARY")
    print("=" * 78)
    print(str(evidence).replace("'", '"'))
    print("\n  OVERALL: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
