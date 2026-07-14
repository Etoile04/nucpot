"""Seed dataset of nuclear material entity pairs for the dedup engine.

This module is the canonical source of ground-truth pairs used to validate the
dedup engine in Phase 2 / B3.1 of the NFMD project.

Positive pairs are entities that the dedup engine should resolve as duplicates
of one another. Negative pairs are distinct entities that the engine must NOT
collapse.

The expected_method field is a hint for which dedup strategy should match the
pair:
    - "exact"        : normalised name strings are identical
    - "alias"        : recognised alias / abbreviation pair
    - "composition"  : chemical composition formulas are equivalent
    - "fuzzy"        : high edit-distance / token-overlap similarity
    - "semantic"     : requires embedding-level similarity
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EntityPair:
    """A single ground-truth entity pair used by the dedup test suite."""

    name_a: str
    name_b: str
    composition_a: str | None = None
    composition_b: str | None = None
    expected_match: bool = True
    expected_method: str | None = None


# ---------------------------------------------------------------------------
# Positive pairs (should match as duplicates of one another).
# ---------------------------------------------------------------------------

POSITIVE_PAIRS: list[EntityPair] = [
    # Uranium dioxide family
    EntityPair(
        name_a="UO2",
        name_b="Uranium Dioxide",
        composition_a="UO2",
        composition_b="UO2",
        expected_match=True,
        expected_method="alias",
    ),
    EntityPair(
        name_a="Uranium Dioxide",
        name_b="uranium(IV) oxide",
        composition_a="UO2",
        composition_b="UO2",
        expected_match=True,
        expected_method="composition",
    ),
    EntityPair(
        name_a="UO2",
        name_b="urania",
        composition_a="UO2",
        composition_b="UO2",
        expected_match=True,
        expected_method="alias",
    ),
    EntityPair(
        name_a="urania",
        name_b="Uranium Dioxide",
        composition_a="UO2",
        composition_b="UO2",
        expected_match=True,
        expected_method="fuzzy",
    ),

    # Zircaloy family
    EntityPair(
        name_a="Zircaloy-4",
        name_b="Zr-1.5Sn",
        composition_a="Zr-1.5Sn-0.2Fe-0.1Cr",
        composition_b="Zr-1.5Sn",
        expected_match=True,
        expected_method="composition",
    ),
    EntityPair(
        name_a="Zircaloy-4",
        name_b="Zry-4",
        composition_a="Zr-1.5Sn-0.2Fe-0.1Cr",
        composition_b="Zr-1.5Sn-0.2Fe-0.1Cr",
        expected_match=True,
        expected_method="alias",
    ),
    EntityPair(
        name_a="Zr-1.5Sn-0.2Fe-0.1Cr",
        name_b="Zry-4",
        composition_a="Zr-1.5Sn-0.2Fe-0.1Cr",
        composition_b="Zr-1.5Sn-0.2Fe-0.1Cr",
        expected_match=True,
        expected_method="alias",
    ),

    # Stainless steel family
    EntityPair(
        name_a="SS-316",
        name_b="Stainless Steel 316",
        composition_a="Fe-18Cr-10Ni",
        composition_b="Fe-18Cr-10Ni",
        expected_match=True,
        expected_method="alias",
    ),
    EntityPair(
        name_a="AISI 316",
        name_b="SS-316",
        composition_a="Fe-18Cr-10Ni",
        composition_b="Fe-18Cr-10Ni",
        expected_match=True,
        expected_method="alias",
    ),
    EntityPair(
        name_a="AISI 316",
        name_b="Stainless Steel 316",
        composition_a="Fe-18Cr-10Ni",
        composition_b="Fe-18Cr-10Ni",
        expected_match=True,
        expected_method="exact",
    ),

    # FeCrAl family
    EntityPair(
        name_a="FeCrAl",
        name_b="Fe-Cr-Al",
        composition_a="Fe-Cr-Al",
        composition_b="Fe-Cr-Al",
        expected_match=True,
        expected_method="alias",
    ),
    EntityPair(
        name_a="FeCrAl",
        name_b="Iron-Chromium-Aluminum",
        composition_a="Fe-Cr-Al",
        composition_b="Fe-Cr-Al",
        expected_match=True,
        expected_method="semantic",
    ),
    EntityPair(
        name_a="Kanthal APM",
        name_b="FeCrAl",
        composition_a="Fe-22Cr-5Al",
        composition_b="Fe-Cr-Al",
        expected_match=True,
        expected_method="fuzzy",
    ),

    # SiC family
    EntityPair(
        name_a="SiC",
        name_b="Silicon Carbide",
        composition_a="SiC",
        composition_b="SiC",
        expected_match=True,
        expected_method="alias",
    ),
    EntityPair(
        name_a="Silicon Carbide",
        name_b="carborundum",
        composition_a="SiC",
        composition_b="SiC",
        expected_match=True,
        expected_method="alias",
    ),
    EntityPair(
        name_a="SiC",
        name_b="β-SiC",
        composition_a="SiC",
        composition_b="SiC",
        expected_match=True,
        expected_method="exact",
    ),

    # B4C family
    EntityPair(
        name_a="B4C",
        name_b="Boron Carbide",
        composition_a="B4C",
        composition_b="B4C",
        expected_match=True,
        expected_method="alias",
    ),
    EntityPair(
        name_a="B₄C",
        name_b="B4C",
        composition_a="B4C",
        composition_b="B4C",
        expected_match=True,
        expected_method="fuzzy",
    ),
    EntityPair(
        name_a="Boron Carbide",
        name_b="B₄C",
        composition_a="B4C",
        composition_b="B4C",
        expected_match=True,
        expected_method="exact",
    ),

    # ZrO2 family
    EntityPair(
        name_a="ZrO2",
        name_b="Zirconia",
        composition_a="ZrO2",
        composition_b="ZrO2",
        expected_match=True,
        expected_method="alias",
    ),
    EntityPair(
        name_a="Zirconium Dioxide",
        name_b="ZrO2",
        composition_a="ZrO2",
        composition_b="ZrO2",
        expected_match=True,
        expected_method="alias",
    ),
    EntityPair(
        name_a="yttria-stabilized zirconia (YSZ)",
        name_b="ZrO2",
        composition_a="ZrO2-Y2O3",
        composition_b="ZrO2",
        expected_match=True,
        expected_method="composition",
    ),

    # BeO family
    EntityPair(
        name_a="BeO",
        name_b="Beryllium Oxide",
        composition_a="BeO",
        composition_b="BeO",
        expected_match=True,
        expected_method="alias",
    ),
    EntityPair(
        name_a="Beryllium Oxide",
        name_b="beryllia",
        composition_a="BeO",
        composition_b="BeO",
        expected_match=True,
        expected_method="alias",
    ),
    EntityPair(
        name_a="BeO",
        name_b="beryllia",
        composition_a="BeO",
        composition_b="BeO",
        expected_match=True,
        expected_method="exact",
    ),
]


# ---------------------------------------------------------------------------
# Negative pairs (must NOT be matched as duplicates).
# ---------------------------------------------------------------------------

NEGATIVE_PAIRS: list[EntityPair] = [
    EntityPair(
        name_a="SS-316",
        name_b="SS-304",
        composition_a="Fe-18Cr-10Ni",
        composition_b="Fe-19Cr-9Ni",
        expected_match=False,
    ),
    EntityPair(
        name_a="UO2",
        name_b="MOX",
        composition_a="UO2",
        composition_b="(U,Pu)O2",
        expected_match=False,
    ),
    EntityPair(
        name_a="Zircaloy-4",
        name_b="SiC",
        composition_a="Zr-1.5Sn-0.2Fe-0.1Cr",
        composition_b="SiC",
        expected_match=False,
    ),
    EntityPair(
        name_a="FeCrAl",
        name_b="Inconel",
        composition_a="Fe-Cr-Al",
        composition_b="Ni-Cr-Fe",
        expected_match=False,
    ),
    EntityPair(
        name_a="B4C",
        name_b="Diamond",
        composition_a="B4C",
        composition_b="C",
        expected_match=False,
    ),
    EntityPair(
        name_a="ZrO2",
        name_b="UO2",
        composition_a="ZrO2",
        composition_b="UO2",
        expected_match=False,
    ),
    EntityPair(
        name_a="SS-316",
        name_b="Hastelloy-X",
        composition_a="Fe-18Cr-10Ni",
        composition_b="Ni-22Cr-9Mo-18Fe",
        expected_match=False,
    ),
    EntityPair(
        name_a="SiC",
        name_b="Graphite",
        composition_a="SiC",
        composition_b="C",
        expected_match=False,
    ),
    EntityPair(
        name_a="BeO",
        name_b="Al2O3",
        composition_a="BeO",
        composition_b="Al2O3",
        expected_match=False,
    ),
    EntityPair(
        name_a="UO2",
        name_b="UN",
        composition_a="UO2",
        composition_b="UN",
        expected_match=False,
    ),
    EntityPair(
        name_a="Stainless Steel 316",
        name_b="Stainless Steel 304",
        composition_a="Fe-18Cr-10Ni",
        composition_b="Fe-19Cr-9Ni",
        expected_match=False,
    ),
    EntityPair(
        name_a="Silicon Carbide",
        name_b="Boron Carbide",
        composition_a="SiC",
        composition_b="B4C",
        expected_match=False,
    ),
]


ALL_PAIRS: list[EntityPair] = POSITIVE_PAIRS + NEGATIVE_PAIRS


__all__ = [
    "ALL_PAIRS",
    "NEGATIVE_PAIRS",
    "POSITIVE_PAIRS",
    "EntityPair",
]
