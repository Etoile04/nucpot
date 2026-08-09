"""TDD tests for the RawTextLoader step (NFM-2677 B2).

Strangler-fig pipeline decomposition — Step 1: RawTextLoader. Loads
and normalizes raw document text. The step is pure and idempotent:
running it twice on the same input must produce equal outputs.

These tests are the RED signal. The implementation under
``nfm_db.services.extraction.steps.raw_text_loader`` does NOT YET
EXIST — the failing import is itself the RED signal.
"""

from __future__ import annotations

import pytest

# RED: this import will fail until B2 ships.
from nfm_db.services.extraction import ExtractionChunk
from nfm_db.services.extraction.steps.raw_text_loader import RawTextLoader


def test_raw_text_loader_step_name():
    """Stable identifier used by orchestrator routing."""
    assert RawTextLoader().step_name == "raw_text_loader"


def test_raw_text_loader_step_order_is_zero():
    """Step 1 of 5 — runs first in the pipeline."""
    assert RawTextLoader().step_order == 0


def test_raw_text_loader_normalizes_runs_of_whitespace():
    """Collapse runs of spaces/tabs/newlines into single spaces; strip
    leading/trailing whitespace."""
    step = RawTextLoader()
    src = ExtractionChunk(
        content="  UO2   lattice\n\nconstant:\t5.47   angstrom  ",
        chunk_type="raw_text",
        _source_span=(0, 46),
        metadata={"source_uri": "https://example.org/paper.md"},
    )
    out = step.execute(src)
    assert out.content == "UO2 lattice constant: 5.47 angstrom"


def test_raw_text_loader_preserves_source_span():
    """The loader never re-spans; provenance stays anchored to the
    original byte range so downstream steps can trace back."""
    step = RawTextLoader()
    src = ExtractionChunk(
        content="hello   world",
        chunk_type="raw_text",
        _source_span=(10, 23),
        metadata={},
    )
    out = step.execute(src)
    assert out._source_span == (10, 23)


def test_raw_text_loader_chunks_type_is_normalized_raw_text():
    """Output is a 'raw_text' chunk with metadata flag indicating it
    has been normalized."""
    step = RawTextLoader()
    src = ExtractionChunk(
        content="text",
        chunk_type="raw_text",
        _source_span=(0, 4),
        metadata={},
    )
    out = step.execute(src)
    assert out.chunk_type == "raw_text"
    assert out.metadata.get("normalized") is True


def test_raw_text_loader_is_idempotent():
    """Re-running on an already-normalized input yields an equal output
    (NFM-2677 AC: 'each step must be independently re-runnable')."""
    step = RawTextLoader()
    src = ExtractionChunk(
        content="  hello   world  ",
        chunk_type="raw_text",
        _source_span=(0, 17),
        metadata={},
    )
    once = step.execute(src)
    twice = step.execute(once)
    assert once == twice


def test_raw_text_loader_parent_chunk_id_propagates():
    """If the input has a parent_chunk_id, the output carries it so the
    orchestrator can stitch the lineage."""
    step = RawTextLoader()
    src = ExtractionChunk(
        content="x",
        chunk_type="raw_text",
        _source_span=(0, 1),
        metadata={},
        parent_chunk_id="root-chunk",
    )
    out = step.execute(src)
    assert out.parent_chunk_id == "root-chunk"
