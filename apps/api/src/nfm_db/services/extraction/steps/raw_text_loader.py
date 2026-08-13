"""Step 1 of the strangler-fig extraction pipeline (NFM-2677 B2).

Loads and normalizes raw document text. The transformation is
deliberately minimal — collapse runs of whitespace into a single
space and trim — so re-running the step on already-normalized input
is a no-op (idempotent).
"""

from __future__ import annotations

import re

from nfm_db.services.extraction import ExtractionChunk, ExtractionStep

_WHITESPACE_RUN = re.compile(r"\s+")


class RawTextLoader(ExtractionStep):
    """Step 1: normalize raw document text.

    The loader never re-spans content: ``_source_span`` is forwarded
    unchanged so downstream steps can trace back to the original byte
    offsets. Only ``content`` and ``metadata`` change.
    """

    @property
    def step_name(self) -> str:
        return "raw_text_loader"

    @property
    def step_order(self) -> int:
        return 0

    def execute(self, input_chunk: ExtractionChunk) -> ExtractionChunk:
        normalized = _WHITESPACE_RUN.sub(" ", input_chunk.content).strip()
        return ExtractionChunk(
            content=normalized,
            chunk_type=input_chunk.chunk_type,
            _source_span=input_chunk._source_span,
            metadata={**input_chunk.metadata, "normalized": True},
            parent_chunk_id=input_chunk.parent_chunk_id,
        )
