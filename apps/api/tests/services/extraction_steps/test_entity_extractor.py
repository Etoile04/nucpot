"""TDD tests for the EntityExtractor step (NFM-2677 B4).

Strangler-fig pipeline decomposition — Step 3: EntityExtractor.
Pulls material-science entities (chemical formulas, property names,
numeric measurements with units) out of a section chunk and stamps
them onto ``metadata``.  The chunk content is preserved unchanged so
downstream steps can see the entity annotations alongside the raw
section text.
"""

from __future__ import annotations

# RED: this import will fail until B4 ships.
from nfm_db.services.extraction import ExtractionChunk
from nfm_db.services.extraction.steps.entity_extractor import (
    EntityExtractor,
)


def test_entity_extractor_step_name():
    assert EntityExtractor().step_name == "entity_extractor"


def test_entity_extractor_step_order_is_two():
    assert EntityExtractor().step_order == 2


def test_entity_extractor_extracts_chemical_formulas():
    """Capital-letter + optional lowercase + digits chemical formulas
    are captured (UO2, U3O8, ThO2, Pu239)."""
    step = EntityExtractor()
    src = ExtractionChunk(
        content="UO2 and U3O8 are common nuclear fuel oxides.",
        chunk_type="section",
        _source_span=(0, 41),
        metadata={"section_index": 0},
    )
    out = step.execute(src)
    formulas = out.metadata.get("entities", {}).get("formulas", [])
    assert "UO2" in formulas
    assert "U3O8" in formulas


def test_entity_extractor_extracts_property_names():
    """Known material-science property names (case-insensitive) are
    detected."""
    step = EntityExtractor()
    src = ExtractionChunk(
        content="The lattice constant of UO2 is 5.47 angstrom.",
        chunk_type="section",
        _source_span=(0, 42),
        metadata={},
    )
    out = step.execute(src)
    properties = out.metadata.get("entities", {}).get("properties", [])
    assert "lattice constant" in properties


def test_entity_extractor_extracts_measurements():
    """Numeric measurements with units are captured."""
    step = EntityExtractor()
    src = ExtractionChunk(
        content="The melting point of UO2 is 3120 K.",
        chunk_type="section",
        _source_span=(0, 33),
        metadata={},
    )
    out = step.execute(src)
    measurements = out.metadata.get("entities", {}).get("measurements", [])
    assert any("3120" in m and "K" in m for m in measurements)


def test_entity_extractor_preserves_content_and_span():
    """The step annotates metadata only — it never rewrites content
    or shifts the source span."""
    step = EntityExtractor()
    src = ExtractionChunk(
        content="UO2 has density 10.97 g/cm^3.",
        chunk_type="section",
        _source_span=(100, 130),
        metadata={"section_index": 2},
    )
    out = step.execute(src)
    assert out.content == src.content
    assert out._source_span == (100, 130)


def test_entity_extractor_returns_entity_chunk_type():
    """The output chunk type is 'entity' so the orchestrator and
    downstream steps can distinguish entity-annotated sections from
    plain sections."""
    step = EntityExtractor()
    src = ExtractionChunk(
        content="UO2 is an oxide.",
        chunk_type="section",
        _source_span=(0, 16),
        metadata={},
    )
    out = step.execute(src)
    assert out.chunk_type == "entity"


def test_entity_extractor_is_idempotent():
    """Running the step on its own output must yield an equal chunk —
    the entities are already stamped into metadata and the content
    is unchanged."""
    step = EntityExtractor()
    src = ExtractionChunk(
        content="UO2 lattice constant: 5.47 angstrom.",
        chunk_type="section",
        _source_span=(0, 38),
        metadata={},
    )
    once = step.execute(src)
    twice = step.execute(once)
    assert once == twice


def test_entity_extractor_handles_no_matches():
    """Section with no detectable entities yields an empty entities
    block — not an error."""
    step = EntityExtractor()
    src = ExtractionChunk(
        content="No formulas or numbers here.",
        chunk_type="section",
        _source_span=(0, 27),
        metadata={},
    )
    out = step.execute(src)
    entities = out.metadata.get("entities", {})
    assert entities == {"formulas": [], "properties": [], "measurements": []}
