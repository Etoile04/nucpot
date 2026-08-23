"""Generate the 100-sample parity fixture for NFM-3565 / D2.

Run from the repo root::

    python3 apps/api/tests/fixtures/gap_scan_parity_fixture_generator.py \\
        --output apps/api/tests/fixtures/gap_scan_parity_100.jsonl

The fixture is consumed by ``apps/api/tests/services/test_gap_scan_parity.py``.
Every entry has the shape the harness expects::

    {
      "id": <int>,                  # 1..100
      "kind": <str>,                # human-readable category tag
      "description": <str>,         # 1-line summary of what this entry covers
      "target_triple": [str, str, str],   # [element_system, phase, property_name]
      "canonical_ontology": {      # what canonical.extract_entity_types + iter_property_names consume
        "entity_types": [
          {"name": <str>, "properties": [<str> | {name: <str>, ...}, ...]},
          ...
        ]
      }
    }

The 100 entries satisfy the four coverage requirements documented in
``test_gap_scan_parity.TestFixtureCoverage``:

* Every legacy ``_DEFAULT_TARGET_TUPLES`` triple appears at least once
  (entries #1-#12 use the legacy table verbatim).
* At least 10 entries drive ``iter_property_names``'s dict-input path
  (entries #13-#24 use dict-form property entries).
* The triple's ``property_name`` is always surfaced by the canonical
  ontology, so the structural-parity rule
  ``triple[2] in property_names`` holds across all 100 entries.
* Exactly 100 entries — the harness enforces this with a hard assertion.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Property sets per element system.  These are intentionally rich — every
# legacy ``_DEFAULT_TARGET_TUPLES`` property is present in the matching
# element's set so the canonical ontology always surfaces the triple's
# property_name.
# ---------------------------------------------------------------------------

_PROPERTIES_BY_ELEMENT: dict[str, list[str]] = {
    "U": [
        "lattice_constant",
        "bulk_modulus",
        "thermal_conductivity",
    ],
    "UO2": [
        "lattice_constant",
        "bulk_modulus",
        "thermal_conductivity",
        "linear_expansion",
    ],
    "Zr": [
        "lattice_constant",
        "bulk_modulus",
        "thermal_conductivity",
    ],
    # Extended elements (not in the legacy 12-tuple default — used by
    # entries #49+ to expand the fixture beyond the legacy table).
    "UC": ["lattice_constant", "bulk_modulus"],
    "Pu": ["lattice_constant", "thermal_expansion"],
    "ThO2": ["lattice_constant", "thermal_conductivity", "heat_capacity"],
}


# The legacy default target tuples — verbatim from
# apps/api/src/nfm_db/services/gap_scan_service.py:32-45.
_LEGACY_DEFAULT_TUPLES: list[tuple[str, str, str]] = [
    ("U", "BCC", "lattice_constant"),
    ("U", "BCC", "bulk_modulus"),
    ("U", "BCC", "thermal_conductivity"),
    ("U", "FCC", "lattice_constant"),
    ("U", "FCC", "bulk_modulus"),
    ("UO2", "FCC", "lattice_constant"),
    ("UO2", "FCC", "bulk_modulus"),
    ("UO2", "FCC", "thermal_conductivity"),
    ("UO2", "FCC", "linear_expansion"),
    ("Zr", "HCP", "lattice_constant"),
    ("Zr", "HCP", "bulk_modulus"),
    ("Zr", "HCP", "thermal_conductivity"),
]


def _dict_properties(props: list[str]) -> list[dict[str, str]]:
    """Wrap a list of property names in dict-form for ``iter_property_names``'s
    dict-input branch."""
    return [{"name": p, "datatype": "float"} for p in props]


def _ontology_for_element(
    element_system: str,
    *,
    property_format: str = "str",
    extra_entity_types: list[dict[str, Any]] | None = None,
    whitespace_property: str | None = None,
) -> dict[str, Any]:
    """Build a canonical ontology JSON blob for a given element system.

    Args:
        element_system: The element whose properties become the entity_type.
        property_format: ``"str"`` for string properties, ``"dict"`` for
            dict-form, ``"mixed"`` for both.
        extra_entity_types: Additional entity types appended to the
            ontology (used by ``extended_ontology`` entries to broaden
            the surface area).
        whitespace_property: If set, append a property whose name has
            leading/trailing whitespace — exercises the
            ``prop.strip()`` defensive code in ``iter_property_names``.
    """
    props = _PROPERTIES_BY_ELEMENT[element_system]
    if whitespace_property is not None:
        props = list(props) + [whitespace_property]

    if property_format == "str":
        properties: list[Any] = list(props)
    elif property_format == "dict":
        properties = _dict_properties(props)
    elif property_format == "mixed":
        # Half strings, half dicts (alternating).
        properties = []
        for idx, p in enumerate(props):
            if idx % 2 == 0:
                properties.append(p)
            else:
                properties.append({"name": p, "datatype": "float"})
    else:
        raise ValueError(f"Unknown property_format: {property_format!r}")

    entity_types: list[dict[str, Any]] = [
        {"name": element_system, "properties": properties},
    ]
    if extra_entity_types:
        entity_types.extend(extra_entity_types)
    return {"entity_types": entity_types}


def _entry(
    entry_id: int,
    kind: str,
    description: str,
    triple: tuple[str, str, str],
    ontology: dict[str, Any],
) -> dict[str, Any]:
    return {
        "id": entry_id,
        "kind": kind,
        "description": description,
        "target_triple": list(triple),
        "canonical_ontology": ontology,
    }


# ---------------------------------------------------------------------------
# Fixture builder — produces exactly 100 entries.
# ---------------------------------------------------------------------------


def build_fixture() -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []

    # ---- 1-12: legacy default tuples verbatim (12 entries) ----------------
    for idx, triple in enumerate(_LEGACY_DEFAULT_TUPLES, start=1):
        entries.append(
            _entry(
                idx,
                "default_tuple",
                f"Legacy default tuple #{idx}: {triple}",
                triple,
                _ontology_for_element(triple[0]),
            ),
        )

    # ---- 13-24: same legacy tuples, dict-form properties (12 entries) -----
    for offset, triple in enumerate(_LEGACY_DEFAULT_TUPLES):
        entries.append(
            _entry(
                13 + offset,
                "dict_properties",
                f"Legacy tuple with dict-form properties: {triple}",
                triple,
                _ontology_for_element(triple[0], property_format="dict"),
            ),
        )

    # ---- 25-36: same legacy tuples with extended entity types (12) -------
    for offset, triple in enumerate(_LEGACY_DEFAULT_TUPLES):
        extra = [
            {
                "name": "Material",
                "properties": ["density", "melting_point"],
            },
            {
                "name": "Property",
                "properties": [{"name": "unit", "datatype": "string"}],
            },
        ]
        entries.append(
            _entry(
                25 + offset,
                "extended_ontology",
                f"Legacy tuple with extra Material+Property entity types: {triple}",
                triple,
                _ontology_for_element(
                    triple[0], property_format="str", extra_entity_types=extra,
                ),
            ),
        )

    # ---- 37-48: whitespace-padded property_name (12) ---------------------
    for offset, triple in enumerate(_LEGACY_DEFAULT_TUPLES):
        padded = f"  {triple[2]}  "
        entries.append(
            _entry(
                37 + offset,
                "whitespace_property",
                f"Triple with whitespace-padded property_name: {triple!r}",
                triple,
                _ontology_for_element(
                    triple[0],
                    property_format="str",
                    whitespace_property=padded,
                ),
            ),
        )

    # ---- 49-72: extended tuples (24) --------------------------------------
    # New element/phase/property combinations not in the legacy 12.
    extended: list[tuple[str, str, str]] = [
        ("UC", "FCC", "lattice_constant"),
        ("UC", "FCC", "bulk_modulus"),
        ("UC", "BCC", "lattice_constant"),
        ("UC", "BCC", "bulk_modulus"),
        ("Pu", "FCC", "lattice_constant"),
        ("Pu", "FCC", "thermal_expansion"),
        ("Pu", "BCC", "lattice_constant"),
        ("Pu", "BCC", "thermal_expansion"),
        ("ThO2", "FCC", "lattice_constant"),
        ("ThO2", "FCC", "thermal_conductivity"),
        ("ThO2", "FCC", "heat_capacity"),
        ("ThO2", "BCC", "lattice_constant"),
        ("U", "LIQUID", "lattice_constant"),
        ("U", "AMORPHOUS", "bulk_modulus"),
        ("UO2", "LIQUID", "thermal_conductivity"),
        ("UO2", "AMORPHOUS", "linear_expansion"),
        ("Zr", "FCC", "lattice_constant"),
        ("Zr", "BCC", "bulk_modulus"),
        ("UC", "HCP", "lattice_constant"),
        ("Pu", "HCP", "thermal_expansion"),
        ("ThO2", "HCP", "heat_capacity"),
        ("U", "HCP", "lattice_constant"),
        ("UO2", "HCP", "bulk_modulus"),
        ("Zr", "AMORPHOUS", "thermal_conductivity"),
    ]
    assert len(extended) == 24, f"expected 24 extended tuples, got {len(extended)}"
    for offset, triple in enumerate(extended):
        entries.append(
            _entry(
                49 + offset,
                "extended_tuple",
                f"Extended (non-legacy) tuple: {triple}",
                triple,
                _ontology_for_element(triple[0], property_format="str"),
            ),
        )

    # ---- 73-88: mixed (str+dict) property formats on legacy triples (16) -
    # Take the first 16 legacy triples (wraps if needed) but skew toward
    # using dict-form for half + str-form for half.
    for offset in range(16):
        triple = _LEGACY_DEFAULT_TUPLES[offset % len(_LEGACY_DEFAULT_TUPLES)]
        entries.append(
            _entry(
                73 + offset,
                "mixed_properties",
                f"Legacy tuple with mixed str/dict property entries: {triple}",
                triple,
                _ontology_for_element(triple[0], property_format="mixed"),
            ),
        )

    # ---- 89-100: edge cases (12) -----------------------------------------
    # All edge cases still satisfy the parity rule (property_name surfaces),
    # but exercise branches like dict-only, single-property entity,
    # property with empty string sentinel, etc.
    edge: list[tuple[str, str, str, dict[str, Any]]] = [
        (
            "U", "BCC", "lattice_constant",
            _ontology_for_element("U", property_format="dict"),
        ),
        (
            "UO2", "FCC", "thermal_conductivity",
            _ontology_for_element("UO2", property_format="dict"),
        ),
        (
            "Zr", "HCP", "thermal_conductivity",
            _ontology_for_element("Zr", property_format="dict"),
        ),
        (
            "UC", "FCC", "lattice_constant",
            _ontology_for_element("UC", property_format="dict"),
        ),
        (
            "U", "BCC", "lattice_constant",
            # Same as the default but property list contains the value twice
            # — exercises the no-dedup behavior of iter_property_names.
            {"entity_types": [
                {"name": "U", "properties": ["lattice_constant", "lattice_constant"]},
            ]},
        ),
        (
            "U", "BCC", "lattice_constant",
            # entity_types with extra empty properties list (no-op branch).
            {"entity_types": [
                {"name": "U", "properties": ["lattice_constant"]},
                {"name": "_empty_", "properties": []},
            ]},
        ),
        (
            "UO2", "FCC", "bulk_modulus",
            _ontology_for_element("UO2", property_format="dict"),
        ),
        (
            "Zr", "HCP", "lattice_constant",
            _ontology_for_element("Zr", property_format="dict"),
        ),
        (
            "Pu", "FCC", "lattice_constant",
            _ontology_for_element("Pu", property_format="dict"),
        ),
        (
            "ThO2", "FCC", "heat_capacity",
            _ontology_for_element("ThO2", property_format="dict"),
        ),
        (
            "UC", "FCC", "bulk_modulus",
            _ontology_for_element("UC", property_format="dict"),
        ),
        (
            "U", "BCC", "lattice_constant",
            # Properties as a mix including a dict with empty name — exercises
            # iter_property_names' defensive drop of empty-name dict entries.
            {"entity_types": [
                {"name": "U", "properties": [
                    "lattice_constant",
                    {"name": "", "datatype": "string"},
                    {"name": "lattice_constant", "datatype": "float"},
                ]},
            ]},
        ),
    ]
    assert len(edge) == 12, f"expected 12 edge cases, got {len(edge)}"
    for offset, (es, phase, prop, ontology) in enumerate(edge):
        entries.append(
            _entry(
                89 + offset,
                "edge_case",
                f"Edge case #{offset + 1}: ({es}, {phase}, {prop})",
                (es, phase, prop),
                ontology,
            ),
        )

    assert len(entries) == 100, f"fixture must be exactly 100 entries, got {len(entries)}"
    return entries


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parent / "gap_scan_parity_100.jsonl",
        help="Destination JSONL path.",
    )
    args = parser.parse_args()

    entries = build_fixture()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w") as handle:
        for entry in entries:
            handle.write(json.dumps(entry, separators=(",", ":")))
            handle.write("\n")
    print(f"Wrote {len(entries)} entries to {args.output}")


if __name__ == "__main__":
    main()
