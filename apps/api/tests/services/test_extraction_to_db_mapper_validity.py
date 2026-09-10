"""Integration tests for NFM-4550 G1-D validity_check (spec §8.4, AC-10).

These tests run ``extraction_to_db_mapper.map_and_persist`` end-to-end
against the SQLite in-memory db_session fixture, and assert that:

1. A measurement inside its property's valid range is persisted with
   ``validity_check.status='ok'`` and ``review_status`` follows the
   confidence-based mapper default.
2. AC-10 outliers (键长 0.3 Å, 密度 0.05 g/cm³) are persisted with
   ``validity_check.status='fail'`` AND ``review_status='invalid'``
   — the latter is the §8.2 step 4 "fail 阻断 confirmed" rule.
3. A property with no valid_range configured yields
   ``validity_check.status='warn'`` and does NOT override
   ``review_status``.
4. The mapper continues to fall back to ``value_text`` (no numeric
   value) without breaking validity_check.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models import (
    DataSource,
    Material,
    PropertyCategory,
    PropertyMeasurement,
    PropertyType,
)
from nfm_db.services.extraction_to_db_mapper import map_and_persist

# ---------------------------------------------------------------------------
# Helpers — copied/adapted from test_extraction_to_db_mapper to keep
# this file standalone (no import of private helpers from sibling test).
# ---------------------------------------------------------------------------


async def _seed_property_type(
    db: AsyncSession,
    *,
    category_slug: str,
    property_name: str,
    property_slug: str,
    valid_range_min: float | None = None,
    valid_range_max: float | None = None,
) -> PropertyType:
    """Create a PropertyCategory + PropertyType pair with optional valid range."""

    category = PropertyCategory(
        name=category_slug,
        slug=category_slug,
        description=f"{category_slug} properties",
    )
    db.add(category)
    await db.flush()

    pt = PropertyType(
        category_id=category.id,
        name=property_name,
        slug=property_slug,
        value_type="scalar",
        valid_range_min=valid_range_min,
        valid_range_max=valid_range_max,
    )
    db.add(pt)
    await db.commit()
    await db.refresh(pt)
    return pt


async def _seed_minimum(
    db: AsyncSession,
    *,
    property_type: PropertyType,
    source_doi: str | None = "10.1234/test",
    material_name: str = "UO2",
) -> tuple[DataSource, Material]:
    """Seed a DataSource + Material so map_and_persist can build the row."""

    source = DataSource(
        doi=source_doi,
        title="Test source",
        source_type="journal_article",
    )
    db.add(source)
    await db.flush()

    material = Material(
        name=material_name,
        formula=material_name,
    )
    db.add(material)
    await db.flush()
    return source, material


def _make_extracted_property(
    *,
    source_doi: str | None = "10.1234/test",
    material_name: str = "UO2",
    property_category: str = "physical",
    property_name: str = "density",
    value: str = "10.97",
    unit: str | None = "g/cm³",
    confidence: str = "high",
) -> dict[str, Any]:
    return {
        "source_file": "test.md",
        "source_doi": source_doi,
        "material_name": material_name,
        "composition": material_name,
        "property_category": property_category,
        "property": property_name,
        "value": value,
        "unit": unit,
        "conditions": None,
        "reference": "Test reference",
        "method": "",
        "confidence": confidence,
        "context": None,
        "uncertainty": None,
    }


async def _all_measurements(db: AsyncSession) -> list[PropertyMeasurement]:
    result = await db.execute(
        select(PropertyMeasurement).order_by(PropertyMeasurement.created_at)
    )
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# AC-10 — physical invalid values fail at落库
# ---------------------------------------------------------------------------


async def test_ac10_density_0p05_is_marked_invalid_at_insert(
    db_session: AsyncSession,
) -> None:
    """AC-10: density 0.05 g/cm³ (below any solid) → fail + review_status='invalid'."""

    pt = await _seed_property_type(
        db_session,
        category_slug="physical",
        property_name="density",
        property_slug="density",
        valid_range_min=0.5,
        valid_range_max=25.0,
    )
    source, material = await _seed_minimum(db_session, property_type=pt)

    result = await map_and_persist(
        db=db_session,
        extraction_output=[
            _make_extracted_property(
                property_name="density",
                value="0.05",
                unit="g/cm³",
            )
        ],
    )
    assert result.created_measurements == 1

    rows = await _all_measurements(db_session)
    assert len(rows) == 1
    row = rows[0]
    assert row.review_status == "invalid", (
        f"AC-10: expected review_status='invalid' for fail, got {row.review_status!r}"
    )
    vc = row.validity_check
    assert isinstance(vc, dict)
    assert vc["status"] == "fail"
    assert isinstance(vc["reason"], str) and "density=0.05" in vc["reason"]
    assert "outside physical range" in vc["reason"]


async def test_ac10_lattice_0p3_angstrom_is_marked_invalid_at_insert(
    db_session: AsyncSession,
) -> None:
    """AC-10: 键长 0.3 Å (below atomic radius) → fail + review_status='invalid'."""

    pt = await _seed_property_type(
        db_session,
        category_slug="physical",
        property_name="lattice_constant",
        property_slug="lattice-constant",
        valid_range_min=1.0,
        valid_range_max=10.0,
    )
    source, material = await _seed_minimum(
        db_session,
        property_type=pt,
        material_name="UO2",
    )

    result = await map_and_persist(
        db=db_session,
        extraction_output=[
            _make_extracted_property(
                property_name="lattice_constant",
                material_name="UO2",
                value="0.3",
                unit="Å",
            )
        ],
    )
    assert result.created_measurements == 1

    rows = await _all_measurements(db_session)
    assert len(rows) == 1
    row = rows[0]
    assert row.review_status == "invalid"
    assert row.validity_check["status"] == "fail"
    assert "lattice_constant=0.3" in row.validity_check["reason"]


# ---------------------------------------------------------------------------
# Nominal cases — should pass with status='ok'
# ---------------------------------------------------------------------------


async def test_nominal_density_passes_with_status_ok(
    db_session: AsyncSession,
) -> None:
    """UO2 density 10.97 g/cm³ → ok, review_status stays confidence-derived."""

    pt = await _seed_property_type(
        db_session,
        category_slug="physical",
        property_name="density",
        property_slug="density",
        valid_range_min=0.5,
        valid_range_max=25.0,
    )
    source, material = await _seed_minimum(db_session, property_type=pt)

    result = await map_and_persist(
        db=db_session,
        extraction_output=[
            _make_extracted_property(
                property_name="density",
                value="10.97",
                unit="g/cm³",
                confidence="high",  # → review_status='approved'
            )
        ],
    )
    assert result.created_measurements == 1

    rows = await _all_measurements(db_session)
    row = rows[0]
    assert row.review_status == "approved", (
        "Confidence 'high' should map to review_status='approved'; "
        "validity_check status='ok' must NOT override that."
    )
    assert row.validity_check["status"] == "ok"
    assert row.validity_check["reason"] is None


async def test_nominal_lattice_passes_with_status_ok(
    db_session: AsyncSession,
) -> None:
    """UO2 lattice 5.47 Å → ok."""

    pt = await _seed_property_type(
        db_session,
        category_slug="physical",
        property_name="lattice_constant",
        property_slug="lattice-constant",
        valid_range_min=1.0,
        valid_range_max=10.0,
    )
    source, material = await _seed_minimum(
        db_session,
        property_type=pt,
        material_name="UO2",
    )

    await map_and_persist(
        db=db_session,
        extraction_output=[
            _make_extracted_property(
                property_name="lattice_constant",
                material_name="UO2",
                value="5.47",
                unit="Å",
            )
        ],
    )
    rows = await _all_measurements(db_session)
    row = rows[0]
    assert row.review_status != "invalid"
    assert row.validity_check["status"] == "ok"


# ---------------------------------------------------------------------------
# No valid_range configured → warn, not fail
# ---------------------------------------------------------------------------


async def test_no_valid_range_yields_warn_not_override(
    db_session: AsyncSession,
) -> None:
    """Property with no range set → status='warn', review_status untouched."""

    pt = await _seed_property_type(
        db_session,
        category_slug="thermal",
        property_name="thermal_conductivity",
        property_slug="thermal-conductivity",
        # No valid_range_min / max
    )
    source, material = await _seed_minimum(db_session, property_type=pt)

    await map_and_persist(
        db=db_session,
        extraction_output=[
            _make_extracted_property(
                property_category="thermal",
                property_name="thermal_conductivity",
                value="0.0001",  # would be wildly out-of-range if configured
                unit="W/(m·K)",
                confidence="high",
            )
        ],
    )
    rows = await _all_measurements(db_session)
    row = rows[0]
    assert row.review_status == "approved", (
        "No range configured means warn, NOT fail; review_status "
        "should remain confidence-derived."
    )
    assert row.validity_check["status"] == "warn"
    assert row.validity_check["reason"] is not None
    assert "range" in row.validity_check["reason"].lower()


# ---------------------------------------------------------------------------
# Boundary case — value exactly at the upper bound is ok
# ---------------------------------------------------------------------------


async def test_upper_bound_inclusive_is_ok(db_session: AsyncSession) -> None:
    """Density 25.0 g/cm³ (the seeded upper bound) is still ok."""

    pt = await _seed_property_type(
        db_session,
        category_slug="physical",
        property_name="density",
        property_slug="density",
        valid_range_min=0.5,
        valid_range_max=25.0,
    )
    source, material = await _seed_minimum(db_session, property_type=pt)

    await map_and_persist(
        db=db_session,
        extraction_output=[
            _make_extracted_property(
                property_name="density",
                value="25.0",
                unit="g/cm³",
            )
        ],
    )
    rows = await _all_measurements(db_session)
    row = rows[0]
    assert row.validity_check["status"] == "ok"
    assert row.review_status != "invalid"


# ---------------------------------------------------------------------------
# Mixed batch — one fail + one ok in the same mapper call
# ---------------------------------------------------------------------------


async def test_mixed_batch_only_fail_rows_become_invalid(
    db_session: AsyncSession,
) -> None:
    """Two rows: density 0.05 (fail) + lattice 5.47 (ok). Only the
    density row is review_status='invalid'."""

    density_pt = await _seed_property_type(
        db_session,
        category_slug="physical-density",
        property_name="density",
        property_slug="density",
        valid_range_min=0.5,
        valid_range_max=25.0,
    )
    lattice_pt = await _seed_property_type(
        db_session,
        category_slug="physical-lattice",
        property_name="lattice_constant",
        property_slug="lattice-constant",
        valid_range_min=1.0,
        valid_range_max=10.0,
    )
    source, material = await _seed_minimum(
        db_session,
        property_type=density_pt,
        material_name="UO2",
    )

    result = await map_and_persist(
        db=db_session,
        extraction_output=[
            _make_extracted_property(
                property_category="physical-density",
                property_name="density",
                value="0.05",
                unit="g/cm³",
            ),
            _make_extracted_property(
                property_category="physical-lattice",
                property_name="lattice_constant",
                material_name="UO2",
                value="5.47",
                unit="Å",
            ),
        ],
    )
    assert result.created_measurements == 2

    rows = await _all_measurements(db_session)
    rows_by_name = {
        next(
            iter(pt for pt in [density_pt, lattice_pt] if pt.id == r.property_type_id)
        ).name: r
        for r in rows
    }
    assert rows_by_name["density"].review_status == "invalid"
    assert rows_by_name["density"].validity_check["status"] == "fail"
    assert rows_by_name["lattice_constant"].review_status != "invalid"
    assert rows_by_name["lattice_constant"].validity_check["status"] == "ok"


# ---------------------------------------------------------------------------
# value_text fallback — parser failure doesn't crash validity_check
# ---------------------------------------------------------------------------


async def test_unparseable_value_persists_with_warn(
    db_session: AsyncSession,
) -> None:
    """LLM returns '3 to 4' — mapper falls back to value_text, validity
    check should warn (no numeric value), not crash."""

    pt = await _seed_property_type(
        db_session,
        category_slug="physical",
        property_name="density",
        property_slug="density",
        valid_range_min=0.5,
        valid_range_max=25.0,
    )
    source, material = await _seed_minimum(db_session, property_type=pt)

    result = await map_and_persist(
        db=db_session,
        extraction_output=[
            _make_extracted_property(
                property_name="density",
                value="3 to 4",  # unparseable as float
                unit="g/cm³",
            )
        ],
    )
    assert result.created_measurements == 1

    rows = await _all_measurements(db_session)
    row = rows[0]
    assert row.value_scalar is None
    assert row.value_text == "3 to 4"
    assert row.validity_check["status"] == "warn"
    assert row.validity_check["reason"] is not None
