"""Create Phase 1 core tables

Revision ID: 009
Revises: 008
Create Date: 2026-07-06 00:00:00.000000

Creates 14 Phase 1 core tables:
- units (2 tables: units, unit_conversions)
- sources (3 tables: data_sources, authors, data_source_authors)
- materials (4 tables: material_categories, materials, material_aliases, material_compositions)
- properties (5 tables: property_categories, property_types, datasets, property_measurements, measurement_conditions)

Tables are created in foreign key dependency order, with UUID primary keys,
TIMESTAMPTZ timestamps, and proper CHECK constraints.
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "009"
down_revision: str | Sequence[str] | None = "008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create all Phase 1 core tables in dependency order."""

    # =========================================================================
    # UNITS (2 tables)
    # =========================================================================

    op.execute("""
        CREATE TABLE units (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            name VARCHAR(100) NOT NULL,
            symbol VARCHAR(20) NOT NULL,
            dimension VARCHAR(100) NOT NULL,
            description TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_units_name UNIQUE (name),
            CONSTRAINT uq_units_symbol UNIQUE (symbol)
        )
    """)

    op.execute("""
        CREATE TABLE unit_conversions (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            source_unit_id UUID NOT NULL REFERENCES units(id) ON DELETE CASCADE,
            target_unit_id UUID NOT NULL REFERENCES units(id) ON DELETE CASCADE,
            factor NUMERIC(20, 10) NOT NULL,
            offset NUMERIC(20, 10) NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_unit_conversions_source_target UNIQUE (source_unit_id, target_unit_id)
        )
    """)

    # =========================================================================
    # SOURCES (3 tables)
    # =========================================================================

    op.execute("""
        CREATE TABLE data_sources (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            doi VARCHAR(255),
            title VARCHAR(1000) NOT NULL,
            journal VARCHAR(500),
            year INTEGER,
            volume VARCHAR(50),
            pages VARCHAR(50),
            source_type VARCHAR(50) NOT NULL,
            abstract TEXT,
            external_url VARCHAR(1000),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_data_sources_doi UNIQUE (doi)
        )
    """)

    op.execute("""
        CREATE TABLE authors (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            full_name VARCHAR(300) NOT NULL,
            last_name VARCHAR(100) NOT NULL,
            first_name VARCHAR(100),
            orcid VARCHAR(19),
            affiliation VARCHAR(500),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_authors_orcid UNIQUE (orcid)
        )
    """)

    op.execute("""
        CREATE TABLE data_source_authors (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            data_source_id UUID NOT NULL REFERENCES data_sources(id) ON DELETE CASCADE,
            author_id UUID NOT NULL REFERENCES authors(id) ON DELETE CASCADE,
            author_order INTEGER NOT NULL,
            is_corresponding BOOLEAN NOT NULL DEFAULT false,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_data_source_authors_source_order UNIQUE (data_source_id, author_order)
        )
    """)

    # =========================================================================
    # MATERIALS (4 tables)
    # =========================================================================

    op.execute("""
        CREATE TABLE material_categories (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            name VARCHAR(200) NOT NULL,
            slug VARCHAR(200) NOT NULL,
            description TEXT,
            parent_id UUID REFERENCES material_categories(id) ON DELETE SET NULL,
            sort_order INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_material_categories_name UNIQUE (name),
            CONSTRAINT uq_material_categories_slug UNIQUE (slug)
        )
    """)

    op.create_index("ix_mat_cat_parent", "material_categories", ["parent_id"])

    op.execute("""
        CREATE TABLE materials (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            name VARCHAR(500) NOT NULL,
            formula VARCHAR(200),
            crystal_structure VARCHAR(100),
            category_id UUID REFERENCES material_categories(id) ON DELETE SET NULL,
            description TEXT,
            is_active BOOLEAN NOT NULL DEFAULT true,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)

    op.create_index("idx_materials_category", "materials", ["category_id"])
    op.create_index("idx_materials_formula", "materials", ["formula"])

    op.execute("""
        CREATE TABLE material_aliases (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            material_id UUID NOT NULL REFERENCES materials(id) ON DELETE CASCADE,
            alias_name VARCHAR(500) NOT NULL,
            alias_type VARCHAR(50) NOT NULL,
            source VARCHAR(200),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_material_aliases_material_name UNIQUE (material_id, alias_name),
            CONSTRAINT ck_material_aliases_alias_type CHECK (alias_type IN (
                'common_name', 'iupac_name', 'cas_number', 'legacy_name',
                'abbreviation', 'trademark', 'other'
            ))
        )
    """)

    op.create_index("idx_mat_aliases_material", "material_aliases", ["material_id"])
    op.create_index("idx_mat_aliases_alias_name", "material_aliases", ["alias_name"])

    op.execute("""
        CREATE TABLE material_compositions (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            material_id UUID NOT NULL REFERENCES materials(id) ON DELETE CASCADE,
            element VARCHAR(20) NOT NULL,
            fraction NUMERIC(10, 6) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT ck_material_compositions_fraction CHECK (fraction >= 0 AND fraction <= 1)
        )
    """)

    op.create_index("idx_mat_comp_material", "material_compositions", ["material_id"])
    op.create_index("idx_mat_comp_element", "material_compositions", ["element"])

    # =========================================================================
    # PROPERTIES (5 tables)
    # =========================================================================

    op.execute("""
        CREATE TABLE property_categories (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            name VARCHAR(200) NOT NULL,
            slug VARCHAR(200) NOT NULL,
            description TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_property_categories_name UNIQUE (name),
            CONSTRAINT uq_property_categories_slug UNIQUE (slug)
        )
    """)

    op.execute("""
        CREATE TABLE property_types (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            category_id UUID NOT NULL REFERENCES property_categories(id) ON DELETE CASCADE,
            name VARCHAR(200) NOT NULL,
            slug VARCHAR(200) NOT NULL,
            value_type VARCHAR(50) NOT NULL,
            unit_id UUID REFERENCES units(id) ON DELETE SET NULL,
            description TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_property_types_category_slug UNIQUE (category_id, slug),
            CONSTRAINT ck_property_types_value_type CHECK (value_type IN (
                'scalar', 'range', 'expression', 'list', 'text'
            ))
        )
    """)

    op.create_index("idx_property_types_category", "property_types", ["category_id"])

    op.execute("""
        CREATE TABLE datasets (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            material_id UUID NOT NULL REFERENCES materials(id) ON DELETE CASCADE,
            source_id UUID NOT NULL REFERENCES data_sources(id) ON DELETE CASCADE,
            title VARCHAR(500) NOT NULL,
            description TEXT,
            measurement_date DATE,
            is_verified BOOLEAN NOT NULL DEFAULT false,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)

    op.create_index("idx_datasets_material", "datasets", ["material_id"])
    op.create_index("idx_datasets_source", "datasets", ["source_id"])

    op.execute("""
        CREATE TABLE property_measurements (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            dataset_id UUID NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
            property_type_id UUID NOT NULL REFERENCES property_types(id) ON DELETE CASCADE,
            value_scalar NUMERIC(16, 6),
            value_min NUMERIC(16, 6),
            value_max NUMERIC(16, 6),
            value_expression TEXT,
            value_list JSONB,
            value_text TEXT,
            uncertainty NUMERIC(16, 6),
            unit_id UUID REFERENCES units(id) ON DELETE SET NULL,
            notes TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT ck_property_measurements_value_present CHECK (
                value_scalar IS NOT NULL OR value_min IS NOT NULL OR
                value_max IS NOT NULL OR value_expression IS NOT NULL OR
                value_list IS NOT NULL OR value_text IS NOT NULL
            )
        )
    """)

    op.create_index("idx_pm_dataset", "property_measurements", ["dataset_id"])
    op.create_index("idx_pm_property_type", "property_measurements", ["property_type_id"])

    op.execute("""
        CREATE TABLE measurement_conditions (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            measurement_id UUID NOT NULL REFERENCES property_measurements(id) ON DELETE CASCADE,
            temperature NUMERIC(10, 2),
            pressure NUMERIC(10, 2),
            environment VARCHAR(200),
            irradiation_dose NUMERIC(16, 6),
            notes TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)

    op.create_index("idx_mc_measurement", "measurement_conditions", ["measurement_id"])


def downgrade() -> None:
    """Drop all Phase 1 core tables in reverse dependency order."""

    # PROPERTIES (5 tables) — drop first (depends on materials + units + sources)
    op.drop_index("idx_mc_measurement", table_name="measurement_conditions")
    op.execute("DROP TABLE IF EXISTS measurement_conditions CASCADE")

    op.drop_index("idx_pm_property_type", table_name="property_measurements")
    op.drop_index("idx_pm_dataset", table_name="property_measurements")
    op.execute("DROP TABLE IF EXISTS property_measurements CASCADE")

    op.drop_index("idx_datasets_source", table_name="datasets")
    op.drop_index("idx_datasets_material", table_name="datasets")
    op.execute("DROP TABLE IF EXISTS datasets CASCADE")

    op.drop_index("idx_property_types_category", table_name="property_types")
    op.execute("DROP TABLE IF EXISTS property_types CASCADE")
    op.execute("DROP TABLE IF EXISTS property_categories CASCADE")

    # MATERIALS (4 tables)
    op.drop_index("idx_mat_comp_element", table_name="material_compositions")
    op.drop_index("idx_mat_comp_material", table_name="material_compositions")
    op.execute("DROP TABLE IF EXISTS material_compositions CASCADE")

    op.drop_index("idx_mat_aliases_alias_name", table_name="material_aliases")
    op.drop_index("idx_mat_aliases_material", table_name="material_aliases")
    op.execute("DROP TABLE IF EXISTS material_aliases CASCADE")

    op.drop_index("idx_materials_formula", table_name="materials")
    op.drop_index("idx_materials_category", table_name="materials")
    op.execute("DROP TABLE IF EXISTS materials CASCADE")

    op.drop_index("ix_mat_cat_parent", table_name="material_categories")
    op.execute("DROP TABLE IF EXISTS material_categories CASCADE")

    # SOURCES (3 tables)
    op.execute("DROP TABLE IF EXISTS data_source_authors CASCADE")
    op.execute("DROP TABLE IF EXISTS authors CASCADE")
    op.execute("DROP TABLE IF EXISTS data_sources CASCADE")

    # UNITS (2 tables) — drop last (no dependencies)
    op.execute("DROP TABLE IF EXISTS unit_conversions CASCADE")
    op.execute("DROP TABLE IF EXISTS units CASCADE")
