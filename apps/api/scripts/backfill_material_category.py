#!/usr/bin/env python3
"""Backfill ``materials.category_id`` from formula / crystal_structure heuristics.

NFM-3916 (Tier 1C) — companion to migration
``066_seed_material_categories``.  The seed migration inserts the
canonical 8-row taxonomy; this script assigns each material row to one
of those taxonomy rows using deterministic, formula-driven rules.

Why a separate script (not part of the alembic migration)
----------------------------------------------------------
The mapping is a *policy* decision, not a schema change.  Future
re-classifications (e.g. when Nuclear Domain Expert reviews the
names) should be re-runnable without rewriting the schema, and the
backfill output must be auditable (which formula → which category)
per row.  Keeping the policy outside alembic also means a downgrade
of the seed migration does not drag the backfill with it — operators
can roll back either side independently.

Idempotency contract
--------------------
Running this script twice against the same dataset produces the same
``category_id`` for every row.  Concretely:

* Rows whose ``category_id`` already equals the computed target are
  left untouched (``UPDATE ... WHERE category_id IS DISTINCT FROM``).
* Rows that match no rule are kept at ``category_id = NULL`` — per
  ticket NFM-3916 ("匹配不上的行保持 ``category_id=NULL`` (不硬塞进
  ``other``, 避免制造假分类)").  This makes the script deterministic
  by construction: no rule touches a NULL input.

Rule precedence
---------------
Rules are evaluated in the order listed below; the first matching rule
wins.  This ordering reflects specificity: structural / compositional
overrides come before fallbacks.

0. **Trade-name cladding** — formula or name matches a known
   cladding trade name (``ZIRLO``, ``Zircaloy-4``, ``Zircaloy-2``,
   ``Zircaloy-2/4``, ``Zr-4``, ``M5``, ``E110``)  → ``cladding_alloy``.
   Fires before any element-based rule so a name-only entry
   (``Zircaloy-4`` written into the formula column with no
   chemistry) resolves correctly, and so ``ZIRLO`` cannot fall
   through to the oxygen rule via the letter ``O``.
1. ``crystal_structure == 'Fluorite'``  → ``oxide_fuel``  (covers
   ``cubic_Fluorite`` style ceramic fuels, e.g. ``CeO2``, ``ThO2``,
   ``UO2``; the 2 known Fluorite rows in the 131-row dataset map
   here).
2. Formula contains ``O`` AND an actinide (``U``/``Pu``/``Th``)  →
   ``oxide_fuel``  (UO2, PuO2, MOX, ...).  **Narrowed in NFM-3983**
   — non-fuel oxides (``H2O``, ``Cr2O3``, ``ZrO2``) without an
   actinide fall through to subsequent rules.
3. Formula contains ``U`` / ``Pu`` / ``Th`` AND ``C``  →
   ``carbide_nitride_fuel``  (UC, (U,Pu)C).  The periodic-table
   validator (see AC #1) discards phantom ``C`` from provenance
   tags like ``CR13`` and ``900C``, so this rule fires only on
   genuine carbides.
4. Formula contains ``U`` / ``Pu`` / ``Th`` AND ``N``  →
   ``carbide_nitride_fuel``  (UN, (U,Pu)N).  Phantom ``N`` from
   provenance tags like ``LANL`` is discarded by the validator.
5. Formula contains ``U`` / ``Pu`` / ``Th`` (no O/C/N)  →
   ``metallic_fuel``  (U-Zr, U-Pu-Zr, U-Mo binary / ternary
   intermetallics).
6. ``Zr`` is the **first** element AND the alloy has ≤4 metals
   → ``cladding_alloy``  (Zircaloy, M5, E110, Zr-Nb).  **Narrowed
   in NFM-3983** — the matrix-first + ≤4-metals heuristic rejects
   ``Al-3Cu-2Mg-0.5Zr`` (Al matrix) and ``CoCrFeMnNi`` (Co matrix)
   to NULL.
7. ``Fe`` is the **first** element AND the alloy has ≤4 metals
   → ``structural_steel``  (SS304, SS316, HT9, F82H, Eurofer97).
   Same narrowing as rule 6.
8. Formula contains any refractory metal ``W`` / ``Mo`` / ``Nb`` /
   ``Ta`` (no U/Pu/Th/Zr/Fe)  → ``refractory_metal``  (resolves
   ``Cr-Mo-V``, ``Pt-W``, and ``Nb-V`` to refractory; Nb-V lands
   here because Nb is a refractory base).  Note: a refractory
   symbol presence alone is sufficient — a binary alloy like
   ``Nb-V`` has 50% refractory and 50% non-refractory, so a
   strict-majority rule would miss it.  The earlier
   "non-ferrous cladding/structural" rules (6, 7) take precedence
   over rule 8 to keep ``Zr-Nb`` as a cladding alloy, not a
   refractory metal.
9. **REMOVED in NFM-3983** — the "2+ distinct metals →
   ``metallic_fuel``" rule used to misclassify noble-metal ordering
   alloys (``CuAu``, ``Ag-Pt``, ``Au-Pt``, ``Cu3Au``, ``CuAu3``)
   and HEAs (``CoCrFeMnNi``, ``Al-3Cu-2Mg-0.5Zr``) as nuclear fuel.
   These are CALPHAD model systems / aluminium grain-refined alloys,
   not fuel, and the ticket rule "do not force into other" says they
   must stay NULL.
10. Otherwise  → ``NULL`` (leave unchanged; pure metals like Au, Cu
    that have no alloy context stay unclassified — the ticket
    explicitly forbids force-fitting).

Why precision over coverage
---------------------------
NFM-3983 measured 18 of 93 classified prod rows as wrong (precision
80.6%) under v1.  The fix above is precision-first: the rule
narrowing means **coverage will legitimately drop below the v1
70.5% figure** as false positives are removed.  Acceptable — the
``/materials`` dropdown telling the truth is worth more than
breadth.

Element extraction pipeline
---------------------------
The parser is a three-stage pipeline.  None of the stages alone is
sufficient; each protects against a different real failure mode
observed on the 132-row prod dataset.

1. **Strip the chemistry head** (``_strip_to_chemistry_head``).
   Walk the formula character-by-character (with hyphen and
   underscore normalisation) and accept only tokens that look like
   chemistry chunks — single element symbol, element + trailing
   stoichiometry, leading-digit stoichiometry + element, or
   concatenated element symbols.  The first non-chemistry token
   terminates the head.  This drops citation tags (``_CR13``,
   ``_LANL``, ``_METAPHIX``), property names (``_elastic_modulus``,
   ``_thermal_conductivity``, ``_hardness``, ``_tensile``,
   ``_density``, ``_poisson_ratio``, ``_shear_modulus``,
   ``_electrical_resistivity``, ``_hot_hardness``, ``_compressive``,
   ``_vapor_pressure``), condition tokens (``_RT``, ``_annealed``,
   ``_as_cast``, ``_900C``), and free-form alloy names.

2. **Periodic-table validation** (AC #1).  Every extracted symbol
   is checked against ``_PERIODIC_TABLE``.  Phantom capitalised
   tokens (``R``, ``L``, ``X``, ``I``, ``M``, ``Can``, ``An``,
   ``To``, ``Ph``, ``Ta`'-shaped`, ``LA``, ``NL``, ``ME``, ``TA``)
   are discarded.  This is the single most important fix — v1's
   ``_ELEMENT_RE`` regex matched any ``[A-Z][a-z]{0,2}`` span, so
   ``ZIRLO`` extracted ``{I, L, O, R, Z}`` and triggered the
   oxygen rule.

3. **First-element / ≤4-metals guard** (rules 6, 7 in NFM-3983).
   Before classifying a non-actinide alloy as cladding or
   structural steel, the classifier checks the **first** element
   in the chemistry head.  Al-matrix and Co-matrix alloys that
   contain a small amount of Zr or Fe (e.g. ``Al-3Cu-2Mg-0.5Zr``)
   no longer route to ``cladding_alloy`` or ``structural_steel``;
   they stay NULL.  Compositional dominance heuristics without
   stoichiometry would over-fit on either side; the matrix-first
   signal plus the ≤4-metals bound is the conservative reading of
   the data.

Coverage expectation
--------------------
Production has 132 material rows (NFM-3983 measurement).  Of those:

* ``crystal_structure = 'Fluorite'`` → 2 rows → ``oxide_fuel``
* Trade-name cladding (ZIRLO, Zircaloy-2/4, Zircaloy-4, M5) →
  4 rows → ``cladding_alloy``
* U-Pu-Zr metallic alloys (with or without provenance suffix) →
  ~53 rows → ``metallic_fuel``
* U-Mo alloys → ~7 rows → ``metallic_fuel``
* Pure actinide reference / phase rows → ~10 rows → ``metallic_fuel``
* Zircaloy Oxide (ZrO2) → 1 row → ``cladding_alloy``
* ``Nb-V``, ``Pt-W``, ``Cr-Mo-V``, ``Cr-Mo``, ``Cr-Nb`` → 5 rows →
  ``refractory_metal``

Pure ``Au`` / ``Cu`` / noble-metal binaries (``CuAu``, ``Cu3Au``,
``CuAu3``, ``Ag-Pt``, ``Au-Pt``), HEAs (``CoCrFeMnNi``),
Al-matrix alloys (``Al-3Cu-2Mg-0.5Zr``), and non-fuel oxides
(``H2O``, ``Cr2O3``) all stay ``NULL`` under rule 10.  Target
precision ≥95%; coverage will legitimately drop from v1's 70.5% as
the false positives are removed.  The CPO approves the prod run off
the audit table; this script does **not** write to prod as part of
this issue.

Usage
-----
::

    cd apps/api && python scripts/backfill_material_category.py
    cd apps/api && python scripts/backfill_material_category.py --dry-run
    cd apps/api && python scripts/backfill_material_category.py --verbose
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import re
import sys
from dataclasses import dataclass
from pathlib import Path

# Make ``nfm_db`` importable when run as a script (mirrors the pattern
# used in ``scripts/_generate_sample_candidates.py`` and
# ``scripts/doi_etl_admit.py``).
_SCRIPT_DIR = Path(__file__).resolve().parent
_SRC_DIR = _SCRIPT_DIR.parent / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

import sqlalchemy as sa  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from nfm_db.config import get_settings  # noqa: E402
from nfm_db.database import async_session_factory  # noqa: E402


logger = logging.getLogger("backfill_material_category")


# ---------------------------------------------------------------------------
# Element extraction
# ---------------------------------------------------------------------------
# Formula conventions observed in the 131-row production dataset:
#
# * Pure element:        "Au", "Cu"
# * Hyphen-separated:    "Nb-V", "Cr-Mo-V", "Pt-W", "Ag-Pt"
# * Concatenated:        "CuAu"  (Cu + Au)
# * Stoichiometric:      "UO2", "PuO2", "U_15Pu_10Zr_alloy"
# * Provenance-suffixed: "U_15Pu_10Zr_hardness_900C_annealed",
#                        "UPuZr_elastic_modulus_CR13",
#                        "UPuZr_phase_transition_enthalpy"
#
# The parser is a three-stage pipeline (see module docstring):
# 1. Strip provenance / property / condition suffixes from the head
# 2. Validate every extracted symbol against the periodic table
# 3. Apply matrix-first / ≤4-metals guards inside classify()

# Element symbol: capital letter followed by 0-1 lowercase letters.
# All real periodic-table symbols are 1 or 2 chars total
# (H, He, Li, ..., Og), so ``[a-z]?`` (0 or 1 lowercase, greedy) is
# the correct bound.  The earlier v1 pattern ``[A-Z][a-z]{0,2}``
# greedily matched ``Crd`` from ``Cr-doped`` as a single span (3
# chars), which the periodic-table validator then rejected because
# ``Crd`` is not an element — losing the real ``Cr`` along with it.
# Trimming to ``[a-z]?`` keeps real element symbols whole (``Mo``,
# ``Nb``, ``Cr``, ...) while never glomming an extra trailing
# lowercase letter.
_ELEMENT_RE = re.compile(r"([A-Z][a-z]?)")

# Periodic-table symbol set (118 elements, H..Og).  Used by AC #1 to
# discard phantom matches from provenance tags.  Examples from prod:
#   "ZIRLO"  -> regex {I, L, O, R, Z}; only O is periodic
#   "LANL"   -> regex {L, A, N, L}; only N is periodic
#   "CR13"   -> regex {C, R}       ; only C is periodic
#   "Can"    -> regex {C, An}      ; only C is periodic
#   "900C"   -> regex {C}          ; only C is periodic
#   "CoCrFeMnNi Cantor合金" -> many matches; non-periodic noise
#                                (An, To, Ph, etc.) kills the head.
_PERIODIC_TABLE: frozenset[str] = frozenset({
    "H", "He",
    "Li", "Be", "B", "C", "N", "O", "F", "Ne",
    "Na", "Mg", "Al", "Si", "P", "S", "Cl", "Ar",
    "K", "Ca", "Sc", "Ti", "V", "Cr", "Mn", "Fe", "Co", "Ni", "Cu", "Zn",
    "Ga", "Ge", "As", "Se", "Br", "Kr",
    "Rb", "Sr", "Y", "Zr", "Nb", "Mo", "Tc", "Ru", "Rh", "Pd", "Ag", "Cd",
    "In", "Sn", "Sb", "Te", "I", "Xe",
    "Cs", "Ba",
    "La", "Ce", "Pr", "Nd", "Pm", "Sm", "Eu", "Gd", "Tb", "Dy", "Ho", "Er",
    "Tm", "Yb", "Lu",
    "Hf", "Ta", "W", "Re", "Os", "Ir", "Pt", "Au", "Hg",
    "Tl", "Pb", "Bi", "Po", "At", "Rn",
    "Fr", "Ra",
    "Ac", "Th", "Pa", "U", "Np", "Pu", "Am", "Cm", "Bk", "Cf", "Es", "Fm",
    "Md", "No", "Lr",
    "Rf", "Db", "Sg", "Bh", "Hs", "Mt", "Ds", "Rg", "Cn",
    "Nh", "Fl", "Mc", "Lv", "Ts", "Og",
})

# Refractory metals per the NFM-3916 ticket taxonomy.  We use the set
# directly rather than fetching it from the taxonomy so the rule
# engine remains pure-Python and unit-testable without a DB.
_REFRACTORY_METALS: frozenset[str] = frozenset({"W", "Mo", "Nb", "Ta"})

# Actinides that mark a material as a fuel.
_ACTINIDES: frozenset[str] = frozenset({"U", "Pu", "Th"})

# Known cladding trade names.  Matched case-insensitively against
# both the formula and name columns, after hyphen normalisation.
# Firing this rule BEFORE the element-based rules (see classify)
# means:
#   * "ZIRLO" cannot fall through to oxide_fuel via phantom "O"
#   * "Zircaloy-4", "M5", "E110" (name-only entries) resolve
#     instead of staying NULL.
# Sourced from NFM-3916 (Tier 1C) and NFM-3983 (this issue).
_CLADDING_TRADE_NAMES: tuple[str, ...] = (
    "ZIRLO",
    "Zircaloy-4",
    "Zircaloy-2",
    "Zircaloy-2/4",
    "Zr-4",
    "M5",
    "E110",
)

# Pre-computed normalised set for fast case-insensitive matching.
# Normalisation: lowercase + strip hyphens.  e.g. "Zircaloy-2/4"
# becomes "zircaloy2/4"; we keep the slash because some trade names
# embed it (Zircaloy-2/4) and removing it would collapse
# "Zircaloy-2/4" into "zircaloy24" — still unique, but the slash
# makes the substring match unambiguous against a hypothetical
# "Zircaloy-2" prefix that might appear mid-token elsewhere.
_CLADDING_TRADE_NAMES_NORMALIZED: frozenset[str] = frozenset(
    t.lower().replace("-", "") for t in _CLADDING_TRADE_NAMES
)


# ---------------------------------------------------------------------------
# Chemistry-head stripping (AC #2)
# ---------------------------------------------------------------------------
#
# Real prod formulas have a chemistry head followed by an
# underscore-separated provenance suffix.  Examples:
#
#   "U_15Pu_10Zr_alloy"
#   "U_15Pu_10Zr_hardness_900C_annealed"
#   "U_15Pu_10Zr_tensile_RT_LANL"
#   "UPuZr_elastic_modulus_CR13"
#   "UPuZr_phase_transition_enthalpy"
#
# The walker accepts tokens that look like chemistry chunks:
#   * single element, e.g. "U", "Pu", "Zr", "Mo"
#   * element + trailing digits, e.g. "O2", "U2", "Cr23"
#   * leading digits + element, e.g. "15Pu", "10Zr", "20Pu"
#   * concatenated symbols, e.g. "UPuZr", "CuAu"
#
# Anything else (lowercase, multiple uppercase, ALL-CAPS words,
# temperature tokens) terminates the head.  Hyphens are flattened
# so "Nb-V" is treated as two chunks "Nb" and "V".

# A chemistry chunk is either:
#   * a single element symbol (uppercase + 0-2 lowercase letters)
#   * an element symbol followed by trailing digits (UO2, Pu2, ...)
#   * leading digits followed by an element symbol (15Pu, 10Zr, ...)
_CHEM_CHUNK_RE = re.compile(r"^(?:\d+)?[A-Z][a-z]?$")

# Allowed leftover (non-element, non-chunk) characters in a token
# that is otherwise valid chemistry.  ``() , . %`` let ``(U,Pu)O2``
# and ``Zr-1%Nb`` and ``Al-3Cu-2Mg-0.5Zr`` through after hyphen
# flattening.  Underscore is NOT in here — it's our token separator.
_CHEM_LEFTOVER_ALLOWED = frozenset("(),.%-+ \t")


def _periodic_candidates(token: str) -> list[str]:
    """Return only the periodic-table element candidates in ``token``.

    Used by both the chemistry-head walker and the element
    extractor.  Filters the raw regex output against
    ``_PERIODIC_TABLE`` so phantom letters (e.g. ``Z``, ``L``, ``R``
    from ``ZIRLO``) are discarded while real element symbols
    survive (``O`` survives from ``ZIRLO``).
    """
    return [c for c in _ELEMENT_RE.findall(token) if c in _PERIODIC_TABLE]


def _strip_to_chemistry_head(formula: str | None) -> str:
    """Return the chemistry head of ``formula`` with provenance stripped.

    Walks the formula left-to-right, accepting only chemistry chunks.
    The first non-chemistry token terminates the head.  Hyphens are
    flattened so "Nb-V" parses as two chunks.  An empty or None input
    returns an empty string.

    Some prod formulas have a lowercase descriptive prefix before the
    chemistry begins (e.g. ``delta_Pu_solid_solution``,
    ``alpha_U_solid_solution``).  The walker skips those leading
    non-chemistry tokens and starts the head at the first token that
    contains a periodic-table element.

    Examples
    --------
    >>> _strip_to_chemistry_head("U_15Pu_10Zr_alloy")
    'U_15Pu_10Zr'
    >>> _strip_to_chemistry_head("UPuZr_elastic_modulus_CR13")
    'UPuZr'
    >>> _strip_to_chemistry_head("UO2")
    'UO2'
    >>> _strip_to_chemistry_head(None)
    ''
    >>> _strip_to_chemistry_head("CoCrFeMnNi Cantor合金")
    ''
    >>> _strip_to_chemistry_head("delta_Pu_solid_solution")
    'Pu'
    """
    if not formula:
        return ""
    # Flatten hyphens: "Nb-V" -> "NbV" (also "Zr-1%Nb" -> "Zr1%Nb",
    # which is still valid after element matching).
    normalised = formula.replace("-", "")
    tokens = normalised.split("_")
    # Locate the first token that contains at least one periodic
    # element.  This lets us skip lowercase descriptive prefixes
    # like ``delta_`` / ``alpha_`` that appear on phase-reference
    # rows in the prod dataset.
    first_periodic_idx: int | None = None
    for i, tok in enumerate(tokens):
        if _periodic_candidates(tok):
            first_periodic_idx = i
            break
    if first_periodic_idx is None:
        return ""
    head_tokens: list[str] = []
    for tok in tokens[first_periodic_idx:]:
        if _is_chemistry_token(tok):
            head_tokens.append(tok)
        else:
            break
    return "_".join(head_tokens)


def _is_chemistry_token(token: str) -> bool:
    """Return True iff ``token`` is a valid chemistry chunk.

    A chemistry chunk is:
      * a single element symbol (1-2 letters, capital + lowercase)
      * an element symbol followed by trailing digits ("O2", "U2")
      * leading digits followed by an element symbol ("15Pu")
      * concatenated element symbols ("UPuZr", "CuAu")

    The token is valid if **every** element-shape candidate is a
    periodic-table symbol — a single non-periodic match rejects the
    whole token.  This is the strict reading of AC #1: a
    trade-name-shaped string like ``METAPHIX`` or ``ZIRLO`` has
    SOME periodic candidates (``Ta``, ``I``, ``O``) but mixing them
    with non-periodic tokens (``M``, ``E``, ``Ph``, ``X``, ``Z``,
    ``L``, ``R``) means the whole string is provenance, not
    chemistry.

    Trade-name strings that slip past the periodic validator (none
    in the current dataset, but ``Can`` is the canonical case) are
    also rejected by the strict reading — the regex glomps ``Can``
    into one candidate which is not periodic, so the token is
    rejected as chemistry.

    The leftover (everything not element-shaped) must be empty or
    composed only of structural punctuation and Unicode letters /
    digits (e.g. ``U-Mo (γ-alloy)`` is allowed via ``γ``, hyphen,
    parentheses).
    """
    if not token:
        return False
    candidates = _ELEMENT_RE.findall(token)
    if not candidates:
        return False
    # Strict: ALL element candidates must be periodic.  A single
    # non-periodic match rejects the whole token.  This is what
    # blocks ``METAPHIX`` (M, E, Ph, X), ``CR13`` (R), ``ZIRLO``
    # (Z, R, L) and ``LANL`` (L, A, L) from being treated as
    # chemistry even though they contain SOME periodic symbols.
    if any(c not in _PERIODIC_TABLE for c in candidates):
        return False
    # Reconstruct: remove all element-shape spans, see what's left.
    leftover = _ELEMENT_RE.sub("", token)
    if leftover:
        # Allow anything that's a Unicode letter / digit, plus a
        # small set of structural punctuation used in real chemistry
        # notation (at%, wt%, ~, ≥, ≤, parentheses, comma, dot,
        # Greek letters in phase labels).  This is permissive on
        # purpose: provenance protection comes from the strict
        # "all periodic" check above, not from this leftover test.
        for ch in leftover:
            if ch.isalnum() or ch in "(),.%<>~-+ \t":
                continue
            return False
    return True


def _formula_to_elements(formula: str | None) -> set[str]:
    """Return the set of periodic-table element symbols in ``formula``.

    Two pre-filter steps run before element extraction:

    1. Provenance / property / condition suffixes are stripped by
       ``_strip_to_chemistry_head``.  A row like
       ``"U_15Pu_10Zr_hardness_900C_annealed"`` collapses to
       ``"U_15Pu_10Zr"`` before extraction.
    2. Extracted symbols are filtered against ``_PERIODIC_TABLE``.
       A row like ``"ZIRLO"`` extracts ``{I, L, O, R, Z}`` from the
       regex, but only ``{O}`` survives periodic validation.

    The function returns an empty set if the formula is None / empty
    or if no chemistry head can be extracted.

    Examples
    --------
    >>> sorted(_formula_to_elements("UO2"))
    ['O', 'U']
    >>> sorted(_formula_to_elements("Nb-V"))
    ['Nb', 'V']
    >>> sorted(_formula_to_elements("CuAu"))
    ['Au', 'Cu']
    >>> sorted(_formula_to_elements("Cr-Mo-V"))
    ['Cr', 'Mo', 'V']
    >>> sorted(_formula_to_elements("U_15Pu_10Zr_hardness_900C_annealed"))
    ['Pu', 'U', 'Zr']
    >>> _formula_to_elements(None)
    set()
    >>> _formula_to_elements("ZIRLO")
    {'O'}
    """
    if not formula:
        return set()
    head = _strip_to_chemistry_head(formula)
    if not head:
        return set()
    flattened = head.replace("-", "")
    return set(_periodic_candidates(flattened))


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Classification:
    """Result of classifying a single material row.

    Attributes
    ----------
    target_slug
        The taxonomy slug this row should be assigned to, or ``None``
        if no rule matched (and the row stays ``category_id = NULL``).
    rule_id
        Identifier of the rule that fired (``"fluorite"``,
        ``"oxide_o"``, ..., ``"unmatched"``).  Surfaced in the audit
        log so a reviewer can trace each row's classification back
        to the rule that produced it.
    """

    target_slug: str | None
    rule_id: str


def _matches_cladding_trade_name(formula: str | None, name: str | None) -> bool:
    """Return True iff ``formula`` or ``name`` mentions a cladding trade name.

    Both columns are searched (case-insensitive, hyphen-stripped).
    Substring matching is permissive — the trade-name list is short
    and the domain (nuclear fuel materials) makes accidental matches
    unlikely.  This was added in NFM-3983.
    """
    for haystack in (formula, name):
        if not haystack:
            continue
        normalised = haystack.replace("-", "").lower()
        for trade in _CLADDING_TRADE_NAMES_NORMALIZED:
            if trade in normalised:
                return True
    return False


def _ordered_elements(formula: str | None) -> list[str]:
    """Return the element list of ``formula`` in extraction order.

    Used by rules 6 / 7 (matrix-first guard).  The first element is
    the matrix in conventional alloy notation: ``U-Zr`` → ``U``
    matrix, ``Zr-Nb`` → ``Zr`` matrix, ``Al-3Cu-2Mg-0.5Zr`` → ``Al``
    matrix, ``CoCrFeMnNi`` → ``Co`` matrix.  Empty list when no
    chemistry head was extracted.
    """
    if not formula:
        return []
    head = _strip_to_chemistry_head(formula)
    if not head:
        return []
    flattened = head.replace("-", "")
    candidates = _ELEMENT_RE.findall(flattened)
    # Preserve insertion order while filtering against the periodic
    # table.  ``dict.fromkeys`` is the standard ordered-dedupe idiom
    # pre-Python 3.7.
    return list(dict.fromkeys(c for c in candidates if c in _PERIODIC_TABLE))


def classify(
    *,
    formula: str | None,
    crystal_structure: str | None,
    name: str | None = None,
) -> Classification:
    """Map a material row to a taxonomy slug.

    Pure function — no DB access, no side effects.  Tests can call it
    directly with synthetic formula/name/crystal_structure values
    without provisioning a database.

    Parameters
    ----------
    formula
        The ``materials.formula`` column.  May contain hyphens
        (e.g. ``"Nb-V"``) or concatenated symbols (e.g. ``"CuAu"``),
        or trade names (``"Zircaloy-4"``, ``"ZIRLO"``), or provenance
        suffixes (``"U_15Pu_10Zr_hardness_900C_annealed"``).
    crystal_structure
        The ``materials.crystal_structure`` column.  Only the
        ``"Fluorite"`` value is consulted for a structural override;
        other structures fall through to the formula-based rules.
    name
        The ``materials.name`` column.  Searched in addition to
        ``formula`` for cladding trade names (AC #3) — a row whose
        formula column is empty / sparse may still carry
        ``"Zircaloy-4"`` in the name.

    Returns
    -------
    Classification
        See the dataclass docstring.
    """
    # Rule 0 (AC #3): trade-name cladding lookup.  Fires BEFORE
    # every element-based rule so a name-only entry resolves to
    # ``cladding_alloy`` and ``ZIRLO`` cannot fall through to the
    # oxide rule via its embedded letter ``O``.
    if _matches_cladding_trade_name(formula, name):
        return Classification(
            target_slug="cladding_alloy",
            rule_id="trade_name_cladding",
        )

    elements = _formula_to_elements(formula)
    element_order = _ordered_elements(formula)

    # Rule 1: structural override for fluorite-type ceramics.  Fires
    # before the O-containing rule so we don't accidentally catch
    # ``CeO2``-style non-fuel oxides (none in the current dataset,
    # but the explicit override is defensive against future imports).
    if crystal_structure == "Fluorite":
        return Classification(target_slug="oxide_fuel", rule_id="fluorite")

    # Rules 2-5: actinide-bearing ceramics and metals (the canonical
    # NFMD fuel taxonomy).
    has_actinide = bool(elements & _ACTINIDES)

    # Rule 2 (NFM-3983 AC #4): O-bearing formulas become
    # ``oxide_fuel`` only when an actinide is present.  ``H2O``,
    # ``Cr2O3``, ``ZrO2`` etc. fall through to subsequent rules
    # rather than being force-fitted into the nuclear fuel taxonomy.
    if "O" in elements and has_actinide:
        return Classification(target_slug="oxide_fuel", rule_id="oxide_o")

    if has_actinide and "C" in elements:
        return Classification(
            target_slug="carbide_nitride_fuel",
            rule_id="carbide_actinide",
        )

    if has_actinide and "N" in elements:
        return Classification(
            target_slug="carbide_nitride_fuel",
            rule_id="nitride_actinide",
        )

    if has_actinide:
        return Classification(
            target_slug="metallic_fuel",
            rule_id="metallic_actinide",
        )

    # Rules 6-7 (NFM-3983 narrowing): Zr / Fe matrix + ≤4 metals.
    # ``Al-3Cu-2Mg-0.5Zr`` (Al matrix, 4 metals) and ``CoCrFeMnNi``
    # (Co matrix, 5 metals) must NOT route to ``cladding_alloy`` or
    # ``structural_steel``; the matrix-first + ≤4-metals guard
    # rejects them.
    if (
        "Zr" in elements
        and element_order
        and element_order[0] == "Zr"
        and len(element_order) <= 4
    ):
        return Classification(
            target_slug="cladding_alloy",
            rule_id="cladding_zr",
        )

    if (
        "Fe" in elements
        and element_order
        and element_order[0] == "Fe"
        and len(element_order) <= 4
    ):
        return Classification(
            target_slug="structural_steel",
            rule_id="structural_fe",
        )

    # Rule 8: refractory-dominated alloy (no actinides / Zr / Fe).
    # Any refractory symbol (W, Mo, Nb, Ta) is sufficient — strict
    # majority is too restrictive for binary alloys like Nb-V
    # (50/50 refractory split).  Cladding (rule 6) and structural
    # steel (rule 7) already filtered the cases where Zr / Fe
    # dominates, so by the time we reach rule 8 the refractory
    # element is the most specific fuel-relevant marker.
    if elements & _REFRACTORY_METALS:
        return Classification(
            target_slug="refractory_metal",
            rule_id="refractory_any",
        )

    # Rule 9 (NFM-3983 REMOVED): the old "2+ distinct metals →
    # ``metallic_fuel``" rule misclassified noble-metal ordering
    # alloys (CuAu, Ag-Pt, Au-Pt, Cu3Au, CuAu3) and HEAs
    # (CoCrFeMnNi, Al-3Cu-2Mg-0.5Zr) as nuclear fuel.  These are
    # CALPHAD model systems / aluminium grain-refined alloys, not
    # fuel, and AC #5 requires them to stay NULL.

    # Rule 10: pure element or unrecognised formula → leave NULL.
    # Per ticket: do NOT force into "other" (would manufacture a
    # fake classification).
    return Classification(target_slug=None, rule_id="unmatched")


# ---------------------------------------------------------------------------
# Database runner
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CoverageReport:
    """Summary of the backfill pass, suitable for logging and CI smoke tests."""

    total_rows: int
    matched_rows: int
    unmatched_rows: int
    already_correct_rows: int
    updated_rows: int
    by_rule: dict[str, int]
    by_slug: dict[str, int]

    @property
    def coverage_pct(self) -> float:
        """Matched rows as a percentage of total, 0.0-100.0."""
        if self.total_rows == 0:
            return 0.0
        return round(100.0 * self.matched_rows / self.total_rows, 2)

    def render(self) -> str:
        """Human-readable summary for the comment / log output."""
        lines = [
            "== backfill_material_category coverage report ==",
            f"total materials:     {self.total_rows}",
            f"matched (target):    {self.matched_rows}",
            f"unmatched (NULL):    {self.unmatched_rows}",
            f"already correct:     {self.already_correct_rows}",
            f"updated:             {self.updated_rows}",
            f"coverage:            {self.coverage_pct}%",
            "",
            "-- by rule --",
        ]
        for rule_id, count in sorted(self.by_rule.items()):
            lines.append(f"  {rule_id:<24s} {count}")
        lines.append("-- by assigned slug --")
        for slug, count in sorted(self.by_slug.items()):
            lines.append(f"  {slug:<24s} {count}")
        return "\n".join(lines)


async def _load_category_lookup(session: AsyncSession) -> dict[str, str]:
    """Build a slug → id map for the seeded material_categories.

    The backfill only writes category_ids that are actually present
    in the taxonomy table, so we materialise the full set up front.
    Missing taxonomy rows (e.g. operator never ran migration 065)
    produce an empty map and the backfill aborts with a clear error
    rather than silently writing orphaned category_ids.
    """
    rows = await session.execute(
        sa.text("SELECT slug, id FROM material_categories")
    )
    return {row[0]: str(row[1]) for row in rows.fetchall()}


async def _load_materials(session: AsyncSession) -> list[tuple[str, str | None, str | None, str | None]]:
    """Return ``[(id, formula, crystal_structure, current_category_id), ...]``.

    We deliberately read ``category_id`` so the idempotency guard
    (``WHERE category_id IS DISTINCT FROM ...``) can skip rows that
    are already correctly classified on a re-run.
    """
    result = await session.execute(
        sa.text(
            "SELECT id, formula, crystal_structure, category_id "
            "FROM materials"
        )
    )
    return [(str(r[0]), r[1], r[2], str(r[3]) if r[3] is not None else None)
            for r in result.fetchall()]


async def run_backfill(
    session: AsyncSession,
    *,
    dry_run: bool = False,
    verbose: bool = False,
) -> CoverageReport:
    """Execute the backfill inside an existing session and return a coverage report.

    Parameters
    ----------
    session
        Open async session.  The caller is responsible for
        committing; this function only emits ``UPDATE`` statements
        via ``session.execute``.  ``dry_run=True`` skips the
        ``UPDATE`` so a reviewer can preview the rule decisions
        without writing.
    dry_run
        When ``True``, classify every row and compute the report
        but emit no ``UPDATE``.  Useful for staging previews.
    verbose
        Emit per-row log lines (``logger.debug``).

    Returns
    -------
    CoverageReport
        See the dataclass docstring.
    """
    slug_to_id = await _load_category_lookup(session)
    if not slug_to_id:
        raise RuntimeError(
            "No material_categories rows found — has migration "
            "066_seed_material_categories been applied? Aborting "
            "to avoid orphaned category_id writes."
        )
    logger.info("loaded %d taxonomy rows from material_categories", len(slug_to_id))

    materials = await _load_materials(session)
    total = len(materials)
    logger.info("read %d material rows", total)

    matched = 0
    unmatched = 0
    already_correct = 0
    updated = 0
    by_rule: dict[str, int] = {}
    by_slug: dict[str, int] = {}

    for mat_id, formula, crystal_structure, current_category_id in materials:
        decision = classify(
            formula=formula,
            crystal_structure=crystal_structure,
        )

        by_rule[decision.rule_id] = by_rule.get(decision.rule_id, 0) + 1

        if decision.target_slug is None:
            unmatched += 1
            if verbose:
                logger.debug(
                    "row %s formula=%r crystal=%r → unmatched (NULL)",
                    mat_id, formula, crystal_structure,
                )
            continue

        matched += 1
        by_slug[decision.target_slug] = by_slug.get(decision.target_slug, 0) + 1
        target_id = slug_to_id.get(decision.target_slug)
        if target_id is None:
            # Defensive: classify() emits a slug the taxonomy does
            # not contain.  This should be impossible because
            # classify() only emits the eight canonical slugs, but
            # we fail loud rather than silently dropping the row.
            raise RuntimeError(
                f"classifier emitted slug {decision.target_slug!r} "
                "but material_categories has no row with that slug; "
                "check classify() and the seed migration for drift."
            )

        if current_category_id == target_id:
            already_correct += 1
            if verbose:
                logger.debug(
                    "row %s formula=%r crystal=%r → %s (already correct)",
                    mat_id, formula, crystal_structure, decision.target_slug,
                )
            continue

        if dry_run:
            logger.debug(
                "row %s formula=%r crystal=%r → %s (dry-run, no write)",
                mat_id, formula, crystal_structure, decision.target_slug,
            )
            continue

        # ``IS DISTINCT FROM`` treats NULL = NULL as equal, so a row
        # whose current category_id is NULL and whose target is also
        # NULL (rule 10 — but we already returned earlier for that)
        # would never reach this UPDATE.  Rows that reach this point
        # have ``current_category_id != target_id`` by construction.
        await session.execute(
            sa.text(
                "UPDATE materials "
                "SET category_id = CAST(:new_id AS UUID) "
                "WHERE id = CAST(:row_id AS UUID) "
                "AND category_id IS DISTINCT FROM CAST(:new_id AS UUID)"
            ),
            {"new_id": target_id, "row_id": mat_id},
        )
        updated += 1
        if verbose:
            logger.debug(
                "row %s formula=%r crystal=%r → %s (updated)",
                mat_id, formula, crystal_structure, decision.target_slug,
            )

    return CoverageReport(
        total_rows=total,
        matched_rows=matched,
        unmatched_rows=unmatched,
        already_correct_rows=already_correct,
        updated_rows=updated,
        by_rule=dict(sorted(by_rule.items())),
        by_slug=dict(sorted(by_slug.items())),
    )


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="backfill_material_category",
        description=(
            "Backfill materials.category_id from formula / crystal_structure "
            "heuristics (NFM-3916 Tier 1C). Idempotent: re-running against "
            "the same dataset produces the same category_id for every row."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Classify every row and print the coverage report, but emit no UPDATE.",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Emit per-row DEBUG log lines showing each rule decision.",
    )
    return parser.parse_args(argv)


async def _main(args: argparse.Namespace) -> int:
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    settings = get_settings()
    logger.info("connecting to %s", _redact_url(settings.database_url))
    async with async_session_factory() as session:
        report = await run_backfill(
            session, dry_run=args.dry_run, verbose=args.verbose,
        )
        if args.dry_run:
            # Roll back so a dry-run leaves the DB untouched even if
            # the runner emitted UPDATEs (it does not, but this is
            # belt-and-braces against future refactors that start
            # writing in dry-run mode).
            await session.rollback()
        else:
            await session.commit()

    print(report.render())
    return 0


def _redact_url(url: str) -> str:
    """Strip credentials from a database URL for log lines."""
    if "@" in url:
        scheme, _, rest = url.partition("://")
        _, _, host_part = rest.partition("@")
        return f"{scheme}://***@{host_part}"
    return url


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_main(_parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
