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
    ONTOFUEL_CATEGORY_TO_SLUG,
    MappingError,
    MappingResult,
    _normalize_category_slug,
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
        """Properties with unknown category/name should not create measurements.

        Uses a valid Literal value ("other") but does NOT seed any matching
        PropertyType row, so the mapper hits the lookup miss path.
        """
        # Don't seed any PropertyType
        inputs = [_make_extracted_property(property_category="other")]

        result = await map_and_persist(db_session, inputs)

        assert result.created_measurements == 0
        # But source and material should still be created
        assert result.created_sources == 1
        assert result.created_materials == 1


@pytest.mark.unit
class TestMapAndPersistTransaction:
    """Tests for transactional behavior."""

    async def test_validation_error_partial_success(self, db_session: AsyncSession):
        """Valid items persist even when a sibling fails validation.

        NFM-1984 changed the mapper from all-or-nothing to partial-success:
        a single bad item no longer discards the entire batch. The valid
        item should be persisted; the invalid one should be counted as a
        validation error but not block the good data.
        """
        await _seed_property_type(
            db_session,
            property_name="Thermal Conductivity",
            property_slug="thermal-conductivity",
        )

        inputs = [
            _make_extracted_property(source_doi="10.1000/test1"),
            {"bad": "data"},  # invalid
        ]

        result = await map_and_persist(db_session, inputs)

        assert result.validation_errors == 1
        # Valid item should still be persisted (partial-success, NFM-1984)
        assert result.created_sources == 1
        assert result.created_measurements == 1


@pytest.mark.unit
class TestMappingResult:
    """Tests for the MappingResult dataclass."""

    def test_result_attributes(self):
        result = MappingResult(
            created_sources=1,
            created_materials=2,
            created_datasets=3,
            created_measurements=4,
            reused_entities=5,
            skipped_duplicate_measurements=3,
            validation_errors=1,
        )
        assert result.created_sources == 1
        assert result.created_materials == 2
        assert result.total_created == 10  # 1+2+3+4
        assert result.reused_entities == 5
        assert result.skipped_duplicate_measurements == 3

    def test_result_defaults(self):
        result = MappingResult()
        assert result.created_sources == 0
        assert result.validation_errors == 0
        assert result.total_created == 0
        assert result.reused_entities == 0
        assert result.skipped_duplicate_measurements == 0

    def test_skipped_duplicates_backward_compat_alias(self):
        """skipped_duplicates must be a backward-compat alias summing all skip reasons."""
        result = MappingResult(
            reused_entities=2,
            skipped_duplicate_measurements=3,
        )
        assert result.skipped_duplicates == 5  # 2 + 3

    def test_skipped_duplicates_zero_when_no_skips(self):
        result = MappingResult()
        assert result.skipped_duplicates == 0


@pytest.mark.unit
class TestMappingError:
    """Tests for the MappingError exception."""

    def test_mapping_error_message(self):
        err = MappingError("test error", item_index=3)
        assert "test error" in str(err)
        assert "3" in str(err)


# ---------------------------------------------------------------------------
# NFM-1994: OntoFuel category literal → DB slug normalisation
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestOntofuelCategoryNormalization:
    """OntoFuel category literals must resolve to DB property_types via slug.

    The DB ``property_categories`` table stores English ``name`` (e.g.
    "Thermal properties") and a short ``slug`` (e.g. "thermal").
    OntoFuel emits lowercase literals that match the slug, not the name.
    """

    async def test_thermal_literal_matches_slug(self, db_session: AsyncSession):
        """OntoFuel 'thermal' resolves via slug, not name."""
        await _seed_property_type(
            db_session,
            category_name="Thermal properties",  # production-style name
            category_slug="thermal",
            property_name="Thermal Conductivity",
            property_slug="thermal-conductivity",
        )

        extraction_output = [
            _make_extracted_property(
                property_category="thermal",
                property_name="Thermal Conductivity",
                value="8.5",
                unit="W/(m·K)",
            ),
        ]
        result = await map_and_persist(db_session, extraction_output)
        assert result.created_measurements == 1
        assert result.skipped_unknown_properties == 0

    async def test_mechanical_literal_matches_slug(self, db_session: AsyncSession):
        """OntoFuel 'mechanical' resolves via slug."""
        await _seed_property_type(
            db_session,
            category_name="Mechanical properties",
            category_slug="mechanical",
            property_name="Density",
            property_slug="density",
        )

        extraction_output = [
            _make_extracted_property(
                property_category="mechanical",
                property_name="Density",
                value="10.97",
                unit="g/cm3",
            ),
        ]
        result = await map_and_persist(db_session, extraction_output)
        assert result.created_measurements == 1

    async def test_nuclear_literal_matches_slug(self, db_session: AsyncSession):
        """OntoFuel 'nuclear' resolves via slug."""
        await _seed_property_type(
            db_session,
            category_name="Nuclear properties",
            category_slug="nuclear",
            property_name="fission_cross_section",
            property_slug="fission-cross-section",
        )

        extraction_output = [
            _make_extracted_property(
                property_category="nuclear",
                property_name="fission_cross_section",
                value="2.7",
                unit="barn",
            ),
        ]
        result = await map_and_persist(db_session, extraction_output)
        assert result.created_measurements == 1

    async def test_physical_literal_matches_slug(self, db_session: AsyncSession):
        """OntoFuel 'physical' resolves via slug."""
        await _seed_property_type(
            db_session,
            category_name="Physical properties",
            category_slug="physical",
            property_name="Melting Point",
            property_slug="melting-point",
        )

        extraction_output = [
            _make_extracted_property(
                property_category="physical",
                property_name="Melting Point",
                value="3138",
                unit="K",
            ),
        ]
        result = await map_and_persist(db_session, extraction_output)
        assert result.created_measurements == 1

    async def test_diffusion_literal_falls_back_to_physical(self, db_session: AsyncSession):
        """OntoFuel 'diffusion' has no DB category; falls back to 'physical'."""
        await _seed_property_type(
            db_session,
            category_name="Physical properties",
            category_slug="physical",
            property_name="diffusion_coefficient",
            property_slug="diffusion-coefficient",
        )

        extraction_output = [
            _make_extracted_property(
                property_category="diffusion",
                property_name="diffusion_coefficient",
                value="1.2e-10",
                unit="m2/s",
            ),
        ]
        result = await map_and_persist(db_session, extraction_output)
        assert result.created_measurements == 1
        assert result.skipped_unknown_properties == 0

    async def test_irradiation_literal_falls_back_to_nuclear(self, db_session: AsyncSession):
        """OntoFuel 'irradiation' has no DB category; falls back to 'nuclear'."""
        await _seed_property_type(
            db_session,
            category_name="Nuclear properties",
            category_slug="nuclear",
            property_name="swelling_rate",
            property_slug="swelling-rate",
        )

        extraction_output = [
            _make_extracted_property(
                property_category="irradiation",
                property_name="swelling_rate",
                value="0.5",
                unit="%/dpa",
            ),
        ]
        result = await map_and_persist(db_session, extraction_output)
        assert result.created_measurements == 1
        assert result.skipped_unknown_properties == 0

    async def test_none_category_skips_unknown(self, db_session: AsyncSession):
        """A None property_category is skipped (skipped_unknown_properties)."""
        await _seed_property_type(
            db_session,
            category_name="Thermal properties",
            category_slug="thermal",
            property_name="Thermal Conductivity",
            property_slug="thermal-conductivity",
        )

        extraction_output = [
            _make_extracted_property(
                property_category=None,
                property_name="Thermal Conductivity",
                value="8.5",
                unit="W/(m·K)",
            ),
        ]
        result = await map_and_persist(db_session, extraction_output)
        assert result.created_measurements == 0
        assert result.skipped_unknown_properties == 1

    async def test_other_literal_falls_back_to_thermal(self, db_session: AsyncSession):
        """OntoFuel 'other' has no DB category; falls back to 'thermal'."""
        await _seed_property_type(
            db_session,
            category_name="Thermal properties",
            category_slug="thermal",
            property_name="custom_value",
            property_slug="custom-value",
        )

        extraction_output = [
            _make_extracted_property(
                property_category="other",
                property_name="custom_value",
                value="42",
                unit="unitless",
            ),
        ]
        result = await map_and_persist(db_session, extraction_output)
        assert result.created_measurements == 1
        assert result.skipped_unknown_properties == 0

    def test_normalize_category_slug_direct_matches(self):
        """The four OntoFuel literals with direct DB slugs map 1:1."""
        assert _normalize_category_slug("thermal") == "thermal"
        assert _normalize_category_slug("mechanical") == "mechanical"
        assert _normalize_category_slug("nuclear") == "nuclear"
        assert _normalize_category_slug("physical") == "physical"

    def test_normalize_category_slug_fallbacks(self):
        """OntoFuel literals without dedicated DB categories fall back."""
        assert _normalize_category_slug("diffusion") == "physical"
        assert _normalize_category_slug("irradiation") == "nuclear"
        assert _normalize_category_slug("other") == "thermal"

    def test_normalize_category_slug_unknown_returns_none(self):
        """Unrecognised literals return None."""
        assert _normalize_category_slug("nonexistent") is None
        assert _normalize_category_slug("") is None

    def test_mapping_covers_all_ontofuel_literals(self):
        """Every PropertyCategoryLiteral has an entry in the mapping."""
        from nfm_db.schemas.extraction import PropertyCategoryLiteral

        # PropertyCategoryLiteral is a Literal type; inspect its __args__
        literals = PropertyCategoryLiteral.__args__  # type: ignore[attr-defined]
        for lit in literals:
            assert lit in ONTOFUEL_CATEGORY_TO_SLUG, (
                f"OntoFuel literal {lit!r} missing from ONTOFUEL_CATEGORY_TO_SLUG"
            )


# ---------------------------------------------------------------------------
# NFM-1996: split counter separation tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSplitCounterSeparation:
    """NFM-1996: reused_entities vs skipped_duplicate_measurements.

    Entity reuse (DataSource/Material found in DB) → reused_entities.
    5-tuple measurement dedup → skipped_duplicate_measurements.
    Unknown property type → neither (tracked internally only).
    """

    async def test_entity_reuse_increments_reused_entities(
        self, db_session: AsyncSession,
    ) -> None:
        """Pre-seeded DataSource in DB + same DOI → reused_entities incremented.

        Entity reuse only fires when _find_source_by_doi finds an existing
        row in the DB. Same-batch dedup uses in-memory maps, not reused_entities.
        """
        await _seed_property_type(
            db_session,
            property_name="Thermal Conductivity",
            property_slug="thermal-conductivity",
        )
        # Pre-seed a DataSource in the DB
        existing_source = DataSource(
            doi="10.1000/reuse-test",
            title="Pre-existing Paper",
            source_type="journal_article",
        )
        db_session.add(existing_source)
        await db_session.commit()

        inputs = [
            _make_extracted_property(source_doi="10.1000/reuse-test"),
        ]

        result = await map_and_persist(db_session, inputs)

        assert result.reused_entities == 1, (
            "DataSource found by DOI in DB → entity reuse"
        )
        assert result.skipped_duplicate_measurements == 0, (
            "No measurement dedup — single unique item"
        )
        assert result.skipped_duplicates == result.reused_entities + result.skipped_duplicate_measurements

    async def test_material_reuse_increments_reused_entities(
        self, db_session: AsyncSession,
    ) -> None:
        """Pre-seeded Material in DB + same formula → reused_entities incremented.

        Uses different DOI so DataSource is NOT reused — only Material reuse.
        """
        await _seed_property_type(
            db_session,
            property_name="Thermal Conductivity",
            property_slug="thermal-conductivity",
        )
        # Pre-seed a Material in the DB
        existing_material = Material(
            name="UO2",
            formula="UO2",
            is_active=True,
        )
        db_session.add(existing_material)
        await db_session.commit()

        inputs = [
            _make_extracted_property(
                material_name="UO2",
                composition="UO2",
                source_doi="10.1000/mat-a",
            ),
        ]

        result = await map_and_persist(db_session, inputs)

        assert result.reused_entities >= 1, (
            "Material found by formula in DB → entity reuse"
        )

    async def test_measurement_dedup_increments_skipped_duplicate_measurements(
        self, db_session: AsyncSession,
    ) -> None:
        """Two items with identical 5-tuple → second is measurement dedup.

        skipped_duplicate_measurements must be incremented.
        reused_entities may also be incremented (same DOI/material).
        """
        await _seed_property_type(
            db_session,
            property_name="Thermal Conductivity",
            property_slug="thermal-conductivity",
        )

        item = _make_extracted_property(
            material_name="UO2",
            property_name="Thermal Conductivity",
            source_doi="10.1000/dedup-test",
        )
        dup = dict(item)

        result = await map_and_persist(db_session, [item, dup])

        assert result.skipped_duplicate_measurements == 1, (
            "Exact 5-tuple duplicate → skipped_duplicate_measurements"
        )
        assert result.skipped_duplicates == (
            result.reused_entities + result.skipped_duplicate_measurements
        )

    async def test_unknown_property_not_in_split_counters(
        self, db_session: AsyncSession,
    ) -> None:
        """Unknown PropertyType should NOT increment reused_entities or
        skipped_duplicate_measurements — it's a separate skip reason.
        """
        # Don't seed any PropertyType
        inputs = [_make_extracted_property(property_category="other")]

        result = await map_and_persist(db_session, inputs)

        assert result.created_measurements == 0
        assert result.reused_entities == 0, (
            "Unknown property skip is not entity reuse"
        )
        assert result.skipped_duplicate_measurements == 0, (
            "Unknown property skip is not measurement dedup"
        )


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
            category_name="physical",
            category_slug="physical",
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
                property_category="physical",
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


# ---------------------------------------------------------------------------
# AC-4 (NFM-1979): value float-conversion + raw-string fallback
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestValueFloatConversion:
    """Mapper must float-parse value, falling back to value_text + warning."""

    async def test_valid_float_writes_value_scalar(
        self, db_session: AsyncSession, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A numeric `value` is parsed into value_scalar (Numeric column)."""
        await _seed_property_type(
            db_session,
            property_name="Thermal Conductivity",
            property_slug="thermal-conductivity",
        )

        inputs = [_make_extracted_property(value="8.5")]

        with caplog.at_level("WARNING", logger="nfm_db.services.extraction_to_db_mapper"):
            result = await map_and_persist(db_session, inputs)

        assert result.created_measurements == 1
        measurements = (await db_session.execute(select(PropertyMeasurement))).scalars().all()
        assert len(measurements) == 1
        assert float(measurements[0].value_scalar) == 8.5
        assert measurements[0].value_text is None

    async def test_non_parseable_value_falls_back_to_value_text(
        self, db_session: AsyncSession, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A non-parseable `value` ("3 to 4") must NOT crash the batch.

        Behavior (NFM-1979 AC-4):
        - mapper writes raw string into `value_text`
        - logs a WARNING
        - the rest of the batch continues
        """
        await _seed_property_type(
            db_session,
            property_name="Thermal Conductivity",
            property_slug="thermal-conductivity",
        )

        inputs = [_make_extracted_property(value="3 to 4")]

        with caplog.at_level("WARNING", logger="nfm_db.services.extraction_to_db_mapper"):
            result = await map_and_persist(db_session, inputs)

        assert result.created_measurements == 1
        measurements = (await db_session.execute(select(PropertyMeasurement))).scalars().all()
        assert len(measurements) == 1
        assert measurements[0].value_scalar is None
        assert measurements[0].value_text == "3 to 4"
        # Warning emitted with the raw value so we can diagnose upstream
        warning_records = [
            r for r in caplog.records if r.levelname == "WARNING"
        ]
        assert any("3 to 4" in r.getMessage() for r in warning_records), (
            f"expected WARNING mentioning '3 to 4', got: {[r.getMessage() for r in warning_records]}"
        )

    async def test_non_parseable_value_does_not_block_batch(
        self, db_session: AsyncSession
    ) -> None:
        """One bad value must NOT block other items in the same batch."""
        await _seed_property_type(
            db_session,
            category_name="thermal",
            category_slug="thermal",
            property_name="Thermal Conductivity",
            property_slug="thermal-conductivity",
        )
        await _seed_property_type(
            db_session,
            category_name="physical",
            category_slug="physical",
            property_name="Density",
            property_slug="density",
        )

        inputs = [
            _make_extracted_property(
                value="not a number",
                source_doi="10.1000/bad",
                property_category="thermal",
                property_name="Thermal Conductivity",
            ),
            _make_extracted_property(
                value="10.97",
                source_doi="10.1000/good",
                property_category="physical",
                property_name="Density",
                unit="g/cm³",
            ),
        ]

        result = await map_and_persist(db_session, inputs)

        assert result.validation_errors == 0
        assert result.created_measurements == 2
        measurements = (await db_session.execute(select(PropertyMeasurement))).scalars().all()
        text_only = [m for m in measurements if m.value_text is not None]
        scalar_only = [m for m in measurements if m.value_scalar is not None]
        assert len(text_only) == 1
        assert text_only[0].value_text == "not a number"
        assert len(scalar_only) == 1
        assert float(scalar_only[0].value_scalar) == 10.97


@pytest.mark.unit
class TestConditionsStandardKeysRoundTrip:
    """Mapper must preserve all 5 standard conditions keys (NFM-1979 AC-4).

    Standard keys: temperature, pressure, neutron_flux, dose, strain_rate.
    Unknown keys are captured in `notes`.
    """

    async def test_standard_conditions_round_trip(
        self, db_session: AsyncSession
    ) -> None:
        await _seed_property_type(
            db_session,
            property_name="Thermal Conductivity",
            property_slug="thermal-conductivity",
        )

        inputs = [
            _make_extracted_property(
                conditions={
                    "temperature": 600,
                    "pressure": 0.1,
                    "neutron_flux": 1.0e18,
                    "dose": 5.0,
                    "strain_rate": 1.0e-6,
                }
            )
        ]

        result = await map_and_persist(db_session, inputs)

        assert result.created_measurements == 1
        conds = (await db_session.execute(select(MeasurementCondition))).scalars().all()
        assert len(conds) == 1
        c = conds[0]
        # temperature + pressure have direct DB columns
        assert float(c.temperature) == 600.0
        assert float(c.pressure) == 0.1
        # dose key maps to irradiation_dose column
        assert float(c.irradiation_dose) == 5.0
        # neutron_flux and strain_rate are NOT in DB columns → captured in notes
        assert c.notes is not None
        assert "neutron_flux" in c.notes
        assert "strain_rate" in c.notes

    async def test_unknown_conditions_key_captured_in_notes(
        self, db_session: AsyncSession
    ) -> None:
        """An unknown conditions key (e.g., 'humidity') is preserved in notes."""
        await _seed_property_type(
            db_session,
            property_name="Thermal Conductivity",
            property_slug="thermal-conductivity",
        )

        inputs = [
            _make_extracted_property(
                conditions={"temperature": 300, "humidity": 0.65}
            )
        ]

        await map_and_persist(db_session, inputs)

        conds = (await db_session.execute(select(MeasurementCondition))).scalars().all()
        assert len(conds) == 1
        assert conds[0].notes is not None
        assert "humidity" in conds[0].notes


# ---------------------------------------------------------------------------
# NFM-1981 AC-2: 5-tuple measurement dedup (refined by NFM-2032)
# ---------------------------------------------------------------------------
# NFM-1981 CPO Decision 1: Dedup key = (material_name, property_name,
#   source_reference, conditions_hash, measurement_method).
# NFM-1981 CPO Decision 2: Strategy C (keep all) — only skip when 5-tuple
#   is identical.
# NFM-2032: cross-request dedup is DB-backed via
#   (dataset_id, property_type_id, conditions_hash).  ``method`` is NOT
#   a column on ``property_measurements`` (yet) so it cannot be part of
#   the queryable dedup key today.  This is a known limitation — a
#   follow-up migration adding ``method`` to the table will let the
#   DB-level dedup honour all 5 tuple components.  Different
#   conditions / material / property / dataset still produce separate
#   measurements, which is what the AC requires.
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFiveTupleMeasurementDedup:
    """Dedup behaviour for the queryable subset of the 5-tuple.

    NFM-2032 narrows the DB-side dedup to
    ``(dataset_id, property_type_id, conditions_hash)`` because
    ``method`` is not yet a column on ``property_measurements``.  The
    in-memory short-circuit still uses the full 5-tuple key, but the
    DB-level cross-request check is the authoritative one.  Tests in
    this class cover material / property / conditions / dataset
    differences, which the AC explicitly requires to produce separate
    measurements.
    """

    async def test_exact_5tuple_match_skips_duplicate(
        self, db_session: AsyncSession,
    ) -> None:
        """Two items with identical 5-tuple → second is skipped.

        skipped_duplicates must be incremented so the response is
        reproducibly verifiable.
        """
        await _seed_property_type(
            db_session,
            property_name="Thermal Conductivity",
            property_slug="thermal-conductivity",
        )

        item = _make_extracted_property(
            material_name="U-10Mo",
            property_name="Thermal Conductivity",
            value="11.0",
            unit="W/(m·K)",
            conditions={"temperature": 25},
            reference="Smith et al.",
        )
        # Exact duplicate — same everything
        dup = dict(item)

        result = await map_and_persist(db_session, [item, dup])

        assert result.created_measurements == 1
        assert result.skipped_duplicates == 1

    async def test_different_conditions_two_measurements(
        self, db_session: AsyncSession,
    ) -> None:
        """Same material+property+source+method but different conditions → 2 rows.

        Rationale (CPO): U-10Mo thermal conductivity at 25°C vs 400°C
        are genuinely different measurements.
        """
        await _seed_property_type(
            db_session,
            property_name="Thermal Conductivity",
            property_slug="thermal-conductivity",
        )

        item_a = _make_extracted_property(
            material_name="U-10Mo",
            property_name="Thermal Conductivity",
            value="11.0",
            unit="W/(m·K)",
            conditions={"temperature": 25},
            reference="Smith et al.",
        )
        item_b = _make_extracted_property(
            material_name="U-10Mo",
            property_name="Thermal Conductivity",
            value="8.5",
            unit="W/(m·K)",
            conditions={"temperature": 400},
            reference="Smith et al.",
        )

        result = await map_and_persist(db_session, [item_a, item_b])

        assert result.created_measurements == 2
        assert result.skipped_duplicates == 0

    async def test_different_method_two_measurements(
        self, db_session: AsyncSession,
    ) -> None:
        """Same material+property+source+conditions but different method → 2 rows.

        NFM-2032 (NFM-1972 AC-2): the 5-tuple dedup key includes
        ``measurement_method`` (NFM-1981 AC-2).  Migration 033 persists
        ``method`` on ``property_measurements`` and the composite UNIQUE
        INDEX ``uq_pm_dedup`` includes it in the dedup predicate.  Two
        measurements that differ only in ``method`` therefore create
        distinct rows.
        """
        await _seed_property_type(
            db_session,
            property_name="Yield Strength",
            property_slug="yield-strength",
        )

        item_a = _make_extracted_property(
            material_name="U-10Mo",
            property_name="Yield Strength",
            value="350",
            unit="MPa",
            conditions={"temperature": 25},
            reference="Smith et al.",
        )
        item_b = {
            **_make_extracted_property(
                material_name="U-10Mo",
                property_name="Yield Strength",
                value="420",
                unit="MPa",
                conditions={"temperature": 25},
                reference="Smith et al.",
            ),
            "method": "nanoindentation",
        }

        result = await map_and_persist(db_session, [item_a, item_b])

        # NFM-2032: method is now part of the DB-side 5-tuple dedup key.
        # Different method → exactly 2 rows (the original NFM-1981 AC-2
        # expectation, restored after the rejected `11eef99` test was
        # incorrectly inverted to accept data loss).
        assert result.created_measurements == 2, (
            "NFM-2032: different measurement methods must produce 2 "
            "distinct rows (5-tuple dedup)."
        )
        assert result.skipped_duplicate_measurements == 0

        # Verify two rows actually exist on disk.
        from sqlalchemy import func, select

        from nfm_db.models.property import PropertyMeasurement

        count = (
            await db_session.execute(
                select(func.count(PropertyMeasurement.id))
            )
        ).scalar_one()
        assert count == 2

    async def test_different_material_two_measurements(
        self, db_session: AsyncSession,
    ) -> None:
        """Different material_name → 2 measurements (different 5-tuple)."""
        await _seed_property_type(
            db_session,
            property_name="Thermal Conductivity",
            property_slug="thermal-conductivity",
        )

        item_a = _make_extracted_property(
            material_name="UO2",
            composition="UO2",
            property_name="Thermal Conductivity",
        )
        item_b = _make_extracted_property(
            material_name="U-10Mo",
            composition="U-10Mo",
            property_name="Thermal Conductivity",
        )

        result = await map_and_persist(db_session, [item_a, item_b])

        assert result.created_measurements == 2
        assert result.skipped_duplicates == 0

    async def test_different_property_two_measurements(
        self, db_session: AsyncSession,
    ) -> None:
        """Different property name → 2 measurements (different 5-tuple)."""
        await _seed_property_type(
            db_session,
            property_name="Thermal Conductivity",
            property_slug="thermal-conductivity",
        )
        await _seed_property_type(
            db_session,
            category_name="physical",
            category_slug="physical",
            property_name="Melting Point",
            property_slug="melting-point",
        )

        item_a = _make_extracted_property(property_name="Thermal Conductivity")
        item_b = _make_extracted_property(
            property_name="Melting Point",
            value="3138",
            unit="K",
            property_category="physical",
        )

        result = await map_and_persist(db_session, [item_a, item_b])

        assert result.created_measurements == 2
        assert result.skipped_duplicates == 0

    async def test_skipped_duplicates_reproducible_in_response(
        self, db_session: AsyncSession,
    ) -> None:
        """skipped_duplicates count must be deterministic and verifiable.

        Two identical items in the same batch → 1 created, 1 skipped.
        """
        await _seed_property_type(
            db_session,
            property_name="Thermal Conductivity",
            property_slug="thermal-conductivity",
        )

        unique_item = _make_extracted_property(
            material_name="UO2",
            property_name="Thermal Conductivity",
            conditions={"temperature": 500},
        )
        dup_item = dict(unique_item)

        result = await map_and_persist(db_session, [unique_item, dup_item])

        assert result.skipped_duplicates == 1
        assert result.created_measurements == 1

    async def test_conditions_hash_stable_across_key_order(
        self, db_session: AsyncSession,
    ) -> None:
        """conditions dict with different key order must produce same hash.

        Ensures stable JSON serialization (sort_keys=True).
        """
        await _seed_property_type(
            db_session,
            property_name="Thermal Conductivity",
            property_slug="thermal-conductivity",
        )

        item_a = _make_extracted_property(
            conditions={"temperature": 25, "pressure": 0.1},
        )
        item_b = _make_extracted_property(
            conditions={"pressure": 0.1, "temperature": 25},
        )

        result = await map_and_persist(db_session, [item_a, item_b])

        # Same conditions in different order → same hash → skipped
        assert result.created_measurements == 1
        assert result.skipped_duplicates == 1

    async def test_none_conditions_vs_empty_conditions_same_key(
        self, db_session: AsyncSession,
    ) -> None:
        """None conditions and {} conditions should hash identically.

        Both represent "no conditions".
        """
        await _seed_property_type(
            db_session,
            property_name="Thermal Conductivity",
            property_slug="thermal-conductivity",
        )

        item_a = _make_extracted_property(conditions=None)
        item_b = _make_extracted_property(conditions={})

        result = await map_and_persist(db_session, [item_a, item_b])

        assert result.created_measurements == 1
        assert result.skipped_duplicates == 1
