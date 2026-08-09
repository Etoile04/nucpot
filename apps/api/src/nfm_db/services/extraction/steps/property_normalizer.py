"""Step 4 of the strangler-fig extraction pipeline (NFM-2677 B5).

Normalizes property names to snake_case and measurement units to
canonical symbols so the orchestrator and downstream ontology
mapping see a stable vocabulary.
"""

from __future__ import annotations

import re

from nfm_db.services.extraction import ExtractionChunk, ExtractionStep

# Canonical form overrides for known units.
_UNIT_ALIASES: dict[str, str] = {
    "angstrom": "Å",
    "angstroms": "Å",
    "deg C": "°C",
    "degrees C": "°C",
    "deg K": "K",
}

# Canonical property-name aliases (case-insensitive match → snake_case).
_PROPERTY_ALIASES: dict[str, str] = {
    "lattice constant": "lattice_constant",
    "lattice parameter": "lattice_parameter",
    "melting point": "melting_point",
    "boiling point": "boiling_point",
    "thermal conductivity": "thermal_conductivity",
    "specific heat": "specific_heat",
    "heat capacity": "heat_capacity",
    "youngs modulus": "youngs_modulus",
    "bulk modulus": "bulk_modulus",
    "shear modulus": "shear_modulus",
    "band gap": "band_gap",
    "formation energy": "formation_energy",
    "cohesive energy": "cohesive_energy",
}

_WHITESPACE_RUN = re.compile(r"\s+")


def _normalize_property(name: str) -> str:
    """Map a property name to its canonical snake_case form."""
    lowered = name.strip().lower()
    return _PROPERTY_ALIASES.get(lowered, _WHITESPACE_RUN.sub("_", lowered))


def _normalize_measurement(measurement: str) -> str:
    """Rewrite known unit aliases inside the measurement string."""
    result = measurement
    for alias, canonical in _UNIT_ALIASES.items():
        # Word-boundary substitution so "K" inside other tokens stays.
        result = re.sub(
            rf"\b{re.escape(alias)}\b",
            canonical,
            result,
            flags=re.IGNORECASE,
        )
    return result


class PropertyNormalizer(ExtractionStep):
    """Step 4: canonicalize property names and measurement units."""

    @property
    def step_name(self) -> str:
        return "property_normalizer"

    @property
    def step_order(self) -> int:
        return 3

    def execute(self, input_chunk: ExtractionChunk) -> ExtractionChunk:
        entities = dict(input_chunk.metadata.get("entities", {}))
        entities["properties"] = [
            _normalize_property(p) for p in entities.get("properties", [])
        ]
        entities["measurements"] = [
            _normalize_measurement(m) for m in entities.get("measurements", [])
        ]
        return ExtractionChunk(
            content=input_chunk.content,
            chunk_type="property",
            _source_span=input_chunk._source_span,
            metadata={**input_chunk.metadata, "entities": entities},
            parent_chunk_id=input_chunk.parent_chunk_id,
        )
