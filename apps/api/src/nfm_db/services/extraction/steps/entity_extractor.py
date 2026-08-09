"""Step 3 of the strangler-fig extraction pipeline (NFM-2677 B4).

Stamps material-science entities (chemical formulas, property names,
numeric measurements with units) onto a section chunk's metadata.
Content and ``_source_span`` are forwarded unchanged — downstream
steps need to see the entity annotations alongside the raw text, not
a mutated copy.
"""

from __future__ import annotations

import re

from nfm_db.services.extraction import ExtractionChunk, ExtractionStep

# A real chemical formula has at least two element tokens (U + O, Th
# + O, etc.) — single capital-lowercase words like "No" or "The" are
# not formulas.  Each token is ``[A-Z][a-z]?`` followed by optional
# digits; the outer group requires 2+ tokens.
_FORMULA_RE = re.compile(r"\b(?:[A-Z][a-z]?\d*){2,}\b")

# Known material-science property names (lowercased, matched case-
# insensitively).  Add to this list as new ontologies are wired in.
_PROPERTY_NAMES: tuple[str, ...] = (
    "lattice constant",
    "lattice parameter",
    "melting point",
    "boiling point",
    "density",
    "thermal conductivity",
    "specific heat",
    "heat capacity",
    "youngs modulus",
    "bulk modulus",
    "shear modulus",
    "band gap",
    "formation energy",
    "cohesive energy",
)

# Numeric measurement followed by a unit token.  Captures the full
# ``"<num> <unit>"`` string so downstream normalizers can split.
_MEASUREMENT_RE = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:K|°C|GPa|MPa|eV|angstrom|Å|cm\^3|g/cm\^3)"
    r"(?:\^?-?\d?)?",
    re.IGNORECASE,
)


def _extract_entities(content: str) -> dict[str, list[str]]:
    formulas = sorted(set(_FORMULA_RE.findall(content)))

    properties: list[str] = []
    lowered = content.lower()
    for name in _PROPERTY_NAMES:
        if name in lowered:
            properties.append(name)

    measurements = sorted(set(_MEASUREMENT_RE.findall(content)))

    return {
        "formulas": formulas,
        "properties": properties,
        "measurements": measurements,
    }


class EntityExtractor(ExtractionStep):
    """Step 3: extract material-science entities into chunk metadata."""

    @property
    def step_name(self) -> str:
        return "entity_extractor"

    @property
    def step_order(self) -> int:
        return 2

    def execute(self, input_chunk: ExtractionChunk) -> ExtractionChunk:
        entities = _extract_entities(input_chunk.content)
        return ExtractionChunk(
            content=input_chunk.content,
            chunk_type="entity",
            _source_span=input_chunk._source_span,
            metadata={
                **input_chunk.metadata,
                "entities": entities,
            },
            parent_chunk_id=input_chunk.parent_chunk_id,
        )
