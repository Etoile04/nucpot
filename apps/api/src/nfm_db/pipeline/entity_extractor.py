"""EntityExtractor pipeline step — NFM-2683 / NFM-2677-B2.

Extracts material entities (names, formulas, property values, units)
from section-level text produced by the upstream chunking step (Step 2).

This module provides:

- :class:`EntityChunk` — an immutable dataclass representing a single
  extracted entity with ``source_span`` provenance back into the parent
  section text.
- :func:`extract_entities_from_section` — a standalone async helper
  that extracts entities from a single section string.
- :class:`EntityExtractor` — a concrete :class:`ExtractionStep`
  implementation that wires into the V2 orchestrator.

Design notes
-----------

Entity extraction uses **pattern-based regex matching** (no LLM call)
to keep the step deterministic, fast, and idempotent.  The patterns
are deliberately conservative — they recognise common nuclear-material
text conventions (chemical formulas, property-value-unit triples,
material name phrases) without requiring external services.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from nfm_db.pipeline.extraction_step import (
    StepContext,
    StepResult,
    validate_step_type,
)

__all__ = [
    "ENTITY_KINDS",
    "EntityChunk",
    "EntityExtractor",
    "extract_entities_from_section",
]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Canonical entity kinds emitted by this step.
ENTITY_KINDS: tuple[str, ...] = (
    "material_name",
    "formula",
    "property_value",
    "unit",
)

_STEP_ORDER = 3
_CHUNK_TYPE = "entity"

# ---------------------------------------------------------------------------
# Entity output dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EntityChunk:
    """Immutable record for a single extracted entity with provenance.

    Attributes
    ----------
    text:
        The extracted entity text (substring of the parent section).
    entity_kind:
        One of :data:`ENTITY_KINDS`.
    source_span:
        Character-level offsets ``{start, end}`` into the parent
        section text.  ``section_text[start:end] == text`` is
        guaranteed by the extractor.
    chunk_type:
        Fixed to ``"entity"`` for pipeline dispatch.
    step_order:
        Fixed to ``3`` — this is Step 3 in the pipeline.
    """

    text: str
    entity_kind: str
    source_span: dict[str, int]
    chunk_type: str = _CHUNK_TYPE
    step_order: int = _STEP_ORDER


# ---------------------------------------------------------------------------
# Extraction patterns
# ---------------------------------------------------------------------------

# Chemical formula pattern — matches common nuclear material formulas.
# Covers: UO2, UO₂, U₃O₈, PuO₂, ZrO₂, FeCrAl, etc.
# Handles subscript Unicode digits (₂₃₄₅₆₇₈₉₀) and regular digits.
_FORMULA_RE = re.compile(
    r"\b([A-Z][a-z]?(?:[₂₃₄₅₆₇₈₉₀]?\d*)"
    r"(?:[A-Z][a-z]?(?:[₂₃₄₅₆₇₈₉₀]?\d*)*)*"
    r"(?:O(?:[₂₃₄₅₆₇₈₉₀]?\d*)?)"
    r"|"
    r"[A-Z][a-z]?(?:₂₃₄₅₆₇₈₉₀]?\d*)(?:-[A-Z][a-z]?)+)\b"
)

# Property value + unit pattern — matches "5.47 Å", "207.5 GPa",
# "7.5 W/(m·K)", "300 K", "1000 °C", etc.
_PROPERTY_VALUE_RE = re.compile(
    r"\b(\d+(?:\.\d+)?)\s*"
    r"(?:Å|°C|K|GPa|MPa|W/(?:m·K)|W·m⁻¹·K⁻¹|"
    r"mol%|wt%|at%|cm⁻¹|meV|eV|nm|μm|mm|kg/m³|g/cm³)\b"
)

# Standalone unit pattern — units not captured as part of a value+unit pair.
_UNIT_RE = re.compile(
    r"\b(Å|GPa|MPa|W/(?:m·K)|W·m⁻¹·K⁻¹|mol%|wt%|at%|"
    r"cm⁻¹|meV|eV|kg/m³|g/cm³)\b"
)

# Material name phrase pattern — descriptive names like
# "uranium dioxide", "plutonium oxide", "stainless steel alloy",
# "fuel cladding".  Case-insensitive so mid-sentence occurrences
# (e.g. lowercase "uranium dioxide") are captured.
_MATERIAL_NAME_RE = re.compile(
    r"\b([A-Za-z]+(?:\s+[A-Za-z]+)*\s+"
    r"(?:dioxide|oxide|carbide|nitride|silicide|boride|hydride|"
    r"alloy|metal|ceramic|fuel|cladding|absorber))\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Extraction logic
# ---------------------------------------------------------------------------


async def extract_entities_from_section(
    section_text: str,
) -> list[EntityChunk]:
    """Extract material entities from a single section text string.

    Uses deterministic regex patterns to find chemical formulas,
    property values with units, standalone units, and material name
    phrases.  Returns a list of :class:`EntityChunk` objects, each
    carrying a ``source_span`` that maps back into *section_text*.

    Idempotent: the same input always produces the same output list
    (regex matching is deterministic and results are sorted by span
    offset).
    """
    if not section_text or not section_text.strip():
        return []

    entities: list[EntityChunk] = []
    seen_spans: set[tuple[int, int]] = set()

    def _add_entity(
        text: str, kind: str, start: int, end: int
    ) -> None:
        """Deduplicate by span before adding."""
        span_key = (start, end)
        if span_key in seen_spans:
            return
        seen_spans.add(span_key)
        entities.append(
            EntityChunk(
                text=text,
                entity_kind=kind,
                source_span={"start": start, "end": end},
            )
        )

    # 1. Chemical formulas
    for m in _FORMULA_RE.finditer(section_text):
        matched = m.group()
        # Skip purely lowercase matches (not chemical formulas).
        if matched[0].islower():
            continue
        _add_entity(matched, "formula", m.start(), m.end())

    # 2. Property values with units (captures the full "value + unit")
    prop_spans: set[tuple[int, int]] = set()
    for m in _PROPERTY_VALUE_RE.finditer(section_text):
        matched = m.group()
        _add_entity(matched, "property_value", m.start(), m.end())
        prop_spans.add((m.start(), m.end()))

    # 3. Standalone units not already captured as part of property values
    for m in _UNIT_RE.finditer(section_text):
        span_key = (m.start(), m.end())
        if span_key not in prop_spans:
            _add_entity(m.group(), "unit", m.start(), m.end())

    # 4. Material name phrases
    for m in _MATERIAL_NAME_RE.finditer(section_text):
        matched = m.group()
        _add_entity(matched, "material_name", m.start(), m.end())

    # Sort by span offset for deterministic output.
    entities.sort(key=lambda e: e.source_span["start"])
    return entities


# ---------------------------------------------------------------------------
# Pipeline step implementation
# ---------------------------------------------------------------------------


class EntityExtractor:
    """Concrete :class:`ExtractionStep` for entity extraction (Step 3).

    Conforms to the :class:`nfm_db.pipeline.extraction_step.ExtractionStep`
    Protocol via structural typing — no base class inheritance.

    Stateless: all mutable state lives in the :class:`StepContext`
    passed to :meth:`execute`.
    """

    step_type: str = "extract"
    input_keys: tuple[str, ...] = ("sections",)

    async def execute(
        self,
        context: StepContext,
        **kwargs: Any,
    ) -> StepResult:
        """Run entity extraction against section text from context.

        Reads ``"sections"`` from context (a string or list of section
        strings from the upstream chunking step).  Returns a
        :class:`StepResult` with ``produced_keys=("entities",)`` and
        the full entity list in ``outputs["entities"]``.
        """
        validate_step_type(self.step_type)

        sections = context.get("sections")
        all_entities: list[EntityChunk] = []

        # Accept either a single section string or a list of strings.
        if isinstance(sections, str):
            section_list = [sections]
        elif isinstance(sections, list):
            section_list = sections
        else:
            logger.warning(
                "EntityExtractor: unexpected sections type %r — "
                "expected str or list[str]",
                type(sections).__name__,
            )
            section_list = []

        for section in section_list:
            if isinstance(section, str) and section.strip():
                entities = await extract_entities_from_section(section)
                all_entities.extend(entities)

        return StepResult(
            produced_keys=("entities",),
            outputs={
                "entities": all_entities,
                "entity_count": len(all_entities),
            },
        )
