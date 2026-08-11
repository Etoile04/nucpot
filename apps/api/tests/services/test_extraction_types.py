"""Tests for clean-slate ExtractionChunk and ExtractionStep ABC (NFM-2679).

Strangler-fig pipeline decomposition (NFM-2677 P1, B1) — the new
``apps/api/src/nfm_db/services/extraction/`` sub-package defines the
core data model and step contract. These tests lock the contract so
sibling B2 tasks can build on it without coupling to the legacy
``extraction_pipeline`` / ``extraction_orchestrator`` modules.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

# These imports DO NOT YET EXIST — they are the target of the GREEN step.
# The failing import is itself the RED signal.
from nfm_db.services.extraction import ExtractionChunk, ExtractionStep

# ---------------------------------------------------------------------------
# ExtractionChunk construction + value semantics
# ---------------------------------------------------------------------------


def test_extraction_chunk_stores_all_fields():
    chunk = ExtractionChunk(
        content="UO2 lattice constant: 5.47 angstrom",
        chunk_type="property",
        _source_span=(0, 38),
        metadata={"step_name": "entity_extractor", "confidence": "high"},
        parent_chunk_id="chunk-abc-123",
    )
    assert chunk.content == "UO2 lattice constant: 5.47 angstrom"
    assert chunk.chunk_type == "property"
    assert chunk._source_span == (0, 38)
    assert chunk.metadata == {
        "step_name": "entity_extractor",
        "confidence": "high",
    }
    assert chunk.parent_chunk_id == "chunk-abc-123"


def test_extraction_chunk_parent_chunk_id_defaults_to_none():
    chunk = ExtractionChunk(
        content="raw markdown",
        chunk_type="raw_text",
        _source_span=(0, 12),
        metadata={},
    )
    assert chunk.parent_chunk_id is None


def test_extraction_chunk_value_equality_across_instances():
    """Chunks with identical fields compare equal — provenance-safe
    deduplication depends on this."""
    a = ExtractionChunk(
        content="same", chunk_type="raw_text",
        _source_span=(0, 4), metadata={"k": 1},
    )
    b = ExtractionChunk(
        content="same", chunk_type="raw_text",
        _source_span=(0, 4), metadata={"k": 1},
    )
    assert a == b


# ---------------------------------------------------------------------------
# ExtractionChunk _source_span validation
# ---------------------------------------------------------------------------


def test_extraction_chunk_rejects_start_greater_than_end():
    with pytest.raises(ValueError, match="start"):
        ExtractionChunk(
            content="x", chunk_type="raw_text",
            _source_span=(10, 5), metadata={},
        )


def test_extraction_chunk_rejects_negative_offsets():
    with pytest.raises(ValueError, match="non-negative"):
        ExtractionChunk(
            content="x", chunk_type="raw_text",
            _source_span=(-1, 5), metadata={},
        )


def test_extraction_chunk_rejects_wrong_arity():
    with pytest.raises(ValueError, match="2"):
        ExtractionChunk(
            content="x", chunk_type="raw_text",
            _source_span=(0,), metadata={},
        )


def test_extraction_chunk_allows_zero_width_span():
    """Empty selections (start == end) are valid pointers."""
    chunk = ExtractionChunk(
        content="", chunk_type="raw_text",
        _source_span=(5, 5), metadata={},
    )
    assert chunk._source_span == (5, 5)


def test_extraction_chunk_is_immutable():
    chunk = ExtractionChunk(
        content="x", chunk_type="raw_text",
        _source_span=(0, 1), metadata={},
    )
    with pytest.raises(FrozenInstanceError):
        chunk.content = "tampered"


# ---------------------------------------------------------------------------
# ExtractionStep ABC + concrete subclass
# ---------------------------------------------------------------------------


def test_extraction_step_abc_cannot_be_instantiated():
    with pytest.raises(TypeError):
        ExtractionStep()  # type: ignore[abstract]


def test_concrete_extraction_step_round_trips_chunk():
    class IdentityStep(ExtractionStep):
        @property
        def step_name(self) -> str:
            return "identity"

        @property
        def step_order(self) -> int:
            return 42

        def execute(self, input_chunk: ExtractionChunk) -> ExtractionChunk:
            # Forward chunk verbatim — chain identity for orchestrator tests.
            return input_chunk

    step = IdentityStep()
    src = ExtractionChunk(
        content="payload", chunk_type="raw_text",
        _source_span=(0, 7), metadata={},
    )
    out = step.execute(src)
    assert out == src
    assert step.step_name == "identity"
    assert step.step_order == 42


# ---------------------------------------------------------------------------
# Clean-slate boundary: no leakage from legacy pipeline
# ---------------------------------------------------------------------------


def test_extraction_subpackage_does_not_re_export_legacy_pipeline():
    """The new package must stand alone — downstream steps must not
    accidentally couple to the legacy trigger_extraction."""
    import nfm_db.services.extraction as new_pkg

    forbidden = {"trigger_extraction", "ExtractionOrchestrator"}
    public = set(getattr(new_pkg, "__all__", ())) | set(dir(new_pkg))
    leaked = forbidden & public
    assert not leaked, (
        f"nfm_db.services.extraction leaked legacy names: {leaked}"
    )
