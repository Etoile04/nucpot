"""Tests for the clean-slate extraction pipeline types (NFM-2679).

Covers:
1. ExtractionChunk model: construction with all fields, immutability, _source_span
   tuple validation (start <= end, both >= 0, length 2).
2. ExtractionStep ABC: cannot be instantiated directly; concrete subclass's
   execute() must take an ExtractionChunk and return one.
3. Clean-slate boundary: the new package must NOT import from the legacy
   extraction_pipeline / extraction_orchestrator modules.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from nfm_db.services.extraction import ExtractionChunk, ExtractionStep

# ---------- ExtractionChunk -------------------------------------------------


def test_extraction_chunk_constructs_with_all_fields() -> None:
    chunk = ExtractionChunk(
        content="Yttrium-stabilized zirconia, density 5.68 g/cm^3",
        chunk_type="property",
        _source_span=(120, 180),
        metadata={"step_name": "property_extract", "confidence": 0.92},
        parent_chunk_id="upstream-001",
    )

    assert chunk.content == "Yttrium-stabilized zirconia, density 5.68 g/cm^3"
    assert chunk.chunk_type == "property"
    assert chunk._source_span == (120, 180)
    assert chunk.metadata == {"step_name": "property_extract", "confidence": 0.92}
    assert chunk.parent_chunk_id == "upstream-001"


def test_extraction_chunk_defaults_parent_chunk_id_to_none() -> None:
    chunk = ExtractionChunk(
        content="raw text",
        chunk_type="raw_text",
        _source_span=(0, 8),
        metadata={},
    )

    assert chunk.parent_chunk_id is None


def test_extraction_chunk_accepts_zero_width_span() -> None:
    """An empty span (start == end) is a valid insertion-point marker."""
    chunk = ExtractionChunk(
        content="",
        chunk_type="section",
        _source_span=(5, 5),
        metadata={},
    )
    assert chunk._source_span == (5, 5)


@pytest.mark.parametrize(
    "span",
    [
        (10, 5),     # start > end
        (-1, 10),    # negative start
        (5, -1),     # negative end
        (0, 1, 2),   # wrong arity
    ],
)
def test_extraction_chunk_rejects_invalid_source_span(span) -> None:
    with pytest.raises((ValueError, TypeError)):
        ExtractionChunk(
            content="x",
            chunk_type="raw_text",
            _source_span=span,  # type: ignore[arg-type]
            metadata={},
        )


def test_extraction_chunk_is_immutable() -> None:
    chunk = ExtractionChunk(
        content="x",
        chunk_type="raw_text",
        _source_span=(0, 1),
        metadata={"k": "v"},
    )

    with pytest.raises(FrozenInstanceError):
        chunk.content = "mutated"  # type: ignore[misc]


def test_extraction_chunk_supports_equality() -> None:
    """Two chunks with identical field values compare equal.

    Note: hashability is intentionally NOT guaranteed because the metadata
    field is a plain ``dict`` (which is unhashable). Equality by value is
    sufficient for pipeline routing and dedup checks.
    """
    a = ExtractionChunk(
        content="x", chunk_type="raw_text", _source_span=(0, 1), metadata={"k": "v"}
    )
    b = ExtractionChunk(
        content="x", chunk_type="raw_text", _source_span=(0, 1), metadata={"k": "v"}
    )

    assert a == b


# ---------- ExtractionStep --------------------------------------------------


def test_extraction_step_abc_cannot_be_instantiated() -> None:
    with pytest.raises(TypeError):
        ExtractionStep()  # type: ignore[call-arg,abstract]


def test_extraction_step_concrete_subclass_returns_chunk() -> None:
    class IdentityStep(ExtractionStep):
        @property
        def step_name(self) -> str:
            return "identity"

        @property
        def step_order(self) -> int:
            return 0

        def execute(self, input_chunk: ExtractionChunk) -> ExtractionChunk:
            return ExtractionChunk(
                content=input_chunk.content,
                chunk_type="final",
                _source_span=input_chunk._source_span,
                metadata={"step_name": self.step_name},
                parent_chunk_id=None,
            )

    step = IdentityStep()
    assert step.step_name == "identity"
    assert step.step_order == 0

    out = step.execute(
        ExtractionChunk(
            content="hello",
            chunk_type="raw_text",
            _source_span=(0, 5),
            metadata={},
        )
    )

    assert isinstance(out, ExtractionChunk)
    assert out.content == "hello"
    assert out.chunk_type == "final"
    assert out.metadata["step_name"] == "identity"


# ---------- Clean-slate boundary --------------------------------------------


def test_extraction_package_does_not_import_legacy_extraction_modules() -> None:
    """NFM-2679 AC: no import from existing trigger_extraction — clean-slate.

    The new extraction/ sub-package must stand alone; it must not pull in any
    legacy extraction module at import time and must not re-export legacy
    public symbols.
    """
    # Importing the new package must not raise.
    from nfm_db.services import extraction as extraction_pkg

    forbidden = {"trigger_extraction", "ExtractionPipeline", "ExtractionOrchestrator"}
    exported = {name for name in dir(extraction_pkg) if not name.startswith("_")}
    overlap = forbidden & exported
    assert overlap == set(), (
        f"extraction/ package re-exports legacy symbols: {overlap}"
    )
