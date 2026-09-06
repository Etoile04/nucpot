"""NFM-4312 (BUG-32) — staged material resolution in the extraction mapper.

Root cause under test: the mapper resolved an extraction item's material
with a single exact ``Material.formula == item.composition`` match.  The
heuristic extractor passes prose phrases as composition, so the match
missed and every run spawned a fragment material row while real
measurements piled onto the "Unknown Material (canonical)" sentinel —
84% of production ``property_measurements`` ended up invisible on every
real material's properties page.

The fix keeps the association topology (measurement → dataset.material_id
→ material) and widens *resolution* into conservative stages:

  1. exact formula
  2. normalized formula (case / whitespace / underscore / unicode
     subscript folding — phase qualifiers deliberately preserved)
  3. curated ``material_aliases`` rows
  4. exact display name

These tests pin each stage plus the create-path phase annotation.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nfm_db.models import (
    Dataset,
    Material,
    MaterialAlias,
    PropertyCategory,
    PropertyType,
)
from nfm_db.services.extraction_to_db_mapper import map_and_persist

# --- Helpers ---


def _item(overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    """Minimal extraction item that passes ExtractedProperty validation."""
    base: dict[str, Any] = {
        "material_name": "UO2",
        "composition": "UO2",
        "property_category": "thermal",
        "property": "lattice_constant",
        "value": "5.470",
        "unit": "angstrom",
        "confidence": "high",
    }
    if overrides:
        base = {**base, **overrides}
    return base


async def _seed_catalog(db_session: AsyncSession) -> PropertyType:
    """Seed the property catalog so measurements persist (not dropped)."""
    cat = PropertyCategory(name="Thermal", slug="thermal")
    db_session.add(cat)
    await db_session.flush()
    pt = PropertyType(
        category_id=cat.id,
        name="lattice_constant",
        slug="lattice-constant",
        value_type="scalar",
    )
    db_session.add(pt)
    await db_session.commit()
    return pt


async def _seed_material(db_session: AsyncSession, **overrides: Any) -> Material:
    defaults: dict[str, Any] = {"name": "UO2", "formula": "UO2"}
    defaults.update(overrides)
    mat = Material(**defaults)
    db_session.add(mat)
    await db_session.commit()
    await db_session.refresh(mat)
    return mat


async def _materials(db_session: AsyncSession) -> list[Material]:
    return list(
        (await db_session.execute(select(Material).order_by(Material.created_at))).scalars().all()
    )


# --- Stage 1: exact formula (pre-existing fast path) ---


@pytest.mark.asyncio
async def test_exact_formula_reuses_existing_material(db_session) -> None:
    await _seed_catalog(db_session)
    existing = await _seed_material(db_session)

    result = await map_and_persist(db_session, [_item()])

    assert result.created_materials == 0
    assert result.material_resolution_counts.get("formula") == 1
    materials = await _materials(db_session)
    assert len(materials) == 1
    assert materials[0].id == existing.id


# --- Stage 2: normalized formula (typography only) ---


@pytest.mark.asyncio
async def test_normalized_formula_matches_case_and_whitespace(
    db_session,
) -> None:
    await _seed_catalog(db_session)
    existing = await _seed_material(db_session, formula="UO2")

    result = await map_and_persist(db_session, [_item({"composition": " uo2 "})])

    assert result.created_materials == 0
    assert result.material_resolution_counts.get("normalized_formula") == 1
    materials = await _materials(db_session)
    assert len(materials) == 1
    assert materials[0].id == existing.id


@pytest.mark.asyncio
async def test_normalized_formula_matches_unicode_subscript(db_session) -> None:
    await _seed_catalog(db_session)
    existing = await _seed_material(db_session, formula="UO2")

    result = await map_and_persist(db_session, [_item({"composition": "UO₂"})])

    assert result.created_materials == 0
    assert result.material_resolution_counts.get("normalized_formula") == 1
    materials = await _materials(db_session)
    assert len(materials) == 1
    assert materials[0].id == existing.id


# --- Phase qualifiers must NOT fold onto the crystalline row ---


@pytest.mark.asyncio
async def test_amorphous_qualifier_creates_distinct_material(db_session) -> None:
    """\"amorphous UO2" is a distinct phase, not a typography variant.

    Folding it onto the crystalline UO2 (Fluorite) row would corrupt the
    experiment-vs-simulation comparison use case with amorphous-phase
    values — the exact mis-filing observed in production on 2026-09-01.
    """
    await _seed_catalog(db_session)
    crystalline = await _seed_material(db_session, crystal_structure="Fluorite")

    result = await map_and_persist(
        db_session, [_item({"composition": "amorphous UO2", "material_name": "amorphous UO2"})]
    )

    assert result.created_materials == 1
    # No stage resolved — the amorphous phrase is intentionally distinct.
    assert result.material_resolution_counts == {}
    materials = await _materials(db_session)
    assert len(materials) == 2
    amorphous = next(m for m in materials if m.id != crystalline.id)
    assert amorphous.formula == "amorphous UO2"
    # Create path annotates the phase so the fragment self-describes.
    assert amorphous.crystal_structure == "amorphous"


@pytest.mark.asyncio
async def test_phase_prefixed_identity_not_labeled_amorphous(db_session) -> None:
    """\"alpha-UO2" merely contains "a-uo2" — it is not an amorphous identity.

    A bare substring match would also flag "beta-UO2" / "gamma-UO2" /
    "Na-UO2" as amorphous; the marker must be a standalone token.
    """
    await _seed_catalog(db_session)

    result = await map_and_persist(
        db_session,
        [_item({"composition": "alpha-UO2", "material_name": "alpha-UO2"})],
    )

    assert result.created_materials == 1
    materials = await _materials(db_session)
    assert materials[0].crystal_structure is None


@pytest.mark.asyncio
async def test_standalone_a_uo2_token_labeled_amorphous(db_session) -> None:
    """\"a-UO2" as a standalone token still self-describes its phase."""
    await _seed_catalog(db_session)

    result = await map_and_persist(
        db_session,
        [_item({"composition": "a-UO2", "material_name": "a-UO2"})],
    )

    assert result.created_materials == 1
    materials = await _materials(db_session)
    assert materials[0].crystal_structure == "amorphous"


# --- Stage 3: curated aliases ---


@pytest.mark.asyncio
async def test_alias_resolution_reuses_material(db_session) -> None:
    await _seed_catalog(db_session)
    existing = await _seed_material(db_session)
    db_session.add(
        MaterialAlias(
            material_id=existing.id,
            alias_name="a-UO2",
            alias_type="common_name",
        )
    )
    await db_session.commit()

    result = await map_and_persist(db_session, [_item({"composition": "a-UO2"})])

    assert result.created_materials == 0
    assert result.material_resolution_counts.get("alias") == 1
    materials = await _materials(db_session)
    assert len(materials) == 1
    assert materials[0].id == existing.id


# --- Stage 4: exact display name (composition omitted) ---


@pytest.mark.asyncio
async def test_name_resolution_when_composition_missing(db_session) -> None:
    """NFM-3919 permits composition=None when the name carries identity."""
    await _seed_catalog(db_session)
    existing = await _seed_material(db_session, name="Zircaloy-4", formula=None)

    result = await map_and_persist(
        db_session,
        [_item({"material_name": "Zircaloy-4", "composition": None})],
    )

    assert result.skipped_unknown_materials == 0
    assert result.created_materials == 0
    assert result.material_resolution_counts.get("name") == 1
    materials = await _materials(db_session)
    assert len(materials) == 1
    assert materials[0].id == existing.id


# --- Stage precedence: exact formula wins over later stages ---


@pytest.mark.asyncio
async def test_exact_formula_outranks_alias(db_session) -> None:
    await _seed_catalog(db_session)
    formula_row = await _seed_material(db_session, name="UO2", formula="UO2")
    other = await _seed_material(db_session, name="Other", formula="XX")
    db_session.add(MaterialAlias(material_id=other.id, alias_name="UO2", alias_type="legacy_name"))
    await db_session.commit()

    result = await map_and_persist(db_session, [_item({"composition": "UO2"})])

    assert result.material_resolution_counts.get("formula") == 1
    assert result.material_resolution_counts.get("alias") is None
    assert result.created_materials == 0
    # The measurement landed on the real formula row, not the alias target.
    materials = {m.id: m for m in await _materials(db_session)}
    assert len(materials) == 2
    assert formula_row.id in materials


# --- CR R2 (NFM-4312 review): retired rows must never resolve ---
#
# A retired duplicate (``is_active=False``) keeps its original formula and
# name as an audit residue after the backfill merges its datasets onto the
# canonical row.  If any stage could still resolve onto it, a re-extraction
# of the same composition would create a *new* dataset on the hidden
# fragment (``uq_datasets_source_material`` no longer blocks it — its old
# datasets were moved away) and measurements would pile up invisibly
# again: a direct BUG-32 recurrence.  Every stage therefore only ever
# matches active rows.


@pytest.mark.asyncio
async def test_inactive_row_never_resolves_on_exact_formula_or_name(
    db_session,
) -> None:
    """No stage may land on a retired row — not even exact-formula.

    Seeds the production shape from the CR review: retired ``794120c1``
    holds formula ``"UO2 (undoped and Cr-doped)"`` as the only exact match
    for the incoming composition.  The expected outcome is a full miss on
    every stage and a fresh *active* material, never a hit on the retired
    fragment.
    """
    await _seed_catalog(db_session)
    retired = await _seed_material(
        db_session,
        name="UO2 (undoped and Cr-doped)",
        formula="UO2 (undoped and Cr-doped)",
        is_active=False,
    )

    result = await map_and_persist(
        db_session,
        [
            _item(
                {
                    "composition": "UO2 (undoped and Cr-doped)",
                    "material_name": "UO2 (undoped and Cr-doped)",
                }
            )
        ],
    )

    assert retired.is_active is False
    # Formula, normalized-formula, alias and name all had a chance to hit
    # the retired row; none may.
    assert result.material_resolution_counts == {}
    assert result.created_materials == 1
    materials = await _materials(db_session)
    assert len(materials) == 2
    fresh = next(m for m in materials if m.id != retired.id)
    assert fresh.is_active is True
    assert fresh.formula == "UO2 (undoped and Cr-doped)"


@pytest.mark.asyncio
async def test_inactive_row_never_resolves_on_name_only(db_session) -> None:
    """Name-stage carrier (composition omitted) also skips retired rows."""
    await _seed_catalog(db_session)
    retired = await _seed_material(db_session, name="Zircaloy-4", formula=None, is_active=False)

    result = await map_and_persist(
        db_session,
        [_item({"material_name": "Zircaloy-4", "composition": None})],
    )

    assert result.material_resolution_counts == {}
    assert result.created_materials == 1
    materials = await _materials(db_session)
    fresh = next(m for m in materials if m.id != retired.id)
    assert fresh.is_active is True


@pytest.mark.asyncio
async def test_alias_pointing_at_inactive_material_does_not_resolve(
    db_session,
) -> None:
    """Stage-3 shares the active-bias: a stale alias must not resurrect."""
    await _seed_catalog(db_session)
    retired = await _seed_material(
        db_session, name="Retired", formula="XX-retired", is_active=False
    )
    db_session.add(
        MaterialAlias(
            material_id=retired.id,
            alias_name="a-UO2",
            alias_type="common_name",
        )
    )
    await db_session.commit()

    result = await map_and_persist(db_session, [_item({"composition": "a-UO2"})])

    assert result.material_resolution_counts == {}
    assert result.created_materials == 1
    materials = await _materials(db_session)
    fresh = next(m for m in materials if m.id != retired.id)
    assert fresh.is_active is True


@pytest.mark.asyncio
async def test_active_row_wins_over_retired_duplicate_formula(
    db_session,
) -> None:
    """CR R3: among exact-formula matches, the canonical active row wins.

    The retired duplicate is seeded first (older ``created_at``) so the
    pre-fix unordered ``limit(1)`` would have returned it; the measurement
    must land on the active canonical row deterministically.
    """
    await _seed_catalog(db_session)
    await _seed_material(db_session, name="UO2 (old fragment)", formula="UO2", is_active=False)
    canonical = await _seed_material(db_session, name="UO2", formula="UO2", is_active=True)

    result = await map_and_persist(db_session, [_item()])

    assert result.material_resolution_counts.get("formula") == 1
    assert result.created_materials == 0
    datasets = list((await db_session.execute(select(Dataset))).scalars().all())
    assert len(datasets) == 1
    assert datasets[0].material_id == canonical.id
