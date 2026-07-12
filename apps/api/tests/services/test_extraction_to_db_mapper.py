"""Tests for the Extraction-to-DB Mapper service (NFM-700).

Covers: map_and_persist with mocked extraction output.
Tests use the db_session fixture from conftest.py (SQLite in-memory).
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models import (
    Dataset,
    DataSource,
    Material,
    MeasurementCondition,
    PropertyCategory,
    PropertyMeasurement,
    PropertyType,
)
from nfm_db.services.extraction_to_db_mapper import (
    MappingError,
    MappingResult,
    map_and_persist,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _seed_property_type(
    db: AsyncSession,
    *,
    category_name: str = "thermal",
    category_slug: str = "thermal",
    property_name: str = "thermal_conductivity",
    property_slug: str = "thermal-conductivity",
    value_type: str = "scalar",
) -> PropertyType:
    """Create a PropertyCategory + PropertyType pair for test lookups."""

    category = PropertyCategory(
        name=category_name,
        slug=category_slug,
        description=f"{category_name} properties",
    )
    db.add(category)
    await db.flush()

    pt = PropertyType(
        category_id=category.id,
        name=property_name,
        slug=property_slug,
        value_type=value_type,
    )
    db.add(pt)
    await db.commit()
    await db.refresh(pt)
    return pt


def _make_extracted_property(
    *,
    source_file: str | None = "literature/UO2_paper.md",
    material_name: str | None = "UO2",
    composition: str | None = "UO2",
    property_category: str | None = "thermal",
    property_name: str = "Thermal Conductivity",
    value: str = "8.5",
    unit: str = "W/(m·K)",
    conditions: dict[str, Any] | None = None,
    reference: str | None = "Smith et al., J. Nucl. Mater.",
    source_doi: str | None = None,
    confidence: str = "high",
    context: str | None = None,
) -> dict[str, Any]:
    """Build a raw ExtractedProperty dict (as extraction_pipeline would output)."""
    props: dict[str, Any] = {
        "source_file": source_file,
        "material_name": material_name,
        "composition": composition,
        "property_category": property_category,
        "property": property_name,
        "value": value,
        "unit": unit,
        "confidence": confidence,
        "reference": reference,
    }
    if conditions is not None:
        props["conditions"] = conditions
    if source_doi is not None:
        props["source_doi"] = source_doi
    if context is not None:
        props["context"] = context
    return props


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMapAndPersistValidation:
    """Tests for input validation before DB writes."""

    async def test_empty_list_returns_zero_result(self, db_session: AsyncSession):
        """Empty input should return zero counts without errors."""
        result = await map_and_persist(db_session, [])

        assert result.created_sources == 0
        assert result.created_materials == 0
        assert result.created_datasets == 0
        assert result.created_measurements == 0
        assert result.skipped_duplicates == 0
        assert result.validation_errors == 0

    async def test_invalid_property_rejected(self, db_session: AsyncSession):
        """ExtractedProperty missing required 'property' field should be rejected."""
        bad_input = [
            {
                "material_name": "UO2",
                "value": "8.5",
                "unit": "W/(m·K)",
                # missing: property (required)
            }
        ]

        result = await map_and_persist(db_session, bad_input)

        assert result.validation_errors == 1
        assert result.created_measurements == 0

    async def test_invalid_value_type_rejected(self, db_session: AsyncSession):
        """Non-string value field should be rejected."""
        bad_input = [
            {
                "property": "Thermal Conductivity",
                "value": 8.5,  # must be string per ExtractedProperty schema
                "unit": "W/(m·K)",
            }
        ]

        result = await map_and_persist(db_session, bad_input)

        assert result.validation_errors == 1
        assert result.created_measurements == 0


@pytest.mark.unit
class TestMapAndPersistDedup:
    """Tests for deduplication by DOI (sources) and formula (materials)."""

    async def test_dedup_same_doi_single_source(self, db_session: AsyncSession):
        """Two properties from same DOI should create only one DataSource."""
        await _seed_property_type(db_session)

        inputs = [
            _make_extracted_property(source_doi="10.1000/test1"),
            _make_extracted_property(
                property_name="Melting Point",
                value="2800",
                unit="K",
                source_doi="10.1000/test1",
            ),
        ]

        result = await map_and_persist(db_session, inputs)

        assert result.created_sources == 1
        assert result.skipped_duplicates >= 1

    async def test_dedup_same_material_single_material(self, db_session: AsyncSession):
        """Two properties with same material_name should create one Material."""
        await _seed_property_type(db_session)

        inputs = [
            _make_extracted_property(
                material_name="UO2",
                composition="UO2",
                source_doi="10.1000/a",
            ),
            _make_extracted_property(
                material_name="UO2",
                composition="UO2",
                property_name="Melting Point",
                value="2800",
                unit="K",
                source_doi="10.1000/b",
            ),
        ]

        result = await map_and_persist(db_session, inputs)

        assert result.created_materials == 1

    async def test_different_dois_two_sources(self, db_session: AsyncSession):
        """Properties from different DOIs should create two DataSources."""
        await _seed_property_type(db_session)

        inputs = [
            _make_extracted_property(source_doi="10.1000/a"),
            _make_extracted_property(source_doi="10.1000/b"),
        ]

        result = await map_and_persist(db_session, inputs)

        assert result.created_sources == 2


@pytest.mark.unit
class TestMapAndPersistMapping:
    """Tests for correct extraction-to-DB field mapping."""

    async def test_creates_data_source_from_extraction(self, db_session: AsyncSession):
        """DataSource should be created with title from reference field."""
        await _seed_property_type(db_session)

        inputs = [
            _make_extracted_property(
                reference="Smith et al., J. Nucl. Mater.",
                source_doi="10.1000/test1",
            )
        ]

        result = await map_and_persist(db_session, inputs)

        assert result.created_sources == 1
        sources = (await db_session.execute(select(DataSource))).scalars().all()
        assert len(sources) == 1
        assert sources[0].doi == "10.1000/test1"
        assert "Smith" in sources[0].title

    async def test_creates_material_from_extraction(self, db_session: AsyncSession):
        """Material should be created with name and formula from extraction."""
        await _seed_property_type(db_session)

        inputs = [
            _make_extracted_property(
                material_name="UO2",
                composition="UO2",
                source_doi="10.1000/test1",
            )
        ]

        await map_and_persist(db_session, inputs)

        materials = (await db_session.execute(select(Material))).scalars().all()
        assert len(materials) == 1
        assert materials[0].name == "UO2"
        assert materials[0].formula == "UO2"

    async def test_creates_dataset_linking_material_and_source(self, db_session: AsyncSession):
        """Dataset should link material and source correctly."""
        await _seed_property_type(db_session)

        inputs = [
            _make_extracted_property(
                material_name="UO2",
                source_doi="10.1000/test1",
            )
        ]

        await map_and_persist(db_session, inputs)

        datasets = (await db_session.execute(select(Dataset))).scalars().all()
        assert len(datasets) == 1
        ds = datasets[0]
        assert ds.material_id is not None
        assert ds.source_id is not None

    async def test_creates_property_measurement(self, db_session: AsyncSession):
        """PropertyMeasurement should store extracted value as scalar."""
        pt = await _seed_property_type(
            db_session,
            property_name="Thermal Conductivity",
            property_slug="thermal-conductivity",
        )

        inputs = [
            _make_extracted_property(
                property_category="thermal",
                property_name="Thermal Conductivity",
                value="8.5",
                unit="W/(m·K)",
            )
        ]

        result = await map_and_persist(db_session, inputs)

        assert result.created_measurements == 1
        measurements = (await db_session.execute(select(PropertyMeasurement))).scalars().all()
        assert len(measurements) == 1
        assert measurements[0].value_scalar == 8.5
        assert measurements[0].property_type_id == pt.id

    async def test_creates_measurement_conditions(self, db_session: AsyncSession):
        """Conditions dict should map to MeasurementCondition fields."""
        await _seed_property_type(
            db_session,
            property_name="Thermal Conductivity",
            property_slug="thermal-conductivity",
        )

        inputs = [
            _make_extracted_property(
                conditions={
                    "temperature": 1000,
                    "pressure": 0.1,
                    "environment": "argon atmosphere",
                },
            )
        ]

        await map_and_persist(db_session, inputs)

        conditions = (await db_session.execute(select(MeasurementCondition))).scalars().all()
        assert len(conditions) == 1
        assert float(conditions[0].temperature) == 1000.0
        assert float(conditions[0].pressure) == 0.1
        assert conditions[0].environment == "argon atmosphere"

    async def test_skips_unknown_property_type(self, db_session: AsyncSession):
        """Properties with unknown category/name should not create measurements."""
        # Don't seed any PropertyType
        inputs = [_make_extracted_property(property_category="unknown_cat")]

        result = await map_and_persist(db_session, inputs)

        assert result.created_measurements == 0
        # But source and material should still be created
        assert result.created_sources == 1
        assert result.created_materials == 1


@pytest.mark.unit
class TestMapAndPersistTransaction:
    """Tests for transactional behavior."""

    async def test_validation_error_does_not_create_partial_records(self, db_session: AsyncSession):
        """If any item fails validation, no DB records should be created."""
        await _seed_property_type(db_session)

        inputs = [
            _make_extracted_property(source_doi="10.1000/test1"),
            {"bad": "data"},  # invalid
        ]

        result = await map_and_persist(db_session, inputs)

        assert result.validation_errors == 1
        sources = (await db_session.execute(select(DataSource))).scalars().all()
        assert len(sources) == 0
        materials = (await db_session.execute(select(Material))).scalars().all()
        assert len(materials) == 0


@pytest.mark.unit
class TestMappingResult:
    """Tests for the MappingResult dataclass."""

    def test_result_attributes(self):
        result = MappingResult(
            created_sources=1,
            created_materials=2,
            created_datasets=3,
            created_measurements=4,
            skipped_duplicates=5,
            validation_errors=1,
        )
        assert result.created_sources == 1
        assert result.created_materials == 2
        assert result.total_created == 10  # 1+2+3+4

    def test_result_defaults(self):
        result = MappingResult()
        assert result.created_sources == 0
        assert result.validation_errors == 0
        assert result.total_created == 0


@pytest.mark.unit
class TestMappingError:
    """Tests for the MappingError exception."""

    def test_mapping_error_message(self):
        err = MappingError("test error", item_index=3)
        assert "test error" in str(err)
        assert "3" in str(err)


@pytest.mark.integration
class TestMapAndPersistIntegration:
    """Integration test: full extraction output → DB records."""

    async def test_full_extraction_pipeline_output(self, db_session: AsyncSession):
        """Given realistic extraction output, verify correct DB records."""
        await _seed_property_type(
            db_session,
            category_name="thermal",
            category_slug="thermal",
            property_name="Thermal Conductivity",
            property_slug="thermal-conductivity",
        )
        await _seed_property_type(
            db_session,
            category_name="thermal",
            category_slug="thermal",
            property_name="Melting Point",
            property_slug="melting-point",
        )
        await _seed_property_type(
            db_session,
            category_name="mechanical",
            category_slug="mechanical",
            property_name="Density",
            property_slug="density",
        )

        extraction_output = [
            _make_extracted_property(
                source_doi="10.1000/uo2-thermal",
                reference="Smith et al., UO2 Thermal Study, J. Nucl. Mater.",
                material_name="UO2",
                composition="UO2",
                property_category="thermal",
                property_name="Thermal Conductivity",
                value="8.5",
                unit="W/(m·K)",
                conditions={"temperature": 500, "pressure": 0.1},
                confidence="high",
            ),
            _make_extracted_property(
                source_doi="10.1000/uo2-thermal",
                reference="Smith et al., UO2 Thermal Study, J. Nucl. Mater.",
                material_name="UO2",
                composition="UO2",
                property_category="thermal",
                property_name="Melting Point",
                value="3138",
                unit="K",
                conditions={"environment": "argon"},
                confidence="high",
            ),
            # Same source+material, different property
            _make_extracted_property(
                source_doi="10.1000/uo2-thermal",
                reference="Smith et al., UO2 Thermal Study, J. Nucl. Mater.",
                material_name="UO2",
                composition="UO2",
                property_category="mechanical",
                property_name="Density",
                value="10.97",
                unit="g/cm³",
                conditions={"temperature": 298},
                confidence="medium",
            ),
        ]

        result = await map_and_persist(db_session, extraction_output)

        # Verify counts
        assert result.created_sources == 1  # same DOI deduped
        assert result.created_materials == 1  # same material deduped
        assert result.created_datasets == 1  # one (material, source) pair
        assert result.created_measurements == 3  # 3 properties
        assert result.validation_errors == 0

        # Verify DB state
        sources = (await db_session.execute(select(DataSource))).scalars().all()
        assert len(sources) == 1
        assert sources[0].doi == "10.1000/uo2-thermal"

        materials = (await db_session.execute(select(Material))).scalars().all()
        assert len(materials) == 1
        assert materials[0].name == "UO2"

        datasets = (await db_session.execute(select(Dataset))).scalars().all()
        assert len(datasets) == 1

        measurements = (await db_session.execute(select(PropertyMeasurement))).scalars().all()
        assert len(measurements) == 3

        conditions = (await db_session.execute(select(MeasurementCondition))).scalars().all()
        assert len(conditions) == 3

        # Verify specific condition mapping
        temp_conditions = [c for c in conditions if c.temperature is not None]
        assert len(temp_conditions) == 2  # 500K and 298K
