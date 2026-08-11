"""Compute expected.json by mirroring the V2 pipeline (NFM-2679, NFM-2677 B2-B6).

Standalone — no project dependencies. Re-implements the EXACT regex
patterns from the V2 steps in
``apps/api/src/nfm_db/services/extraction/steps/`` so the output
matches what the V2 orchestrator emits in stub mode.

The V2 pipeline steps and their data shapes (read from source):

  RawTextLoader.execute(chunk):
      - normalize whitespace collapse + strip
      - forward _source_span unchanged
      - add metadata["normalized"] = True

  SectionSegmenter.execute_many(chunk):
      - splits on blank line + markdown heading boundary
      - emits 1+ chunks, each chunk_type="section"

  EntityExtractor.execute(chunk):
      - extracts formulas via _FORMULA_RE
      - extracts property names via _PROPERTY_NAMES
      - extracts measurements via _MEASUREMENT_RE
      - chunk_type="entity", metadata["entities"]={formulas,properties,measurements}

  PropertyNormalizer.execute(chunk):
      - canonicalizes property names via _PROPERTY_ALIASES
      - normalizes measurement units via _UNIT_ALIASES
      - chunk_type="property"

  ChunkBuilder.execute(chunk):
      - stamps metadata["summary"]={formula_count,property_count,measurement_count}
      - chunk_type="final"
"""
import json
import re
import sys
from pathlib import Path

# ---- EXACT regex/normalization rules copied from V2 step modules ----
_WHITESPACE_RUN = re.compile(r"\s+")

_FORMULA_RE = re.compile(r"\b(?:[A-Z][a-z]?\d*){2,}\b")

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

_MEASUREMENT_RE = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:K|°C|GPa|MPa|eV|angstrom|Å|cm\^3|g/cm\^3)"
    r"(?:\^?-?\d?)?",
    re.IGNORECASE,
)

_UNIT_ALIASES: dict[str, str] = {
    "angstrom": "Å",
    "angstroms": "Å",
    "deg C": "°C",
    "degrees C": "°C",
    "deg K": "K",
}

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


def normalize_property(name: str) -> str:
    """Map property name to canonical snake_case form."""
    lowered = name.strip().lower()
    return _PROPERTY_ALIASES.get(lowered, _WHITESPACE_RUN.sub("_", lowered))


def normalize_measurement(measurement: str) -> str:
    """Rewrite known unit aliases inside the measurement string."""
    result = measurement
    for alias, canonical in _UNIT_ALIASES.items():
        result = re.sub(
            rf"\b{re.escape(alias)}\b",
            canonical,
            result,
            flags=re.IGNORECASE,
        )
    return result


def find_boundaries(content: str) -> list[int]:
    """Return absolute offsets where each new section begins.

    New section starts at offset 0 and after every blank line that
    precedes a markdown heading (line beginning with #).
    """
    boundaries: list[int] = [0]
    for match in re.finditer(r"\n\n(?=^#{1,6}\s)", content, re.MULTILINE):
        boundaries.append(match.end())
    return sorted(set(boundaries))


def step_raw_text_loader(content: str, parent_span: tuple[int, int]) -> tuple[str, dict]:
    """Step 1: normalize whitespace, forward span."""
    normalized = _WHITESPACE_RUN.sub(" ", content).strip()
    return normalized, {"normalized": True}


def step_section_segmenter(content: str, parent_span: tuple[int, int]) -> list[tuple[str, tuple[int, int]]]:
    """Step 2: split normalized text into section chunks."""
    parent_start, _parent_end = parent_span
    boundaries = find_boundaries(content)
    sections: list[tuple[str, tuple[int, int]]] = []
    for index, start in enumerate(boundaries):
        end = boundaries[index + 1] if index + 1 < len(boundaries) else len(content)
        raw_body = content[start:end]
        body = raw_body.rstrip("\n")
        if not body:
            continue
        span = (parent_start + start, parent_start + start + len(body))
        sections.append((body, span))
    return sections


def step_entity_extractor(content: str) -> dict:
    """Step 3: extract material-science entities into metadata."""
    formulas = sorted(set(_FORMULA_RE.findall(content)))
    properties = []
    lowered = content.lower()
    for name in _PROPERTY_NAMES:
        if name in lowered:
            properties.append(name)
    measurements = sorted(set(_MEASUREMENT_RE.findall(content)))
    return {"formulas": formulas, "properties": properties, "measurements": measurements}


def step_property_normalizer(entities: dict) -> dict:
    """Step 4: canonicalize names and units."""
    out = dict(entities)
    out["properties"] = [normalize_property(p) for p in entities.get("properties", [])]
    out["measurements"] = [normalize_measurement(m) for m in entities.get("measurements", [])]
    return out


def step_chunk_builder(content: str, span: tuple[int, int], entities: dict, parent_metadata: dict) -> dict:
    """Step 5: assemble final chunk with entity-count summary."""
    summary = {
        "formula_count": len(entities.get("formulas", [])),
        "property_count": len(entities.get("properties", [])),
        "measurement_count": len(entities.get("measurements", [])),
    }
    return {
        "content": content,
        "chunk_type": "final",
        "_source_span": list(span),
        "metadata": {
            **parent_metadata,
            "entities": entities,
            "summary": summary,
        },
        "parent_chunk_id": None,
    }


def run_v2_pipeline(source_text: str) -> list[dict]:
    """Run all 5 V2 steps and return list of final chunks."""
    parent_span = (0, len(source_text))

    # Step 1: RawTextLoader
    normalized, raw_metadata = step_raw_text_loader(source_text, parent_span)

    # Step 2: SectionSegmenter
    sections = step_section_segmenter(normalized, parent_span)
    section_count = len(sections)

    finals = []
    for idx, (section_body, span) in enumerate(sections):
        parent_metadata = {
            **raw_metadata,
            "section_index": idx,
            "section_count": section_count,
        }
        # Step 3: EntityExtractor
        entities = step_entity_extractor(section_body)
        # Step 4: PropertyNormalizer
        entities = step_property_normalizer(entities)
        # Step 5: ChunkBuilder
        final = step_chunk_builder(section_body, span, entities, parent_metadata)
        finals.append(final)
    return finals


def main() -> None:
    if len(sys.argv) != 3:
        print("Usage: compute_expected.py SOURCE.txt OUTPUT.json", file=sys.stderr)
        sys.exit(2)
    src = Path(sys.argv[1])
    dst = Path(sys.argv[2])
    text = src.read_text(encoding="utf-8")
    finals = run_v2_pipeline(text)
    dst.write_text(json.dumps(finals, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {len(finals)} final chunks to {dst}")


if __name__ == "__main__":
    main()
