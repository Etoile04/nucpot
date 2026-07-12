"""Seed Phase 1 reference data

Revision ID: 010
Revises: 009
Create Date: 2026-07-06 00:00:00.000000

Seeds initial reference data for Phase 1 core tables:
- units: 21 common units (temperature, pressure, energy, length, density, etc.)
- unit_conversions: 12 conversion factors between related units
- property_categories: 7 categories (thermal, mechanical, nuclear, physical, chemical, optical, electrical)
"""
from collections.abc import Sequence

from alembic import op

revision: str = "010"
down_revision: str | Sequence[str] | None = "009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Seed units, unit_conversions, and property_categories."""

    # =========================================================================
    # UNITS (21 rows)
    # =========================================================================

    op.execute("""
        INSERT INTO units (symbol, name, dimension, description) VALUES
            ('K',  'Kelvin',                        'temperature',   'SI base unit of thermodynamic temperature'),
            ('°C', 'Celsius',                       'temperature',   'Common temperature scale, T(°C) = T(K) - 273.15'),
            ('°F', 'Fahrenheit',                     'temperature',   'Imperial temperature scale'),
            ('Pa', 'Pascal',                        'pressure',      'SI base unit of pressure'),
            ('MPa', 'Megapascal',                    'pressure',      '10^6 Pascals, common in nuclear engineering'),
            ('GPa', 'Gigapascal',                    'pressure',      '10^9 Pascals, used for high-pressure measurements'),
            ('atm', 'Standard atmosphere',            'pressure',      '101325 Pa, standard atmospheric pressure'),
            ('J',  'Joule',                         'energy',        'SI base unit of energy'),
            ('kJ', 'Kilojoule',                     'energy',        '10^3 Joules'),
            ('eV', 'Electronvolt',                   'energy',        'Unit of energy in atomic/nuclear physics'),
            ('W/m·K', 'Watt per meter-Kelvin',         'thermal',       'SI unit of thermal conductivity'),
            ('m',  'Meter',                         'length',        'SI base unit of length'),
            ('cm', 'Centimeter',                     'length',        '10^-2 meters'),
            ('μm', 'Micrometer',                    'length',        '10^-6 meters'),
            ('nm', 'Nanometer',                    'length',        '10^-9 meters, common in crystal structure'),
            ('Å',  'Angstrom',                      'length',        '10^-10 meters, traditional unit for lattice parameters'),
            ('kg/m³', 'Kilogram per cubic meter',     'density',       'SI unit of density'),
            ('g/cm³', 'Gram per cubic centimeter',    'density',       'Common unit for material density, 1 g/cm³ = 1000 kg/m³'),
            ('J/kg·K', 'Joule per kilogram-Kelvin',  'heat capacity', 'SI unit of specific heat capacity'),
            ('W/m²', 'Watt per square meter',       'heat flux',     'SI unit of heat flux density'),
            ('1',  'Dimensionless',                  'dimensionless', 'Dimensionless quantity')
        )
    """)

    # =========================================================================
    # UNIT CONVERSIONS (12 rows)
    # =========================================================================

    op.execute("""
        INSERT INTO unit_conversions (source_unit_id, target_unit_id, factor, offset)
        SELECT
            u_from.id,
            u_to.id,
            CASE
                -- Temperature
                WHEN u_from.symbol = '°C' AND u_to.symbol = 'K' THEN 1.0
                WHEN u_from.symbol = '°F' AND u_to.symbol = 'K' THEN 5.0 / 9.0
                -- Pressure
                WHEN u_from.symbol = 'MPa' AND u_to.symbol = 'Pa' THEN 1000000.0
                WHEN u_from.symbol = 'GPa' AND u_to.symbol = 'Pa' THEN 1000000000.0
                WHEN u_from.symbol = 'atm' AND u_to.symbol = 'Pa' THEN 101325.0
                -- Energy
                WHEN u_from.symbol = 'kJ' AND u_to.symbol = 'J' THEN 1000.0
                -- Length
                WHEN u_from.symbol = 'cm' AND u_to.symbol = 'm' THEN 0.01
                WHEN u_from.symbol = 'μm' AND u_to.symbol = 'm' THEN 0.000001
                WHEN u_from.symbol = 'nm' AND u_to.symbol = 'm' THEN 0.000000001
                WHEN u_from.symbol = 'Å' AND u_to.symbol = 'm' THEN 0.0000000001
                -- Density
                WHEN u_from.symbol = 'g/cm³' AND u_to.symbol = 'kg/m³' THEN 1000.0
                ELSE 1.0
            END,
            CASE
                WHEN u_from.symbol = '°C' AND u_to.symbol = 'K' THEN 273.15
                WHEN u_from.symbol = '°F' AND u_to.symbol = 'K' THEN 45967.0 / 180.0
                ELSE 0.0
            END
        FROM units u_from
        JOIN units u_to
        ON (
            u_from.dimension = u_to.dimension
            AND u_from.symbol != u_to.symbol
            AND u_to.symbol IN ('K', 'Pa', 'J', 'm', 'kg/m³')
        )
    """)

    # =========================================================================
    # PROPERTY CATEGORIES (7 rows)
    # =========================================================================

    op.execute("""
        INSERT INTO property_categories (name, slug, description) VALUES
            ('Thermal properties',  'thermal',      'Properties related to heat transfer and thermal behavior of nuclear fuel materials'),
            ('Mechanical properties', 'mechanical',   'Properties describing material response to applied forces'),
            ('Nuclear properties',  'nuclear',       'Properties related to nuclear interactions, cross-sections, and fission behavior'),
            ('Physical properties',  'physical',       'Fundamental physical properties such as density, crystal structure, and phase transitions'),
            ('Chemical properties',  'chemical',      'Properties related to chemical reactivity, corrosion, and compatibility'),
            ('Optical properties',  'optical',       'Properties related to light interaction with materials'),
            ('Electrical properties', 'electrical',    'Properties related to electrical conductivity and electromagnetic behavior')
        )
    """)


def downgrade() -> None:
    """Remove seeded reference data in reverse dependency order."""

    op.execute(
        "DELETE FROM property_categories "
        "WHERE slug IN ('thermal', 'mechanical', 'nuclear', 'physical', 'chemical', 'optical', 'electrical')"
    )
    op.execute("DELETE FROM unit_conversions")
    op.execute(
        "DELETE FROM units "
        "WHERE symbol IN ('K', '°C', '°F', 'Pa', 'MPa', 'GPa', 'atm', "
        "'J', 'kJ', 'eV', 'W/m·K', 'm', 'cm', 'μm', 'nm', 'Å', 'kg/m³', 'g/cm³', 'J/kg·K', 'W/m²', '1')"
    )
