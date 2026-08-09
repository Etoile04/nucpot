"""TDD tests for the PropertyNormalizer step (NFM-2677 B5).

Normalizes property names and measurement units into canonical
strings so downstream ontology mapping can match against a fixed
vocabulary.
"""

from __future__ import annotations

# RED: this import will fail until B5 ships.
from nfm_db.services.extraction import ExtractionChunk
from nfm_db.services.extraction.steps.property_normalizer import (
    PropertyNormalizer,
)


def test_property_normalizer_step_name():
    assert PropertyNormalizer().step_name == "property_normalizer"


def test_property_normalizer_step_order_is_three():
    assert PropertyNormalizer().step_order == 3


def test_property_normalizer_canonicalizes_property_names():
    """``Lattice Constant`` → ``lattice_constant``."""
    step = PropertyNormalizer()
    src = ExtractionChunk(
        content="The Lattice Constant is 5.47 angstrom.",
        chunk_type="entity",
        _source_span=(0, 38),
        metadata={
            "entities": {
                "formulas": [],
                "properties": ["Lattice Constant"],
                "measurements": ["5.47 angstrom"],
            }
        },
    )
    out = step.execute(src)
    props = out.metadata["entities"]["properties"]
    assert "lattice_constant" in props
    assert "Lattice Constant" not in props


def test_property_normalizer_canonicalizes_angstrom_unit():
    """``angstrom`` → ``Å`` in measurement strings."""
    step = PropertyNormalizer()
    src = ExtractionChunk(
        content="lattice constant: 5.47 angstrom",
        chunk_type="entity",
        _source_span=(0, 30),
        metadata={
            "entities": {
                "formulas": [],
                "properties": ["lattice_constant"],
                "measurements": ["5.47 angstrom"],
            }
        },
    )
    out = step.execute(src)
    assert "5.47 Å" in out.metadata["entities"]["measurements"]


def test_property_normalizer_keeps_unknown_units_intact():
    """Unknown units are forwarded unchanged so the chunk isn't lost."""
    step = PropertyNormalizer()
    src = ExtractionChunk(
        content="some value 99 widgets",
        chunk_type="entity",
        _source_span=(0, 21),
        metadata={
            "entities": {
                "formulas": [],
                "properties": [],
                "measurements": ["99 widgets"],
            }
        },
    )
    out = step.execute(src)
    assert "99 widgets" in out.metadata["entities"]["measurements"]


def test_property_normalizer_preserves_content_and_span():
    step = PropertyNormalizer()
    src = ExtractionChunk(
        content="x",
        chunk_type="entity",
        _source_span=(10, 11),
        metadata={"entities": {"formulas": [], "properties": [], "measurements": []}},
    )
    out = step.execute(src)
    assert out.content == "x"
    assert out._source_span == (10, 11)


def test_property_normalizer_returns_property_chunk_type():
    step = PropertyNormalizer()
    src = ExtractionChunk(
        content="x",
        chunk_type="entity",
        _source_span=(0, 1),
        metadata={"entities": {"formulas": [], "properties": [], "measurements": []}},
    )
    out = step.execute(src)
    assert out.chunk_type == "property"


def test_property_normalizer_is_idempotent():
    """Re-running on an already-normalized chunk yields the same
    output (canonical forms are stable)."""
    step = PropertyNormalizer()
    src = ExtractionChunk(
        content="Lattice Constant: 5.47 angstrom",
        chunk_type="entity",
        _source_span=(0, 33),
        metadata={
            "entities": {
                "formulas": [],
                "properties": ["Lattice Constant"],
                "measurements": ["5.47 angstrom"],
            }
        },
    )
    once = step.execute(src)
    twice = step.execute(once)
    assert once == twice
