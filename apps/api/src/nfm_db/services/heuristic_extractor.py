"""Heuristic regex/materials extractor (NFM-2050 demo fallback).

Used when the LLM endpoint is unreachable so the Phase 2 demo can still
show material/property extractions.  Not a replacement for the LLM
extractor — only a fallback for offline / network-restricted environments.

Strategy:
  1. From ``content_md``, find chemical formulae (e.g. UO2, U-10Mo, Cr).
  2. Find numeric values paired with units (e.g. ``5.47 angstrom`` or
     ``10.97 g/cm3``).
  3. Tag each pair with a property name (lattice_constant, density, etc.)
     via a small dictionary of regex rules.
  4. Return a list of dicts in the same shape as :func:`ontofuel_extract`.
"""

from __future__ import annotations

import re
from typing import Any

# ----------------------------------------------------------------------
# Material detection
# ----------------------------------------------------------------------

_KNOWN_ELEMENTS: frozenset[str] = frozenset(
    {
        "H", "He", "Li", "Be", "B", "C", "N", "O", "F", "Ne",
        "Na", "Mg", "Al", "Si", "P", "S", "Cl", "Ar", "K", "Ca",
        "Sc", "Ti", "V", "Cr", "Mn", "Fe", "Co", "Ni", "Cu", "Zn",
        "Ga", "Ge", "As", "Se", "Br", "Kr", "Rb", "Sr", "Y", "Zr",
        "Nb", "Mo", "Tc", "Ru", "Rh", "Pd", "Ag", "Cd", "In", "Sn",
        "Sb", "Te", "I", "Xe", "Cs", "Ba", "La", "Ce", "Pr", "Nd",
        "Pm", "Sm", "Eu", "Gd", "Tb", "Dy", "Ho", "Er", "Tm", "Yb",
        "Lu", "Hf", "Ta", "W", "Re", "Os", "Ir", "Pt", "Au", "Hg",
        "Tl", "Pb", "Bi", "Po", "At", "Rn", "Fr", "Ra", "Ac", "Th",
        "Pa", "U", "Np", "Pu", "Am", "Cm", "Bk", "Cf",
    }
)


def _element_symbols(formula: str) -> list[str]:
    return re.findall(r"[A-Z][a-z]?", formula)


# Optional leading parenthesis (for "UO(2)"→UO2), then a run of element
# symbols with optional stoichiometry numbers and dashes, then optional
# closing parenthesis and trailing fractional notation like ".5".
_MATERIAL_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])"
    r"(?:\()?"
    r"(?:[A-Z][a-z]?\d*(?:[\-\u2013]\d+\.?\d*)?)+"
    r"(?:\))?"
    r"(?!\w)"
)


def _is_real_compound(formula: str) -> bool:
    """Filter rule: every symbol must be in _KNOWN_ELEMENTS AND we must
    have evidence this isn't prose (multi-element OR separator OR ≥2-digit
    stoichiometric suffix that can't plausibly be oxidation state).

    Single-element + single-digit forms like "Cr3", "O2", "Fe2" are
    chemistry oxidation-state notation, NOT materials — they're noisy
    and should be rejected so they don't pollute the review queue.
    Allowed forms:
      * Multi-element formulas (UO2, U3Si2, Cr2O3, Al2O3)
      * Alloy conventions with separator (U-10Mo, Zr-4)
      * Bare element name with no digit (U, Cr, Mo) — accepted as
        element-only when it's ≥2 chars and not a common English word.
    """
    formula = formula.strip().strip("()")
    if not formula or len(formula) > 30:
        return False
    syms = _element_symbols(formula)
    if not syms or any(s not in _KNOWN_ELEMENTS for s in syms):
        return False
    has_digit = bool(re.search(r"\d", formula))
    has_separator = "-" in formula or "\u2013" in formula
    multi_elem = len(syms) >= 2
    # 2+ digit suffix like "U235" or "Si28" is fine — too long for oxidation
    two_digit = bool(re.fullmatch(r"[A-Z][a-z]?\d{2,}.*", formula))
    if multi_elem or has_separator or two_digit:
        return True
    if has_digit:
        # Reject single-element + single-digit (Cr3, O2, Fe2 etc.)
        return False
    # Single bare element symbol — accept iff not a common English word.
    if formula in _FALSE_FRIENDS:
        return False
    return False  # bare single symbol, almost always prose


def _collapse_spaced_formula(text: str) -> str:
    """Collapse space-separated digts adjacent to a chemical symbol.

    PyMuPDF text extraction frequently renders "UO2" as "UO 2". We turn
    those into "UO2" so the materials matcher sees the real formula. We
    mustn't touch prose like "for U" or "in B" — only patterns where the
    next token looks like a stoichiometry digit + (optional) another
    element.
    """
    # Pattern: symbol, optional single space, then 1-3 digit chars,
    # optionally followed by another element symbol.  Examples matched:
    #   "UO 2"      -> "UO2"
    #   "UO 2.4"    -> "UO2.4"
    #   "U3 Si 2"   -> "U3Si2"
    return re.sub(
        r"([A-Z][a-z]?)\s+(\d(?:\.\d+)?)(?=\s|$|\)|,|\.|\b[A-Z][a-z]?\b)",
        r"\1\2",
        text,
    )


# Common English tokens that share the formula grammar but aren't materials.
_FALSE_FRIENDS: frozenset[str] = frozenset(
    {
        "At", "In", "On", "To", "Is", "As", "It", "An", "Be",
        "He", "We", "By", "Or", "No", "So", "Do", "Me", "La", "Ca",
        "Of", "If", "Up", "Us",
    }
)


def _collect_materials(text: str) -> list[tuple[int, int, str]]:
    """Return (start, end, formula) tuples for every valid material in *text*."""
    out: list[tuple[int, int, str]] = []
    for m in _MATERIAL_PATTERN.finditer(text):
        s = m.group(0).strip().strip("()")
        if not s:
            continue
        if s in _FALSE_FRIENDS:
            continue
        if not _is_real_compound(s):
            continue
        out.append((m.start(), m.end(), s))
    return out


# ----------------------------------------------------------------------
# Numeric value + unit extraction
# ----------------------------------------------------------------------

_UNIT_TOKENS = (
    r"angstrom|\u00c5|\u212b|"
    r"nm|pm|mm|cm|"
    r"g/cm3|kg/m3|g/cm\^3|kg/m\^3|"
    r"K|degC|\u00b0C|\u00b0F|"
    r"GPa|MPa|kPa|Pa|"
    r"eV|meV|keV|"
    r"kJ/mol|kcal/mol|eV/atom|"
    r"W/m/K|W/m\u00b7K|"
    r"cm\u00b2/s|cm\^2/s|cm2/s|"
    r"J/kg|kJ/kg|"
    r"wt%|at%|mol%|ppm|appm|"
    r"\u00b5m|micro\W*m"
)

_VALUE_UNIT_RE = re.compile(
    rf"(?P<value>-?\d{{1,3}}(?:[, ]?\d{{3}})*\.?\d*(?:[eE][-+]?\d+)?)"
    rf"\s*(?P<unit>{_UNIT_TOKENS})\b",
    re.UNICODE,
)


def _normalize_number(s: str) -> float | None:
    """Parse a numeric string that may include thin spaces or commas."""
    cleaned = s.replace(",", "").replace(" ", "").replace("\u202f", "")
    try:
        return float(cleaned)
    except ValueError:
        return None


# ----------------------------------------------------------------------
# Property name detection
# ----------------------------------------------------------------------

_PROPERTY_RULES: list[tuple[re.Pattern[str], str, str]] = [
    (re.compile(r"lattice[\s_-]*(?:constant|parameter)\s*[a-z]?\b", re.I), "lattice_constant", "length"),
    (re.compile(r"\bbulk\s+modulus\b", re.I), "bulk_modulus", "pressure"),
    (re.compile(r"\byoung'?s?\s+modulus\b|\byoung'?s?\b|\belastic\s+modulus\b", re.I), "youngs_modulus", "pressure"),
    (re.compile(r"\bshear\s+modulus\b", re.I), "shear_modulus", "pressure"),
    (re.compile(r"\bdensity\b|densit\u00e9\b|\bmass\s+density\b", re.I), "density", "density"),
    (re.compile(r"thermal[\s_-]*conductivity", re.I), "thermal_conductivity", "thermal_cond"),
    (re.compile(r"specific\s+heat(?:\s+capacity)?|heat\s+capacity", re.I), "specific_heat", "specific_heat"),
    (re.compile(r"melting\s+(?:point|temperature)", re.I), "melting_point", "temperature"),
    (re.compile(r"\bcurie\s+temp", re.I), "curie_temperature", "temperature"),
    (re.compile(r"\bcurie\b", re.I), "curie_temperature", "temperature"),
    (re.compile(r"diffusion[\s_-]*(?:coefficient|coeff\.?|constant)|diffusivity", re.I), "diffusion_coefficient", "diffusivity"),
    (re.compile(r"activation[\s_-]*energy", re.I), "activation_energy", "energy"),
    (re.compile(r"formation[\s_-]*energy", re.I), "formation_energy", "energy"),
    (re.compile(r"binding[\s_-]*energy", re.I), "binding_energy", "energy"),
    (re.compile(r"band[\s_-]*gap", re.I), "band_gap", "energy"),
    (re.compile(r"\bcohesive\s+energy\b", re.I), "cohesive_energy", "energy"),
    (re.compile(r"thermal[\s_-]*expansion", re.I), "thermal_expansion_coefficient", "expansion"),
    (re.compile(r"pre[\s_-]*exponential(?:\s+factor)?", re.I), "pre_exponential_factor", "diffusivity"),
    (re.compile(r"grain\s+size", re.I), "grain_size", "length"),
    (re.compile(r"\bporosity\b", re.I), "porosity", "dimensionless"),
    # NFM-3517 [NFM-3424-A]: two NEW F8 scorecard property classes \u2014
    # additive rules appended below; existing patterns above are NOT
    # modified (per AC-A5 regression guard).
    #
    # Maps to kg_nodes.label 'RDF\u5cf0' (radial distribution function peaks)
    # for the NDE F8 checkpoint #7 (2.28 \u00c5, 2.83 \u00c5).
    # The regex tolerates up to 60 chars between the RDF indicator and
    # the word "peak"/"peaks" so it matches natural prose like
    # "the radial distribution function (RDF) of UO2 shows two
    # prominent peaks at 2.28 angstrom" \u2014 not just the bare
    # "RDF peak" / "radial distribution function peak" forms.
    (
        re.compile(
            r"(?:rdf|radial\s+distribution\s+function|g\s*\(\s*r\s*\))"
            r"(?:[^.]{1,120})"
            r"\bpeaks?\b"
            r"|\brdf\s+peak|radial\s+distribution\s+function\s+peak|\bg\(r\)\s+peak",
            re.I,
        ),
        "rdf_peak",
        "length",
    ),
    # Maps to kg_nodes.label '\u952e\u957f' (bond length) for the NDE F8
    # checkpoint #8 (Cr-O bond length 2.02\u20132.05 \u00c5).
    (
        re.compile(
            r"[A-Za-z]{1,3}[\s\-]*[A-Za-z]{1,3}\s+bond\s+(?:length|distance)"
            r"|\bbond\s+(?:length|distance)\b",
            re.I,
        ),
        "bond_length",
        "length",
    ),
    # ------------------------------------------------------------------
    # NFM-3511: DFT / theoretical calculation property patterns
    # ------------------------------------------------------------------
    # Elastic constants (C11, C12, C44) \u2014 pressure family (GPa)
    (
        re.compile(r"\bC(?:11|12|22|13|23|33|44|55|66)\b", re.I),
        "elastic_constant",
        "pressure",
    ),
    # Heat of formation (variant wording of formation_energy)
    (
        re.compile(r"heat\s+of\s+formation", re.I),
        "formation_energy",
        "energy",
    ),
    # Ordering / disordering temperature \u2014 temperature family (K)
    (
        re.compile(r"(?:order|disorder)(?:ing)?\s+temperature", re.I),
        "ordering_temperature",
        "temperature",
    ),
    # Solubility limit \u2014 dimensionless family (at%, wt%)
    (
        re.compile(r"solubility\s+limit", re.I),
        "solubility_limit",
        "dimensionless",
    ),
    # DOS at Fermi level / N(E_F) \u2014 energy family (eV)
    (
        re.compile(
            r"(?:DOS|density\s+of\s+states?)"
            r"(?:\s+at\s+(?:the\s+)?)?(?:E_F|E[_Ff]|Fermi\s+level)"
            r"|N\s*\(\s*E_F\s*\)",
            re.I,
        ),
        "dos_at_fermi_level",
        "energy",
    ),
]


def _match_property(
    text: str,
    idx: int,
    *,
    unit: str | None = None,
) -> tuple[str, str] | None:
    """Walk back from idx to find a property name. Returns (name, family).

    Search up to 250 chars back so distant headers/captions like
    "Lattice parameter measured using X-ray diffraction ... 5.47 angstrom"
    still match even when the property label sits a sentence or two
    earlier in the paragraph.

    NFM-3517: prefer the CLOSEST (most recent) match in the walk-back
    window, regardless of rule-list order. Previously the first rule in
    ``_PROPERTY_RULES`` order that had *any* match in the window won \u2014
    so ``density`` (early in the list) beat ``activation_energy`` for
    eV values whenever "density" appeared earlier in the paragraph.
    The fix is restricted to picking the closest position; the rule
    list itself is not reordered.

    If ``unit`` is provided, rules whose declared family is compatible
    with that unit are preferred over closer-but-incompatible rules.
    E.g. ``1.27e-9 cm2/s`` should pick ``diffusion_coefficient``
    (diffusivity family) over a closer ``activation_energy`` (energy
    family) mention. When no compatible rule matches in the window,
    falls back to the closest rule regardless of family.
    """
    start = max(0, idx - 250)
    section = text[start:idx]
    best: tuple[str, str] | None = None
    best_pos = -1
    best_compat: tuple[str, str] | None = None
    best_compat_pos = -1
    for pattern, name, family in _PROPERTY_RULES:
        for m in pattern.finditer(section):
            if m.start() > best_pos:
                best = (name, family)
                best_pos = m.start()
            if unit is not None and _units_compatible(unit, family):
                if m.start() > best_compat_pos:
                    best_compat = (name, family)
                    best_compat_pos = m.start()
    return best_compat if best_compat is not None else best


_UNIT_FAMILIES: dict[str, frozenset[str]] = {
    "length": frozenset({"angstrom", "\u00c5", "\u212b", "nm", "pm", "mm", "cm"}),
    "density": frozenset({"g/cm3", "kg/m3", "g/cm^3", "kg/m^3"}),
    "thermal_cond": frozenset({"W/m/K", "W/m\u00b7K"}),
    "specific_heat": frozenset({"J/kg", "kJ/kg"}),
    "temperature": frozenset({"K", "degC", "\u00b0C", "\u00b0F"}),
    "pressure": frozenset({"GPa", "MPa", "kPa", "Pa"}),
    "diffusivity": frozenset({"cm\u00b2/s", "cm^2/s", "cm2/s"}),
    "energy": frozenset({"eV", "meV", "keV", "kJ/mol", "kcal/mol", "eV/atom"}),
    "expansion": frozenset({"1/K", "10\u207b\u2076/K", "ppm/K"}),
    "dimensionless": frozenset({"wt%", "at%", "mol%", "ppm", "appm"}),
}


def _units_compatible(unit: str, family: str) -> bool:
    if family not in _UNIT_FAMILIES:
        return True
    return unit in _UNIT_FAMILIES[family]


# ----------------------------------------------------------------------
# NFM-3511: DFT unitless property patterns (no standard unit token)
# ----------------------------------------------------------------------
# These match property name + bare value in a single regex for
# dimensionless DFT quantities that the main value+unit loop misses.

_DFT_DIRECT_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(
            r"screening\s+constant\s*[=:~]\s*(?P<val>-?\d+\.?\d*(?:[eE][-+]?\d+)?)",
            re.I,
        ),
        "screening_constant",
    ),
    (
        re.compile(
            r"screening\s+constant\s+(?:of\s+\S+\s+)?(?:is|was|equals?)\s+(?P<val>-?\d+\.?\d*(?:[eE][-+]?\d+)?)",
            re.I,
        ),
        "screening_constant",
    ),
]


# ----------------------------------------------------------------------
# Spatial matching — associate property/value pair with nearest material
# ----------------------------------------------------------------------


def _nearest_material(
    materials: list[tuple[int, int, str]],
    idx: int,
    *,
    max_distance: int = 400,
) -> str | None:
    """Pick the material (start,end,formula) closest to *idx*.

    Prefer materials that END before *idx* so we look at preceding context.
    Falls back to materials that start after *idx*.
    Wider max_distance default (400 chars) so distant captions/tables
    still get a sensible attribution.
    """
    if not materials:
        return None
    back: list[int] = [i for i, (s, _e, _) in enumerate(materials) if s <= idx and idx - s <= max_distance]
    fwd: list[int] = [i for i, (s, _e, _) in enumerate(materials) if s > idx and s - idx <= max_distance]
    if back:
        # pick the rightmost (= closest to idx)
        return materials[max(back)][2]
    if fwd:
        return materials[min(fwd)][2]
    return None


# ----------------------------------------------------------------------
# Public API
# ----------------------------------------------------------------------


def heuristic_extract(
    content: str,
    *,
    source_reference: str,
    element_systems: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Run a regex-based extraction. Returns same shape as ontofuel_extract.

    The output dict matches the production schema so downstream
    ``extraction_to_db_mapper.map_and_persist`` can ingest it without
    code changes.
    """
    if not content:
        return []

    # PyMuPDF often renders "UO2" as "UO 2" — collapse those into proper
    # formulas before scanning for materials.
    normalized = _collapse_spaced_formula(content)

    materials = _collect_materials(normalized)
    if not materials:
        return []

    found: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, str, str]] = set()

    for m in _VALUE_UNIT_RE.finditer(normalized):
        value = _normalize_number(m.group("value"))
        if value is None:
            continue
        unit = m.group("unit")
        prop = _match_property(normalized, m.start(), unit=unit)
        if prop is None:
            continue
        name, family = prop
        if not _units_compatible(unit, family):
            continue

        material = _nearest_material(materials, m.start())
        if material is None:
            continue

        # Element filter (to match LLM extractor behavior).
        if element_systems:
            match = any(
                re.search(re.escape(es), material, re.IGNORECASE)
                for es in element_systems
            )
            if not match:
                continue

        # Avoid duplicates.
        key = (material, name, f"{value:g}")
        if key in seen_keys:
            continue
        seen_keys.add(key)

        # Heuristic findings are conservative — treat as ``medium`` so
        # Phase 2's review queue surfaces them for human eyes.
        found.append(
            {
                "element_system": material,
                "phase": "Unknown",
                "property_name": name,
                "value": value,
                "unit": unit,
                "method": "heuristic_regex",
                "source": source_reference,
                "source_doi": None,
                "confidence": "medium",
                "uncertainty": max(abs(value) * 0.05, 0.01),
                "temperature": None,
                "cache_level": "L2",
            }
        )

    # ------ NFM-3511: second pass for unitless DFT properties ------
    for pattern, name in _DFT_DIRECT_PATTERNS:
        for m in pattern.finditer(normalized):
            value = _normalize_number(m.group("val"))
            if value is None:
                continue
            material = _nearest_material(materials, m.start())
            if material is None:
                continue
            if element_systems:
                match = any(
                    re.search(re.escape(es), material, re.IGNORECASE)
                    for es in element_systems
                )
                if not match:
                    continue
            key = (material, name, f"{value:g}")
            if key in seen_keys:
                continue
            seen_keys.add(key)
            found.append(
                {
                    "element_system": material,
                    "phase": "Unknown",
                    "property_name": name,
                    "value": value,
                    "unit": None,
                    "method": "heuristic_regex",
                    "source": source_reference,
                    "source_doi": None,
                    "confidence": "medium",
                    "uncertainty": max(abs(value) * 0.05, 0.01),
                    "temperature": None,
                    "cache_level": "L2",
                }
            )

    return found


__all__ = ["heuristic_extract"]
