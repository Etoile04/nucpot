"""End-to-end integration test: mock extract → persist → query round-trip (NFM-703).

Test flow:
1. Seed prerequisite entities (PropertyCategory, PropertyType, Unit)
2. Call extraction_to_db_mapper.map_and_persist() with mocked extraction output
3. Verify DataSource, Material, Dataset, PropertyMeasurement, MeasurementCondition in DB
4. Test deduplication: call mapper again with same DOI, verify no duplicate DataSource
5. Test multiple material types (UO2, Zr alloys, SiC, ATF)

Note: HTTP CRUD endpoint verification depends on NFM-702 service layer
(completed separately). This test verifies DB-level round-trip directly.
"""

from __future__ import annotations

import pytest
from sqlalchemy import Text, event, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from nfm_db.models import Base
from nfm_db.models.material import Material
from nfm_db.models.property import (
    Dataset,
    MeasurementCondition,
    PropertyCategory,
    PropertyMeasurement,
    PropertyType,
)
from nfm_db.models.source import DataSource
from nfm_db.models.unit import Unit
from nfm_db.services.extraction_to_db_mapper import (
    MappingResult,
    map_and_persist,
)

# ---------------------------------------------------------------------------
# SQLite compatibility: JSONB is PG-only — fall back to TEXT for test DDL.
# ---------------------------------------------------------------------------


@event.listens_for(Base.metadata, "before_create")
def _map_jsonb_to_text_for_sqlite(
    target,
    connection,
    **_kw,
) -> None:
    from sqlalchemy.dialects.postgresql import JSONB as PG_JSONB
    from sqlalchemy.dialects.sqlite.base import SQLiteDialect

    if not isinstance(connection.dialect, SQLiteDialect):
        return

    for table in target.sorted_tables:
        for col in table.columns:
            if isinstance(col.type, PG_JSONB):
                col.type = Text()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def db_session() -> AsyncSession:
    """Create an in-memory SQLite async session for testing."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    async with session_factory() as session:
        yield session

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

    await engine.dispose()


@pytest.fixture
async def seed_prereqs(db_session: AsyncSession) -> dict:
    """Seed PropertyCategory, PropertyType, and Unit required for measurements.

    Returns dict with ids: property_category_id, property_type_id, unit_id.
    """
    cat = PropertyCategory(
        name="Thermal",
        slug="thermal",
        description="Thermal properties of nuclear materials",
    )
    db_session.add(cat)
    await db_session.flush()

    unit = Unit(
        name="Watt per meter-Kelvin",
        symbol="W/m·K",
        dimension="thermal_conductivity",
    )
    db_session.add(unit)
    await db_session.flush()

    prop_type = PropertyType(
        category_id=cat.id,
        name="Thermal Conductivity",
        slug="thermal-conductivity",
        value_type="scalar",
        unit_id=unit.id,
        description="Linear thermal conductivity",
    )
    db_session.add(prop_type)
    await db_session.commit()

    return {
        "property_category_id": cat.id,
        "property_type_id": prop_type.id,
        "unit_id": unit.id,
    }


# ---------------------------------------------------------------------------
# Helper: build mock extraction output dict matching ExtractedProperty schema
# ---------------------------------------------------------------------------


def _build_mock_extraction_dicts(
    *,
    doi: str = "10.1016/j.jnucmat.2020.152307",
    material_name: str = "UO2",
    composition: str = "UO2",
    property_name: str = "Thermal Conductivity",
    value: str = "3.5",
    temperature: float = 1200.0,
    reference: str | None = "Thermal conductivity of UO2 at high temperatures",
) -> list[dict]:
    """Build a list of extraction output dicts matching ExtractedProperty schema."""
    conditions = {"temperature": temperature, "environment": "inert"}
    return [
        {
            "source_doi": doi,
            "reference": reference,
            "material_name": material_name,
            "composition": composition,
            "property_category": "Thermal",
            "property": property_name,
            "value": value,
            "unit": "W/m·K",
            "conditions": conditions,
            "context": "Measured in inert atmosphere",
            "confidence": "high",
            "uncertainty": 0.15,
        },
    ]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
async def test_e2e_mock_extract_persist_and_verify_db(
    db_session: AsyncSession,
    seed_prereqs: dict,
) -> None:
    """End-to-end: mock extract → map_and_persist → verify DB entities.

    Proves the full data pipeline: DataSource, Material, Dataset,
    PropertyMeasurement, and MeasurementCondition are created.
    """
    doi = "10.1016/j.jnucmat.2020.152307"
    extraction_output = _build_mock_extraction_dicts(doi=doi)

    # Call mapper with mocked extraction output
    result = await map_and_persist(
        db=db_session,
        extraction_output=extraction_output,
    )

    assert isinstance(result, MappingResult)
    assert result.created_sources >= 1
    assert result.created_materials >= 1
    assert result.created_datasets >= 1
    assert result.created_measurements >= 1
    assert result.validation_errors == 0

    # Verify DataSource in DB
    source = (
        await db_session.execute(select(DataSource).where(DataSource.doi == doi))
    ).scalar_one_or_none()
    assert source is not None
    assert source.doi == doi
    assert "Thermal conductivity" in source.title

    # Verify Material in DB
    material = (
        await db_session.execute(select(Material).where(Material.name == "UO2"))
    ).scalar_one_or_none()
    assert material is not None
    assert material.name == "UO2"
    assert material.is_active is True

    # Verify Dataset in DB
    dataset = (
        await db_session.execute(
            select(Dataset).where(
                Dataset.source_id == source.id,
                Dataset.material_id == material.id,
            )
        )
    ).scalar_one_or_none()
    assert dataset is not None
    assert dataset.source_id == source.id
    assert dataset.material_id == material.id

    # Verify PropertyMeasurement in DB
    measurement = (
        await db_session.execute(
            select(PropertyMeasurement).where(
                PropertyMeasurement.dataset_id == dataset.id,
            )
        )
    ).scalar_one_or_none()
    assert measurement is not None
    assert float(measurement.value_scalar) == 3.5
    assert float(measurement.uncertainty) == 0.15
    assert measurement.review_status == "pending"

    # Verify MeasurementCondition in DB
    condition = (
        await db_session.execute(
            select(MeasurementCondition).where(
                MeasurementCondition.measurement_id == measurement.id,
            )
        )
    ).scalar_one_or_none()
    assert condition is not None
    assert float(condition.temperature) == 1200.0
    assert condition.environment == "inert"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_e2e_deduplication_same_doi_no_duplicate_source(
    db_session: AsyncSession,
    seed_prereqs: dict,
) -> None:
    """Deduplication: calling map_and_persist twice with the same DOI
    should reuse the existing DataSource, not create a duplicate.
    """
    doi = "10.1016/j.jnucmat.2019.07.004"

    # First call — creates everything
    extraction1 = _build_mock_extraction_dicts(
        doi=doi,
        material_name="Zr-4",
        composition="Zr",
        reference="Thermal properties of Zircaloy-4",
        value="16.0",
        temperature=600.0,
    )
    result1 = await map_and_persist(
        db=db_session,
        extraction_output=extraction1,
    )
    assert result1.created_sources >= 1

    # Count sources after first call
    source_count_1 = (
        await db_session.execute(
            select(func.count()).select_from(DataSource).where(DataSource.doi == doi)
        )
    ).scalar()
    assert source_count_1 == 1

    # Second call with same DOI but different properties
    extraction2 = _build_mock_extraction_dicts(
        doi=doi,
        material_name="Zr-4",
        composition="Zr",
        reference="Thermal properties of Zircaloy-4",
        value="18.0",
        temperature=800.0,
    )
    result2 = await map_and_persist(
        db=db_session,
        extraction_output=extraction2,
    )

    # Source should be deduplicated (skipped)
    assert result2.skipped_duplicates >= 1

    # Only one DataSource with this DOI should exist
    source_count_2 = (
        await db_session.execute(
            select(func.count()).select_from(DataSource).where(DataSource.doi == doi)
        )
    ).scalar()
    assert source_count_2 == 1


@pytest.mark.integration
@pytest.mark.asyncio
async def test_e2e_multiple_materials_distinct_entities(
    db_session: AsyncSession,
    seed_prereqs: dict,
) -> None:
    """Verify the seed flow works for different material types."""
    materials = [
        ("UO2", "UO2", "10.1016/j.jnucmat.2020.uo2"),
        ("Zircaloy-4", "Zr", "10.1016/j.jnucmat.2020.zr4"),
        ("SiC/SiC", "SiC", "10.1016/j.jnucmat.2020.sic"),
        ("FeCrAl", "FeCrAl", "10.1016/j.jnucmat.2020.fecral"),
    ]

    for mat_name, formula, doi in materials:
        extraction = _build_mock_extraction_dicts(
            doi=doi,
            material_name=mat_name,
            composition=formula,
            value="1.0",
            reference=f"Properties of {mat_name}",
        )
        result = await map_and_persist(
            db=db_session,
            extraction_output=extraction,
        )
        assert result.created_materials >= 1

    # Verify each material was created distinctly
    all_materials = (await db_session.execute(select(Material))).scalars().all()
    material_names = {m.name for m in all_materials}
    for mat_name, _, _ in materials:
        assert mat_name in material_names, f"Material {mat_name} not found in DB"

    # Verify we have 4 distinct materials
    assert len(material_names) >= 4


@pytest.mark.integration
@pytest.mark.asyncio
async def test_e2e_seed_dois_file_loadable_and_has_coverage() -> None:
    """Verify seed_dois.json loads and covers multiple material types."""
    import json
    from pathlib import Path

    seed_path = Path(__file__).resolve().parents[2] / "src" / "nfm_db" / "data" / "seed_dois.json"
    assert seed_path.exists(), f"seed_dois.json not found at {seed_path}"

    with seed_path.open() as f:
        data = json.load(f)

    assert "dois" in data
    assert len(data["dois"]) >= 50, f"Expected >= 50 DOIs, got {len(data['dois'])}"
    assert "description" in data
    assert "curated_date" in data
    assert data["curated_date"] == "2026-07-06"
    assert data["count"] == len(data["dois"])

    # Verify DOIs are from expected journals
    dois = data["dois"]
    jnm_count = sum(1 for d in dois if "jnucmat" in d)
    ned_count = sum(1 for d in dois if "nucengdes" in d)
    assert jnm_count > 0, "Expected DOIs from Journal of Nuclear Materials"
    assert ned_count > 0, "Expected DOIs from Nuclear Engineering and Design"
