"""Tests for Phase 1 core models, relationships, constraints, and schemas.

Covers all 14 Phase 1 tables:
- units, unit_conversions
- property_categories, property_types
- material_categories, materials, material_aliases, material_compositions
- data_sources, authors, data_source_authors
- datasets, property_measurements, measurement_conditions
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models import (
    Author,
    DataSource,
    DataSourceAuthor,
    Dataset,
    Material,
    MaterialAlias,
    MaterialCategory,
    MaterialComposition,
    MeasurementCondition,
    PropertyCategory,
    PropertyMeasurement,
    PropertyType,
    Unit,
    UnitConversion,
)

# Helper to eagerly refresh a relationship in async sessions
# (avoids MissingGreenlet from implicit lazy load)
async def _refresh_rel(session: AsyncSession, obj: object, *attrs: str) -> None:
    await session.refresh(obj, list(attrs))


# ============================================================
# Model Creation Tests — one per model
# ============================================================


class TestMaterialCategoryCreation:
    """MaterialCategory model creation tests."""

    @pytest.mark.asyncio
    async def test_create_material_category(
        self, db_session: AsyncSession,
    ) -> None:
        """MaterialCategory can be created with name, slug, timestamps."""
        category = MaterialCategory(name="Fuel", slug="fuel")
        db_session.add(category)
        await db_session.commit()
        await db_session.refresh(category)

        assert category.id is not None
        assert category.name == "Fuel"
        assert category.slug == "fuel"
        assert category.created_at is not None
        assert category.updated_at is not None


class TestMaterialCreation:
    """Material model creation tests."""

    @pytest.mark.asyncio
    async def test_create_material(self, db_session: AsyncSession) -> None:
        """Material can be created with FK to category."""
        category = MaterialCategory(name="Fuel", slug="fuel")
        db_session.add(category)
        await db_session.flush()

        material = Material(
            name="UO2",
            formula="UO2",
            crystal_structure="Fluorite",
            category_id=category.id,
            is_active=True,
        )
        db_session.add(material)
        await db_session.commit()
        await db_session.refresh(material)

        assert material.id is not None
        assert material.name == "UO2"
        assert material.category_id == category.id
        assert material.created_at is not None


class TestMaterialAliasCreation:
    """MaterialAlias model creation tests."""

    @pytest.mark.asyncio
    async def test_create_material_alias(
        self, db_session: AsyncSession,
    ) -> None:
        """MaterialAlias can be created with FK to material."""
        material = Material(name="Zircaloy-4")
        db_session.add(material)
        await db_session.flush()

        alias = MaterialAlias(
            material_id=material.id,
            alias_name="Zr-4",
            alias_type="abbreviation",
        )
        db_session.add(alias)
        await db_session.commit()
        await db_session.refresh(alias)

        assert alias.id is not None
        assert alias.alias_name == "Zr-4"
        assert alias.material_id == material.id


class TestMaterialCompositionCreation:
    """MaterialComposition model creation tests."""

    @pytest.mark.asyncio
    async def test_create_material_composition(
        self, db_session: AsyncSession,
    ) -> None:
        """MaterialComposition can be created with element and fraction."""
        material = Material(name="UO2")
        db_session.add(material)
        await db_session.flush()

        comp = MaterialComposition(
            material_id=material.id,
            element="U",
            fraction=0.6667,
        )
        db_session.add(comp)
        await db_session.commit()
        await db_session.refresh(comp)

        assert comp.id is not None
        assert comp.element == "U"
        assert float(comp.fraction) == pytest.approx(0.6667)


class TestUnitCreation:
    """Unit model creation tests."""

    @pytest.mark.asyncio
    async def test_create_unit(self, db_session: AsyncSession) -> None:
        """Unit can be created with symbol, name, dimension."""
        unit = Unit(symbol="K", name="kelvin", dimension="temperature")
        db_session.add(unit)
        await db_session.commit()
        await db_session.refresh(unit)

        assert unit.id is not None
        assert unit.symbol == "K"
        assert unit.name == "kelvin"
        assert unit.dimension == "temperature"


class TestUnitConversionCreation:
    """UnitConversion model creation tests."""

    @pytest.mark.asyncio
    async def test_create_unit_conversion(
        self, db_session: AsyncSession,
    ) -> None:
        """UnitConversion can be created between two units."""
        k = Unit(symbol="K", name="kelvin", dimension="temperature")
        c = Unit(symbol="°C", name="celsius", dimension="temperature")
        db_session.add_all([k, c])
        await db_session.flush()

        conv = UnitConversion(
            source_unit_id=c.id,
            target_unit_id=k.id,
            factor=1.0,
            offset=273.15,
        )
        db_session.add(conv)
        await db_session.commit()
        await db_session.refresh(conv)

        assert conv.id is not None
        assert conv.source_unit_id == c.id
        assert conv.target_unit_id == k.id
        assert float(conv.factor) == 1.0


class TestPropertyCategoryCreation:
    """PropertyCategory model creation tests."""

    @pytest.mark.asyncio
    async def test_create_property_category(
        self, db_session: AsyncSession,
    ) -> None:
        """PropertyCategory can be created."""
        cat = PropertyCategory(name="Thermal", slug="thermal")
        db_session.add(cat)
        await db_session.commit()
        await db_session.refresh(cat)

        assert cat.id is not None
        assert cat.name == "Thermal"
        assert cat.slug == "thermal"


class TestPropertyTypeCreation:
    """PropertyType model creation tests."""

    @pytest.mark.asyncio
    async def test_create_property_type(self, db_session: AsyncSession) -> None:
        """PropertyType can be created with FK to category and unit."""
        unit = Unit(symbol="W/(m·K)", name="thermal conductivity", dimension="power")
        cat = PropertyCategory(name="Thermal", slug="thermal")
        db_session.add_all([unit, cat])
        await db_session.flush()

        pt = PropertyType(
            category_id=cat.id,
            name="Thermal Conductivity",
            slug="thermal-conductivity",
            value_type="scalar",
            unit_id=unit.id,
        )
        db_session.add(pt)
        await db_session.commit()
        await db_session.refresh(pt)

        assert pt.id is not None
        assert pt.category_id == cat.id
        assert pt.unit_id == unit.id


class TestDataSourceCreation:
    """DataSource model creation tests."""

    @pytest.mark.asyncio
    async def test_create_data_source(self, db_session: AsyncSession) -> None:
        """DataSource can be created with DOI and title."""
        source = DataSource(
            doi="10.1016/j.jnucmat.2020.152300",
            title="Thermal conductivity of UO2",
            year=2020,
            source_type="journal_article",
        )
        db_session.add(source)
        await db_session.commit()
        await db_session.refresh(source)

        assert source.id is not None
        assert source.doi == "10.1016/j.jnucmat.2020.152300"
        assert source.title == "Thermal conductivity of UO2"


class TestAuthorCreation:
    """Author model creation tests."""

    @pytest.mark.asyncio
    async def test_create_author(self, db_session: AsyncSession) -> None:
        """Author can be created with ORCID."""
        author = Author(
            full_name="John Smith",
            last_name="Smith",
            first_name="John",
            orcid="0000-0001-2345-6789",
            affiliation="MIT",
        )
        db_session.add(author)
        await db_session.commit()
        await db_session.refresh(author)

        assert author.id is not None
        assert author.orcid == "0000-0001-2345-6789"
        assert author.full_name == "John Smith"


class TestDataSourceAuthorCreation:
    """DataSourceAuthor model creation tests."""

    @pytest.mark.asyncio
    async def test_create_data_source_author(
        self, db_session: AsyncSession,
    ) -> None:
        """DataSourceAuthor can be created as M2M join."""
        source = DataSource(title="Paper A", source_type="journal_article")
        author = Author(full_name="Alice", last_name="Alice")
        db_session.add_all([source, author])
        await db_session.flush()

        link = DataSourceAuthor(
            data_source_id=source.id,
            author_id=author.id,
            author_order=1,
            is_corresponding=True,
        )
        db_session.add(link)
        await db_session.commit()
        await db_session.refresh(link)

        assert link.id is not None
        assert link.data_source_id == source.id
        assert link.author_id == author.id
        assert link.is_corresponding is True


class TestDatasetCreation:
    """Dataset model creation tests."""

    @pytest.mark.asyncio
    async def test_create_dataset(self, db_session: AsyncSession) -> None:
        """Dataset can be created with FK to material and source."""
        material = Material(name="UO2")
        source = DataSource(title="Paper A", source_type="journal_article")
        db_session.add_all([material, source])
        await db_session.flush()

        dataset = Dataset(
            material_id=material.id,
            source_id=source.id,
            title="Thermal conductivity dataset",
            is_verified=False,
        )
        db_session.add(dataset)
        await db_session.commit()
        await db_session.refresh(dataset)

        assert dataset.id is not None
        assert dataset.material_id == material.id
        assert dataset.source_id == source.id


class TestPropertyMeasurementCreation:
    """PropertyMeasurement model creation tests."""

    @pytest.mark.asyncio
    async def test_create_property_measurement(
        self, db_session: AsyncSession,
    ) -> None:
        """PropertyMeasurement can be created with value_scalar."""
        unit = Unit(symbol="W/(m·K)", name="thermal conductivity", dimension="power")
        cat = PropertyCategory(name="Thermal", slug="thermal")
        db_session.add_all([unit, cat])
        await db_session.flush()

        pt = PropertyType(
            category_id=cat.id, name="TC", slug="tc",
            value_type="scalar", unit_id=unit.id,
        )
        db_session.add(pt)
        await db_session.flush()

        material = Material(name="UO2")
        source = DataSource(title="Paper A", source_type="journal_article")
        db_session.add_all([material, source])
        await db_session.flush()

        dataset = Dataset(material_id=material.id, source_id=source.id, title="DS1")
        db_session.add(dataset)
        await db_session.flush()

        measurement = PropertyMeasurement(
            dataset_id=dataset.id,
            property_type_id=pt.id,
            value_scalar=3.5,
            unit_id=unit.id,
        )
        db_session.add(measurement)
        await db_session.commit()
        await db_session.refresh(measurement)

        assert measurement.id is not None
        assert float(measurement.value_scalar) == pytest.approx(3.5)


class TestMeasurementConditionCreation:
    """MeasurementCondition model creation tests."""

    @pytest.mark.asyncio
    async def test_create_measurement_condition(
        self, db_session: AsyncSession,
    ) -> None:
        """MeasurementCondition can be created with FK to measurement."""
        unit = Unit(symbol="K", name="kelvin", dimension="temperature")
        cat = PropertyCategory(name="Thermal", slug="thermal")
        db_session.add_all([unit, cat])
        await db_session.flush()

        pt = PropertyType(
            category_id=cat.id, name="TC", slug="tc",
            value_type="scalar", unit_id=unit.id,
        )
        db_session.add(pt)
        await db_session.flush()

        material = Material(name="UO2")
        source = DataSource(title="Paper A", source_type="journal_article")
        db_session.add_all([material, source])
        await db_session.flush()

        dataset = Dataset(material_id=material.id, source_id=source.id, title="DS1")
        db_session.add(dataset)
        await db_session.flush()

        measurement = PropertyMeasurement(
            dataset_id=dataset.id, property_type_id=pt.id, value_scalar=3.5,
        )
        db_session.add(measurement)
        await db_session.flush()

        condition = MeasurementCondition(
            measurement_id=measurement.id,
            temperature=1200.0,
            pressure=101.325,
            environment="inert gas",
        )
        db_session.add(condition)
        await db_session.commit()
        await db_session.refresh(condition)

        assert condition.id is not None
        assert condition.measurement_id == measurement.id
        assert float(condition.temperature) == pytest.approx(1200.0)


# ============================================================
# Relationship Tests
# ============================================================


class TestRelationships:
    """ORM relationship tests across all domains."""

    @pytest.mark.asyncio
    async def test_material_has_many_aliases(
        self, db_session: AsyncSession,
    ) -> None:
        """material -> material_aliases (one-to-many, cascade delete)."""
        material = Material(name="Zircaloy-4")
        db_session.add(material)
        await db_session.flush()

        db_session.add_all([
            MaterialAlias(
                material_id=material.id, alias_name="Zr-4", alias_type="abbreviation",
            ),
            MaterialAlias(
                material_id=material.id,
                alias_name="Zircaloy 4",
                alias_type="common_name",
            ),
        ])
        await db_session.commit()
        await _refresh_rel(db_session, material, "aliases")

        assert len(material.aliases) == 2
        alias_names = {a.alias_name for a in material.aliases}
        assert "Zr-4" in alias_names
        assert "Zircaloy 4" in alias_names

    @pytest.mark.asyncio
    async def test_material_has_many_compositions(
        self, db_session: AsyncSession,
    ) -> None:
        """material -> material_compositions (one-to-many, cascade delete)."""
        material = Material(name="UO2")
        db_session.add(material)
        await db_session.flush()

        db_session.add_all([
            MaterialComposition(material_id=material.id, element="U", fraction=0.6667),
            MaterialComposition(material_id=material.id, element="O", fraction=0.3333),
        ])
        await db_session.commit()
        await _refresh_rel(db_session, material, "composition")

        assert len(material.composition) == 2

    @pytest.mark.asyncio
    async def test_material_category_self_referential(
        self, db_session: AsyncSession,
    ) -> None:
        """material_category -> children (self-referential one-to-many)."""
        parent = MaterialCategory(name="Fuel", slug="fuel")
        db_session.add(parent)
        await db_session.flush()

        child = MaterialCategory(
            name="UO2 Fuel", slug="uo2-fuel", parent_id=parent.id,
        )
        db_session.add(child)
        await db_session.commit()
        await _refresh_rel(db_session, parent, "children")

        assert len(parent.children) == 1
        assert parent.children[0].name == "UO2 Fuel"

    @pytest.mark.asyncio
    async def test_dataset_has_many_measurements(
        self, db_session: AsyncSession,
    ) -> None:
        """dataset -> property_measurements (one-to-many, cascade delete)."""
        cat = PropertyCategory(name="Thermal", slug="thermal")
        db_session.add(cat)
        await db_session.flush()

        pt = PropertyType(
            category_id=cat.id, name="TC", slug="tc",
            value_type="scalar",
        )
        db_session.add(pt)
        await db_session.flush()

        material = Material(name="UO2")
        source = DataSource(title="Paper A", source_type="journal_article")
        db_session.add_all([material, source])
        await db_session.flush()

        dataset = Dataset(material_id=material.id, source_id=source.id, title="DS1")
        db_session.add(dataset)
        await db_session.flush()

        db_session.add_all([
            PropertyMeasurement(
                dataset_id=dataset.id, property_type_id=pt.id, value_scalar=3.5,
            ),
            PropertyMeasurement(
                dataset_id=dataset.id, property_type_id=pt.id, value_scalar=4.0,
            ),
        ])
        await db_session.commit()
        await _refresh_rel(db_session, dataset, "measurements")

        assert len(dataset.measurements) == 2

    @pytest.mark.asyncio
    async def test_measurement_has_many_conditions(
        self, db_session: AsyncSession,
    ) -> None:
        """property_measurement -> measurement_conditions (cascade delete)."""
        cat = PropertyCategory(name="Thermal", slug="thermal")
        db_session.add(cat)
        await db_session.flush()

        pt = PropertyType(
            category_id=cat.id, name="TC", slug="tc",
            value_type="scalar",
        )
        db_session.add(pt)
        await db_session.flush()

        material = Material(name="UO2")
        source = DataSource(title="Paper A", source_type="journal_article")
        db_session.add_all([material, source])
        await db_session.flush()

        dataset = Dataset(material_id=material.id, source_id=source.id, title="DS1")
        db_session.add(dataset)
        await db_session.flush()

        measurement = PropertyMeasurement(
            dataset_id=dataset.id, property_type_id=pt.id, value_scalar=3.5,
        )
        db_session.add(measurement)
        await db_session.flush()

        db_session.add_all([
            MeasurementCondition(measurement_id=measurement.id, temperature=800.0),
            MeasurementCondition(measurement_id=measurement.id, temperature=1200.0),
        ])
        await db_session.commit()
        await _refresh_rel(db_session, measurement, "conditions")

        assert len(measurement.conditions) == 2

    @pytest.mark.asyncio
    async def test_data_source_has_many_author_links(
        self, db_session: AsyncSession,
    ) -> None:
        """data_source -> data_source_authors (one-to-many)."""
        source = DataSource(title="Paper A", source_type="journal_article")
        db_session.add(source)
        await db_session.flush()

        author1 = Author(full_name="Alice", last_name="Alice")
        author2 = Author(full_name="Bob", last_name="Bob")
        db_session.add_all([author1, author2])
        await db_session.flush()

        db_session.add_all([
            DataSourceAuthor(data_source_id=source.id, author_id=author1.id, author_order=1),
            DataSourceAuthor(data_source_id=source.id, author_id=author2.id, author_order=2),
        ])
        await db_session.commit()
        await _refresh_rel(db_session, source, "data_source_authors")

        assert len(source.data_source_authors) == 2

    @pytest.mark.asyncio
    async def test_property_category_has_many_types(
        self, db_session: AsyncSession,
    ) -> None:
        """property_category -> property_types (cascade delete)."""
        cat = PropertyCategory(name="Mechanical", slug="mechanical")
        db_session.add(cat)
        await db_session.flush()

        db_session.add_all([
            PropertyType(
                category_id=cat.id, name="Young Modulus",
                slug="young-modulus", value_type="scalar",
            ),
            PropertyType(
                category_id=cat.id, name="Yield Strength",
                slug="yield-strength", value_type="scalar",
            ),
        ])
        await db_session.commit()
        await _refresh_rel(db_session, cat, "property_types")

        assert len(cat.property_types) == 2


# ============================================================
# Cross-Domain Chain Test
# ============================================================


class TestCrossDomainChain:
    """Full chain: Material -> Dataset -> Measurement -> Condition."""

    @pytest.mark.asyncio
    async def test_material_to_dataset_to_measurement_chain(
        self, db_session: AsyncSession,
    ) -> None:
        """Verify the full FK chain from material to condition."""
        cat = PropertyCategory(name="Thermal", slug="thermal")
        db_session.add(cat)
        await db_session.flush()

        pt = PropertyType(
            category_id=cat.id, name="Thermal conductivity",
            slug="thermal-conductivity", value_type="scalar",
        )
        db_session.add(pt)
        await db_session.flush()

        material = Material(name="UO2")
        source = DataSource(title="Test Journal", source_type="journal_article")
        db_session.add_all([material, source])
        await db_session.flush()

        dataset = Dataset(
            material_id=material.id, source_id=source.id, title="UO2 Thermal",
        )
        db_session.add(dataset)
        await db_session.flush()

        measurement = PropertyMeasurement(
            dataset_id=dataset.id, property_type_id=pt.id, value_scalar=8.5,
        )
        db_session.add(measurement)
        await db_session.flush()

        condition = MeasurementCondition(
            measurement_id=measurement.id, temperature=300.0,
        )
        db_session.add(condition)
        await db_session.commit()

        await _refresh_rel(db_session, material, "datasets")
        assert len(material.datasets) == 1

        await _refresh_rel(db_session, dataset, "measurements")
        assert len(dataset.measurements) == 1

        await _refresh_rel(db_session, measurement, "conditions")
        assert len(measurement.conditions) == 1
        assert measurement.conditions[0].temperature == 300.0


# ============================================================
# Constraint Tests (DB-level)
# ============================================================


class TestDatabaseConstraints:
    """Database-level constraint tests."""

    @pytest.mark.asyncio
    async def test_duplicate_material_category_name_rejected(
        self, db_session: AsyncSession,
    ) -> None:
        """Duplicate material_category name rejected."""
        db_session.add(MaterialCategory(name="Fuel", slug="fuel"))
        await db_session.commit()

        dup = MaterialCategory(name="Fuel", slug="fuel-different")
        db_session.add(dup)
        with pytest.raises((IntegrityError, OperationalError)):
            await db_session.commit()

    @pytest.mark.asyncio
    async def test_duplicate_alias_name_type_rejected(
        self, db_session: AsyncSession,
    ) -> None:
        """Duplicate (alias_name, alias_type) rejected."""
        material = Material(name="UO2")
        db_session.add(material)
        await db_session.flush()

        db_session.add(
            MaterialAlias(
                material_id=material.id, alias_name="UO2", alias_type="common_name",
            ),
        )
        await db_session.commit()

        dup = MaterialAlias(
            material_id=material.id, alias_name="UO2", alias_type="common_name",
        )
        db_session.add(dup)
        with pytest.raises((IntegrityError, OperationalError)):
            await db_session.commit()

    @pytest.mark.asyncio
    async def test_duplicate_unit_symbol_rejected(
        self, db_session: AsyncSession,
    ) -> None:
        """Duplicate unit symbol rejected."""
        db_session.add(Unit(symbol="K", name="kelvin", dimension="temperature"))
        await db_session.commit()

        dup = Unit(symbol="K", name="another-kelvin", dimension="temperature")
        db_session.add(dup)
        with pytest.raises((IntegrityError, OperationalError)):
            await db_session.commit()

    @pytest.mark.asyncio
    async def test_composition_fraction_out_of_range_rejected(
        self, db_session: AsyncSession,
    ) -> None:
        """MaterialComposition fraction > 1 rejected by CHECK constraint."""
        material = Material(name="UO2")
        db_session.add(material)
        await db_session.flush()

        bad = MaterialComposition(material_id=material.id, element="U", fraction=1.5)
        db_session.add(bad)
        with pytest.raises((IntegrityError, OperationalError)):
            await db_session.commit()

    @pytest.mark.asyncio
    async def test_composition_fraction_negative_rejected(
        self, db_session: AsyncSession,
    ) -> None:
        """MaterialComposition fraction < 0 rejected by CHECK constraint."""
        material = Material(name="UO2")
        db_session.add(material)
        await db_session.flush()

        bad = MaterialComposition(material_id=material.id, element="U", fraction=-0.1)
        db_session.add(bad)
        with pytest.raises((IntegrityError, OperationalError)):
            await db_session.commit()

    @pytest.mark.asyncio
    async def test_duplicate_doi_rejected(
        self, db_session: AsyncSession,
    ) -> None:
        """Duplicate data_source DOI rejected."""
        db_session.add(
            DataSource(
                doi="10.1000/test", title="Paper A", source_type="journal_article",
            ),
        )
        await db_session.commit()

        dup = DataSource(
            doi="10.1000/test", title="Paper B", source_type="journal_article",
        )
        db_session.add(dup)
        with pytest.raises((IntegrityError, OperationalError)):
            await db_session.commit()

    @pytest.mark.asyncio
    async def test_duplicate_orcid_rejected(
        self, db_session: AsyncSession,
    ) -> None:
        """Duplicate author ORCID rejected."""
        db_session.add(
            Author(full_name="Alice", last_name="Alice", orcid="0000-0001-2345-6789"),
        )
        await db_session.commit()

        dup = Author(full_name="Bob", last_name="Bob", orcid="0000-0001-2345-6789")
        db_session.add(dup)
        with pytest.raises((IntegrityError, OperationalError)):
            await db_session.commit()

    @pytest.mark.asyncio
    async def test_duplicate_unit_conversion_pair_rejected(
        self, db_session: AsyncSession,
    ) -> None:
        """Duplicate (source_unit_id, target_unit_id) pair rejected."""
        u1 = Unit(symbol="K", name="kelvin", dimension="temperature")
        u2 = Unit(symbol="°C", name="celsius", dimension="temperature")
        db_session.add_all([u1, u2])
        await db_session.flush()

        db_session.add(
            UnitConversion(source_unit_id=u1.id, target_unit_id=u2.id, factor=1.0),
        )
        await db_session.commit()

        dup = UnitConversion(source_unit_id=u1.id, target_unit_id=u2.id, factor=1.0)
        db_session.add(dup)
        with pytest.raises((IntegrityError, OperationalError)):
            await db_session.commit()


# ============================================================
# Pydantic Validation Tests
# ============================================================


class TestSchemaValidation:
    """Pydantic schema validation tests."""

    # -- MaterialCategory schemas --

    def test_valid_material_category_schema(self) -> None:
        """Valid MaterialCategoryCreate passes."""
        from nfm_db.schemas.material import MaterialCategoryCreate

        schema = MaterialCategoryCreate(name="Fuel", slug="fuel")
        assert schema.name == "Fuel"

    def test_invalid_material_category_slug_rejected(self) -> None:
        """MaterialCategoryCreate with invalid slug rejected."""
        from nfm_db.schemas.material import MaterialCategoryCreate

        with pytest.raises(ValidationError):
            MaterialCategoryCreate(name="Fuel", slug="Invalid Slug!")

    # -- MaterialAlias schemas --

    def test_valid_material_alias_schema(self) -> None:
        """Valid MaterialAliasCreate passes."""
        from nfm_db.schemas.material import MaterialAliasCreate

        schema = MaterialAliasCreate(
            material_id="00000000-0000-0000-0000-000000000001",
            alias_name="UO2",
            alias_type="common_name",
        )
        assert schema.alias_type == "common_name"

    def test_invalid_alias_type_rejected(self) -> None:
        """MaterialAliasCreate with invalid alias_type rejected."""
        from nfm_db.schemas.material import MaterialAliasCreate

        with pytest.raises(ValidationError):
            MaterialAliasCreate(
                material_id="00000000-0000-0000-0000-000000000001",
                alias_name="UO2",
                alias_type="not_a_valid_type",
            )

    # -- MaterialComposition schemas --

    def test_fraction_out_of_range_rejected(self) -> None:
        """MaterialCompositionCreate with fraction > 1 rejected."""
        from nfm_db.schemas.material import MaterialCompositionCreate

        with pytest.raises(ValidationError):
            MaterialCompositionCreate(
                material_id="00000000-0000-0000-0000-000000000001",
                element="U",
                fraction=1.5,
            )

    def test_fraction_negative_rejected(self) -> None:
        """MaterialCompositionCreate with fraction < 0 rejected."""
        from nfm_db.schemas.material import MaterialCompositionCreate

        with pytest.raises(ValidationError):
            MaterialCompositionCreate(
                material_id="00000000-0000-0000-0000-000000000001",
                element="U",
                fraction=-0.1,
            )

    # -- DataSource schemas --

    def test_valid_data_source_schema(self) -> None:
        """Valid DataSourceCreate with DOI passes."""
        from nfm_db.schemas.source import DataSourceCreate

        schema = DataSourceCreate(
            doi="10.1016/j.jnucmat.2020.152300",
            title="Thermal conductivity of UO2",
            source_type="journal_article",
        )
        assert schema.doi == "10.1016/j.jnucmat.2020.152300"

    def test_invalid_doi_format_rejected(self) -> None:
        """DataSourceCreate with bad DOI format rejected."""
        from nfm_db.schemas.source import DataSourceCreate

        with pytest.raises(ValidationError):
            DataSourceCreate(
                doi="not-a-doi",
                title="Bad DOI Paper",
                source_type="journal_article",
            )

    def test_invalid_source_type_rejected(self) -> None:
        """DataSourceCreate with invalid source_type rejected."""
        from nfm_db.schemas.source import DataSourceCreate

        with pytest.raises(ValidationError):
            DataSourceCreate(
                title="Bad Type Paper",
                source_type="invalid_type",
            )

    # -- Author schemas --

    def test_valid_author_schema(self) -> None:
        """Valid AuthorCreate with ORCID passes."""
        from nfm_db.schemas.source import AuthorCreate

        schema = AuthorCreate(
            full_name="John Smith",
            last_name="Smith",
            first_name="John",
            orcid="0000-0001-2345-6789",
        )
        assert schema.orcid == "0000-0001-2345-6789"

    def test_invalid_orcid_format_rejected(self) -> None:
        """AuthorCreate with bad ORCID format rejected."""
        from nfm_db.schemas.source import AuthorCreate

        with pytest.raises(ValidationError):
            AuthorCreate(full_name="Bad Author", last_name="Author", orcid="not-an-orcid")

    # -- DataSourceAuthor schemas --

    def test_valid_data_source_author_schema(self) -> None:
        """Valid DataSourceAuthorCreate passes."""
        from nfm_db.schemas.source import DataSourceAuthorCreate

        schema = DataSourceAuthorCreate(
            data_source_id="00000000-0000-0000-0000-000000000001",
            author_id="00000000-0000-0000-0000-000000000002",
            author_order=1,
        )
        assert schema.author_order == 1

    def test_data_source_author_order_must_be_positive(self) -> None:
        """DataSourceAuthorCreate with author_order=0 rejected."""
        from nfm_db.schemas.source import DataSourceAuthorCreate

        with pytest.raises(ValidationError):
            DataSourceAuthorCreate(
                data_source_id="00000000-0000-0000-0000-000000000001",
                author_id="00000000-0000-0000-0000-000000000002",
                author_order=0,
            )

    # -- Unit schemas --

    def test_valid_unit_schema(self) -> None:
        """Valid UnitCreate passes."""
        from nfm_db.schemas.unit import UnitCreate

        schema = UnitCreate(symbol="K", name="kelvin", dimension="temperature")
        assert schema.symbol == "K"

    # -- UnitConversion schemas --

    def test_valid_unit_conversion_schema(self) -> None:
        """Valid UnitConversionCreate passes."""
        from nfm_db.schemas.unit import UnitConversionCreate

        schema = UnitConversionCreate(
            source_unit_id="00000000-0000-0000-0000-000000000001",
            target_unit_id="00000000-0000-0000-0000-000000000002",
            factor=1.0,
            offset=273.15,
        )
        assert float(schema.factor) == 1.0

    # -- PropertyMeasurement schemas --

    def test_valid_property_measurement_schema(self) -> None:
        """Valid PropertyMeasurementCreate with value_scalar passes."""
        from nfm_db.schemas.property import PropertyMeasurementCreate

        schema = PropertyMeasurementCreate(
            dataset_id="00000000-0000-0000-0000-000000000001",
            property_type_id="00000000-0000-0000-0000-000000000002",
            value_scalar=8.5,
        )
        assert schema.value_scalar == 8.5

    def test_measurement_requires_at_least_one_value(self) -> None:
        """PropertyMeasurementCreate with no value fields rejected."""
        from nfm_db.schemas.property import PropertyMeasurementCreate

        with pytest.raises(ValidationError):
            PropertyMeasurementCreate(
                dataset_id="00000000-0000-0000-0000-000000000001",
                property_type_id="00000000-0000-0000-0000-000000000002",
            )

    # -- PropertyType schemas --

    def test_valid_property_type_schema(self) -> None:
        """Valid PropertyTypeCreate passes."""
        from nfm_db.schemas.property import PropertyTypeCreate

        schema = PropertyTypeCreate(
            category_id="00000000-0000-0000-0000-000000000001",
            name="Thermal Conductivity",
            slug="thermal-conductivity",
            value_type="scalar",
        )
        assert schema.value_type == "scalar"

    def test_invalid_value_type_rejected(self) -> None:
        """PropertyTypeCreate with invalid value_type rejected."""
        from nfm_db.schemas.property import PropertyTypeCreate

        with pytest.raises(ValidationError):
            PropertyTypeCreate(
                category_id="00000000-0000-0000-0000-000000000001",
                name="Bad Type",
                slug="bad-type",
                value_type="invalid_type",
            )
