"""Selftest for ``backfill_nfm4312_material_association.py`` (NFM-4312 / BUG-32).

Covers the plan builder (selection predicate, collision guard, residue
accounting), apply semantics (single transaction, dup retirement, target
normalization), dry-run no-write behaviour, and idempotency — against an
in-memory SQLite database shaped like the 2026-09-05 production state.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

# Make the scripts/ package and nfm_db importable for the test process.
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
_REPO_ROOT = _SCRIPTS_DIR.parent.parent
_API_SRC = _REPO_ROOT / "apps" / "api" / "src"
for p in (str(_SCRIPTS_DIR), str(_API_SRC)):
    if p not in sys.path:
        sys.path.insert(0, p)

from backfill_nfm4312_material_association import (  # noqa: E402
    apply_plan,
    build_plan,
    format_report,
)
from sqlalchemy import ARRAY, JSON  # noqa: E402
from sqlalchemy.dialects.postgresql import ARRAY as PG_ARRAY  # noqa: E402
from sqlalchemy.dialects.postgresql import JSONB as PG_JSONB  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from nfm_db.models import (  # noqa: E402
    Base,
    Dataset,
    DataSource,
    Material,
    PropertyCategory,
    PropertyMeasurement,
    PropertyType,
)

# Fixed ids mirror the production snapshot so the plan builder's
# configuration validation is exercised on canonical UUIDs.
SENTINEL_ID = "021036bf-d7cc-434c-8f91-a08030027b4a"
UO2_ID = "068dc946-9dd9-4a8d-bad0-9f24359b8b87"
TARGET_ID = "3d084165-a282-418d-84b0-88a7a95cf98c"
DUP_ID = "794120c1-ab6e-4f9c-8682-e0a40de81011"
OWEN_SRC_ID = "9320cb50-eb65-4178-8d2e-c56aeb848b21"
UUID_TITLE = OWEN_SRC_ID  # NFM-4088 signature


def _kwargs(**overrides):
    base = dict(
        sentinel_material_id=SENTINEL_ID,
        uo2_material_id=UO2_ID,
        target_material_id=TARGET_ID,
        owen_source_id=OWEN_SRC_ID,
        uuid_title=UUID_TITLE,
        duplicate_material_id=DUP_ID,
        target_formula="UO2",
        target_crystal_structure="amorphous",
    )
    return {**base, **overrides}


def _safe_create_all(sync_conn, metadata) -> None:
    """``create_all`` with the conftest SQLite-compat shims.

    Mirrors ``tests/conftest.py::_safe_create_all`` (JSONB/ARRAY →
    JSON, dangling FKs stripped) so the scripts selftest can build the
    schema without importing the tests package.
    """
    for table in metadata.tables.values():
        for col in table.columns:
            if isinstance(col.type, PG_JSONB):
                col.type = JSON()
            if isinstance(col.type, (PG_ARRAY, ARRAY)):
                col.type = JSON()
            dangling = [
                fk
                for fk in list(col.foreign_keys)
                if fk._colspec.split(".")[0].strip('"') not in metadata.tables
            ]
            for fk in dangling:
                col.foreign_keys.discard(fk)
    metadata.create_all(sync_conn)


async def _make_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(_safe_create_all, Base.metadata)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def uid(s: str) -> uuid.UUID:
    return uuid.UUID(s)


async def _seed(session) -> None:
    """Production-shaped seed: 3 movers + 1 residue + 1 dup dataset."""

    def material(mid, name, formula):
        return Material(id=uid(mid), name=name, formula=formula)

    session.add_all(
        [
            material(SENTINEL_ID, "Unknown Material (canonical)", None),
            material(UO2_ID, "UO2", "UO2"),
            material(
                TARGET_ID,
                "amorphous UO2 (undoped and Cr-doped)",
                "UO2 (undoped, 10 at.% Cr, 50 at.% Cr)",
            ),
            material(DUP_ID, "amorphous UO2 (undoped and Cr-doped)", "UO2 (undoped and Cr-doped)"),
        ]
    )
    session.add_all(
        [
            DataSource(
                id=uid(OWEN_SRC_ID),
                title="Owen et al. - 2023 - Diffusion in undoped and Cr-doped amorphous UO2",
                source_type="journal_article",
            ),
            DataSource(
                id=uid("aaaa1111-0000-0000-0000-000000000001"),
                title=UUID_TITLE,
                source_type="other",
            ),
            DataSource(
                id=uid("aaaa1111-0000-0000-0000-000000000002"),
                title=UUID_TITLE,
                source_type="other",
            ),
            DataSource(
                id=uid("aaaa1111-0000-0000-0000-000000000003"),
                title="Unrelated source",
                source_type="other",
            ),
            DataSource(
                id=uid("aaaa1111-0000-0000-0000-000000000004"),
                title="Dup fragment source",
                source_type="other",
            ),
        ]
    )
    session.add(
        PropertyCategory(
            id=uid("cccc0000-0000-0000-0000-000000000001"), name="Thermal", slug="thermal"
        )
    )
    await session.flush()
    session.add(
        PropertyType(
            id=uid("dddd0000-0000-0000-0000-000000000001"),
            category_id=uid("cccc0000-0000-0000-0000-000000000001"),
            name="activation_energy",
            slug="activation-energy",
            value_type="scalar",
        )
    )
    await session.flush()

    def dataset(ds_id, material_id, source_id, title):
        return Dataset(
            id=uid(ds_id), material_id=uid(material_id), source_id=uid(source_id), title=title
        )

    session.add_all(
        [
            dataset(
                "11111111-0000-0000-0000-000000000001",
                SENTINEL_ID,
                OWEN_SRC_ID,
                "main 83-row dataset",
            ),
            dataset(
                "11111111-0000-0000-0000-000000000002",
                SENTINEL_ID,
                "aaaa1111-0000-0000-0000-000000000001",
                "scatter",
            ),
            dataset(
                "11111111-0000-0000-0000-000000000003",
                UO2_ID,
                "aaaa1111-0000-0000-0000-000000000002",
                "misattached",
            ),
            dataset(
                "11111111-0000-0000-0000-000000000004",
                SENTINEL_ID,
                "aaaa1111-0000-0000-0000-000000000003",
                "no provenance",
            ),
            dataset(
                "11111111-0000-0000-0000-000000000005",
                DUP_ID,
                "aaaa1111-0000-0000-0000-000000000004",
                "dup fragment ds",
            ),
        ]
    )
    await session.flush()

    def meas(n, dataset_id):
        return PropertyMeasurement(
            id=uuid.uuid4(),
            dataset_id=uid(dataset_id),
            property_type_id=uid("dddd0000-0000-0000-0000-000000000001"),
            value_scalar=float(n),
        )

    session.add_all(
        [
            meas(1, "11111111-0000-0000-0000-000000000001"),
            meas(2, "11111111-0000-0000-0000-000000000001"),
            meas(3, "11111111-0000-0000-0000-000000000001"),
            meas(4, "11111111-0000-0000-0000-000000000002"),
            meas(5, "11111111-0000-0000-0000-000000000003"),
            meas(6, "11111111-0000-0000-0000-000000000004"),
            meas(7, "11111111-0000-0000-0000-000000000004"),
        ]
    )
    await session.commit()


async def test_plan_selects_owen_provenance_moves_and_counts_residue():
    engine, factory = await _make_db()
    async with factory() as session:
        await _seed(session)
        plan = await build_plan(session, **_kwargs())

    assert sorted(m.dataset_id for m in plan.moves) == [
        "11111111-0000-0000-0000-000000000001",
        "11111111-0000-0000-0000-000000000002",
        "11111111-0000-0000-0000-000000000003",
    ]
    assert plan.moved_measurements == 5
    assert len(plan.duplicate_moves) == 1
    assert plan.collisions == ()
    # Sentinel keeps its 2 unattributed measurements (curation queue).
    assert plan.sentinel_residue_measurements == 2
    assert plan.target_measurements_after == 5
    report = format_report(plan)
    assert "datasets moved: 3 (5 measurements)" in report
    assert "sentinel residue after: 2" in report
    await engine.dispose()


async def test_dry_run_writes_nothing():
    engine, factory = await _make_db()
    async with factory() as session:
        await _seed(session)
        await build_plan(session, **_kwargs())
        await session.rollback()
    async with factory() as session:
        moved = await session.get(Dataset, uid("11111111-0000-0000-0000-000000000001"))
        target = await session.get(Material, uid(TARGET_ID))
        dup = await session.get(Material, uid(DUP_ID))
    assert moved.material_id == uid(SENTINEL_ID)
    assert target.formula == "UO2 (undoped, 10 at.% Cr, 50 at.% Cr)"
    assert target.crystal_structure is None
    assert dup.is_active is True
    await engine.dispose()


async def test_apply_moves_datasets_normalizes_target_and_retires_dup():
    engine, factory = await _make_db()
    async with factory() as session:
        await _seed(session)
        plan = await build_plan(session, **_kwargs())
        await apply_plan(session, plan)
        await session.commit()
    async with factory() as session:
        for ds_id in (
            "11111111-0000-0000-0000-000000000001",
            "11111111-0000-0000-0000-000000000002",
            "11111111-0000-0000-0000-000000000003",
            "11111111-0000-0000-0000-000000000005",
        ):
            moved = await session.get(Dataset, uid(ds_id))
            assert moved.material_id == uid(TARGET_ID), ds_id
        staying = await session.get(Dataset, uid("11111111-0000-0000-0000-000000000004"))
        assert staying.material_id == uid(SENTINEL_ID)
        target = await session.get(Material, uid(TARGET_ID))
        assert target.formula == "UO2"
        assert target.crystal_structure == "amorphous"
        dup = await session.get(Material, uid(DUP_ID))
        assert dup.is_active is False
    await engine.dispose()


async def test_plan_is_idempotent_after_apply():
    engine, factory = await _make_db()
    async with factory() as session:
        await _seed(session)
        plan = await build_plan(session, **_kwargs())
        await apply_plan(session, plan)
        await session.commit()
    async with factory() as session:
        second = await build_plan(session, **_kwargs())
    assert second.moves == ()
    assert second.duplicate_moves == ()
    assert second.moved_measurements == 0
    assert second.target_measurements_after == 5
    await engine.dispose()


async def test_collision_detected_when_target_holds_same_source():
    engine, factory = await _make_db()
    async with factory() as session:
        await _seed(session)
        # Target already owns a dataset from the same source as the
        # scatter mover -> uq_datasets_source_material would reject.
        session.add(
            Dataset(
                id=uid("22222222-0000-0000-0000-000000000001"),
                material_id=uid(TARGET_ID),
                source_id=uid("aaaa1111-0000-0000-0000-000000000001"),
                title="existing target dataset",
            )
        )
        await session.commit()
        plan = await build_plan(session, **_kwargs())

    assert "11111111-0000-0000-0000-000000000002" in plan.collisions
    await engine.dispose()


async def test_rejects_non_uuid_title():
    engine, factory = await _make_db()
    async with factory() as session:
        await _seed(session)
        try:
            await build_plan(session, **_kwargs(uuid_title="Owen et al. 2023"))
            raised = False
        except ValueError:
            raised = True
    assert raised
    await engine.dispose()
